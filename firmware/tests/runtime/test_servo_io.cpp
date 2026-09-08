// Servo I/O ownership and safety gate regressions. The harness drives the
// same deterministic core the target uses: core.io_step() on a fake link
// (servo I/O task), safety_gate() (safety task) and core.submit() (USB main
// task). Time is fully simulated; link operations can be made arbitrarily
// slow to prove the other paths never wait on the servo bus.

#include "lifeos/runtime/servo_io.hpp"

#include <cassert>
#include <cstdio>
#include <string>
#include <vector>

using namespace lifeos;
using lifeos::runtime::FastSafetyLoop;
using lifeos::runtime::OutputKind;
using lifeos::runtime::SafetyGateResult;
using lifeos::runtime::ServoFeedbackSnapshot;
using lifeos::runtime::ServoIoCore;
using lifeos::runtime::ServoIoState;

#define CHECK(condition)                                                     \
  do {                                                                       \
    if (!(condition)) {                                                      \
      std::printf("CHECK failed at line %d: %s\n", __LINE__, #condition);    \
      return 1;                                                              \
    }                                                                        \
  } while (false)

namespace {

class FakeLink final : public runtime::ServoLink {
 public:
  explicit FakeLink(std::uint64_t* clock) : now(clock) {}
  std::uint64_t* now{nullptr};
  std::uint32_t op_delay_ms{0};
  bool vm{false};
  bool torque{false};
  bool hardware{true};
  bool fail_writes{false};
  int fail_reads_remaining{0};  // -1 = always fail
  hal::ServoPosition feedback{1.56F, 45.0F};
  hal::ServoPosition goal{};

  int vm_writes{0};
  int torque_writes{0};
  int goal_writes{0};
  int feedback_reads{0};
  int power_ons{0};
  int wake_lights{0};
  std::vector<const char*> op_order;

  bool vm_enabled() const override { return vm; }
  bool set_vm_enabled(bool enabled) override {
    tick();
    ++vm_writes;
    op_order.push_back(enabled ? "vm_on" : "vm_cut");
    vm = enabled;
    return true;
  }
  bool enable_torque(bool enabled) override {
    tick();
    ++torque_writes;
    op_order.push_back(enabled ? "torque_on" : "torque_off");
    torque = enabled;
    return true;
  }
  bool write_goal(const hal::ServoPosition& position, std::uint16_t& yaw_raw,
                  std::uint16_t& pitch_raw) override {
    tick();
    ++goal_writes;
    op_order.push_back("write");
    if (fail_writes) return false;
    goal = position;
    yaw_raw = 460;
    pitch_raw = 620;
    return true;
  }
  bool read_feedback(hal::ServoPosition& position, std::uint16_t& yaw_raw,
                     std::uint16_t& pitch_raw) override {
    tick();
    ++feedback_reads;
    op_order.push_back("read");
    if (fail_reads_remaining != 0) {
      if (fail_reads_remaining > 0) --fail_reads_remaining;
      return false;
    }
    position = feedback;
    yaw_raw = 460;
    pitch_raw = 620;
    return true;
  }
  bool wake_light() override {
    tick();
    ++wake_lights;
    op_order.push_back("wake_light");
    vm = true;
    *now += 200;
    torque = false;
    return true;
  }
  bool power_on_full() override {
    tick();
    ++power_ons;
    op_order.push_back("power_on_full");
    vm = true;
    *now += 500;
    torque = false;
    return true;
  }
  bool hardware_ready() const override { return hardware; }

  void tick() {
    if (now != nullptr && op_delay_ms != 0) *now += op_delay_ms;
  }
  int total_ops() const {
    return vm_writes + torque_writes + goal_writes + feedback_reads + power_ons +
           wake_lights;
  }
};

struct Harness {
  std::uint64_t now{1000};
  FakeLink link;
  ServoIoCore core;
  FastSafetyLoop safety{1000000};  // watchdog covered by runtime tests
  runtime::StallMonitor monitor;
  bool paused{false};
  bool emergency{false};
  bool fault{false};
  bool frozen{false};
  bool link_lost{false};
  bool clear_requested{false};

  explicit Harness(bool vm_on) : link{&now} {
    safety.heartbeat(now);
    link.vm = vm_on;
    core.init(true, vm_on);
  }

  std::uint32_t submit(float yaw, float pitch, std::uint64_t expires) {
    return core.submit(yaw, pitch, expires);
  }

  SafetyGateResult tick() {
    runtime::SafetyGateInputs inputs;
    inputs.now_ms = now;
    inputs.paused = paused;
    inputs.emergency = emergency;
    inputs.fault_latched = fault;
    inputs.feedback_frozen = frozen;
    inputs.link_lost = link_lost;
    inputs.clear_requested = clear_requested;
    auto result = runtime::safety_gate(core, safety, monitor, inputs);
    if (result.cleared) {
      clear_requested = false;
      fault = false;
      emergency = false;
      paused = false;
      frozen = false;
    }
    if (safety.latched()) fault = true;
    return result;
  }

  void io() { core.io_step(link, now); }
  void run_io(int steps) {
    for (int step = 0; step < steps; ++step) io();
  }
};

int test_ack_path_not_blocked_by_slow_servo_io() {
  Harness harness(true);
  harness.link.op_delay_ms = 1000;
  harness.submit(12.0F, 52.0F, 100000);
  harness.tick();
  harness.io();  // ReadyTorqueOff -> ApplyingCommand
  harness.io();  // torque enable; io task now busy for ~1 s of work
  CHECK(harness.link.torque_writes == 1);
  // The USB main path must complete without touching the servo bus.
  const auto generation = harness.core.submit(20.0F, 50.0F, 100000);
  CHECK(generation == 2);
  CHECK(harness.link.goal_writes == 0);
  CHECK(harness.link.total_ops() == 1);
  CHECK(harness.core.take_command(harness.now).generation == 2);
  CHECK(harness.core.feedback().sampled_at_ms <= harness.now);
  return 0;
}

int test_safety_tick_never_calls_blocking_servo_api() {
  Harness harness(true);
  harness.submit(12.0F, 52.0F, 100000);
  for (int index = 0; index < 20; ++index) {
    harness.now += 20;
    const auto gate = harness.tick();
    CHECK(gate.output.kind == OutputKind::Motion);
    CHECK(harness.link.total_ops() == 0);
  }
  // The link still works when the owner task drives it.
  harness.run_io(4);
  CHECK(harness.link.goal_writes == 1);
  return 0;
}

int test_stall_timer_does_not_start_before_command_applied() {
  Harness harness(true);
  harness.submit(12.0F, 52.0F, 100000);
  harness.tick();
  harness.io();  // -> ApplyingCommand
  harness.io();  // torque enabled; position write not issued yet
  harness.now += 5000;
  const auto gate = harness.tick();
  CHECK(!gate.command_applied);
  CHECK(!gate.stall);
  CHECK(!harness.fault);
  CHECK(harness.core.feedback().command_applied_ms == 0);
  return 0;
}

int test_command_applied_only_after_write_success() {
  Harness harness(true);
  harness.run_io(2);  // idle feedback samples make the snapshot valid
  CHECK(harness.core.feedback().valid);
  harness.link.fail_writes = true;
  harness.submit(12.0F, 52.0F, 100000);
  harness.tick();
  harness.io();             // -> ApplyingCommand
  harness.io();             // torque enable
  harness.io();             // write fails -> Stopping/Faulted
  CHECK(harness.link.goal_writes == 1);
  CHECK(harness.core.feedback().command_applied_ms == 0);

  // Recovery: clear the fault, then a fresh command applies with a timestamp.
  harness.clear_requested = true;
  const auto cleared = harness.tick();
  CHECK(cleared.cleared);
  harness.run_io(4);  // stop drain -> Faulted -> Reset -> PowerOff(full_test_next)
  CHECK(harness.core.io_state() == ServoIoState::PowerOff);
  harness.link.fail_writes = false;
  const auto recovered = harness.submit(12.0F, 52.0F, 100000);
  harness.tick();
  harness.run_io(6);  // full self-test wake + torque + write
  CHECK(harness.link.power_ons == 1);
  const auto snapshot = harness.core.feedback();
  CHECK(snapshot.command_generation == recovered);
  CHECK(snapshot.command_applied_ms > 0);
  CHECK(snapshot.command_applied_ms <= harness.now);
  return 0;
}

int test_transient_feedback_failures_do_not_latch() {
  Harness harness(true);
  harness.run_io(2);
  harness.submit(12.0F, 52.0F, 100000);
  harness.tick();
  harness.run_io(3);  // applied -> Tracking
  harness.link.fail_reads_remaining = 2;
  for (int index = 0; index < 2; ++index) {
    harness.now += 30;
    harness.io();
    const auto gate = harness.tick();
    CHECK(!gate.stall);
    CHECK(!harness.fault);
    CHECK(gate.health.degraded);
  }
  CHECK(harness.core.feedback().consecutive_failures == 2);
  harness.now += 30;
  harness.io();  // recovers
  const auto gate = harness.tick();
  CHECK(!gate.stall);
  CHECK(harness.core.feedback().consecutive_failures == 0);
  CHECK(harness.link.goal_writes == 1);  // motion continued throughout
  return 0;
}

int test_persistent_feedback_stale_latches_after_threshold() {
  Harness harness(true);
  harness.run_io(2);
  harness.submit(12.0F, 52.0F, 100000);
  harness.tick();
  harness.run_io(3);
  harness.link.fail_reads_remaining = -1;
  bool latched = false;
  int ticks = 0;
  while (!latched && ticks < 60) {
    harness.now += 30;
    harness.io();
    const auto gate = harness.tick();
    latched = gate.stall && harness.safety.latched();
    ++ticks;
  }
  CHECK(latched);
  CHECK(ticks >= 12);  // no latch inside the first ~360 ms of failures
  CHECK(harness.core.feedback().consecutive_failures >= 3);
  // Latched critical fault stops output and cuts power via the owner task.
  harness.run_io(5);
  CHECK(harness.core.io_state() == ServoIoState::PowerOff);
  CHECK(!harness.link.vm);
  CHECK(!harness.link.torque);
  // With the rail down the frozen failure counter must not keep re-latching:
  // clear_fault has to take effect on the next tick.
  harness.clear_requested = true;
  const auto cleared = harness.tick();
  CHECK(cleared.cleared);
  CHECK(!harness.fault);
  harness.io();
  return 0;
}

int test_no_progress_latches_after_window() {
  Harness harness(true);
  harness.run_io(2);
  harness.submit(12.0F, 52.0F, 100000);
  harness.tick();
  harness.run_io(3);  // applied at now=1000 (no artificial op delay)
  const auto applied_at = harness.core.feedback().command_applied_ms;
  CHECK(applied_at == 1000);
  while (harness.now < applied_at + 690) {
    harness.now += 30;
    harness.io();
    const auto gate = harness.tick();
    CHECK(!gate.progress_stall);
  }
  harness.now = applied_at + 710;
  harness.io();
  const auto gate = harness.tick();
  CHECK(gate.progress_stall);
  CHECK(gate.stall);
  CHECK(harness.safety.latched());
  return 0;
}

int test_progress_refreshes_window() {
  Harness harness(true);
  harness.run_io(2);
  harness.submit(12.0F, 52.0F, 100000);
  harness.tick();
  harness.run_io(3);
  const auto applied_at = harness.core.feedback().command_applied_ms;
  while (harness.now < applied_at + 600) {
    harness.now += 30;
    harness.io();
    harness.tick();
  }
  // 0.94 deg of movement refreshes the progress window.
  harness.link.feedback.yaw_deg = 2.5F;
  harness.now += 30;
  harness.io();
  auto gate = harness.tick();
  CHECK(!gate.progress_stall);
  while (harness.now < applied_at + 1300) {
    harness.now += 30;
    harness.io();
    gate = harness.tick();
    CHECK(!gate.progress_stall);
  }
  harness.now += 30;
  harness.io();
  gate = harness.tick();
  CHECK(gate.progress_stall);
  return 0;
}

int test_expired_command_does_not_delay_next_execution() {
  Harness harness(true);
  harness.run_io(2);
  const auto stale = harness.submit(12.0F, 52.0F, harness.now + 100);
  harness.tick();
  harness.now += 200;  // stale command expires before the io task applies it
  const auto gate = harness.tick();
  CHECK(gate.output.kind == OutputKind::Stop);
  const auto fresh = harness.submit(9.0F, 51.0F, harness.now + 100000);
  const auto motion_gate = harness.tick();
  CHECK(motion_gate.output.kind == OutputKind::Motion);
  CHECK(motion_gate.output.generation == fresh);
  harness.run_io(4);
  const auto snapshot = harness.core.feedback();
  CHECK(snapshot.command_generation == fresh);
  CHECK(harness.link.goal.yaw_deg == 9.0F);
  CHECK(harness.link.goal.pitch_deg == 51.0F);
  (void)stale;
  return 0;
}

int test_pause_cancels_generation_and_cuts_power() {
  Harness harness(true);
  harness.run_io(2);
  harness.submit(12.0F, 52.0F, 100000);
  harness.tick();
  harness.run_io(3);
  CHECK(harness.link.torque);
  harness.paused = true;
  const auto gate = harness.tick();
  CHECK(gate.output.kind == OutputKind::Stop);
  CHECK(gate.output.power_cut);
  harness.run_io(5);
  CHECK(!harness.link.torque);
  CHECK(!harness.link.vm);
  CHECK(harness.core.io_state() == ServoIoState::PowerOff);
  // The cancelled generation must not execute after resume; only a fresh one.
  // The fresh command wakes from VM-off, so it must wait out the settle
  // window before the servo answers.
  harness.paused = false;
  const auto next = harness.submit(12.0F, 52.0F, 100000);
  CHECK(next != 0);
  harness.tick();
  harness.run_io(2);
  CHECK(harness.core.io_state() == ServoIoState::PoweringOn);
  for (int index = 0; index < 10 && harness.core.io_state() == ServoIoState::PoweringOn; ++index) {
    harness.now += 100;
    harness.io();
  }
  CHECK(harness.core.io_state() == ServoIoState::ReadyTorqueOff);
  harness.run_io(3);
  CHECK(harness.core.feedback().command_generation == next);
  return 0;
}

int test_preflight_wakes_and_samples_without_motion() {
  Harness harness(false);
  harness.core.request_preflight();
  const auto gate = harness.tick();
  CHECK(gate.output.kind == OutputKind::Wake);
  CHECK(harness.link.goal_writes == 0);
  CHECK(harness.link.torque_writes == 0);

  harness.io();  // PowerOff -> PoweringOn
  harness.io();  // VM on
  CHECK(harness.link.vm);
  CHECK(!harness.link.torque);
  harness.now += 300;
  harness.io();  // first feedback sample
  harness.now += 100;
  harness.io();  // confirm feedback -> ReadyTorqueOff

  const auto snapshot = harness.core.feedback();
  CHECK(harness.core.io_state() == ServoIoState::ReadyTorqueOff);
  CHECK(snapshot.valid);
  CHECK(snapshot.sampled_at_ms == harness.now);
  CHECK(snapshot.vm_enabled);
  CHECK(!snapshot.torque_enabled);
  CHECK(harness.link.goal_writes == 0);
  CHECK(harness.link.torque_writes == 1);
  CHECK(std::string(harness.link.op_order.back()) == "torque_off");

  // The SafetyTask normally publishes a non-cut Stop while idle. That must
  // retain periodic feedback sampling instead of letting the preflight sample
  // age out before the browser can unlock manual controls.
  harness.now += 100;
  harness.tick();
  harness.io();
  const auto refreshed = harness.core.feedback();
  CHECK(refreshed.sampled_at_ms == harness.now);
  CHECK(!refreshed.torque_enabled);
  CHECK(harness.link.goal_writes == 0);
  return 0;
}

int test_link_loss_cancels_generation() {
  Harness harness(true);
  harness.run_io(2);
  harness.submit(12.0F, 52.0F, 100000);
  harness.tick();
  harness.run_io(3);
  harness.link_lost = true;
  harness.tick();
  harness.run_io(5);
  CHECK(!harness.link.torque);
  CHECK(!harness.link.vm);
  CHECK(harness.core.io_state() == ServoIoState::PowerOff);
  return 0;
}

int test_emergency_cuts_vm_before_any_uart_work() {
  Harness harness(true);
  harness.run_io(2);
  harness.submit(12.0F, 52.0F, 100000);
  harness.tick();
  harness.run_io(3);
  CHECK(harness.link.vm);
  harness.link.op_order.clear();
  harness.emergency = true;
  harness.core.mark_emergency();
  harness.io();  // owner-side emergency cleanup
  CHECK(harness.link.op_order.size() == 1);
  CHECK(std::string(harness.link.op_order.back()) == "vm_cut");
  CHECK(!harness.link.vm);
  // The physical torque state is irrelevant once the rail is down; the core
  // reports torque-free immediately.
  CHECK(!harness.core.feedback().torque_enabled);
  CHECK(harness.core.io_state() == ServoIoState::PowerOff);
  const auto gate = harness.tick();
  CHECK(gate.output.kind == OutputKind::Stop);
  CHECK(gate.output.power_cut);
  CHECK(harness.fault);
  return 0;
}

int test_clear_fault_does_not_resume_old_command() {
  Harness harness(true);
  harness.run_io(2);
  const auto cancelled = harness.submit(12.0F, 52.0F, 100000);
  harness.tick();
  harness.link.fail_writes = true;
  harness.run_io(6);  // write fails -> Stopping -> Faulted
  CHECK(harness.core.io_state() == ServoIoState::Faulted);
  harness.now += 1000;  // snapshot goes stale, but VM is off so clear is legal
  harness.clear_requested = true;
  const auto gate = harness.tick();
  CHECK(gate.cleared);
  harness.io();  // Reset -> PowerOff + full_test_next
  CHECK(harness.core.take_command(harness.now).generation == 0);
  harness.run_io(4);
  CHECK(harness.link.goal_writes == 1);  // only the original failed attempt
  // A new command is required for motion; it wakes with a full self-test.
  harness.link.fail_writes = false;
  const auto next = harness.submit(0.0F, 45.0F, 100000);
  CHECK(next != cancelled);
  harness.tick();
  harness.run_io(6);
  CHECK(harness.link.power_ons == 1);
  CHECK(harness.core.feedback().command_generation == next);
  return 0;
}

int test_late_old_generation_results_never_overwrite_new_state() {
  Harness harness(true);
  harness.run_io(2);
  harness.submit(12.0F, 52.0F, 100000);
  harness.tick();
  harness.io();  // -> ApplyingCommand
  harness.io();  // torque enabled for the first generation
  // Newer generation supersedes the half-applied one.
  const auto second = harness.submit(9.0F, 51.0F, 100000);
  harness.tick();
  harness.run_io(3);
  auto snapshot = harness.core.feedback();
  CHECK(snapshot.command_generation == second);
  CHECK(snapshot.command_applied_ms > 0);
  CHECK(harness.link.goal.yaw_deg == 9.0F);

  // A cancelled generation never completes its pending write either.
  harness.core.cancel_all();
  harness.io();
  const auto applied_before = snapshot.command_applied_ms;
  CHECK(harness.core.io_state() == ServoIoState::Stopping);
  harness.run_io(2);
  snapshot = harness.core.feedback();
  CHECK(snapshot.command_applied_ms == 0 ||
        snapshot.command_applied_ms == applied_before);
  CHECK(!harness.link.torque);
  return 0;
}

int test_snapshot_timestamps_and_generations_are_monotonic() {
  Harness harness(true);
  harness.run_io(2);
  std::uint64_t last_sampled = 0;
  std::uint64_t last_applied = 0;
  const auto first = harness.submit(12.0F, 52.0F, 100000);
  harness.tick();
  harness.run_io(3);
  auto snapshot = harness.core.feedback();
  CHECK(snapshot.sampled_at_ms >= last_sampled);
  last_sampled = snapshot.sampled_at_ms;
  CHECK(snapshot.command_applied_ms >= last_applied);
  last_applied = snapshot.command_applied_ms;
  CHECK(snapshot.command_generation == first);
  for (int index = 0; index < 5; ++index) {
    harness.now += 30;
    harness.io();
    snapshot = harness.core.feedback();
    CHECK(snapshot.sampled_at_ms >= last_sampled);
    last_sampled = snapshot.sampled_at_ms;
  }
  const auto second = harness.submit(9.0F, 51.0F, 100000);
  harness.tick();
  harness.run_io(3);
  snapshot = harness.core.feedback();
  CHECK(snapshot.command_generation == second);
  CHECK(snapshot.command_applied_ms > last_applied);
  CHECK(snapshot.sampled_at_ms >= last_sampled);
  return 0;
}

int test_hard_limit_never_reaches_servo_io() {
  Harness harness(true);
  harness.run_io(2);
  harness.submit(91.0F, 50.0F, 100000);
  const auto gate = harness.tick();
  CHECK(gate.output.kind == OutputKind::Stop);
  CHECK(harness.safety.latched());
  harness.run_io(4);
  CHECK(harness.link.goal_writes == 0);
  CHECK(harness.link.torque_writes == 0);
  // The latched hard limit cuts the motor rail, so the pair powers down.
  CHECK(harness.core.io_state() == ServoIoState::PowerOff);
  CHECK(!harness.link.vm);
  return 0;
}

int test_safety_period_independent_of_slow_uart() {
  Harness harness(true);
  harness.link.op_delay_ms = 500;  // every owner transaction blocks "500 ms"
  harness.submit(12.0F, 52.0F, 100000);
  int motion_outputs = 0;
  for (int index = 0; index < 20; ++index) {
    harness.now += 20;  // the safety task keeps its own absolute cadence
    const auto gate = harness.tick();
    if (gate.output.kind == OutputKind::Motion) ++motion_outputs;
  }
  CHECK(motion_outputs == 20);
  CHECK(harness.core.take_command(harness.now).generation == 1);
  return 0;
}

int test_post_wake_grace_defers_failure_latch_only() {
  Harness harness(false);  // powered off: exercises the light wake
  harness.submit(12.0F, 52.0F, 100000);
  harness.tick();
  harness.run_io(2);  // PowerOff -> PoweringOn, VM on requested
  for (int index = 0; index < 20 && harness.core.io_state() == ServoIoState::PoweringOn; ++index) {
    harness.now += 100;
    harness.io();
  }
  CHECK(harness.core.io_state() == ServoIoState::ReadyTorqueOff);
  harness.run_io(3);  // torque + write -> Tracking (applied)
  const auto applied_at = harness.core.feedback().command_applied_ms;
  CHECK(applied_at > 0);
  // Flaky just-woken servo: every read fails inside the grace window.
  harness.link.fail_reads_remaining = -1;
  while (harness.now < applied_at + 600) {
    harness.now += 30;
    harness.io();
    harness.tick();
    CHECK(!harness.fault);  // failure-count latch deferred by the grace
  }
  // The independent 700 ms no-progress monitor still catches a dead servo.
  bool latched = false;
  while (harness.now < applied_at + 1600 && !latched) {
    harness.now += 30;
    harness.io();
    latched = harness.tick().stall && harness.safety.latched();
  }
  CHECK(latched);
  return 0;
}

}  // namespace

int main() {
  int failures = 0;
  failures += test_ack_path_not_blocked_by_slow_servo_io();
  failures += test_safety_tick_never_calls_blocking_servo_api();
  failures += test_stall_timer_does_not_start_before_command_applied();
  failures += test_command_applied_only_after_write_success();
  failures += test_transient_feedback_failures_do_not_latch();
  failures += test_persistent_feedback_stale_latches_after_threshold();
  failures += test_no_progress_latches_after_window();
  failures += test_progress_refreshes_window();
  failures += test_expired_command_does_not_delay_next_execution();
  failures += test_pause_cancels_generation_and_cuts_power();
  failures += test_preflight_wakes_and_samples_without_motion();
  failures += test_link_loss_cancels_generation();
  failures += test_emergency_cuts_vm_before_any_uart_work();
  failures += test_clear_fault_does_not_resume_old_command();
  failures += test_late_old_generation_results_never_overwrite_new_state();
  failures += test_snapshot_timestamps_and_generations_are_monotonic();
  failures += test_hard_limit_never_reaches_servo_io();
  failures += test_safety_period_independent_of_slow_uart();
  failures += test_post_wake_grace_defers_failure_latch_only();
  if (failures == 0) {
    std::printf("servo_io tests passed\n");
    return 0;
  }
  std::printf("servio tests failures=%d\n", failures);
  return 1;
}
