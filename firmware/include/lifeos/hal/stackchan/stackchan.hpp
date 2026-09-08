#pragma once

#include <array>
#include <cstddef>
#include <cstdint>

#include "driver/gpio.h"
#include "driver/i2c_master.h"
#include "driver/spi_master.h"
#include "driver/uart.h"
#include "esp_err.h"
#include "freertos/FreeRTOS.h"
#include "freertos/semphr.h"
#include "lifeos/hal/hal.hpp"
#include "lifeos/hal/stackchan/face.hpp"

namespace lifeos::hal::stackchan {

inline constexpr i2c_port_t kI2cPort = I2C_NUM_1;
inline constexpr gpio_num_t kI2cScl = GPIO_NUM_11;
inline constexpr gpio_num_t kI2cSda = GPIO_NUM_12;
inline constexpr uart_port_t kServoUart = UART_NUM_1;
inline constexpr gpio_num_t kServoTx = GPIO_NUM_6;
inline constexpr gpio_num_t kServoRx = GPIO_NUM_7;
inline constexpr std::uint8_t kPy32Address = 0x6F;
inline constexpr std::uint8_t kPy32AlternateAddress = 0x71;
inline constexpr std::uint8_t kHeadTouchAddress = 0x68;
inline constexpr std::uint8_t kDisplayTouchAddress = 0x38;
inline constexpr std::uint8_t kDisplayExpanderAddress = 0x58;
inline constexpr std::uint8_t kPowerManagementAddress = 0x34;
inline constexpr std::uint8_t kImuAddress = 0x69;
inline constexpr std::uint8_t kCameraAddress = 0x21;

class I2cDevice final {
 public:
  explicit I2cDevice(std::uint8_t address, std::uint32_t scl_speed_hz = 400000)
      : address_(address), scl_speed_hz_(scl_speed_hz) {}

  bool bind(i2c_master_bus_handle_t bus);
  bool probe() const;
  bool read_register(std::uint8_t reg, std::uint8_t* data, std::size_t length) const;
  bool write_register(std::uint8_t reg, const std::uint8_t* data, std::size_t length) const;
  bool read_u8(std::uint8_t reg, std::uint8_t& value) const;
  bool write_u8(std::uint8_t reg, std::uint8_t value) const;

 private:
  std::uint8_t address_;
  std::uint32_t scl_speed_hz_;
  i2c_master_bus_handle_t bus_{nullptr};
  i2c_master_dev_handle_t device_{nullptr};
};

class Py32IoExpander final {
 public:
  bool bind(i2c_master_bus_handle_t bus) {
    return device_.bind(bus) && alternate_device_.bind(bus);
  }
  bool begin();
  bool ready() const { return ready_; }
  std::uint8_t version() const { return version_; }
  bool set_vm_enabled(bool enabled);
  // Bounded-latency emergency cut: one output-register read-modify-write,
  // guarded by a short mutex wait so the emergency path never queues behind
  // an SCS transaction. Safe to call concurrently with set_vm_enabled().
  bool cut_power_fast();
  bool vm_enabled() const { return vm_enabled_; }

 private:
  static constexpr std::uint8_t kDirectionLow = 0x03;
  static constexpr std::uint8_t kOutputLow = 0x05;
  static constexpr std::uint8_t kPullUpLow = 0x09;
  static constexpr std::uint8_t kVersion = 0x02;
  static constexpr std::uint32_t kMutexTimeoutMs = 20;
  I2cDevice device_{kPy32Address, 100000};
  I2cDevice alternate_device_{kPy32AlternateAddress, 100000};
  I2cDevice* active_device_{nullptr};
  bool ready_{false};
  bool vm_enabled_{false};
  std::uint8_t version_{0};
  // Concurrency-safe access to the shared I2C bus register RMWs.
  bool lock();
  void unlock();
  StaticSemaphore_t mutex_storage_{};
  SemaphoreHandle_t mutex_{nullptr};

  bool update_bit(std::uint8_t reg, std::uint8_t bit, bool value);
  I2cDevice* active_device() { return active_device_; }
  const I2cDevice* active_device() const { return active_device_; }
};

class ScsServoBus final {
 public:
  bool begin();
  void set_response_timeout(std::uint32_t timeout_ms) {
    response_timeout_ms_ = timeout_ms;
  }
  bool ping(std::uint8_t id, std::uint8_t* error_code = nullptr);
  bool enable_torque(std::uint8_t id, bool enabled);
  bool write_position(std::uint8_t id, std::uint16_t raw_position,
                     std::uint16_t time_ms = 20, std::uint16_t speed = 0);
  bool read_position(std::uint8_t id, std::uint16_t& raw_position);
  bool read_current(std::uint8_t id, std::int16_t& current);
  bool read_load(std::uint8_t id, std::int16_t& load);
  bool read_torque(std::uint8_t id, bool& enabled);

