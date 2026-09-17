#include "lifeos/runtime/firmware_update.hpp"

#include <algorithm>
#include <cassert>
#include <cstring>
#include <iostream>
#include <string>
#include <vector>

using namespace lifeos::runtime;

namespace {

FirmwareUpdateManifest manifest() {
  FirmwareUpdateManifest value;
  std::strcpy(value.image_ref, "release-1");
  std::strcpy(value.version, "1.0.0");
  std::strcpy(value.hardware_id, "02:00:00:00:00:01");
  std::memset(value.sha256_hex, 'a', 64);
  value.size_bytes = 4;
  value.secure_version = 2;
  value.signature_der[0] = 0x30;
  value.signature_der_size = 1;
  return value;
}

// Storage/authentication fault-injection boundary. This tests core ordering and
// crash consistency; it is not a substitute for ESP-IDF crypto or power-cut HIL.
struct FakeBackend final : FirmwareUpdateBackend {
  FirmwareUpdateError ready{FirmwareUpdateError::None};
  FirmwareUpdateError signature{FirmwareUpdateError::None};
  FirmwareUpdateError begin_error{FirmwareUpdateError::None};
  FirmwareUpdateError finish_error{FirmwareUpdateError::None};
  bool write_ok{true};
  bool boot_ok{true};
  bool open{false};
  bool boot_is_old{true};
  bool image_checked{false};
  unsigned erase_calls{0};
  unsigned abort_calls{0};
  unsigned boot_calls{0};
  std::string verified_text;
  std::vector<unsigned char> inactive;

