#pragma once

#include "lifeos/hal/hal.hpp"

namespace lifeos::hal {

class FakeCamera final : public Camera {
 public:
  Capability capability() const override { return Capability::Camera; }
  CapabilityStatus status() const override { return status_; }
  bool capture(CameraFrame& frame) override;
  void set_frame(CameraFrame frame) { frame_ = frame; }
  void set_available(bool available);
  void inject_fault(Fault fault);
 private:
  CameraFrame frame_{};
  CapabilityStatus status_{CapabilityState::Available, {}};
};

class FakeTouch final : public Touch {
 public:
  Capability capability() const override { return Capability::Touch; }
  CapabilityStatus status() const override { return status_; }
  bool read(TouchSample& sample) override;
  void set_sample(TouchSample sample) { sample_ = sample; }
  void set_available(bool available);
  void inject_fault(Fault fault);
 private:
  TouchSample sample_{};
  CapabilityStatus status_{CapabilityState::Available, {}};
};

class FakeImu final : public Imu {
 public:
  Capability capability() const override { return Capability::Imu; }
  CapabilityStatus status() const override { return status_; }
  bool read(ImuSample& sample) override;
  void set_sample(ImuSample sample) { sample_ = sample; }
  void set_available(bool available);
  void inject_fault(Fault fault);
 private:
  ImuSample sample_{};
  CapabilityStatus status_{CapabilityState::Available, {}};
};
using FakeIMU = FakeImu;

class FakeProximity final : public Proximity {
 public:
  Capability capability() const override { return Capability::Proximity; }
  CapabilityStatus status() const override { return status_; }
  bool read(ProximitySample& sample) override;
  void set_sample(ProximitySample sample) { sample_ = sample; }
  void set_available(bool available);
  void inject_fault(Fault fault);
 private:
  ProximitySample sample_{};
  CapabilityStatus status_{CapabilityState::Available, {}};
};

class FakeServo final : public Servo {
 public:
  Capability capability() const override { return Capability::ServoYaw; }
  CapabilityStatus status() const override { return status_; }
  bool set_position(ServoPosition position) override;
  bool read_position(ServoPosition& position) override;
  bool stop(bool release_torque) override;
  void set_available(bool available);
  void inject_fault(Fault fault);
  ServoPosition position() const { return position_; }
  bool torque_enabled() const { return torque_enabled_; }
 private:
  ServoPosition position_{};
  bool torque_enabled_{true};
  CapabilityStatus status_{CapabilityState::Available, {}};
};

class FakeDisplay final : public Display {
 public:
  Capability capability() const override { return Capability::Display; }
  CapabilityStatus status() const override { return status_; }
  bool render(const DisplayFrame& frame) override;
  void set_available(bool available);
  void inject_fault(Fault fault);
  const DisplayFrame& last_frame() const { return last_frame_; }
 private:
  DisplayFrame last_frame_{};
  CapabilityStatus status_{CapabilityState::Available, {}};
};

class FakeTelemetry final : public Telemetry {
 public:
  Capability capability() const override { return Capability::Telemetry; }
  CapabilityStatus status() const override { return status_; }
  bool emit(const TelemetryRecord& record) override;
  void set_available(bool available);
  void inject_fault(Fault fault);
  const TelemetryRecord& last_record() const { return last_record_; }
 private:
  TelemetryRecord last_record_{};
  CapabilityStatus status_{CapabilityState::Available, {}};
};

class FakePerceptionSource final : public PerceptionSource {
 public:
  Capability capability() const override { return Capability::Perception; }
  CapabilityStatus status() const override { return status_; }
  bool read(PersonObservation& observation) override;
  void set_observation(PersonObservation observation) { observation_ = observation; }
  void set_available(bool available);
  void inject_fault(Fault fault);
 private:
  PersonObservation observation_{};
  CapabilityStatus status_{CapabilityState::Available, {}};
};

}  // namespace lifeos::hal
