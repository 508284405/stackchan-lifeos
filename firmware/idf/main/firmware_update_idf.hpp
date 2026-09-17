#pragma once

#include "lifeos/runtime/firmware_update.hpp"

#include "esp_ota_ops.h"
#include "mbedtls/sha256.h"

namespace lifeos::idf {

enum class FirmwareBootResult {
  NotPending,
  Accepted,
  RollbackRequested,
  RollbackUnavailable,
  ConfirmationFailed,
};

// Call after local board/safety self-test, before waiting for USB or Bridge.
// This function never clears a motion/pause/fault gate.
FirmwareBootResult confirm_firmware_boot(bool local_safety_ok);
const char* firmware_boot_result_name(FirmwareBootResult result) noexcept;
// Hashes exact validated application-image bytes, not the ELF or partition's
// stored checksum. Compute once at boot and reuse the resulting public digest.
bool running_firmware_image_sha256(char* output, std::size_t capacity);

class EspFirmwareUpdateBackend final : public runtime::FirmwareUpdateBackend {
 public:
  EspFirmwareUpdateBackend();
  ~EspFirmwareUpdateBackend() override;
  EspFirmwareUpdateBackend(const EspFirmwareUpdateBackend&) = delete;
  EspFirmwareUpdateBackend& operator=(const EspFirmwareUpdateBackend&) = delete;

  bool set_hardware_id(const char* hardware_id);
  bool runtime_ready() const { return availability() == runtime::FirmwareUpdateError::None; }
  runtime::FirmwareUpdateError availability() const override;
  runtime::FirmwareUpdateError verify_manifest(const runtime::FirmwareUpdateManifest& manifest,
                                              const char* text, std::size_t size) override;
  runtime::FirmwareUpdateError begin_write(const runtime::FirmwareUpdateManifest& manifest) override;
  bool write_chunk(const std::uint8_t* data, std::size_t size) override;
  runtime::FirmwareUpdateError finish_image(const runtime::FirmwareUpdateManifest& manifest) override;
  bool select_boot_partition() override;
  void abort_write() override;

 private:
  char hardware_id_[65]{};
  const esp_partition_t* target_{nullptr};
  esp_ota_handle_t handle_{0};
  bool open_{false};
  bool verified_{false};
  mutable int trust_valid_{-1};
  mbedtls_sha256_context sha_{};
};

}  // namespace lifeos::idf