  FirmwareUpdateError availability() const override { return ready; }
  FirmwareUpdateError verify_manifest(const FirmwareUpdateManifest&, const char* text,
                                      std::size_t size) override {
    assert(erase_calls == 0);
    verified_text.assign(text, size);
    return signature;
  }
  FirmwareUpdateError begin_write(const FirmwareUpdateManifest&) override {
    assert(!verified_text.empty());
    assert(signature == FirmwareUpdateError::None);
    ++erase_calls;
    open = begin_error == FirmwareUpdateError::None;
    inactive.clear();
    return begin_error;
  }
  bool write_chunk(const std::uint8_t* data, std::size_t size) override {
    assert(open && boot_is_old);
    if (!write_ok) return false;
    inactive.insert(inactive.end(), data, data + size);
    return true;
  }
  FirmwareUpdateError finish_image(const FirmwareUpdateManifest&) override {
    assert(open && boot_is_old);
    image_checked = finish_error == FirmwareUpdateError::None;
    open = false;
    return finish_error;
  }
  bool select_boot_partition() override {
    assert(image_checked && !open);
    ++boot_calls;
    if (boot_ok) boot_is_old = false;
    return boot_ok;
  }
  void abort_write() override { ++abort_calls; open = false; image_checked = false; }
};

constexpr std::uint8_t image[] = {1, 2, 3, 4};

void canonical_manifest() {
  auto value = manifest();
  char text[kFirmwareManifestTextBytes]{};
  std::size_t size = 0;
  assert(firmware_manifest_text(value, text, sizeof(text), size));
  const std::string expected = "lifeos-firmware-v1\nrelease-1\n1.0.0\n02:00:00:00:00:01\n"
      "lifeos.v1\nota_ab_v1\n4\n" + std::string(64, 'a') + "\n2\n";
  assert(std::string(text, size) == expected);
  assert(!firmware_manifest_text(value, text, 10, size) && size == 0);
  std::strcpy(value.version, "1\nother-device");
  assert(!firmware_manifest_text(value, text, sizeof(text), size));
  value = manifest();
  std::memset(value.image_ref, 'x', sizeof(value.image_ref));
  assert(!firmware_manifest_text(value, text, sizeof(text), size));
  value = manifest();
  value.sha256_hex[5] = 'G';
  assert(!firmware_manifest_text(value, text, sizeof(text), size));
  value = manifest();
  value.size_bytes = kFirmwareUpdateImageBytes + 1;
  assert(!firmware_manifest_text(value, text, sizeof(text), size));
  value = manifest();
  std::memset(value.version, 'v', 32);
  value.version[32] = '\0';
  assert(!firmware_manifest_text(value, text, sizeof(text), size));
}

void reject_before_erasing() {
  for (const auto reason : {FirmwareUpdateError::UnsupportedLayout,
                           FirmwareUpdateError::TrustUnavailable,
                           FirmwareUpdateError::RollbackUnavailable}) {
    FakeBackend backend;
    backend.ready = reason;
    FirmwareUpdateCore core(backend);
    assert(core.begin(manifest(), true) == reason);
    assert(backend.erase_calls == 0 && backend.boot_is_old);
  }
  for (const auto reason : {FirmwareUpdateError::InvalidSignature,
                           FirmwareUpdateError::HardwareMismatch,
                           FirmwareUpdateError::VersionMismatch}) {
    FakeBackend backend;
    backend.signature = reason;
    FirmwareUpdateCore core(backend);
    assert(core.begin(manifest(), true) == reason);
    assert(backend.erase_calls == 0 && backend.boot_is_old);
  }
  FakeBackend backend;
  FirmwareUpdateCore core(backend);
  assert(core.begin(manifest(), false) == FirmwareUpdateError::UnsafeState);
  auto invalid = manifest();
  invalid.signature_der_size = sizeof(invalid.signature_der) + 1;
  assert(core.begin(invalid, true) == FirmwareUpdateError::InvalidManifest);
  assert(backend.erase_calls == 0);
}

void interrupted_transfers_keep_old_boot() {
  for (std::size_t bytes = 0; bytes <= sizeof(image); ++bytes) {
    FakeBackend backend;
    FirmwareUpdateCore core(backend);
    assert(core.begin(manifest(), true) == FirmwareUpdateError::None);
    if (bytes > 0) assert(core.write(0, image, bytes) == FirmwareUpdateError::None);
    assert(backend.boot_is_old && backend.boot_calls == 0);
    // Link loss / cancellation before commit closes only the inactive handle.
    core.abort();
    assert(core.state() == FirmwareUpdateState::Aborted);
    assert(!backend.open && backend.boot_is_old);
    assert(core.commit() == FirmwareUpdateError::NotReceiving);
    assert(core.write(0, image, sizeof(image)) == FirmwareUpdateError::NotReceiving);
    core.abort();
    assert(backend.abort_calls == 1);
  }
}

void protocol_failures_never_select_new_boot() {
  for (int scenario = 0; scenario < 5; ++scenario) {
    FakeBackend backend;
    FirmwareUpdateCore core(backend);
    assert(core.begin(manifest(), true) == FirmwareUpdateError::None);
    assert(core.begin(manifest(), true) == FirmwareUpdateError::Busy);
    FirmwareUpdateError result = FirmwareUpdateError::None;
    if (scenario == 0) result = core.write(1, image, 1);
    if (scenario == 1) result = core.write(0, image, 0);
    if (scenario == 2) result = core.write(0, image, kFirmwareUpdateChunkBytes + 1);
    if (scenario == 3) {
      assert(core.write(0, image, 2) == FirmwareUpdateError::None);
      result = core.write(0, image, 2);  // duplicate with a new envelope ID
    }
    if (scenario == 4) {
      assert(core.write(0, image, 3) == FirmwareUpdateError::None);
      result = core.commit();
    }
    assert(result != FirmwareUpdateError::None);
    assert(core.state() == FirmwareUpdateState::Failed);
    assert(backend.boot_is_old && backend.boot_calls == 0 && !backend.open);
  }
}

void storage_verification_and_commit_failures() {
  for (const auto error : {FirmwareUpdateError::HashMismatch,
                          FirmwareUpdateError::InvalidImage,
                          FirmwareUpdateError::VersionMismatch,
                          FirmwareUpdateError::StorageFailure}) {
    FakeBackend backend;
    backend.finish_error = error;
    FirmwareUpdateCore core(backend);
    assert(core.begin(manifest(), true) == FirmwareUpdateError::None);
    assert(core.write(0, image, sizeof(image)) == FirmwareUpdateError::None);
    assert(core.commit() == error);
    assert(core.state() == FirmwareUpdateState::Failed);
    assert(backend.boot_is_old && backend.boot_calls == 0);
  }
  FakeBackend write_failure;
  write_failure.write_ok = false;
  FirmwareUpdateCore failed_write(write_failure);
  assert(failed_write.begin(manifest(), true) == FirmwareUpdateError::None);
  assert(failed_write.write(0, image, 4) == FirmwareUpdateError::StorageFailure);
  assert(write_failure.boot_is_old && !write_failure.open);

  FakeBackend boot_failure;
  boot_failure.boot_ok = false;
  FirmwareUpdateCore failed_commit(boot_failure);
  assert(failed_commit.begin(manifest(), true) == FirmwareUpdateError::None);
  assert(failed_commit.write(0, image, 4) == FirmwareUpdateError::None);
  assert(failed_commit.commit() == FirmwareUpdateError::BootSelectionFailure);
  assert(boot_failure.boot_is_old && failed_commit.state() == FirmwareUpdateState::Failed);
}

void successful_commit_is_a_separate_reboot_boundary() {
  FakeBackend backend;
  FirmwareUpdateCore core(backend);
  assert(core.begin(manifest(), true) == FirmwareUpdateError::None);
  assert(core.write(0, image, 2) == FirmwareUpdateError::None);
  assert(core.write(2, image + 2, 2) == FirmwareUpdateError::None);
  assert(core.received_bytes() == 4);
  assert(core.commit() == FirmwareUpdateError::None);
  assert(core.state() == FirmwareUpdateState::ReadyToReboot);
  assert(!backend.boot_is_old && backend.boot_calls == 1);
  // No reboot is performed by the core. The caller can ACK, then reboot;
  // subsequent link loss must not undo the already committed boot choice.
  core.abort();
  assert(core.state() == FirmwareUpdateState::ReadyToReboot && backend.abort_calls == 0);
  assert(core.begin(manifest(), true) == FirmwareUpdateError::Busy);
  assert(core.commit() == FirmwareUpdateError::NotReceiving);
  assert(backend.boot_calls == 1);
}

}  // namespace

int main() {
  canonical_manifest();
  reject_before_erasing();
  interrupted_transfers_keep_old_boot();
  protocol_failures_never_select_new_boot();
  storage_verification_and_commit_failures();
  successful_commit_is_a_separate_reboot_boundary();
  std::cout << "firmware update core: PASS (host faults, not physical OTA)\n";
}
