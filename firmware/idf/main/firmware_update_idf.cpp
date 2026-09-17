#include "firmware_update_idf.hpp"
#include "lifeos_ota_trust_key.hpp"

#include <cstring>

#include "esp_app_desc.h"
#include "esp_image_format.h"
#include "mbedtls/ecp.h"
#include "mbedtls/pk.h"
#include "sdkconfig.h"

namespace lifeos::idf {
namespace {

using Error = runtime::FirmwareUpdateError;

bool ota_slot(const esp_partition_t* partition) {
  return partition != nullptr && partition->type == ESP_PARTITION_TYPE_APP &&
         (partition->subtype == ESP_PARTITION_SUBTYPE_APP_OTA_0 ||
          partition->subtype == ESP_PARTITION_SUBTYPE_APP_OTA_1);
}

bool recoverable_layout() {
#if defined(CONFIG_BOOTLOADER_APP_ROLLBACK_ENABLE) && CONFIG_BOOTLOADER_APP_ROLLBACK_ENABLE
  const auto* running = esp_ota_get_running_partition();
  const auto* slot0 = esp_partition_find_first(ESP_PARTITION_TYPE_APP,
                                              ESP_PARTITION_SUBTYPE_APP_OTA_0, nullptr);
  const auto* slot1 = esp_partition_find_first(ESP_PARTITION_TYPE_APP,
                                              ESP_PARTITION_SUBTYPE_APP_OTA_1, nullptr);
  const auto* data = esp_partition_find_first(ESP_PARTITION_TYPE_DATA,
                                             ESP_PARTITION_SUBTYPE_DATA_OTA, nullptr);
  return ota_slot(running) && slot0 != nullptr && slot1 != nullptr &&
         slot0->address != slot1->address &&
         slot0->size >= runtime::kFirmwareUpdateImageBytes &&
         slot1->size >= runtime::kFirmwareUpdateImageBytes && data != nullptr && data->size >= 0x2000;
#else
  return false;
#endif
}

bool parse_trusted_key(mbedtls_pk_context& key) {
  const auto* pem = reinterpret_cast<const unsigned char*>(kLifeosOtaTrustedPublicKey);
  const auto length = std::strlen(kLifeosOtaTrustedPublicKey);
  if (length == 0 || mbedtls_pk_parse_public_key(&key, pem, length + 1) != 0 ||
      !mbedtls_pk_can_do(&key, MBEDTLS_PK_ECDSA)) return false;
  const auto* ec = mbedtls_pk_ec(key);
  return ec != nullptr && mbedtls_ecp_keypair_get_group_id(ec) == MBEDTLS_ECP_DP_SECP256R1;
}

bool image_length(const esp_partition_t* partition, std::uint32_t& length) {
  if (partition == nullptr) return false;
  const esp_partition_pos_t position{partition->address, partition->size};
  esp_image_metadata_t metadata{};
  if (esp_image_verify(ESP_IMAGE_VERIFY_SILENT, &position, &metadata) != ESP_OK ||
      metadata.image_len == 0 || metadata.image_len > partition->size) return false;
  length = metadata.image_len;
  return true;
}

}  // namespace

EspFirmwareUpdateBackend::EspFirmwareUpdateBackend() { mbedtls_sha256_init(&sha_); }

EspFirmwareUpdateBackend::~EspFirmwareUpdateBackend() {
  abort_write();
  mbedtls_sha256_free(&sha_);
}

bool EspFirmwareUpdateBackend::set_hardware_id(const char* hardware_id) {
  hardware_id_[0] = '\0';
  if (hardware_id == nullptr) return false;
  std::size_t size = 0;
  while (size < sizeof(hardware_id_) && hardware_id[size] != '\0') ++size;
  if (size == 0 || size >= sizeof(hardware_id_)) return false;
  std::memcpy(hardware_id_, hardware_id, size + 1);
  return true;
}

Error EspFirmwareUpdateBackend::availability() const {
  if (!recoverable_layout()) return Error::UnsupportedLayout;
  if (hardware_id_[0] == '\0') return Error::HardwareMismatch;
  if (trust_valid_ < 0) {
    mbedtls_pk_context key;
    mbedtls_pk_init(&key);
    trust_valid_ = parse_trusted_key(key) ? 1 : 0;
    mbedtls_pk_free(&key);
  }
  if (trust_valid_ != 1) return Error::TrustUnavailable;
  esp_ota_img_states_t state = ESP_OTA_IMG_UNDEFINED;
  if (esp_ota_get_state_partition(esp_ota_get_running_partition(), &state) != ESP_OK ||
      state != ESP_OTA_IMG_VALID) return Error::RollbackUnavailable;
  return Error::None;
}

Error EspFirmwareUpdateBackend::verify_manifest(const runtime::FirmwareUpdateManifest& manifest,
                                               const char* text, std::size_t size) {
  const auto ready = availability();
  if (ready != Error::None) return ready;
  if (std::strcmp(manifest.hardware_id, hardware_id_) != 0) return Error::HardwareMismatch;
  const auto* running_description = esp_app_get_description();
  if (manifest.secure_version < running_description->secure_version) return Error::VersionMismatch;
  mbedtls_pk_context key;
  mbedtls_pk_init(&key);
  std::uint8_t digest[32]{};
  const bool signature_ok = parse_trusted_key(key) &&
      mbedtls_sha256(reinterpret_cast<const unsigned char*>(text), size, digest, 0) == 0 &&
      mbedtls_pk_verify(&key, MBEDTLS_MD_SHA256, digest, sizeof(digest),
                        manifest.signature_der, manifest.signature_der_size) == 0;
  mbedtls_pk_free(&key);
  return signature_ok ? Error::None : Error::InvalidSignature;
}

Error EspFirmwareUpdateBackend::begin_write(const runtime::FirmwareUpdateManifest& manifest) {
  if (open_) return Error::Busy;
  const auto ready = availability();
  if (ready != Error::None) return ready;
  const auto* running = esp_ota_get_running_partition();
  target_ = esp_ota_get_next_update_partition(running);
  if (!ota_slot(target_) || target_->address == running->address ||
      manifest.size_bytes > target_->size) return Error::UnsupportedLayout;
  verified_ = false;
  if (mbedtls_sha256_starts(&sha_, 0) != 0) return Error::StorageFailure;
  // App-only update. Neither bootloader, partition table, NVS nor the running
  // app is erased; an interrupted transfer leaves the old boot choice intact.
  // Erase incrementally with bounded chunks instead of stalling USB/main for
  // a full multi-megabyte erase before the first command can be acknowledged.
  if (esp_ota_begin(target_, OTA_WITH_SEQUENTIAL_WRITES, &handle_) != ESP_OK) {
    target_ = nullptr;
    return Error::StorageFailure;
  }
  open_ = true;
  return Error::None;
}

bool EspFirmwareUpdateBackend::write_chunk(const std::uint8_t* data, std::size_t size) {
  return open_ && esp_ota_write(handle_, data, size) == ESP_OK &&
         mbedtls_sha256_update(&sha_, data, size) == 0;
}

Error EspFirmwareUpdateBackend::finish_image(const runtime::FirmwareUpdateManifest& manifest) {
  if (!open_ || target_ == nullptr) return Error::NotReceiving;
  std::uint8_t digest[32]{};
  if (mbedtls_sha256_finish(&sha_, digest) != 0) return Error::StorageFailure;
  constexpr char hex[] = "0123456789abcdef";
  unsigned difference = 0;
  for (std::size_t i = 0; i < sizeof(digest); ++i) {
    difference |= static_cast<unsigned>(manifest.sha256_hex[i * 2] ^ hex[digest[i] >> 4]);
    difference |= static_cast<unsigned>(manifest.sha256_hex[i * 2 + 1] ^ hex[digest[i] & 0x0f]);
  }
  if (difference != 0) return Error::HashMismatch;
  std::uint32_t validated_size = 0;
  if (!image_length(target_, validated_size) || validated_size != manifest.size_bytes) {
    return Error::InvalidImage;
  }
  esp_app_desc_t description{};
  if (esp_ota_get_partition_description(target_, &description) != ESP_OK) return Error::InvalidImage;
  const auto* running = esp_app_get_description();
  if (std::strncmp(description.version, manifest.version, sizeof(description.version)) != 0 ||
      std::strncmp(description.project_name, running->project_name, sizeof(description.project_name)) != 0 ||
      description.secure_version != manifest.secure_version) return Error::VersionMismatch;
  // esp_ota_end performs ESP image/chip/checksum validation. Authentication was
  // already established by the provisioned P-256 key over the manifest and this
  // exact image SHA-256; a plain checksum is never treated as a signature.
  const auto result = esp_ota_end(handle_);
  open_ = false;  // ESP-IDF frees this handle on both success and failure.
  handle_ = 0;
  if (result != ESP_OK) return Error::InvalidImage;
  verified_ = true;
  return Error::None;
}

bool EspFirmwareUpdateBackend::select_boot_partition() {
  if (!verified_ || target_ == nullptr) return false;
  if (esp_ota_set_boot_partition(target_) != ESP_OK) return false;
  verified_ = false;
  target_ = nullptr;
  return true;
}

void EspFirmwareUpdateBackend::abort_write() {
  if (open_) esp_ota_abort(handle_);
  open_ = false;
  handle_ = 0;
  target_ = nullptr;
  verified_ = false;
}

bool running_firmware_image_sha256(char* output, std::size_t capacity) {
  if (output == nullptr || capacity < 65) return false;
  output[0] = '\0';
  const auto* running = esp_ota_get_running_partition();
  std::uint32_t size = 0;
  if (!image_length(running, size)) return false;
  mbedtls_sha256_context digest;
  mbedtls_sha256_init(&digest);
  bool ok = mbedtls_sha256_starts(&digest, 0) == 0;
  std::uint8_t block[1024]{};
  for (std::uint32_t offset = 0; ok && offset < size;) {
    const std::size_t count = size - offset < sizeof(block) ? size - offset : sizeof(block);
    ok = esp_partition_read(running, offset, block, count) == ESP_OK &&
         mbedtls_sha256_update(&digest, block, count) == 0;
    offset += static_cast<std::uint32_t>(count);
  }
  std::uint8_t hash[32]{};
  if (ok) ok = mbedtls_sha256_finish(&digest, hash) == 0;
  mbedtls_sha256_free(&digest);
  if (!ok) return false;
  constexpr char hex[] = "0123456789abcdef";
  for (std::size_t i = 0; i < sizeof(hash); ++i) {
    output[2 * i] = hex[hash[i] >> 4];
    output[2 * i + 1] = hex[hash[i] & 0xf];
  }
  output[64] = '\0';
  return true;
}

FirmwareBootResult confirm_firmware_boot(bool local_safety_ok) {
#if defined(CONFIG_BOOTLOADER_APP_ROLLBACK_ENABLE) && CONFIG_BOOTLOADER_APP_ROLLBACK_ENABLE
  const auto* running = esp_ota_get_running_partition();
  if (!ota_slot(running)) return FirmwareBootResult::NotPending;
  esp_ota_img_states_t state = ESP_OTA_IMG_UNDEFINED;
  const auto query = esp_ota_get_state_partition(running, &state);
  if (query == ESP_OK && state == ESP_OTA_IMG_PENDING_VERIFY) {
    if (!local_safety_ok) {
      return esp_ota_mark_app_invalid_rollback_and_reboot() == ESP_OK
                 ? FirmwareBootResult::RollbackRequested : FirmwareBootResult::RollbackUnavailable;
    }
    return esp_ota_mark_app_valid_cancel_rollback() == ESP_OK
               ? FirmwareBootResult::Accepted : FirmwareBootResult::ConfirmationFailed;
  }
  if (query == ESP_ERR_NOT_FOUND || (query == ESP_OK && state == ESP_OTA_IMG_UNDEFINED)) {
    // First locally provisioned OTA-slot boot has no otadata yet. Establish
    // the locally tested app as VALID before advertising recoverable updates.
    if (!local_safety_ok) return FirmwareBootResult::ConfirmationFailed;
    if (esp_ota_set_boot_partition(running) != ESP_OK ||
        esp_ota_mark_app_valid_cancel_rollback() != ESP_OK) return FirmwareBootResult::ConfirmationFailed;
    return FirmwareBootResult::Accepted;
  }
  if (query != ESP_OK || state != ESP_OTA_IMG_VALID) return FirmwareBootResult::ConfirmationFailed;
#else
  (void)local_safety_ok;
#endif
  return FirmwareBootResult::NotPending;
}

const char* firmware_boot_result_name(FirmwareBootResult result) noexcept {
  switch (result) {
    case FirmwareBootResult::NotPending: return "not_pending";
    case FirmwareBootResult::Accepted: return "accepted_locally";
    case FirmwareBootResult::RollbackRequested: return "rollback_requested";
    case FirmwareBootResult::RollbackUnavailable: return "rollback_unavailable";
    case FirmwareBootResult::ConfirmationFailed: return "confirmation_failed";
  }
  return "unknown";
}

}  // namespace lifeos::idf