 private:
  static constexpr std::size_t kPacketBytes = 32;
  static constexpr std::uint8_t kInstructionPing = 0x01;
  static constexpr std::uint8_t kInstructionRead = 0x02;
  static constexpr std::uint8_t kInstructionWrite = 0x03;
  static constexpr std::uint8_t kTorqueRegister = 40;
  static constexpr std::uint8_t kGoalPositionRegister = 42;
  static constexpr std::uint8_t kPresentPositionRegister = 56;
  static constexpr std::uint8_t kPresentLoadRegister = 60;
  static constexpr std::uint8_t kPresentCurrentRegister = 69;

  bool ready_{false};

  bool write_packet(std::uint8_t id, std::uint8_t instruction,
                    std::uint8_t address, const std::uint8_t* data,
                    std::size_t length, bool expect_ack);
  bool read_packet(std::uint8_t expected_id, std::uint8_t* data,
                   std::size_t length, std::uint8_t* error_code = nullptr);
  bool read_register(std::uint8_t id, std::uint8_t address, std::uint8_t* data,
                     std::size_t length);
  bool write_register(std::uint8_t id, std::uint8_t address,
                      const std::uint8_t* data, std::size_t length);
  bool read_word(std::uint8_t id, std::uint8_t address, std::uint16_t& value);
  bool write_word(std::uint8_t id, std::uint8_t address, std::uint16_t value);
  std::uint32_t response_timeout_ms_{20};
};

class StackChanServoPair final : public Servo {
 public:
  bool begin(Py32IoExpander& expander);
  bool power_on();
  bool power_off();
  bool hardware_ready() const { return hardware_ready_; }
  bool torque_enabled() const { return torque_enabled_; }
  ServoPosition cached_position() const;
  std::uint16_t raw_yaw() const { return raw_yaw_; }
  std::uint16_t raw_pitch() const { return raw_pitch_; }

  // Primitives for the ServoIoTask owner. Unlike set_position()/stop() these
  // never re-run the boot self-test and never latch the pair state on a
  // transient feedback failure; the owner policy decides fault handling.
  bool wake_light();
  bool read_feedback_pair(ServoPosition& position, std::uint16_t& yaw_raw,
                          std::uint16_t& pitch_raw);
  bool enable_torque_pair(bool enabled);
  bool write_goal_pair(const ServoPosition& position, std::uint16_t& yaw_raw,
                       std::uint16_t& pitch_raw);
  bool torque_off_pair();

  Capability capability() const override { return Capability::ServoYaw; }
  CapabilityStatus status() const override { return status_; }
  bool set_position(ServoPosition position) override;
  bool read_position(ServoPosition& position) override;
  bool stop(bool release_torque) override;

 private:
  static constexpr std::uint8_t kYawId = 1;
  static constexpr std::uint8_t kPitchId = 2;
  static constexpr std::uint16_t kRawMin = 0;
  static constexpr std::uint16_t kRawMax = 1000;
  static constexpr float kHomePitchDegrees = 45.0F;
  static constexpr float kRawStepsPerDegree = 16.0F / 5.0F;

  ScsServoBus bus_;
  Py32IoExpander* expander_{nullptr};
  CapabilityStatus status_{};
  bool hardware_ready_{false};
  bool torque_enabled_{false};
  std::uint16_t raw_yaw_{460};
  std::uint16_t raw_pitch_{620};

  static std::uint16_t angle_to_raw(float angle, float zero, float minimum,
                                    float maximum, std::uint16_t default_zero);
  static float raw_to_angle(std::uint16_t raw, std::uint16_t zero,
                            float home, float minimum, float maximum);
  bool refresh_feedback();
  void fail(const char* code, const char* detail, bool latched);
};

class StackChanHeadTouch final : public Touch {
 public:
  bool bind(i2c_master_bus_handle_t bus);
  bool begin();

  Capability capability() const override { return Capability::Touch; }
  CapabilityStatus status() const override { return status_; }
  bool read(TouchSample& sample) override;

 private:
  I2cDevice device_{kHeadTouchAddress, 100000};
  I2cDevice display_device_{kDisplayTouchAddress};
  CapabilityStatus status_{};
  std::uint64_t touch_started_ms_{0};
  bool touched_{false};
  bool head_available_{false};
  bool display_available_{false};
};

class StackChanImu final : public Imu {
 public:
  bool bind(i2c_master_bus_handle_t bus) { return device_.bind(bus); }
  bool begin();

  Capability capability() const override { return Capability::Imu; }
  CapabilityStatus status() const override { return status_; }
  bool read(ImuSample& sample) override;

