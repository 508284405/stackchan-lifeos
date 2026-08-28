#include "lifeos/hal/fake.hpp"

#include <cassert>

using namespace lifeos::hal;

int main() {
  FakeTouch touch;
  touch.set_sample(TouchSample{42, true, 2, 120});
  TouchSample sample;
  assert(touch.capability() == Capability::Touch);
  assert(touch.status().state == CapabilityState::Available);
  assert(touch.read(sample) && sample.touched && sample.zone == 2);

  touch.set_available(false);
  assert(touch.status().state == CapabilityState::Unavailable);
  assert(!touch.read(sample));
  touch.inject_fault(Fault{"touch.bus", FaultSeverity::Critical, true, "offline"});
  assert(touch.status().state == CapabilityState::Faulted);
  assert(touch.status().fault.latched);

  FakeServo servo;
  assert(servo.set_position({20.0F, 40.0F}));
  assert(servo.position().yaw_deg == 20.0F);
  ServoPosition feedback;
  assert(servo.read_position(feedback) && feedback.pitch_deg == 40.0F);
  assert(servo.stop(true) && !servo.torque_enabled());
  servo.set_available(false);
  assert(!servo.read_position(feedback));
  servo.inject_fault(Fault{"servo.feedback", FaultSeverity::Critical, true, "stalled"});
  assert(servo.status().state == CapabilityState::Faulted);

  FakeDisplay display;
  DisplayFrame frame;
  frame.expression[0] = 'x';
  assert(display.render(frame) && display.last_frame().expression[0] == 'x');

  FakeTelemetry telemetry;
  TelemetryRecord record;
  record.type[0] = 'h';
  assert(telemetry.emit(record) && telemetry.last_record().type[0] == 'h');

  FakePerceptionSource perception;
  assert(perception.capability() == Capability::Perception);
  perception.set_observation(PersonObservation{7, 3, true, 0.25F, -0.1F, 0.9F});
  PersonObservation person;
  assert(perception.read(person) && person.target_id == 3 && person.confidence == 0.9F);
  perception.set_available(false);
  assert(!perception.read(person));
  perception.inject_fault(Fault{"vision.bus", FaultSeverity::Error, false, "offline"});
  assert(perception.status().state == CapabilityState::Faulted);
  return 0;
}
