#include "lifeos/behavior/behavior.hpp"

#include <utility>

namespace lifeos::behavior {

LifeState reduce(LifeState state, const LifeEvent& event, std::uint64_t sleep_after_ms) {
  const auto at = event.at_ms < state.now_ms ? state.now_ms : event.at_ms;
  state.now_ms = at;
  const auto transition = [&state, at](LifeMode mode) {
    state.mode = mode;
    state.last_transition_ms = at;
  };
  if (event.kind == EventKind::EMERGENCY_STOP) {
    state.emergency_stop_latched = true;
    transition(LifeMode::EMERGENCY_STOP);
    return state;
  }
  if (state.emergency_stop_latched) {
    if (event.kind == EventKind::CLEAR_EMERGENCY_STOP) {
      state.emergency_stop_latched = false;
      transition(state.fault_latched ? LifeMode::FAULT : LifeMode::IDLE);
    }
    return state;
  }
  if (event.kind == EventKind::FAULT) {
    state.fault_latched = true;
    transition(LifeMode::FAULT);
    return state;
  }
  if (state.fault_latched) {
    if (event.kind == EventKind::CLEAR_FAULT) {
      state.fault_latched = false;
      transition(LifeMode::IDLE);
    }
    return state;
  }
  if (event.kind == EventKind::PAUSE) {
    state.paused = true;
    transition(LifeMode::PAUSED);
    return state;
  }
  if (event.kind == EventKind::CAPABILITY_DEGRADED) {
    state.capability_degraded = true;
    transition(state.paused ? LifeMode::PAUSED : LifeMode::DEGRADED);
    return state;
  }
  switch (event.kind) {
    case EventKind::BOOT_COMPLETE:
      if (state.mode == LifeMode::BOOT) transition(LifeMode::IDLE);
      break;
    case EventKind::TOUCH:
      ++state.interaction_count;
      state.last_interaction_ms = at;
      state.touch_recent = true;
      transition(LifeMode::ATTENTIVE);
      break;
    case EventKind::PERSON_PRESENT:
      state.person_present = true;
      transition(LifeMode::ATTENTIVE);
      break;
    case EventKind::PERSON_LEFT:
      state.person_present = false;
      if (state.mode == LifeMode::ATTENTIVE) transition(LifeMode::IDLE);
      break;
    case EventKind::ACTION_STARTED:
      transition(LifeMode::ACTING);
      break;
    case EventKind::ACTION_FINISHED:
      transition(state.person_present ? LifeMode::ATTENTIVE : LifeMode::IDLE);
      break;
    case EventKind::SLEEP:
      transition(LifeMode::SLEEPING);
      break;
    case EventKind::WAKE:
      transition(LifeMode::ATTENTIVE);
      state.last_interaction_ms = at;
      break;
    case EventKind::RESUME:
      state.paused = false;
      transition(state.capability_degraded ? LifeMode::DEGRADED : LifeMode::IDLE);
      break;
    case EventKind::CAPABILITY_RESTORED:
      state.capability_degraded = false;
      transition(state.paused ? LifeMode::PAUSED : LifeMode::IDLE);
      break;
    case EventKind::TICK:
      state.touch_recent = false;
      if (state.mode == LifeMode::IDLE && !state.person_present &&
          at - state.last_interaction_ms >= sleep_after_ms) transition(LifeMode::SLEEPING);
      break;
    case EventKind::CLEAR_FAULT:
    case EventKind::FAULT:
    case EventKind::PAUSE:
    case EventKind::CAPABILITY_DEGRADED:
    case EventKind::EMERGENCY_STOP:
    case EventKind::CLEAR_EMERGENCY_STOP:
      break;
  }
  return state;
}

Timeline<4> blink_timeline(std::uint64_t start_ms) {
  Timeline<4> result;
  result.start_ms = start_ms;
  result.add({Motion::BLINK, 100, 80, 0}, 0);
  result.add({Motion::STILL, 0, 80, 0}, 80);
  return result;
}

Timeline<4> touch_happy_timeline(std::uint64_t start_ms) {
  Timeline<4> result;
  result.start_ms = start_ms;
  result.add({Motion::HAPPY, 70, 320, 0}, 0);
  result.add({Motion::BLINK, 100, 160, 0}, 320);
  return result;
}

Timeline<4> look_at_timeline(std::uint64_t start_ms, std::uint32_t target_id) {
  Timeline<4> result;
  result.start_ms = start_ms;
  result.add({Motion::LOOK_AT, 70, 450, target_id}, 0);
  return result;
}

Timeline<4> idle_timeline(std::uint64_t start_ms, std::uint32_t random_value) {
  Timeline<4> result;
  result.start_ms = start_ms;
  const auto sway_duration = static_cast<std::uint16_t>(700 + (random_value % 500));
  result.add({Motion::IDLE_SWAY, 25, sway_duration, 0}, 0);
  return result;
}

Timeline<4> sleep_timeline(std::uint64_t start_ms) {
  Timeline<4> result;
  result.start_ms = start_ms;
  result.add({Motion::SLEEP, 100, 600, 0}, 0);
  return result;
}

BehaviorEngine::BehaviorEngine(Clock clock, Random random)
    : clock_(std::move(clock)), random_(std::move(random)) {}

void BehaviorEngine::dispatch(EventKind kind) {
  const auto now = clock_ ? clock_() : state_.now_ms;
  state_ = reduce(state_, LifeEvent{kind, now});
}

CandidateList<8> BehaviorEngine::candidates() const {
  CandidateList<8> result;
  const auto now = state_.now_ms;
  if (state_.mode == LifeMode::FAULT) {
    result.add({{Motion::STILL, 0, 0, 0}, 90, now + 100});
    return result;
  }
  if (state_.mode == LifeMode::EMERGENCY_STOP) {
    result.add({{Motion::STILL, 0, 0, 0}, 100, now + 100});
    return result;
  }
  if (state_.mode == LifeMode::PAUSED) {
    result.add({{Motion::STILL, 0, 0, 0}, 80, now + 100});
    return result;
  }
  if (state_.mode == LifeMode::DEGRADED) {
    result.add({{Motion::STILL, 0, 0, 0}, 70, now + 100});
    return result;
  }
  if (state_.mode == LifeMode::SLEEPING) {
    result.add({{Motion::SLEEP, 100, 600, 0}, 10, now + 600});
    return result;
  }
  if (state_.mode == LifeMode::ATTENTIVE) {
    if (state_.touch_recent) {
      result.add({{Motion::HAPPY, 70, 320, 0}, 80, now + 320});
    } else {
      result.add({{Motion::LOOK_AT, 60, 400, 0}, 40, now + 400});
    }
  } else if (state_.mode == LifeMode::IDLE) {
    const auto random_value = random_ ? random_() : 0U;
    result.add({{Motion::IDLE_SWAY, 20, 900, 0}, 10, now + 900});
    if (random_value % 4 == 0) result.add({{Motion::BLINK, 100, 80, 0}, 20, now + 80});
  }
  return result;
}

} // namespace lifeos::behavior