 private:
  I2cDevice device_{kImuAddress};
  CapabilityStatus status_{};
};

class StackChanProximity final : public Proximity {
 public:
  bool bind(i2c_master_bus_handle_t bus) { return device_.bind(bus); }
  bool begin();

  Capability capability() const override { return Capability::Proximity; }
  CapabilityStatus status() const override { return status_; }
  bool read(ProximitySample& sample) override;

 private:
  I2cDevice device_{0x23};
  CapabilityStatus status_{};
};

class StackChanCamera final : public Camera {
 public:
  struct JpegFrame {
    const std::uint8_t* data{nullptr};
    std::size_t size{0};
    std::uint16_t width{0};
    std::uint16_t height{0};
    std::uint64_t timestamp_ms{0};
    std::uintptr_t handle{0};
    bool converted{false};
  };

  bool begin();

  Capability capability() const override { return Capability::Camera; }
  CapabilityStatus status() const override { return status_; }
  bool capture(CameraFrame& frame) override;
  bool capture_jpeg(JpegFrame& frame);
  void release_jpeg(JpegFrame& frame);

 private:
  CapabilityStatus status_{};
  bool initialized_{false};
  StaticSemaphore_t capture_mutex_storage_{};
  SemaphoreHandle_t capture_mutex_{nullptr};
};

class StackChanPerception final : public PerceptionSource {
 public:
  explicit StackChanPerception(StackChanCamera& camera) : camera_(camera) {}

  Capability capability() const override { return Capability::Perception; }
  CapabilityStatus status() const override { return camera_.status(); }
  bool read(PersonObservation& observation) override;

 private:
  StackChanCamera& camera_;
};

class StackChanDisplay final : public Display, public FaceWriter {
 public:
  StackChanDisplay() : face_(*this) {}
  bool begin();

  Capability capability() const override { return Capability::Display; }
  CapabilityStatus status() const override { return status_; }
  bool render(const DisplayFrame& frame) override;

  // FaceWriter: window + row streaming over the SPI panel.
  bool begin_region(int x0, int y0, int x1, int y1) override;
  bool write_row(const std::uint8_t* rgb565_msb_first,
                 std::size_t byte_count) override;

 private:
  static constexpr spi_host_device_t kSpiHost = SPI3_HOST;
  static constexpr gpio_num_t kMosi = GPIO_NUM_37;
  static constexpr gpio_num_t kSclk = GPIO_NUM_36;
  static constexpr gpio_num_t kCs = GPIO_NUM_3;
  static constexpr gpio_num_t kDc = GPIO_NUM_35;
  static constexpr int kWidth = 320;
  static constexpr int kHeight = 240;
  static_assert(kWidth == FaceRaster::kWidth && kHeight == FaceRaster::kHeight,
                "StackChan display geometry must match the face raster");

  spi_device_handle_t device_{nullptr};
  CapabilityStatus status_{};
  SmileyFace face_;

  bool command(std::uint8_t value);
  bool data(const void* buffer, std::size_t length);
};

class StackChanTelemetry final : public Telemetry {
 public:
  Capability capability() const override { return Capability::Telemetry; }
  CapabilityStatus status() const override {
    return {CapabilityState::Available, {}};
  }
  bool emit(const TelemetryRecord& record) override {
    last_record_ = record;
    return true;
  }
  const TelemetryRecord& last_record() const { return last_record_; }

 private:
  TelemetryRecord last_record_{};
};

class StackChanBoard final {
 public:
  bool begin();
  bool motion_ready() const { return servo_.hardware_ready(); }
  bool motion_power_on() { return servo_.power_on(); }
  bool motion_power_off() { return servo_.power_off(); }

  StackChanHeadTouch& touch() { return touch_; }
  StackChanImu& imu() { return imu_; }
  StackChanProximity& proximity() { return proximity_; }
  StackChanCamera& camera() { return camera_; }
  StackChanPerception& perception() { return perception_; }
  StackChanServoPair& servo() { return servo_; }
  Py32IoExpander& expander() { return expander_; }
  StackChanDisplay& display() { return display_; }
  StackChanTelemetry& telemetry() { return telemetry_; }

 private:
  bool begin_i2c();
  bool begin_power_management();
  bool begin_display_expander();

  i2c_master_bus_handle_t i2c_bus_{nullptr};
  I2cDevice power_management_{kPowerManagementAddress};
  I2cDevice display_expander_{kDisplayExpanderAddress};
  Py32IoExpander expander_{};
  StackChanServoPair servo_{};
  StackChanHeadTouch touch_{};
  StackChanImu imu_{};
  StackChanProximity proximity_{};
  StackChanCamera camera_{};
  StackChanPerception perception_{camera_};
  StackChanDisplay display_{};
  StackChanTelemetry telemetry_{};
};

}  // namespace lifeos::hal::stackchan
