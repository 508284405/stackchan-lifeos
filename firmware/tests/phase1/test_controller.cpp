#include "lifeos/hal/fake.hpp"
#include "lifeos/phase1/controller.hpp"

#include <cassert>

using namespace lifeos;

int main() {
  std::uint64_t now = 1000;
  hal::FakeTouch touch;
  hal::FakeProximity proximity;
  hal::FakePerceptionSource perception;
  hal::FakeServo servo;
  hal::FakeDisplay display;
  hal::FakeTelemetry telemetry;
  phase1::Controller controller(touch, proximity, perception, servo, display,
                                telemetry, [&now] { return now; }, [] { return 1U; });

  assert(controller.boot());
  assert(controller.state().mode == behavior::LifeMode::IDLE);
  assert(!servo.torque_enabled());

  const char* hello =
      "{\"schema\":\"lifeos.v1\",\"kind\":\"hello\",\"type\":\"hello.host\","
      "\"event_id\":\"hello-1\",\"device_id\":\"stackchan-01\",\"seq\":1,\"ts_ms\":1000,\"payload\":{}}";
  const char* pause =
      "{\"schema\":\"lifeos.v1\",\"kind\":\"command\",\"type\":\"command.control\","
      "\"event_id\":\"pause-1\",\"device_id\":\"stackchan-01\",\"seq\":2,\"ts_ms\":1001,\"payload\":{\"action\":\"pause\"}}";
  assert(controller.ingest_line(hello).accepted);
  assert(controller.ingest_line(pause).accepted);
  assert(controller.state().mode == behavior::LifeMode::PAUSED);
  assert(!servo.torque_enabled());
  assert(!controller.control(phase1::ControlAction::Home));
  now += 1;
  assert(controller.graph_tick());
  assert(!controller.safety_tick().accepted);
  assert(!servo.torque_enabled());
  assert(controller.control(phase1::ControlAction::Resume));

  hal::ProximitySample near;
  near.timestamp_ms = now;
  near.present = true;
  proximity.set_sample(near);
  hal::PersonObservation person;
  person.timestamp_ms = now;
  person.target_id = 7;
  person.present = true;
  person.x = 0.5F;
  person.y = 0.2F;
  person.confidence = 0.9F;
  perception.set_observation(person);
  assert(controller.graph_tick());
  assert(controller.state().mode == behavior::LifeMode::ATTENTIVE);
  auto decision = controller.safety_tick();
  assert(decision.accepted);
  assert(servo.position().yaw_deg == 30.0F);

  controller.control(phase1::ControlAction::Pause);
  assert(controller.state().mode == behavior::LifeMode::PAUSED);
  assert(!servo.torque_enabled());

  controller.control(phase1::ControlAction::Resume);
  now += 10;
  assert(controller.graph_tick());
  decision = controller.safety_tick(true, false);
  assert(!decision.accepted);
  assert(controller.state().mode == behavior::LifeMode::EMERGENCY_STOP);
  assert(!controller.control(phase1::ControlAction::ClearFault, false));

  hal::TouchSample long_touch;
  long_touch.timestamp_ms = now;
  long_touch.touched = true;
  long_touch.duration_ms = 1600;
  touch.set_sample(long_touch);
  now += 1;
  assert(controller.graph_tick());
  assert(controller.state().mode != behavior::LifeMode::EMERGENCY_STOP);

  controller.control(phase1::ControlAction::Home);
  now += 1;
  decision = controller.safety_tick();
  assert(decision.accepted);
  assert(servo.position().yaw_deg == 0.0F);
  assert(servo.position().pitch_deg == 45.0F);

  servo.inject_fault({"servo.feedback", hal::FaultSeverity::Critical, true,
                      "feedback unavailable"});
  now += 1;
  decision = controller.safety_tick();
  assert(!decision.accepted);
  assert(controller.state().mode == behavior::LifeMode::FAULT);
  assert(!servo.torque_enabled());
  return 0;
}
