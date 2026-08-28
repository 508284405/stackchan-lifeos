#include "lifeos/behavior/behavior.hpp"

#include <cassert>

using namespace lifeos::behavior;

int main() {
  LifeState initial;
  auto state = reduce(initial, {EventKind::BOOT_COMPLETE, 10});
  assert(state.mode == LifeMode::IDLE);
  state = reduce(state, {EventKind::PERSON_PRESENT, 20});
  assert(state.mode == LifeMode::ATTENTIVE && state.person_present);
  state = reduce(state, {EventKind::ACTION_STARTED, 30});
  assert(state.mode == LifeMode::ACTING);
  state = reduce(state, {EventKind::ACTION_FINISHED, 40});
  assert(state.mode == LifeMode::ATTENTIVE);
  state = reduce(state, {EventKind::PERSON_LEFT, 50});
  state = reduce(state, {EventKind::TICK, 300050});
  assert(state.mode == LifeMode::SLEEPING);
  state = reduce(state, {EventKind::FAULT, 300060});
  assert(state.mode == LifeMode::FAULT && state.fault_latched);
  state = reduce(state, {EventKind::TOUCH, 300070});
  assert(state.mode == LifeMode::FAULT); // fault is latched
  state = reduce(state, {EventKind::CLEAR_FAULT, 300080});
  assert(state.mode == LifeMode::IDLE && !state.fault_latched);

  // Safety and operator modes dominate ordinary behavior and require explicit local clears.
  struct TransitionCase { EventKind event; LifeMode expected; };
  const TransitionCase transitions[] = {
      {EventKind::PAUSE, LifeMode::PAUSED},
      {EventKind::RESUME, LifeMode::IDLE},
      {EventKind::CAPABILITY_DEGRADED, LifeMode::DEGRADED},
      {EventKind::CAPABILITY_RESTORED, LifeMode::IDLE},
      {EventKind::EMERGENCY_STOP, LifeMode::EMERGENCY_STOP},
  };
  for (const auto& test : transitions) {
    state = reduce(state, {test.event, state.now_ms + 1});
    assert(state.mode == test.expected);
  }
  state = reduce(state, {EventKind::TOUCH, state.now_ms + 1});
  assert(state.mode == LifeMode::EMERGENCY_STOP);
  state = reduce(state, {EventKind::CLEAR_EMERGENCY_STOP, state.now_ms + 1});
  assert(state.mode == LifeMode::IDLE && !state.emergency_stop_latched);

  state = reduce(state, {EventKind::PAUSE, state.now_ms + 1});
  state = reduce(state, {EventKind::CAPABILITY_DEGRADED, state.now_ms + 1});
  assert(state.mode == LifeMode::PAUSED); // PAUSED outranks DEGRADED.
  state = reduce(state, {EventKind::RESUME, state.now_ms + 1});
  assert(state.mode == LifeMode::DEGRADED);
  state = reduce(state, {EventKind::CAPABILITY_RESTORED, state.now_ms + 1});
  assert(state.mode == LifeMode::IDLE);

  CandidateList<2> candidates;
  candidates.add({{Motion::IDLE_SWAY, 10, 100, 0}, 10, 200});
  candidates.add({{Motion::HAPPY, 50, 100, 0}, 40, 200});
  assert(choose_highest(candidates, 100)->target.motion == Motion::HAPPY);
  assert(choose_highest(candidates, 200) == nullptr);
  assert(blink_timeline(0).size == 2);
  assert(touch_happy_timeline(0).duration_ms == 480);
  assert(look_at_timeline(0, 7).steps[0].target.target_id == 7);
  assert(idle_timeline(0, 0).steps[0].target.duration_ms == 700);

  std::uint64_t now = 100;
  BehaviorEngine engine([&now] { return now; }, [] { return 0U; });
  engine.dispatch(EventKind::BOOT_COMPLETE);
  assert(engine.state().mode == LifeMode::IDLE);
  assert(engine.candidates().size == 2); // deterministic blink branch
  engine.dispatch(EventKind::EMERGENCY_STOP);
  assert(engine.candidates().values[0].priority == 100);
  engine.dispatch(EventKind::CLEAR_EMERGENCY_STOP);
  engine.dispatch(EventKind::TOUCH);
  assert(engine.candidates().values[0].target.motion == Motion::HAPPY);
  return 0;
}
