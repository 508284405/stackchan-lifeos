#pragma once

#include <array>
#include <cstdint>
#include <string>

namespace lifeos::hal {

enum class Capability : std::uint8_t {
  Camera,
  Perception,
  Touch,
  Imu,
  Proximity,
  ServoYaw,
  ServoPitch,
  Display,
  Telemetry,
};

enum class CapabilityState : std::uint8_t {
  Unknown,
  Available,
  Unavailable,
  Faulted,
  Disabled,
};

enum class FaultSeverity : std::uint8_t { Info, Warning, Error, Critical };

struct Fault {
  std::string code;
  FaultSeverity severity{FaultSeverity::Error};
  bool latched{false};
  std::string detail;
};

struct CapabilityStatus {
  CapabilityState state{CapabilityState::Unknown};
  Fault fault;
};

class Device {
 public:
  virtual ~Device() = default;
  virtual Capability capability() const = 0;
  virtual CapabilityStatus status() const = 0;
};

struct CameraFrame {
  std::uint64_t timestamp_ms{0};
  std::uint16_t width{0};
  std::uint16_t height{0};
  bool person_present{false};
};

class Camera { public: virtual ~Camera() = default; virtual Capability capability() const = 0; virtual CapabilityStatus status() const = 0; virtual bool capture(CameraFrame& frame) = 0; };

struct TouchSample {
  std::uint64_t timestamp_ms{0};
  bool touched{false};
  std::uint16_t zone{0};
  std::uint32_t duration_ms{0};
};

class Touch { public: virtual ~Touch() = default; virtual Capability capability() const = 0; virtual CapabilityStatus status() const = 0; virtual bool read(TouchSample& sample) = 0; };

struct ImuSample {
  std::uint64_t timestamp_ms{0};
  float accel_x{0.0F};
  float accel_y{0.0F};
  float accel_z{0.0F};
  float gyro_x{0.0F};
  float gyro_y{0.0F};
  float gyro_z{0.0F};
  bool shaken{false};
};

class Imu { public: virtual ~Imu() = default; virtual Capability capability() const = 0; virtual CapabilityStatus status() const = 0; virtual bool read(ImuSample& sample) = 0; };
using IMU = Imu;

struct ProximitySample {
  std::uint64_t timestamp_ms{0};
  bool present{false};
  std::uint16_t distance_mm{0};
};

class Proximity { public: virtual ~Proximity() = default; virtual Capability capability() const = 0; virtual CapabilityStatus status() const = 0; virtual bool read(ProximitySample& sample) = 0; };

struct ServoPosition { float yaw_deg{0.0F}; float pitch_deg{45.0F}; };

class Servo { public: virtual ~Servo() = default; virtual Capability capability() const = 0; virtual CapabilityStatus status() const = 0; virtual bool set_position(ServoPosition position) = 0; virtual bool read_position(ServoPosition& position) = 0; virtual bool stop(bool release_torque) = 0; };

struct DisplayFrame {
  std::uint64_t timestamp_ms{0};
  std::array<char, 64> expression{};
};

class Display { public: virtual ~Display() = default; virtual Capability capability() const = 0; virtual CapabilityStatus status() const = 0; virtual bool render(const DisplayFrame& frame) = 0; };

struct TelemetryRecord {
  std::uint64_t timestamp_ms{0};
  std::array<char, 32> type{};
  std::array<char, 96> payload{};
};

class Telemetry { public: virtual ~Telemetry() = default; virtual Capability capability() const = 0; virtual CapabilityStatus status() const = 0; virtual bool emit(const TelemetryRecord& record) = 0; };

struct PersonObservation {
  std::uint64_t timestamp_ms{0};
  std::uint16_t target_id{0};
  bool present{false};
  float x{0.0F};
  float y{0.0F};
  float confidence{0.0F};
};

class PerceptionSource {
 public:
  virtual ~PerceptionSource() = default;
  virtual Capability capability() const = 0;
  virtual CapabilityStatus status() const = 0;
  virtual bool read(PersonObservation& observation) = 0;
};

}  // namespace lifeos::hal
