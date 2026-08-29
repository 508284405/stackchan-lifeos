#include <algorithm>
#include <cstdio>
#include <cstring>
#include <string_view>

#include "driver/usb_serial_jtag.h"
#include "driver/usb_serial_jtag_vfs.h"
#include "esp_heap_caps.h"
#include "esp_mac.h"
#include "esp_system.h"
#include "esp_timer.h"
#include "lifeos/protocol/protocol.hpp"
#include "lifeos/runtime/runtime.hpp"

namespace {

constexpr const char* kDeviceId = "stackchan-01";
constexpr const char* kFirmware = "lifeos-phase1-hil-0.1.0";
char input_line[lifeos::protocol::kMaxLineBytes + 2]{};
char output_line[lifeos::protocol::kMaxLineBytes + 2]{};
std::uint64_t output_sequence = 0;
// Fixed-capacity singleton in static storage: the ingest call path alone
// needs tens of kilobytes of stack for its bounded buffers, which no task
// stack should have to carry on top.
lifeos::protocol::Gateway gateway{};

std::uint64_t now_ms() {
  return static_cast<std::uint64_t>(esp_timer_get_time() / 1000);
}

template <typename Field>
void set_text(Field& field, std::string_view value) {
  const auto size = std::min<std::size_t>(value.size(), field.data.size() - 1);
  std::memcpy(field.data.data(), value.data(), size);
  field.size = size;
  field.data[size] = '\0';
}

void emit(lifeos::protocol::Envelope envelope) {
  envelope.seq = ++output_sequence;
  envelope.ts_ms = now_ms();
  std::size_t written = 0;
  if (lifeos::protocol::serialize(envelope, output_line, sizeof(output_line), written)) {
    std::fwrite(output_line, 1, written, stdout);
    std::fflush(stdout);
  }
}

void emit_hello(const lifeos::protocol::Envelope& request) {
  lifeos::protocol::Envelope response;
  response.kind = lifeos::protocol::Kind::Hello;
  set_text(response.type, "hello.device");
  set_text(response.event_id, "device-hello");
  set_text(response.correlation_id, request.event_id.view());
  set_text(response.device_id, kDeviceId);
  char mac_text[24]{};
  std::uint8_t mac[6]{};
  esp_read_mac(mac, ESP_MAC_WIFI_STA);
  std::snprintf(mac_text, sizeof(mac_text), "%02x:%02x:%02x:%02x:%02x:%02x",
                mac[0], mac[1], mac[2], mac[3], mac[4], mac[5]);
  char payload[384]{};
  std::snprintf(payload, sizeof(payload),
                "{\"firmware\":\"%s\",\"board\":\"esp32s3\","
                "\"mac\":\"%s\",\"protocol_versions\":[\"lifeos.v1\"],"
                "\"capabilities\":[\"status\",\"protocol\",\"safety_core\"],"
                "\"motion_enabled\":false,\"heap_free\":%u}",
                kFirmware, mac_text, static_cast<unsigned>(esp_get_free_heap_size()));
  set_text(response.payload, payload);
  emit(response);
}

void emit_status(const lifeos::protocol::Envelope& request) {
  auto response = lifeos::protocol::make_ack(
      request, lifeos::protocol::AckStatus::Completed, true, 0, now_ms());
  char payload[256]{};
  std::snprintf(payload, sizeof(payload),
                "{\"status\":\"completed\",\"idempotent\":true,"
                "\"firmware\":\"%s\",\"motion_enabled\":false,"
                "\"heap_free\":%u,\"uptime_ms\":%llu}",
                kFirmware, static_cast<unsigned>(esp_get_free_heap_size()),
                static_cast<unsigned long long>(now_ms()));
  set_text(response.payload, payload);
  emit(response);
}

void emit_error(const lifeos::protocol::GatewayResult& result) {
  if (result.envelope.event_id.size == 0) return;
  auto response = lifeos::protocol::make_error(
      result.envelope, lifeos::protocol::ErrorCode::Unauthorized, 0, now_ms());
  emit(response);
}

}  // namespace

extern "C" void app_main() {
  // This HIL image proves target compilation, USB protocol, bounded parsing and
  // telemetry. It deliberately has no M5Stack actuator adapter; torque remains
  // disabled until the separately reviewed board HAL is linked.
  lifeos::runtime::FastSafetyLoop safety;
  safety.heartbeat(now_ms());

  setvbuf(stdout, nullptr, _IONBF, 0);
  // Console output goes through the interrupt-driven USB Serial/JTAG driver.
  // Input reads the driver directly with a bounded line buffer: the stdio
  // stdin path trips newlib's "Uninitialized lock used" assertion once real
  // bytes arrive, so fgets must not be used on this target.
  usb_serial_jtag_driver_config_t usb_config = USB_SERIAL_JTAG_DRIVER_CONFIG_DEFAULT();
  ESP_ERROR_CHECK(usb_serial_jtag_driver_install(&usb_config));
  usb_serial_jtag_vfs_use_driver();
  std::printf("LIFEOS_HIL_READY %s motion=disabled\n", kFirmware);

  std::size_t length = 0;
  bool overflow = false;
  std::uint8_t byte = 0;
  while (true) {
    if (usb_serial_jtag_read_bytes(&byte, 1, portMAX_DELAY) <= 0) {
      continue;
    }
    if (byte == '\r') continue;
    if (byte != '\n') {
      if (length < std::size(input_line) - 1) {
        input_line[length++] = static_cast<char>(byte);
      } else {
        // Keep consuming the oversized line; the stored prefix already
        // exceeds kMaxLineBytes so ingest rejects it with TooLarge.
        overflow = true;
      }
      continue;
    }
    const auto line = std::string_view(input_line, length);
    const auto result = gateway.ingest(line, now_ms());
    if (!result.accepted) {
      emit_error(result);
    } else if (result.duplicate) {
      emit(result.response);
    } else if (result.envelope.kind == lifeos::protocol::Kind::Hello) {
      emit_hello(result.envelope);
    } else if (result.envelope.kind == lifeos::protocol::Kind::Command &&
               result.envelope.type.view() == "command.control" &&
               result.envelope.payload.view().find("\"action\":\"status\"") != std::string_view::npos) {
      emit_status(result.envelope);
    } else {
      emit_error(result);
    }
    length = 0;
    overflow = false;
  }
}
