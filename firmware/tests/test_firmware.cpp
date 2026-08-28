#include "lifeos/firmware.hpp"

#include <cassert>
#include <iostream>

using namespace lifeos;

int main() {
  MotionSafety safety;
  SensorSnapshot sensors;
  sensors.timestamp_ms = 1000;

  BehaviorCommand safe{"look", Priority::Agent, 0.5F, 900, 2000, 40.0F, 45.0F};
  assert(safety.evaluate(safe, sensors, 1000).accepted);

  BehaviorCommand soft_clamped{"look", Priority::Agent, 0.5F, 900, 2000, 80.0F, 45.0F};
  auto clamped = safety.evaluate(soft_clamped, sensors, 1000);
  assert(clamped.accepted && clamped.yaw_deg == MotionSafety::kYawSoftMax);

  BehaviorCommand hard_rejected{"look", Priority::Agent, 0.5F, 900, 2000, 91.0F, 45.0F};
  assert(!safety.evaluate(hard_rejected, sensors, 1000).accepted);

  BehaviorCommand pitch_rejected{"look", Priority::Agent, 0.5F, 900, 2000, 0.0F, 90.0F};
  assert(!safety.evaluate(pitch_rejected, sensors, 1000).accepted);

  sensors.stall = true;
  auto stopped = safety.evaluate(safe, sensors, 1000);
  assert(!stopped.accepted && !stopped.torque_enabled);

  CommandArbiter arbiter;
  std::vector<BehaviorCommand> choices{
      {"idle", Priority::Idle, 0.1F, 900, 2000, std::nullopt, std::nullopt},
      {"agent", Priority::Agent, 0.5F, 900, 2000, std::nullopt, std::nullopt},
      {"reflex", Priority::Reflex, 0.8F, 900, 2000, std::nullopt, std::nullopt}};
  assert(arbiter.choose(choices, 1000)->name == "reflex");

  StaticDag cycle;
  assert(cycle.add_node("a", [](GraphContext&) { return true; }));
  assert(cycle.add_node("b", [](GraphContext&) { return true; }));
  assert(cycle.add_dependency("a", "b"));
  assert(cycle.add_dependency("b", "a"));
  assert(!cycle.compile());

  auto graph = build_body_graph();
  GraphContext context;
  context.sensors.timestamp_ms = 1000;
  context.sensors.touch = true;
  assert(graph.run(context));
  assert(context.selected && context.selected->name == "happy");

  std::cout << "lifeos firmware tests passed\n";
  return 0;
}
