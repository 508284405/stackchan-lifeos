#pragma once

#include <cstddef>
#include <cstdint>

namespace lifeos::runtime {

constexpr std::size_t kFirmwareUpdateChunkBytes = 3072;
constexpr std::uint32_t kFirmwareUpdateImageBytes = 4U * 1024U * 1024U;
constexpr std::size_t kFirmwareManifestTextBytes = 768;

// No pointers into the transient JSON receive buffer survive admission.
struct FirmwareUpdateManifest {
  char image_ref[97]{};
  char version[65]{};
  char hardware_id[65]{};
  char protocol_version[17]{"lifeos.v1"};
  char partition_layout[17]{"ota_ab_v1"};
  std::uint32_t size_bytes{0};
  char sha256_hex[65]{};
  std::uint32_t secure_version{0};
  char signature_algorithm[25]{"ecdsa-p256-sha256"};
  std::uint8_t signature_der[80]{};
  std::size_t signature_der_size{0};
};

enum class FirmwareUpdateError {
  None,
  InvalidManifest,
  UnsafeState,
  Busy,
  UnsupportedLayout,
  TrustUnavailable,
  HardwareMismatch,
  InvalidSignature,
  VersionMismatch,
  RollbackUnavailable,
  NotReceiving,
  InvalidChunk,
  OffsetMismatch,
  IncompleteImage,
  HashMismatch,
  StorageFailure,
  InvalidImage,
  BootSelectionFailure,
  Aborted,
};

enum class FirmwareUpdateState { Idle, Receiving, ReadyToReboot, Failed, Aborted };

const char* firmware_update_error_name(FirmwareUpdateError error) noexcept;
const char* firmware_update_state_name(FirmwareUpdateState state) noexcept;

// Returns the exact signed UTF-8/ASCII text, including its final newline.
// Both caller-provided output capacity and every manifest string are bounded.
bool firmware_manifest_text(const FirmwareUpdateManifest& manifest, char* output,
                            std::size_t capacity, std::size_t& written) noexcept;

class FirmwareUpdateBackend {
 public:
  virtual ~FirmwareUpdateBackend() = default;
  virtual FirmwareUpdateError availability() const = 0;
  virtual FirmwareUpdateError verify_manifest(const FirmwareUpdateManifest& manifest,
                                             const char* text, std::size_t size) = 0;
  virtual FirmwareUpdateError begin_write(const FirmwareUpdateManifest& manifest) = 0;
  virtual bool write_chunk(const std::uint8_t* data, std::size_t size) = 0;
  virtual FirmwareUpdateError finish_image(const FirmwareUpdateManifest& manifest) = 0;
  virtual bool select_boot_partition() = 0;
  virtual void abort_write() = 0;
};

class FirmwareUpdateCore {
 public:
  explicit FirmwareUpdateCore(FirmwareUpdateBackend& backend) : backend_(backend) {}

  FirmwareUpdateError begin(const FirmwareUpdateManifest& manifest, bool safety_permitted);
  FirmwareUpdateError write(std::uint32_t offset, const std::uint8_t* data, std::size_t size);
  FirmwareUpdateError commit();
  void abort();

  FirmwareUpdateState state() const noexcept { return state_; }
  FirmwareUpdateError last_error() const noexcept { return last_error_; }
  std::uint32_t received_bytes() const noexcept { return received_bytes_; }
  const FirmwareUpdateManifest& manifest() const noexcept { return manifest_; }

 private:
  FirmwareUpdateError fail(FirmwareUpdateError error);
  FirmwareUpdateBackend& backend_;
  FirmwareUpdateManifest manifest_{};
  FirmwareUpdateState state_{FirmwareUpdateState::Idle};
  FirmwareUpdateError last_error_{FirmwareUpdateError::None};
  std::uint32_t received_bytes_{0};
};

}  // namespace lifeos::runtime
