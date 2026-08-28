#include "lifeos/hal/fake.hpp"

#include <utility>

namespace lifeos::hal {
namespace {
bool usable(const CapabilityStatus& status) {
  return status.state == CapabilityState::Available;
}
void available(CapabilityStatus& status, bool value) {
  status = CapabilityStatus{value ? CapabilityState::Available
                                  : CapabilityState::Unavailable, {}};
}
void fault(CapabilityStatus& status, Fault value) {
  status.state = CapabilityState::Faulted;
  status.fault = std::move(value);
}
}  // namespace

bool FakeCamera::capture(CameraFrame& frame) { if (!usable(status_)) return false; frame = frame_; return true; }
void FakeCamera::set_available(bool value) { available(status_, value); }
void FakeCamera::inject_fault(Fault value) { fault(status_, std::move(value)); }

bool FakeTouch::read(TouchSample& sample) { if (!usable(status_)) return false; sample = sample_; return true; }
void FakeTouch::set_available(bool value) { available(status_, value); }
void FakeTouch::inject_fault(Fault value) { fault(status_, std::move(value)); }

bool FakeImu::read(ImuSample& sample) { if (!usable(status_)) return false; sample = sample_; return true; }
void FakeImu::set_available(bool value) { available(status_, value); }
void FakeImu::inject_fault(Fault value) { fault(status_, std::move(value)); }

bool FakeProximity::read(ProximitySample& sample) { if (!usable(status_)) return false; sample = sample_; return true; }
void FakeProximity::set_available(bool value) { available(status_, value); }
void FakeProximity::inject_fault(Fault value) { fault(status_, std::move(value)); }

bool FakeServo::set_position(ServoPosition value) { if (!usable(status_)) return false; position_ = value; torque_enabled_ = true; return true; }
bool FakeServo::read_position(ServoPosition& value) { if (!usable(status_)) return false; value = position_; return true; }
bool FakeServo::stop(bool release_torque) {
  // The safety path must remain callable after a capability fault. A target
  // adapter should route this to the lowest-level torque-off primitive.
  torque_enabled_ = !release_torque;
  return true;
}
void FakeServo::set_available(bool value) { available(status_, value); }
void FakeServo::inject_fault(Fault value) { fault(status_, std::move(value)); }

bool FakeDisplay::render(const DisplayFrame& frame) { if (!usable(status_)) return false; last_frame_ = frame; return true; }
void FakeDisplay::set_available(bool value) { available(status_, value); }
void FakeDisplay::inject_fault(Fault value) { fault(status_, std::move(value)); }

bool FakeTelemetry::emit(const TelemetryRecord& record) { if (!usable(status_)) return false; last_record_ = record; return true; }
void FakeTelemetry::set_available(bool value) { available(status_, value); }
void FakeTelemetry::inject_fault(Fault value) { fault(status_, std::move(value)); }

bool FakePerceptionSource::read(PersonObservation& value) { if (!usable(status_)) return false; value = observation_; return true; }
void FakePerceptionSource::set_available(bool value) { available(status_, value); }
void FakePerceptionSource::inject_fault(Fault value) { fault(status_, std::move(value)); }

}  // namespace lifeos::hal
