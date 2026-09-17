#include "lifeos/runtime/firmware_update.hpp"

#include <cstdio>
#include <cstring>

namespace lifeos::runtime {
namespace {

template <std::size_t N>
bool token(const char (&value)[N]) {
  const auto* end = static_cast<const char*>(std::memchr(value, '\0', N));
  if (end == nullptr || end == value) return false;
  for (const char* cursor = value; cursor != end; ++cursor) {
    const char c = *cursor;
    if (!((c >= 'a' && c <= 'z') || (c >= 'A' && c <= 'Z') ||
          (c >= '0' && c <= '9') || c == '-' || c == '_' || c == '.' || c == ':')) {
      return false;
    }
  }
  return true;
}

bool valid(const FirmwareUpdateManifest& manifest) {
  if (!token(manifest.image_ref) || !token(manifest.version) ||
      !token(manifest.hardware_id) || !token(manifest.protocol_version) ||
      !token(manifest.partition_layout) || !token(manifest.signature_algorithm)) return false;
  if (std::strlen(manifest.version) > 31 ||
      std::strcmp(manifest.protocol_version, "lifeos.v1") != 0 ||
      std::strcmp(manifest.partition_layout, "ota_ab_v1") != 0 ||
      std::strcmp(manifest.signature_algorithm, "ecdsa-p256-sha256") != 0 ||
      manifest.size_bytes == 0 || manifest.size_bytes > kFirmwareUpdateImageBytes ||
      manifest.signature_der_size == 0 ||
      manifest.signature_der_size > sizeof(manifest.signature_der) ||
      manifest.sha256_hex[64] != '\0') return false;
  for (std::size_t i = 0; i < 64; ++i) {
    const char c = manifest.sha256_hex[i];
    if (!((c >= '0' && c <= '9') || (c >= 'a' && c <= 'f'))) return false;
  }
  return true;
}

}  // namespace

bool firmware_manifest_text(const FirmwareUpdateManifest& manifest, char* output,
                            std::size_t capacity, std::size_t& written) noexcept {
  written = 0;
  if (output == nullptr || capacity == 0 || !valid(manifest)) return false;
  const int count = std::snprintf(
      output, capacity, "lifeos-firmware-v1\n%s\n%s\n%s\n%s\n%s\n%u\n%s\n%u\n",
      manifest.image_ref, manifest.version, manifest.hardware_id,
      manifest.protocol_version, manifest.partition_layout,
      static_cast<unsigned>(manifest.size_bytes), manifest.sha256_hex,
      static_cast<unsigned>(manifest.secure_version));
  if (count < 0 || static_cast<std::size_t>(count) >= capacity) return false;
  written = static_cast<std::size_t>(count);
  return true;
}

FirmwareUpdateError FirmwareUpdateCore::begin(const FirmwareUpdateManifest& manifest,
                                             bool safety_permitted) {
  if (state_ == FirmwareUpdateState::Receiving || state_ == FirmwareUpdateState::ReadyToReboot) {
    return FirmwareUpdateError::Busy;
  }
  if (!safety_permitted) return FirmwareUpdateError::UnsafeState;
  char signed_text[kFirmwareManifestTextBytes]{};
  std::size_t text_size = 0;
  if (!firmware_manifest_text(manifest, signed_text, sizeof(signed_text), text_size)) {
    return FirmwareUpdateError::InvalidManifest;
  }
  auto error = backend_.availability();
  if (error != FirmwareUpdateError::None) return error;
  error = backend_.verify_manifest(manifest, signed_text, text_size);
  if (error != FirmwareUpdateError::None) return error;
  // Signature, exact hardware identity, rollback layout and local safety have
  // all been checked before the backend can erase the inactive app slot.
  error = backend_.begin_write(manifest);
  if (error != FirmwareUpdateError::None) return fail(error);
  manifest_ = manifest;
  received_bytes_ = 0;
  last_error_ = FirmwareUpdateError::None;
  state_ = FirmwareUpdateState::Receiving;
  return FirmwareUpdateError::None;
}

FirmwareUpdateError FirmwareUpdateCore::write(std::uint32_t offset,
                                             const std::uint8_t* data, std::size_t size) {
  if (state_ != FirmwareUpdateState::Receiving) return FirmwareUpdateError::NotReceiving;
  if (data == nullptr || size == 0 || size > kFirmwareUpdateChunkBytes ||
      size > manifest_.size_bytes - received_bytes_) return fail(FirmwareUpdateError::InvalidChunk);
  // Envelope retries are deduplicated upstream. Never seek backwards or write
  // a replayed chunk a second time, even after the bounded envelope window.
  if (offset != received_bytes_) return fail(FirmwareUpdateError::OffsetMismatch);
  if (!backend_.write_chunk(data, size)) return fail(FirmwareUpdateError::StorageFailure);
  received_bytes_ += static_cast<std::uint32_t>(size);
  return FirmwareUpdateError::None;
}

FirmwareUpdateError FirmwareUpdateCore::commit() {
  if (state_ != FirmwareUpdateState::Receiving) return FirmwareUpdateError::NotReceiving;
  if (received_bytes_ != manifest_.size_bytes) return fail(FirmwareUpdateError::IncompleteImage);
  const auto error = backend_.finish_image(manifest_);
  if (error != FirmwareUpdateError::None) return fail(error);
  if (!backend_.select_boot_partition()) return fail(FirmwareUpdateError::BootSelectionFailure);
  state_ = FirmwareUpdateState::ReadyToReboot;
  last_error_ = FirmwareUpdateError::None;
  return FirmwareUpdateError::None;
}

FirmwareUpdateError FirmwareUpdateCore::fail(FirmwareUpdateError error) {
  backend_.abort_write();
  state_ = FirmwareUpdateState::Failed;
  last_error_ = error;
  return error;
}

void FirmwareUpdateCore::abort() {
  if (state_ != FirmwareUpdateState::Receiving) return;
  backend_.abort_write();
  state_ = FirmwareUpdateState::Aborted;
  last_error_ = FirmwareUpdateError::Aborted;
}

const char* firmware_update_error_name(FirmwareUpdateError error) noexcept {
  switch (error) {
    case FirmwareUpdateError::None: return "none";
    case FirmwareUpdateError::InvalidManifest: return "invalid_manifest";
    case FirmwareUpdateError::UnsafeState: return "unsafe_state";
    case FirmwareUpdateError::Busy: return "update_busy";
    case FirmwareUpdateError::UnsupportedLayout: return "unsupported_partition_layout";
    case FirmwareUpdateError::TrustUnavailable: return "update_trust_unavailable";
    case FirmwareUpdateError::HardwareMismatch: return "hardware_mismatch";
    case FirmwareUpdateError::InvalidSignature: return "invalid_signature";
    case FirmwareUpdateError::VersionMismatch: return "image_version_mismatch";
    case FirmwareUpdateError::RollbackUnavailable: return "rollback_unavailable";
    case FirmwareUpdateError::NotReceiving: return "update_not_receiving";
    case FirmwareUpdateError::InvalidChunk: return "invalid_update_chunk";
    case FirmwareUpdateError::OffsetMismatch: return "update_offset_mismatch";
    case FirmwareUpdateError::IncompleteImage: return "incomplete_update_image";
    case FirmwareUpdateError::HashMismatch: return "update_hash_mismatch";
    case FirmwareUpdateError::StorageFailure: return "update_storage_failure";
    case FirmwareUpdateError::InvalidImage: return "invalid_update_image";
    case FirmwareUpdateError::BootSelectionFailure: return "update_boot_selection_failed";
    case FirmwareUpdateError::Aborted: return "update_aborted";
  }
  return "unknown_update_error";
}

const char* firmware_update_state_name(FirmwareUpdateState state) noexcept {
  switch (state) {
    case FirmwareUpdateState::Idle: return "idle";
    case FirmwareUpdateState::Receiving: return "receiving";
    case FirmwareUpdateState::ReadyToReboot: return "ready_to_reboot";
    case FirmwareUpdateState::Failed: return "failed";
    case FirmwareUpdateState::Aborted: return "aborted";
  }
  return "unknown";
}

}  // namespace lifeos::runtime
