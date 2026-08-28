#include "lifeos/runtime/runtime.hpp"

#include <cassert>
#include <cstdint>

using namespace lifeos::runtime;

namespace {
struct Context { int value{0}; };
bool first(void* raw) { static_cast<Context*>(raw)->value = 1; return true; }
bool second(void* raw) { static_cast<Context*>(raw)->value *= 2; return true; }
bool fail(void*) { return false; }
}

int main() {
  StaticGraph graph;
  assert(graph.add_node("first", first));
  assert(graph.add_node("second", second));
  assert(graph.add_dependency(1, 0));
  assert(graph.compile());
  Context context;
  assert(StaticExecutor(graph).execute(&context) && context.value == 2);

  StaticGraph cycle;
  assert(cycle.add_node("a", first) && cycle.add_node("b", second));
  assert(cycle.add_dependency(0, 1) && cycle.add_dependency(1, 0));
  assert(!cycle.compile());
  StaticGraph failed;
  assert(failed.add_node("fail", fail) && failed.compile());
  assert(!failed.run(&context));

  FastSafetyLoop safety;
  MotionRequest request{true, 80.0F, 45.0F, 2000};
  SafetySample sample;
  sample.now_ms = 1000;
  safety.heartbeat(1000);
  auto decision = safety.tick(sample, request);
  assert(decision.accepted && decision.yaw_deg == FastSafetyLoop::kYawSoftMax);

  request.expires_at_ms = 1000;
  assert(!safety.tick(sample, request).accepted);
  request.expires_at_ms = 2000;
  sample.now_ms = 1300;
  auto watchdog = safety.tick(sample, request);
  assert(has_fault(watchdog.faults, SafetyFault::Watchdog) && safety.latched());
  assert(!safety.clear_latched(false));
  assert(safety.clear_latched(true));

  safety.heartbeat(1300);
  sample.now_ms = 1300;
  sample.emergency_stop = true;
  auto stopped = safety.tick(sample, request);
  assert(!stopped.accepted && !stopped.torque_enabled);
  assert(has_fault(stopped.faults, SafetyFault::EmergencyStop));
  assert(safety.clear_latched(true));
  sample.emergency_stop = false;
  sample.stall = true;
  auto stalled = safety.tick(sample, request);
  assert(!stalled.torque_enabled && has_fault(stalled.faults, SafetyFault::Stall));
  assert(safety.clear_latched(true));
  sample.stall = false;
  sample.yaw_limit = true;
  auto limited = safety.tick(sample, request);
  assert(!limited.torque_enabled && has_fault(limited.faults, SafetyFault::HardLimit));
  return 0;
}
