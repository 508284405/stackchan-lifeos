#include "lifeos/hal/stackchan/stackchan.hpp"

#include <algorithm>
#include <cmath>
#include <cstdlib>
#include <cstring>
#include <iterator>

#include "driver/gpio.h"
#include "esp_camera.h"
#include "esp_heap_caps.h"
#include "esp_log.h"
#include "esp_timer.h"
#include "freertos/FreeRTOS.h"
#include "freertos/task.h"
#include "img_converters.h"

namespace lifeos::hal::stackchan {
namespace {

constexpr const char* kTag = "lifeos-stackchan";
constexpr std::uint32_t kI2cTimeoutMs = 100;
// The GC0308 emits RGB565, so frame2jpg() uses the software encoder's
// 1–100 quality scale (higher is better). 80 matches the upstream converter
// example and preserves QVGA detail while remaining below the 64 KiB frame
// ceiling enforced by the device protocol.
constexpr std::uint8_t kCameraJpegQuality = 80;
// Match the official StackChan SCSerial IOTimeOut for every complete SCS
// transaction.  The safety loop treats a failed transaction as a hard stop;
// a later non-blocking transaction split can optimize scheduling separately.
constexpr std::uint32_t kServoStartupTimeoutMs = 100;
constexpr std::uint32_t kServoRuntimeTimeoutMs = 100;

std::uint64_t now_ms() {
  return static_cast<std::uint64_t>(esp_timer_get_time() / 1000);
}

void set_status(CapabilityStatus& status, CapabilityState state,
                const char* code = nullptr, const char* detail = nullptr,
                bool latched = false) {
  status.state = state;
  status.fault = {};
  if (code != nullptr) {
    status.fault.code = code;
    status.fault.severity = state == CapabilityState::Faulted
                                ? FaultSeverity::Critical
                                : FaultSeverity::Warning;
    status.fault.latched = latched;
    if (detail != nullptr) status.fault.detail = detail;
  }
}

bool read_byte(uart_port_t uart, std::uint8_t& byte,
               std::uint32_t timeout_ms) {
  const auto started_ms = now_ms();
  while (now_ms() - started_ms <= timeout_ms) {
    std::size_t available = 0;
    if (uart_get_buffered_data_len(uart, &available) != ESP_OK) return false;
    if (available != 0) {
      return uart_read_bytes(uart, &byte, 1, 0) == 1;
    }
    // The official SCSerial reader polls one byte at a time.  This both
    // avoids waiting for an entire packet before returning data and gives
    // each byte its own IOTimeOut window, matching the upstream behavior.
    vTaskDelay(pdMS_TO_TICKS(1));
  }
  return false;
}

std::uint16_t big_endian_word(const std::uint8_t* bytes) {
  return static_cast<std::uint16_t>(bytes[0] << 8 | bytes[1]);
}

std::int16_t signed_current(std::uint16_t value) {
  if ((value & 0x8000U) != 0) {
    return static_cast<std::int16_t>(-(value & 0x7FFFU));
  }
  return static_cast<std::int16_t>(value);
}

std::int16_t signed_load(std::uint16_t value) {
  if ((value & 0x0400U) != 0) {
    return static_cast<std::int16_t>(-(value & 0x03FFU));
  }
  return static_cast<std::int16_t>(value & 0x03FFU);
}

}  // namespace

bool I2cDevice::bind(i2c_master_bus_handle_t bus) {
  if (bus == nullptr) return false;
  i2c_device_config_t config{};
  config.dev_addr_length = I2C_ADDR_BIT_LEN_7;
  config.device_address = address_;
  config.scl_speed_hz = scl_speed_hz_;
  bus_ = bus;
  const esp_err_t error = i2c_master_bus_add_device(bus_, &config, &device_);
  return error == ESP_OK;
}

bool I2cDevice::probe() const {
  return bus_ != nullptr &&
         i2c_master_probe(bus_, address_, kI2cTimeoutMs) == ESP_OK;
}

bool I2cDevice::read_register(std::uint8_t reg, std::uint8_t* data,
                              std::size_t length) const {
  if (data == nullptr || length == 0) return false;
  return device_ != nullptr &&
         i2c_master_transmit_receive(device_, &reg, 1, data, length,
                                      kI2cTimeoutMs) == ESP_OK;
}

bool I2cDevice::write_register(std::uint8_t reg, const std::uint8_t* data,
                               std::size_t length) const {
  if (length > 32) return false;
  std::array<std::uint8_t, 33> buffer{};
  buffer[0] = reg;
  if (length != 0 && data != nullptr) {
    std::memcpy(buffer.data() + 1, data, length);
  }
  return device_ != nullptr &&
         i2c_master_transmit(device_, buffer.data(), length + 1,
                             kI2cTimeoutMs) == ESP_OK;
}

bool I2cDevice::read_u8(std::uint8_t reg, std::uint8_t& value) const {
  return read_register(reg, &value, 1);
}

bool I2cDevice::write_u8(std::uint8_t reg, std::uint8_t value) const {
  return write_register(reg, &value, 1);
}

bool Py32IoExpander::update_bit(std::uint8_t reg, std::uint8_t bit,
                                bool value) {
  if (active_device_ == nullptr) return false;
  std::uint8_t current = 0;
  if (!active_device_->read_u8(reg, current)) return false;
  if (value) current = static_cast<std::uint8_t>(current | (1U << bit));
  else current = static_cast<std::uint8_t>(current & ~(1U << bit));
  return active_device_->write_u8(reg, current);
}

bool Py32IoExpander::lock() {
  if (mutex_ == nullptr) return true;  // boot path before concurrency starts
  return xSemaphoreTake(mutex_, pdMS_TO_TICKS(kMutexTimeoutMs)) == pdTRUE;
}

void Py32IoExpander::unlock() {
  if (mutex_ != nullptr) xSemaphoreGive(mutex_);
}

bool Py32IoExpander::begin() {
  if (mutex_ == nullptr) mutex_ = xSemaphoreCreateMutexStatic(&mutex_storage_);
  active_device_ = nullptr;
  std::uint8_t primary_version = 0;
  if (device_.read_u8(kVersion, primary_version) && primary_version != 0 &&
      primary_version != 0xFF) {
    active_device_ = &device_;
    version_ = primary_version;
  } else if (alternate_device_.read_u8(kVersion, version_) && version_ != 0 &&
             version_ != 0xFF) {
    active_device_ = &alternate_device_;
  }
  if (active_device_ == nullptr) {
    ESP_LOGW(kTag, "PY32L020 at 0x%02x/0x%02x did not report a valid version",
             kPy32Address, kPy32AlternateAddress);
    ready_ = false;
    return false;
  }
  ready_ = update_bit(kDirectionLow, 0, true) &&
           update_bit(kPullUpLow, 0, true) &&
           update_bit(kOutputLow, 0, false);
  vm_enabled_ = false;
  ESP_LOGI(kTag, "PY32L020 address=0x%02x version=0x%02x ready=%d",
           active_device_ == &device_ ? kPy32Address : kPy32AlternateAddress,
           version_, ready_);
  return ready_;
}

bool Py32IoExpander::set_vm_enabled(bool enabled) {
  if (vm_enabled_ == enabled) return true;
  if (!lock()) {
    return false;
  }
  bool success = false;
  if (active_device_ != nullptr && ready_) {
    success = update_bit(kDirectionLow, 0, true) &&
              update_bit(kPullUpLow, 0, true) &&
              update_bit(kOutputLow, 0, enabled);
    if (success) {
      std::uint8_t output = 0;
      const bool readback = active_device_->read_u8(kOutputLow, output);
      success = readback && (((output & 0x01U) != 0) == enabled);
    }
  }
  vm_enabled_ = success ? enabled : false;
  unlock();
  ESP_LOGI(kTag, "PY32 VM_EN requested=%d result=%d", enabled, success);
  return success;
}

bool Py32IoExpander::cut_power_fast() {
  if (!lock()) return false;
  bool success = false;
  if (active_device_ != nullptr && ready_) {
    std::uint8_t output = 0;
    if (active_device_->read_u8(kOutputLow, output)) {
      output = static_cast<std::uint8_t>(output & ~(1U << 0));
      success = active_device_->write_u8(kOutputLow, output);
    }
  }
  vm_enabled_ = false;
  unlock();
  return success;
}

bool ScsServoBus::begin() {
  uart_config_t config{};
  config.baud_rate = 1000000;
  config.data_bits = UART_DATA_8_BITS;
  config.parity = UART_PARITY_DISABLE;
  config.stop_bits = UART_STOP_BITS_1;
  config.flow_ctrl = UART_HW_FLOWCTRL_DISABLE;
  // Match the official StackChan SCS adapter: UART1 at 1 Mbps from APB.
  config.source_clk = UART_SCLK_APB;

  esp_err_t error = uart_driver_install(kServoUart, 512, 512, 0, nullptr, 0);
  if (error != ESP_OK && error != ESP_ERR_INVALID_STATE) return false;
  error = uart_param_config(kServoUart, &config);
  if (error != ESP_OK) return false;
  error = uart_set_pin(kServoUart, kServoTx, kServoRx, UART_PIN_NO_CHANGE,
                       UART_PIN_NO_CHANGE);
  if (error != ESP_OK) return false;
  uart_flush_input(kServoUart);
  response_timeout_ms_ = kServoRuntimeTimeoutMs;
  ready_ = true;
  return true;
}

bool ScsServoBus::read_packet(std::uint8_t expected_id, std::uint8_t* data,
                              std::size_t length, std::uint8_t* error_code) {
  if (!ready_ || (data == nullptr && length != 0)) return false;
  if (error_code != nullptr) *error_code = 0;
  std::uint8_t byte = 0;
  std::uint8_t previous = 0;
  bool header = false;
  for (std::size_t index = 0; index < 12; ++index) {
    if (!read_byte(kServoUart, byte, response_timeout_ms_)) return false;
    if (previous == 0xFF && byte == 0xFF) {
      header = true;
      break;
    }
    previous = byte;
  }
  if (!header) return false;

  std::uint8_t prefix[3]{};
  for (auto& value : prefix) {
    if (!read_byte(kServoUart, value, response_timeout_ms_)) return false;
  }
  const std::uint8_t id = prefix[0];
  const std::uint8_t packet_length = prefix[1];
  const std::uint8_t error = prefix[2];
  if (error_code != nullptr) *error_code = error;
  if (id != expected_id || packet_length != length + 2 ||
      packet_length < 2 || packet_length > kPacketBytes) {
    return false;
  }
  for (std::size_t index = 0; index < length; ++index) {
    if (!read_byte(kServoUart, data[index], response_timeout_ms_)) return false;
  }
  std::uint8_t checksum = 0;
  if (!read_byte(kServoUart, checksum, response_timeout_ms_)) return false;
  std::uint8_t sum = static_cast<std::uint8_t>(id + packet_length + error);
  for (std::size_t index = 0; index < length; ++index) sum = static_cast<std::uint8_t>(sum + data[index]);
  // A syntactically valid SCS packet can carry a non-zero servo error byte.
  // Keep framing/checksum validity separate from device health so callers can
  // report the actual error instead of treating it as a missing response.
  return static_cast<std::uint8_t>(~sum) == checksum;
}

bool ScsServoBus::write_packet(std::uint8_t id, std::uint8_t instruction,
                               std::uint8_t address,
                               const std::uint8_t* data, std::size_t length,
                               bool expect_ack) {
  if (!ready_ || length > kPacketBytes - 7 || (length != 0 && data == nullptr)) return false;
  std::array<std::uint8_t, kPacketBytes> packet{};
  const auto packet_length = static_cast<std::uint8_t>(length + 3);
  packet[0] = 0xFF;
  packet[1] = 0xFF;
  packet[2] = id;
  packet[3] = packet_length;
  packet[4] = instruction;
  packet[5] = address;
  if (length != 0) std::memcpy(packet.data() + 6, data, length);
  std::uint8_t sum = static_cast<std::uint8_t>(id + packet_length + instruction + address);
  for (std::size_t index = 0; index < length; ++index) sum = static_cast<std::uint8_t>(sum + data[index]);
  packet[6 + length] = static_cast<std::uint8_t>(~sum);
  const std::size_t total = length + 7;
  uart_flush_input(kServoUart);
  if (uart_write_bytes(kServoUart, reinterpret_cast<const char*>(packet.data()), total) !=
      static_cast<int>(total)) return false;
  if (uart_wait_tx_done(kServoUart, pdMS_TO_TICKS(response_timeout_ms_)) != ESP_OK) return false;
  if (!expect_ack) return true;
  std::uint8_t error = 0;
  return read_packet(id, nullptr, 0, &error) && error == 0;
}

bool ScsServoBus::ping(std::uint8_t id, std::uint8_t* error_code) {
  if (!ready_) return false;
  const std::uint8_t packet[] = {0xFF, 0xFF, id, 0x02, kInstructionPing,
                                 static_cast<std::uint8_t>(~(id + 0x02 + kInstructionPing))};
  uart_flush_input(kServoUart);
  if (uart_write_bytes(kServoUart, reinterpret_cast<const char*>(packet), sizeof(packet)) !=
      static_cast<int>(sizeof(packet))) return false;
  if (uart_wait_tx_done(kServoUart, pdMS_TO_TICKS(response_timeout_ms_)) != ESP_OK) return false;
  std::uint8_t response_error = 0;
  const bool valid = read_packet(id, nullptr, 0, &response_error);
  if (error_code != nullptr) *error_code = response_error;
  if (valid && response_error != 0) {
    ESP_LOGW(kTag, "SCS ping id=%u returned servo_error=0x%02x", id, response_error);
  }
  return valid && response_error == 0;
}

bool ScsServoBus::write_register(std::uint8_t id, std::uint8_t address,
                                 const std::uint8_t* data, std::size_t length) {
  return write_packet(id, kInstructionWrite, address, data, length, true);
}

bool ScsServoBus::read_register(std::uint8_t id, std::uint8_t address,
                                std::uint8_t* data, std::size_t length) {
  if (length == 0 || length > kPacketBytes - 2) return false;
  const auto requested = static_cast<std::uint8_t>(length);
  if (!write_packet(id, kInstructionRead, address, &requested, 1, false)) return false;
  std::uint8_t error = 0;
  return read_packet(id, data, length, &error) && error == 0;
}

bool ScsServoBus::read_word(std::uint8_t id, std::uint8_t address,
                            std::uint16_t& value) {
  std::uint8_t bytes[2]{};
  if (!read_register(id, address, bytes, sizeof(bytes))) return false;
  value = big_endian_word(bytes);
  return true;
}

bool ScsServoBus::write_word(std::uint8_t id, std::uint8_t address,
                             std::uint16_t value) {
  const std::uint8_t bytes[] = {
      static_cast<std::uint8_t>(value >> 8), static_cast<std::uint8_t>(value & 0xFF)};
  return write_register(id, address, bytes, sizeof(bytes));
}

bool ScsServoBus::enable_torque(std::uint8_t id, bool enabled) {
  const std::uint8_t value = enabled ? 1 : 0;
  return write_register(id, kTorqueRegister, &value, 1);
}

bool ScsServoBus::write_position(std::uint8_t id, std::uint16_t raw_position,
                                 std::uint16_t time_ms, std::uint16_t speed) {
  const std::uint8_t bytes[] = {
      static_cast<std::uint8_t>(raw_position >> 8),
      static_cast<std::uint8_t>(raw_position & 0xFF),
      static_cast<std::uint8_t>(time_ms >> 8), static_cast<std::uint8_t>(time_ms & 0xFF),
      static_cast<std::uint8_t>(speed >> 8), static_cast<std::uint8_t>(speed & 0xFF)};
  return write_register(id, kGoalPositionRegister, bytes, sizeof(bytes));
}

bool ScsServoBus::read_position(std::uint8_t id, std::uint16_t& raw_position) {
  return read_word(id, kPresentPositionRegister, raw_position);
}

bool ScsServoBus::read_current(std::uint8_t id, std::int16_t& current) {
  std::uint16_t raw = 0;
  if (!read_word(id, kPresentCurrentRegister, raw)) return false;
  current = signed_current(raw);
  return true;
}

bool ScsServoBus::read_load(std::uint8_t id, std::int16_t& load) {
  std::uint16_t raw = 0;
  if (!read_word(id, kPresentLoadRegister, raw)) return false;
  load = signed_load(raw);
  return true;
}

bool ScsServoBus::read_torque(std::uint8_t id, bool& enabled) {
  std::uint8_t value = 0;
  if (!read_register(id, kTorqueRegister, &value, 1)) return false;
  enabled = value != 0;
  return true;
}

void StackChanServoPair::fail(const char* code, const char* detail,
                              bool latched) {
  set_status(status_, CapabilityState::Faulted, code, detail, latched);
  hardware_ready_ = false;
  torque_enabled_ = false;
}

std::uint16_t StackChanServoPair::angle_to_raw(float angle, float zero,
                                                float minimum, float maximum,
                                                std::uint16_t default_zero) {
  if (!std::isfinite(angle) || angle < minimum || angle > maximum) return 0;
  // The StackChan servos are mounted opposite to the raw-counter convention.
  // Keep higher layers in physical coordinates; only this HAL conversion
  // applies the mechanical inversion.
  const float raw = static_cast<float>(default_zero) - (angle - zero) * kRawStepsPerDegree;
  if (raw < kRawMin || raw > kRawMax) return 0;
  return static_cast<std::uint16_t>(std::lround(raw));
}

float StackChanServoPair::raw_to_angle(std::uint16_t raw, std::uint16_t zero,
                                       float home, float minimum, float maximum) {
  const float angle = home - (static_cast<float>(raw) - static_cast<float>(zero)) /
                                kRawStepsPerDegree;
  return std::max(minimum, std::min(maximum, angle));
}

bool StackChanServoPair::begin(Py32IoExpander& expander) {
  expander_ = &expander;
  status_ = {};
  if (!expander.ready() || !bus_.begin()) {
    fail("servo.bus_unavailable", "PY32 or UART1 initialization failed", true);
    return false;
  }
  return power_on();
}

bool StackChanServoPair::power_on() {
  bus_.set_response_timeout(kServoStartupTimeoutMs);
  if (expander_ == nullptr || !expander_->ready()) {
    bus_.set_response_timeout(kServoRuntimeTimeoutMs);
    fail("servo.power_unavailable", "PY32 VM_EN is unavailable", true);
    return false;
  }
  if (!expander_->vm_enabled()) {
    if (!expander_->set_vm_enabled(true)) {
      bus_.set_response_timeout(kServoRuntimeTimeoutMs);
      fail("servo.power_on_failed", "unable to enable servo motor voltage", true);
      return false;
    }
    vTaskDelay(pdMS_TO_TICKS(200));
  }
  bool yaw_ping = false;
  bool yaw_feedback = false;
  bool pitch_ping = false;
  bool pitch_feedback = false;
  for (int attempt = 0; attempt < 3 && (!yaw_feedback || !pitch_feedback); ++attempt) {
    yaw_ping = bus_.ping(kYawId);
    // A valid present-position read is the authoritative startup proof.  A
    // few SCS firmware revisions do not answer PING reliably even though
    // register reads and writes work; do not discard usable feedback because
    // the optional probe failed.
    yaw_feedback = bus_.read_position(kYawId, raw_yaw_);
    pitch_ping = bus_.ping(kPitchId);
    pitch_feedback = bus_.read_position(kPitchId, raw_pitch_);
    if (!yaw_feedback || !pitch_feedback) vTaskDelay(pdMS_TO_TICKS(100));
  }
  const bool torque_off = bus_.enable_torque(kYawId, false) &&
                          bus_.enable_torque(kPitchId, false);
  bus_.set_response_timeout(kServoRuntimeTimeoutMs);
  ESP_LOGI(kTag, "servo self-test vm=%d yaw_ping=%d yaw_feedback=%d pitch_ping=%d pitch_feedback=%d torque_off=%d raw=%u/%u",
           expander_->vm_enabled(), yaw_ping, yaw_feedback, pitch_ping,
           pitch_feedback, torque_off, raw_yaw_, raw_pitch_);
  if (!yaw_feedback || !pitch_feedback || !torque_off || raw_yaw_ > kRawMax || raw_pitch_ > kRawMax) {
    (void)expander_->set_vm_enabled(false);
    fail("servo.self_test_failed", "servo ping, feedback, or torque-off failed", true);
    return false;
  }
  hardware_ready_ = true;
  torque_enabled_ = false;
  set_status(status_, CapabilityState::Available);
  ESP_LOGI(kTag, "servos ready yaw_raw=%u pitch_raw=%u torque=off",
           raw_yaw_, raw_pitch_);
  return true;
}

bool StackChanServoPair::power_off() {
  bool result = true;
  if (expander_ != nullptr && expander_->vm_enabled()) {
    result = bus_.enable_torque(kYawId, false) && bus_.enable_torque(kPitchId, false);
    result = expander_->set_vm_enabled(false) && result;
  }
  torque_enabled_ = false;
  return result;
}

ServoPosition StackChanServoPair::cached_position() const {
  return {raw_to_angle(raw_yaw_, 460, 0.0F, -90.0F, 90.0F),
          raw_to_angle(raw_pitch_, 620, kHomePitchDegrees, 5.0F, 85.0F)};
}

bool StackChanServoPair::wake_light() {
  if (expander_ == nullptr || !expander_->ready()) return false;
  if (!expander_->vm_enabled() && !expander_->set_vm_enabled(true)) return false;
  vTaskDelay(pdMS_TO_TICKS(200));
  ServoPosition feedback;
  std::uint16_t yaw_raw = 0;
  std::uint16_t pitch_raw = 0;
  if (!read_feedback_pair(feedback, yaw_raw, pitch_raw)) return false;
  return torque_off_pair();
}

bool StackChanServoPair::read_feedback_pair(ServoPosition& position,
                                            std::uint16_t& yaw_raw,
                                            std::uint16_t& pitch_raw) {
  std::uint16_t yaw = 0;
  std::uint16_t pitch = 0;
  if (!bus_.read_position(kYawId, yaw) || !bus_.read_position(kPitchId, pitch) ||
      yaw > kRawMax || pitch > kRawMax) {
    ESP_LOGW(kTag, "servo feedback read failed yaw=%u pitch=%u", yaw, pitch);
    return false;
  }
  raw_yaw_ = yaw;
  raw_pitch_ = pitch;
  yaw_raw = yaw;
  pitch_raw = pitch;
  position = {raw_to_angle(yaw, 460, 0.0F, -90.0F, 90.0F),
              raw_to_angle(pitch, 620, kHomePitchDegrees, 5.0F, 85.0F)};
  return true;
}

bool StackChanServoPair::enable_torque_pair(bool enabled) {
  const bool yaw_result = bus_.enable_torque(kYawId, enabled);
  const bool pitch_result = bus_.enable_torque(kPitchId, enabled);
  if (!yaw_result || !pitch_result) {
    ESP_LOGW(kTag, "servo torque enable(%d) yaw=%d pitch=%d", enabled,
             yaw_result, pitch_result);
    return false;
  }
  torque_enabled_ = enabled;
  return true;
}

bool StackChanServoPair::write_goal_pair(const ServoPosition& position,
                                         std::uint16_t& yaw_raw,
                                         std::uint16_t& pitch_raw) {
  if (!std::isfinite(position.yaw_deg) || !std::isfinite(position.pitch_deg)) {
    return false;
  }
  const auto yaw = angle_to_raw(position.yaw_deg, 0.0F, -90.0F, 90.0F, 460);
  const auto pitch = angle_to_raw(position.pitch_deg, kHomePitchDegrees, 5.0F, 85.0F, 620);
  if (yaw < kRawMin || yaw > kRawMax || pitch < kRawMin || pitch > kRawMax) {
    return false;
  }
  const bool yaw_goal = bus_.write_position(kYawId, yaw, 50, 0);
  const bool pitch_goal = bus_.write_position(kPitchId, pitch, 50, 0);
  if (!yaw_goal || !pitch_goal) {
    ESP_LOGW(kTag, "servo position write yaw=%d pitch=%d raw=%u/%u",
             yaw_goal, pitch_goal, yaw, pitch);
    return false;
  }
  yaw_raw = yaw;
  pitch_raw = pitch;
  return true;
}

bool StackChanServoPair::torque_off_pair() {
  return enable_torque_pair(false);
}

bool StackChanServoPair::refresh_feedback() {
  if (!hardware_ready_) return false;
  std::uint16_t yaw = 0;
  std::uint16_t pitch = 0;
  if (!bus_.read_position(kYawId, yaw) || !bus_.read_position(kPitchId, pitch) ||
      yaw > kRawMax || pitch > kRawMax) {
    fail("servo.feedback_invalid", "position feedback unavailable or out of range", true);
    return false;
  }
  raw_yaw_ = yaw;
  raw_pitch_ = pitch;
  return true;
}

bool StackChanServoPair::set_position(ServoPosition position) {
  if (!std::isfinite(position.yaw_deg) || !std::isfinite(position.pitch_deg) ||
      position.yaw_deg < -90.0F || position.yaw_deg > 90.0F ||
      position.pitch_deg < 5.0F || position.pitch_deg > 85.0F) {
    fail("servo.hard_limit", "position outside LifeOS limits", true);
    (void)power_off();
    return false;
  }
  // No implicit power_on() here: re-running the full boot self-test inside a
  // position request blocked the safety loop for seconds. The ServoIoTask
  // owner decides when to wake or self-test the pair.
  if (!hardware_ready_ || expander_ == nullptr || !expander_->vm_enabled()) {
    fail("servo.power_unavailable", "set_position requires a powered servo pair", false);
    return false;
  }
  const auto yaw = angle_to_raw(position.yaw_deg, 0.0F, -90.0F, 90.0F, 460);
  const auto pitch = angle_to_raw(position.pitch_deg, kHomePitchDegrees, 5.0F, 85.0F, 620);
  if (yaw < kRawMin || yaw > kRawMax || pitch < kRawMin || pitch > kRawMax) {
    fail("servo.hard_limit", "mapped raw position outside servo range", true);
    (void)power_off();
    return false;
  }
  if (!torque_enabled_) {
    const bool yaw_torque = bus_.enable_torque(kYawId, true);
    const bool pitch_torque = bus_.enable_torque(kPitchId, true);
    if (!yaw_torque || !pitch_torque) {
      ESP_LOGW(kTag, "servo torque enable yaw=%d pitch=%d", yaw_torque, pitch_torque);
      fail("servo.torque_enable_failed", "servo torque enable rejected", true);
      (void)power_off();
      return false;
    }
    torque_enabled_ = true;
  }
  const bool yaw_goal = bus_.write_position(kYawId, yaw, 50, 0);
  const bool pitch_goal = bus_.write_position(kPitchId, pitch, 50, 0);
  if (!yaw_goal || !pitch_goal) {
    ESP_LOGW(kTag, "servo position write yaw=%d pitch=%d raw=%u/%u",
             yaw_goal, pitch_goal, yaw, pitch);
    fail("servo.command_failed", "servo position command rejected", true);
    (void)power_off();
    return false;
  }
  return true;
}

bool StackChanServoPair::read_position(ServoPosition& position) {
  if (!hardware_ready_ || expander_ == nullptr || !expander_->vm_enabled()) {
    position = {raw_to_angle(raw_yaw_, 460, 0.0F, -90.0F, 90.0F),
                raw_to_angle(raw_pitch_, 620, kHomePitchDegrees, 5.0F, 85.0F)};
    return true;
  }
  if (!refresh_feedback()) return false;
  position = {raw_to_angle(raw_yaw_, 460, 0.0F, -90.0F, 90.0F),
              raw_to_angle(raw_pitch_, 620, kHomePitchDegrees, 5.0F, 85.0F)};
  return true;
}

bool StackChanServoPair::stop(bool release_torque) {
  bool result = true;
  if (expander_ != nullptr && expander_->vm_enabled()) {
    result = bus_.enable_torque(kYawId, false) && bus_.enable_torque(kPitchId, false);
    torque_enabled_ = false;
    if (release_torque) result = expander_->set_vm_enabled(false) && result;
  }
  torque_enabled_ = false;
  return result;
}

bool StackChanHeadTouch::bind(i2c_master_bus_handle_t bus) {
  return device_.bind(bus) && display_device_.bind(bus);
}

bool StackChanHeadTouch::begin() {
  head_available_ = device_.probe();
  display_available_ = display_device_.probe();
  if (!head_available_ && !display_available_) {
    set_status(status_, CapabilityState::Unavailable, "touch.not_found",
               "Si12T 0x68 and FT6336U 0x38 did not acknowledge");
    return false;
  }

  bool configured = true;
  if (head_available_) {
    for (std::uint8_t reg = 0x0A; reg <= 0x0F; ++reg) {
      configured = device_.write_u8(reg, 0x00) && configured;
    }
    configured = device_.write_u8(0x09, 0x0F) && configured;
    configured = device_.write_u8(0x09, 0x07) && configured;
    configured = device_.write_u8(0x08, 0x22) && configured;
    for (std::uint8_t reg = 0x02; reg <= 0x06; ++reg) {
      configured = device_.write_u8(reg, 0xCC) && configured;
    }
  }
  if (!configured && !display_available_) {
    set_status(status_, CapabilityState::Faulted, "touch.init_failed",
               "Si12T configuration write failed", true);
    return false;
  }
  if (!configured) {
    set_status(status_, CapabilityState::Available, "touch.head_init_failed",
               "FT6336U remains available; Si12T configuration failed");
  } else {
    set_status(status_, CapabilityState::Available);
  }
  return true;
}

bool StackChanHeadTouch::read(TouchSample& sample) {
  sample = {};
  sample.timestamp_ms = now_ms();
  if (status_.state != CapabilityState::Available) return false;

  bool read_any = false;
  std::uint8_t head_value = 0;
  if (head_available_) {
    read_any = device_.read_u8(0x10, head_value);
  }
  std::uint8_t display_data[6]{};
  bool display_touched = false;
  if (display_available_) {
    const bool display_read = display_device_.read_register(0x02, display_data,
                                                            sizeof(display_data));
    read_any = display_read || read_any;
    display_touched = display_read && (display_data[0] & 0x0F) != 0;
  }
  if (!read_any) {
    set_status(status_, CapabilityState::Faulted, "touch.read_failed",
               "Si12T/FT6336U result read failed", false);
    return false;
  }

  std::uint8_t max_intensity = 0;
  std::uint16_t zone = 0;
  if (head_available_) {
    for (std::uint16_t index = 0; index < 3; ++index) {
      const auto intensity = static_cast<std::uint8_t>((head_value >> (index * 2)) & 0x03);
      if (intensity > max_intensity) {
        max_intensity = intensity;
        zone = index;
      }
    }
  }
  sample.touched = max_intensity != 0 || display_touched;
  if (display_touched && max_intensity == 0) zone = 3;
  sample.zone = zone;
  if (sample.touched && !touched_) touch_started_ms_ = sample.timestamp_ms;
  sample.duration_ms = sample.touched
                           ? static_cast<std::uint32_t>(sample.timestamp_ms - touch_started_ms_)
                           : 0;
  touched_ = sample.touched;
  return true;
}

bool StackChanImu::begin() {
  std::uint8_t chip_id = 0;
  if (!device_.read_u8(0x00, chip_id) || chip_id != 0x24) {
    set_status(status_, CapabilityState::Unavailable, "imu.not_found",
               "BMI270 chip id was not 0x24");
    return false;
  }
  // Basic accelerometer/gyro power-up. The safety loop does not depend on IMU
  // configuration; a failed optional configuration degrades perception only.
  const bool configured = device_.write_u8(0x7D, 0x0E) &&
                          device_.write_u8(0x40, 0x28) &&
                          device_.write_u8(0x42, 0x28);
  if (!configured) {
    set_status(status_, CapabilityState::Faulted, "imu.init_failed",
               "BMI270 power configuration failed");
    return false;
  }
  set_status(status_, CapabilityState::Available);
  return true;
}

bool StackChanImu::read(ImuSample& sample) {
  sample = {};
  sample.timestamp_ms = now_ms();
  if (status_.state != CapabilityState::Available) return false;
  std::uint8_t data[12]{};
  if (!device_.read_register(0x0C, data, sizeof(data))) {
    set_status(status_, CapabilityState::Faulted, "imu.read_failed",
               "BMI270 sample read failed");
    return false;
  }
  const auto raw = [](const std::uint8_t* bytes) -> std::int16_t {
    return static_cast<std::int16_t>(bytes[0] | (bytes[1] << 8));
  };
  sample.accel_x = static_cast<float>(raw(data + 0)) / 16384.0F;
  sample.accel_y = static_cast<float>(raw(data + 2)) / 16384.0F;
  sample.accel_z = static_cast<float>(raw(data + 4)) / 16384.0F;
  sample.gyro_x = static_cast<float>(raw(data + 6)) / 131.0F;
  sample.gyro_y = static_cast<float>(raw(data + 8)) / 131.0F;
  sample.gyro_z = static_cast<float>(raw(data + 10)) / 131.0F;
  const float acceleration = std::sqrt(sample.accel_x * sample.accel_x +
                                        sample.accel_y * sample.accel_y +
                                        sample.accel_z * sample.accel_z);
  sample.shaken = std::fabs(acceleration - 1.0F) > 0.45F;
  return true;
}

bool StackChanProximity::begin() {
  if (!device_.probe()) {
    set_status(status_, CapabilityState::Unavailable, "proximity.not_found",
               "LTR-553 at 0x23 did not acknowledge");
    return false;
  }
  set_status(status_, CapabilityState::Available);
  return true;
}

bool StackChanProximity::read(ProximitySample& sample) {
  sample = {};
  sample.timestamp_ms = now_ms();
  if (status_.state != CapabilityState::Available) return false;
  std::uint8_t data[2]{};
  if (!device_.read_register(0x8D, data, sizeof(data))) {
    set_status(status_, CapabilityState::Faulted, "proximity.read_failed",
               "LTR-553 proximity read failed");
    return false;
  }
  sample.distance_mm = static_cast<std::uint16_t>(data[0] | ((data[1] & 0x07) << 8));
  sample.present = sample.distance_mm != 0;
  return true;
}

bool StackChanCamera::begin() {
  camera_config_t config{};
  config.pin_pwdn = -1;
  config.pin_reset = -1;
  config.pin_xclk = -1;  // StackChan supplies the GC0308 clock from its module.
  config.pin_sccb_sda = -1;
  config.pin_sccb_scl = -1;
  config.pin_d7 = GPIO_NUM_47;
  config.pin_d6 = GPIO_NUM_48;
  config.pin_d5 = GPIO_NUM_16;
  config.pin_d4 = GPIO_NUM_15;
  config.pin_d3 = GPIO_NUM_42;
  config.pin_d2 = GPIO_NUM_41;
  config.pin_d1 = GPIO_NUM_40;
  config.pin_d0 = GPIO_NUM_39;
  config.pin_vsync = GPIO_NUM_46;
  config.pin_href = GPIO_NUM_38;
  config.pin_pclk = GPIO_NUM_45;
  config.xclk_freq_hz = 20000000;
  config.ledc_timer = LEDC_TIMER_0;
  config.ledc_channel = LEDC_CHANNEL_0;
  // GC0308 has no hardware JPEG output. Capture its native RGB565 pixels so
  // the browser receives the original sensor colour channels, then convert
  // each frame to JPEG on the ESP32-S3.
  config.pixel_format = PIXFORMAT_RGB565;
  config.frame_size = FRAMESIZE_QVGA;
  config.jpeg_quality = kCameraJpegQuality;
  // Two PSRAM frame buffers keep sensor DMA running while the preview task
  // converts the previous frame to JPEG. CAMERA_GRAB_LATEST prevents an old
  // queued frame from winning when encoding briefly falls behind.
  config.fb_count = 2;
  config.fb_location = CAMERA_FB_IN_PSRAM;
  config.grab_mode = CAMERA_GRAB_LATEST;
  config.sccb_i2c_port = static_cast<int>(kI2cPort);

  const esp_err_t error = esp_camera_init(&config);
  if (error != ESP_OK) {
    set_status(status_, CapabilityState::Unavailable, "camera.init_failed",
               esp_err_to_name(error));
    ESP_LOGW(kTag, "GC0308 init failed: %s", esp_err_to_name(error));
    return false;
  }
  sensor_t* sensor = esp_camera_sensor_get();
  if (sensor == nullptr || sensor->id.PID != GC0308_PID) {
    set_status(status_, CapabilityState::Faulted, "camera.sensor_mismatch",
               "expected GC0308 sensor", true);
    return false;
  }
  // The GC0308 defaults to a conservative exposure target and the strong
  // backlight in the StackChan enclosure makes the preview nearly black.
  // Keep the sensor's closed-loop exposure/gain controls enabled and raise
  // only the bounded AE target; no browser-supplied camera tuning is allowed.
  const bool exposure_ready = sensor->set_exposure_ctrl == nullptr ||
                              sensor->set_exposure_ctrl(sensor, 1) == 0;
  const bool gain_ready = sensor->set_gain_ctrl == nullptr ||
                          sensor->set_gain_ctrl(sensor, 1) == 0;
  const bool ae_ready = sensor->set_ae_level == nullptr ||
                        sensor->set_ae_level(sensor, 2) == 0;
  if (!exposure_ready || !gain_ready || !ae_ready) {
    set_status(status_, CapabilityState::Faulted, "camera.sensor_tuning_failed",
               "GC0308 automatic exposure setup failed", true);
    return false;
  }
  ESP_LOGI(kTag, "GC0308 auto exposure enabled target=+2");
  capture_mutex_ = xSemaphoreCreateMutexStatic(&capture_mutex_storage_);
  if (capture_mutex_ == nullptr) {
    set_status(status_, CapabilityState::Unavailable, "camera.mutex_failed",
               "camera capture mutex unavailable");
    return false;
  }
  initialized_ = true;
  set_status(status_, CapabilityState::Available);
  ESP_LOGI(kTag, "GC0308 ready 320x240 RGB565->JPEG PSRAM=%u",
           static_cast<unsigned>(heap_caps_get_free_size(MALLOC_CAP_SPIRAM)));
  return true;
}

bool StackChanCamera::capture_jpeg(JpegFrame& frame) {
  frame = {};
  if (!initialized_ || status_.state != CapabilityState::Available || capture_mutex_ == nullptr ||
      xSemaphoreTake(capture_mutex_, pdMS_TO_TICKS(500)) != pdTRUE) {
    return false;
  }
  camera_fb_t* buffer = esp_camera_fb_get();
  if (buffer == nullptr) {
    set_status(status_, CapabilityState::Faulted, "camera.capture_failed",
               "frame buffer unavailable");
    xSemaphoreGive(capture_mutex_);
    return false;
  }
  std::uint8_t* jpeg_data = buffer->buf;
  std::size_t jpeg_size = buffer->len;
  bool converted = false;
  if (buffer->format != PIXFORMAT_JPEG) {
    jpeg_data = nullptr;
    jpeg_size = 0;
    if (!frame2jpg(buffer, kCameraJpegQuality, &jpeg_data, &jpeg_size) || jpeg_data == nullptr ||
        jpeg_size == 0) {
      if (jpeg_data != nullptr) free(jpeg_data);
      esp_camera_fb_return(buffer);
      set_status(status_, CapabilityState::Faulted, "camera.jpeg_encode_failed",
                 "RGB565 frame could not be converted to JPEG");
      xSemaphoreGive(capture_mutex_);
      return false;
    }
    converted = true;
  }
  frame.data = jpeg_data;
  frame.size = jpeg_size;
  frame.width = static_cast<std::uint16_t>(buffer->width);
  frame.height = static_cast<std::uint16_t>(buffer->height);
  frame.timestamp_ms = static_cast<std::uint64_t>(buffer->timestamp.tv_sec) * 1000U +
                       static_cast<std::uint64_t>(buffer->timestamp.tv_usec) / 1000U;
  frame.handle = reinterpret_cast<std::uintptr_t>(buffer);
  frame.converted = converted;
  return true;
}

void StackChanCamera::release_jpeg(JpegFrame& frame) {
  if (frame.handle != 0 && capture_mutex_ != nullptr) {
    if (frame.converted && frame.data != nullptr) {
      free(const_cast<std::uint8_t*>(frame.data));
    }
    esp_camera_fb_return(reinterpret_cast<camera_fb_t*>(frame.handle));
    frame = {};
    xSemaphoreGive(capture_mutex_);
  }
}

bool StackChanCamera::capture(CameraFrame& frame) {
  frame = {};
  JpegFrame jpeg;
  if (!capture_jpeg(jpeg)) return false;
  frame.width = jpeg.width;
  frame.height = jpeg.height;
  frame.person_present = false;  // Phase 1 only proves capture, not face inference.
  frame.timestamp_ms = jpeg.timestamp_ms;
  release_jpeg(jpeg);
  return true;
}

bool StackChanPerception::read(PersonObservation& observation) {
  observation = {};
  CameraFrame frame;
  if (!camera_.capture(frame)) return false;
  observation.timestamp_ms = frame.timestamp_ms;
  observation.present = frame.person_present;
  observation.confidence = frame.person_present ? 1.0F : 0.0F;
  return true;
}

bool StackChanDisplay::command(std::uint8_t value) {
  if (device_ == nullptr) return false;
  gpio_set_level(kDc, 0);
  spi_transaction_t transaction{};
  transaction.length = 8;
  transaction.tx_data[0] = value;
  transaction.flags = SPI_TRANS_USE_TXDATA;
  // The display is the sole user of this SPI device. Polling avoids the
  // interrupt return-queue wakeup path, which is not reliable on the CoreS3
  // dual-core FreeRTOS configuration when the display task is pinned.
  return spi_device_polling_transmit(device_, &transaction) == ESP_OK;
}

bool StackChanDisplay::data(const void* buffer, std::size_t length) {
  if (device_ == nullptr || buffer == nullptr || length == 0) return false;
  gpio_set_level(kDc, 1);
  spi_transaction_t transaction{};
  transaction.length = length * 8;
  transaction.tx_buffer = buffer;
  return spi_device_polling_transmit(device_, &transaction) == ESP_OK;
}

bool StackChanDisplay::begin_region(int x0, int y0, int x1, int y1) {
  const std::uint8_t column[] = {
      static_cast<std::uint8_t>(x0 >> 8), static_cast<std::uint8_t>(x0 & 0xFF),
      static_cast<std::uint8_t>(x1 >> 8), static_cast<std::uint8_t>(x1 & 0xFF)};
  const std::uint8_t row[] = {
      static_cast<std::uint8_t>(y0 >> 8), static_cast<std::uint8_t>(y0 & 0xFF),
      static_cast<std::uint8_t>(y1 >> 8), static_cast<std::uint8_t>(y1 & 0xFF)};
  return command(0x2A) && data(column, sizeof(column)) &&
         command(0x2B) && data(row, sizeof(row)) && command(0x2C);
}

bool StackChanDisplay::write_row(const std::uint8_t* rgb565_msb_first,
                                 std::size_t byte_count) {
  return data(rgb565_msb_first, byte_count);
}

bool StackChanDisplay::begin() {
  spi_bus_config_t bus{};
  bus.mosi_io_num = kMosi;
  bus.miso_io_num = GPIO_NUM_NC;
  bus.sclk_io_num = kSclk;
  bus.quadwp_io_num = GPIO_NUM_NC;
  bus.quadhd_io_num = GPIO_NUM_NC;
  bus.max_transfer_sz = kWidth * sizeof(std::uint16_t);
  esp_err_t error = spi_bus_initialize(kSpiHost, &bus, SPI_DMA_CH_AUTO);
  if (error != ESP_OK && error != ESP_ERR_INVALID_STATE) {
    set_status(status_, CapabilityState::Unavailable, "display.spi_failed",
               esp_err_to_name(error));
    return false;
  }
  spi_device_interface_config_t device_config{};
  device_config.clock_speed_hz = 40 * 1000 * 1000;
  device_config.mode = 2;
  device_config.spics_io_num = kCs;
  device_config.queue_size = 1;
  error = spi_bus_add_device(kSpiHost, &device_config, &device_);
  if (error != ESP_OK && error != ESP_ERR_INVALID_STATE) {
    set_status(status_, CapabilityState::Unavailable, "display.device_failed",
               esp_err_to_name(error));
    return false;
  }
  gpio_config_t dc{};
  dc.pin_bit_mask = 1ULL << kDc;
  dc.mode = GPIO_MODE_OUTPUT;
  dc.pull_up_en = GPIO_PULLUP_DISABLE;
  dc.pull_down_en = GPIO_PULLDOWN_DISABLE;
  dc.intr_type = GPIO_INTR_DISABLE;
  if (gpio_config(&dc) != ESP_OK) {
    set_status(status_, CapabilityState::Unavailable, "display.dc_failed",
               "ILI9342 DC GPIO configuration failed");
    return false;
  }
  static constexpr std::uint8_t init_commands[][16] = {
      {0xDD, 0x01}, {0x3A, 0x55}, {0x21}, {0x36, 0x08}, {0xD5, 0x00},
      {0xB1, 0x22}, {0xC8, 0x38}, {0xCB, 0x1C}, {0xC9, 0x1A}, {0xCA, 0x1A},
      {0xB7, 0x5A, 0x41, 0x11, 0x19},
      {0xE4, 0x04, 0x08, 0x11, 0x06, 0x12, 0x07, 0x3A, 0x76, 0x47, 0x07, 0x0F, 0x0A, 0x11, 0x19, 0x05},
      {0xE5, 0x02, 0x03, 0x07, 0x06, 0x12, 0x07, 0x36, 0x5F, 0x48, 0x06, 0x10, 0x0C, 0x16, 0x14, 0x09},
  };
  static constexpr std::uint8_t init_lengths[] = {1, 2, 1, 2, 2, 2, 2, 2, 2, 2, 5, 16, 16};
  bool initialized = command(0x01);
  vTaskDelay(pdMS_TO_TICKS(120));
  initialized = command(0x11) && initialized;
  vTaskDelay(pdMS_TO_TICKS(120));
  for (std::size_t index = 0; index < std::size(init_commands) && initialized; ++index) {
    initialized = command(init_commands[index][0]);
    if (init_lengths[index] > 1) initialized = data(init_commands[index] + 1, init_lengths[index] - 1) && initialized;
  }
  initialized = command(0x29) && initialized;
  if (!initialized) {
    set_status(status_, CapabilityState::Faulted, "display.init_failed",
               "ILI9342 command sequence failed", true);
    return false;
  }
  set_status(status_, CapabilityState::Available);
  return true;
}

bool StackChanDisplay::render(const DisplayFrame& frame) {
  if (status_.state != CapabilityState::Available) return false;
  // The SmileyFace paints the full 320x240 screen on the first call or on an
  // expression change, then only repaints the animated eye/mouth region on
  // the display task cadence (see face.hpp for the animation model).
  const std::string_view expression(frame.expression.data());
  return face_.draw(expression, frame.timestamp_ms);
}

bool StackChanBoard::begin_i2c() {
  i2c_master_bus_config_t config{};
  config.i2c_port = kI2cPort;
  config.sda_io_num = kI2cSda;
  config.scl_io_num = kI2cScl;
  config.clk_source = I2C_CLK_SRC_DEFAULT;
  config.glitch_ignore_cnt = 7;
  config.trans_queue_depth = 0;
  config.flags.enable_internal_pullup = 1;
  esp_err_t error = i2c_new_master_bus(&config, &i2c_bus_);
  if (error == ESP_ERR_INVALID_STATE) error = i2c_master_get_bus_handle(kI2cPort, &i2c_bus_);
  if (error != ESP_OK || i2c_bus_ == nullptr) return false;
  return expander_.bind(i2c_bus_) && touch_.bind(i2c_bus_) &&
         imu_.bind(i2c_bus_) && proximity_.bind(i2c_bus_) &&
         display_expander_.bind(i2c_bus_) && power_management_.bind(i2c_bus_);
}

bool StackChanBoard::begin_power_management() {
  if (!power_management_.probe()) return false;
  std::uint8_t chip_id = 0;
  if (!power_management_.read_u8(0x03, chip_id) || chip_id != 0x4A) return false;
  // Match M5Unified's CoreS3/StackChan AXP2101 baseline.  In particular,
  // keep all four ALDO rails enabled at 3.3 V and leave the boost path under
  // the AW9523 BUS_EN/BOOST_EN gate handled below.
  static constexpr std::array<std::array<std::uint8_t, 2>, 9> kRegisters = {{
      {{0x90, 0xBF}},  // ALDO1..4 enable / PMIC LDO control
      {{0x92, 0x0D}},  // ALDO1 1.8 V
      {{0x93, 0x1C}},  // ALDO2 3.3 V
      {{0x94, 0x1C}},  // ALDO3 3.3 V
      {{0x95, 0x1C}},  // ALDO4 3.3 V
      {{0x27, 0x00}},  // power-key hold/off timing
      {{0x69, 0x11}},  // charge LED behavior
      {{0x10, 0x30}},  // PMIC common configuration
      {{0x30, 0x0F}},  // ADC enable
  }};
  bool configured = true;
  for (const auto& entry : kRegisters) {
    configured = power_management_.write_u8(entry[0], entry[1]) && configured;
  }
  ESP_LOGI(kTag, "AXP2101 chip=0x%02x configured=%d", chip_id, configured);
  return configured;
}

bool StackChanBoard::begin_display_expander() {
  if (!display_expander_.probe()) return false;
  bool configured = display_expander_.write_u8(0x02, 0x07) &&
                    display_expander_.write_u8(0x03, 0x8F) &&
                    display_expander_.write_u8(0x04, 0x18) &&
                    display_expander_.write_u8(0x05, 0x0C) &&
                    display_expander_.write_u8(0x11, 0x10) &&
                    display_expander_.write_u8(0x12, 0xFF) &&
                    display_expander_.write_u8(0x13, 0xFF);
  if (!configured) return false;
  configured = display_expander_.write_u8(0x03, 0x81);
  vTaskDelay(pdMS_TO_TICKS(20));
  configured = display_expander_.write_u8(0x03, 0x83) && configured;
  vTaskDelay(pdMS_TO_TICKS(10));
  // CoreS3's external bus is shared by the StackChan base.  M5Unified's
  // setExtOutput() explicitly keeps AW9523 P0_1 (BUS_EN) and P1_7
  // (BOOST_EN) high; do the same read-modify-write and verify the latch
  // before the servo self-test runs.
  std::uint8_t outputs[2]{};
  configured = display_expander_.read_register(0x02, outputs, sizeof(outputs)) && configured;
  outputs[0] = static_cast<std::uint8_t>(outputs[0] | 0x02U);
  outputs[1] = static_cast<std::uint8_t>(outputs[1] | 0x80U);
  configured = display_expander_.write_register(0x02, outputs, sizeof(outputs)) && configured;
  std::uint8_t verified_outputs[2]{};
  const bool verified = display_expander_.read_register(
      0x02, verified_outputs, sizeof(verified_outputs));
  configured = verified && configured;
  ESP_LOGI(kTag, "AW9523 external bus outputs p0=0x%02x p1=0x%02x bus_en=%d boost_en=%d",
           verified_outputs[0], verified_outputs[1],
           (verified_outputs[0] & 0x02U) != 0,
           (verified_outputs[1] & 0x80U) != 0);
  return configured;
}

bool StackChanBoard::begin() {
  const bool i2c_ready = begin_i2c();
  if (!i2c_ready) ESP_LOGE(kTag, "I2C bus init failed");
  const bool power_ready = begin_power_management();
  bool expander_ready = false;
  for (int attempt = 0; attempt < 6 && !expander_ready; ++attempt) {
    expander_ready = expander_.begin();
    if (!expander_ready && attempt + 1 < 6) vTaskDelay(pdMS_TO_TICKS(200));
  }
  // CoreS3 exposes the StackChan motor rail through AW9523 BUS_EN/BOOST_EN.
  // M5Unified enables that external bus before M5StackChan initializes SCS;
  // keep the same ordering so the servo self-test observes real motor power.
  const bool display_expander_ready = begin_display_expander();
  const bool servo_ready = servo_.begin(expander_);
  const bool touch_ready = touch_.begin();
  const bool imu_ready = imu_.begin();
  const bool proximity_ready = proximity_.begin();
  const bool display_ready = display_.begin();
  const bool camera_ready = camera_.begin();
  ESP_LOGI(kTag, "bringup i2c=%d power=%d expander=%d servo=%d touch=%d imu=%d proximity=%d display_io=%d display=%d camera=%d",
           i2c_ready, power_ready, expander_ready, servo_ready, touch_ready, imu_ready,
           proximity_ready, display_expander_ready, display_ready, camera_ready);
  return i2c_ready && expander_ready && servo_ready && touch_ready;
}

}  // namespace lifeos::hal::stackchan
