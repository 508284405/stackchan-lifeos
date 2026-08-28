#include "lifeos/phase1/controller.hpp"

#include <algorithm>
#include <cstdio>
#include <cstring>
#include <utility>

namespace lifeos::phase1 {
namespace {
bool available(const hal::CapabilityStatus& status) {
  return status.state == hal::CapabilityState::Available;
}

const char* mode_name(behavior::LifeMode mode) {
  switch (mode) {
    case behavior::LifeMode::BOOT: return "BOOT";
    case behavior::LifeMode::IDLE: return "IDLE";
    case behavior::LifeMode::ATTENTIVE: return "ATTENTIVE";
    case behavior::LifeMode::ACTING: return "ACTING";
    case behavior::LifeMode::SLEEPING: return "SLEEPING";
    case behavior::LifeMode::PAUSED: return "PAUSED";
    case behavior::LifeMode::DEGRADED: return "DEGRADED";
    case behavior::LifeMode::FAULT: return "FAULT";
    case behavior::LifeMode::EMERGENCY_STOP: return "EMERGENCY_STOP";
  }
  return "UNKNOWN";
}
}  // namespace

Controller::Controller(hal::Touch& touch, hal::Proximity& proximity,
                       hal::PerceptionSource& perception, hal::Servo& servo,
                       hal::Display& display, hal::Telemetry& telemetry,
                       behavior::Clock clock, behavior::Random random)
    : touch_(touch),
      proximity_(proximity),
      perception_(perception),
      servo_(servo),
      display_(display),
      telemetry_(telemetry),
      clock_(std::move(clock)),
      behavior_(clock_, std::move(random)) {}

bool Controller::boot() {
  const auto now = clock_ ? clock_() : 0;
  servo_.stop(true);
  if (!available(servo_.status()) || !available(touch_.status())) {
    behavior_.dispatch(behavior::EventKind::FAULT);
    render_state(now);
    emit_state(now);
    return false;
  }
  if (!available(proximity_.status()) || !available(perception_.status()) ||
      !available(display_.status()) || !available(telemetry_.status())) {
    behavior_.dispatch(behavior::EventKind::CAPABILITY_DEGRADED);
  } else {
    behavior_.dispatch(behavior::EventKind::BOOT_COMPLETE);
  }
  safety_.heartbeat(now);
  render_state(now);
  emit_state(now);
  return true;
}

bool Controller::graph_tick() {
  const auto now = clock_ ? clock_() : 0;
  bool healthy = true;

  hal::TouchSample touch;
  if (touch_.read(touch) && touch.touched) {
    if (touch.duration_ms >= 1500 &&
        (behavior_.state().fault_latched || behavior_.state().emergency_stop_latched)) {
      safety_.clear_latched(true);
      behavior_.dispatch(behavior::EventKind::CLEAR_EMERGENCY_STOP);
      behavior_.dispatch(behavior::EventKind::CLEAR_FAULT);
    } else {
      behavior_.dispatch(behavior::EventKind::TOUCH);
    }
  } else if (!available(touch_.status())) {
    healthy = false;
    behavior_.dispatch(behavior::EventKind::FAULT);
  }

  hal::ProximitySample proximity;
  if (proximity_.read(proximity) && proximity.present != last_present_) {
    last_present_ = proximity.present;
    behavior_.dispatch(proximity.present ? behavior::EventKind::PERSON_PRESENT
                                         : behavior::EventKind::PERSON_LEFT);
  }

  hal::PersonObservation person;
  const bool has_person = perception_.read(person) && person.present;
  behavior_.dispatch(behavior::EventKind::TICK);
  const auto candidates = behavior_.candidates();
  const auto* selected = behavior::choose_highest(candidates, now);
  if (selected != nullptr) {
    set_motion_from(selected->target, has_person ? person : hal::PersonObservation{}, now);
  } else {
    pending_motion_ = {};
  }
  if (healthy) safety_.heartbeat(now);
  render_state(now);
  emit_state(now);
  return healthy;
}

protocol::GatewayResult Controller::ingest_line(std::string_view line) {
  const auto result = gateway_.ingest(line, clock_ ? clock_() : 0);
  if (!result.accepted || result.duplicate || result.envelope.kind != protocol::Kind::Command) {
    return result;
  }
  const auto type = result.envelope.type.view();
  const auto payload = result.envelope.payload.view();
  if (type == "command.emergency_stop") {
    control(ControlAction::EmergencyStop);
  } else if (type == "command.control") {
    if (payload.find("\"action\":\"pause\"") != std::string_view::npos) {
      control(ControlAction::Pause);
    } else if (payload.find("\"action\":\"resume\"") != std::string_view::npos) {
      control(ControlAction::Resume);
    } else if (payload.find("\"action\":\"home\"") != std::string_view::npos) {
      control(ControlAction::Home);
    }
  }
  return result;
}

runtime::SafetyDecision Controller::safety_tick(bool emergency_stop, bool stall) {
  const auto now = clock_ ? clock_() : 0;
  const auto mode = behavior_.state().mode;
  if (mode == behavior::LifeMode::PAUSED || mode == behavior::LifeMode::FAULT ||
      mode == behavior::LifeMode::EMERGENCY_STOP) {
    pending_motion_ = {};
    emergency_stop = emergency_stop || mode == behavior::LifeMode::EMERGENCY_STOP;
    stall = stall || mode == behavior::LifeMode::FAULT;
  }
  hal::ServoPosition feedback;
  const bool feedback_ok = servo_.read_position(feedback);
  runtime::SafetySample sample;
  sample.now_ms = now;
  sample.measured_yaw_deg = feedback.yaw_deg;
  sample.measured_pitch_deg = feedback.pitch_deg;
  sample.emergency_stop = emergency_stop;
  sample.stall = stall || !feedback_ok;
  const auto decision = safety_.tick(sample, pending_motion_);
  if (decision.accepted) {
    servo_.set_position({decision.yaw_deg, decision.pitch_deg});
  } else {
    servo_.stop(true);
  }
  if (runtime::has_fault(decision.faults, runtime::SafetyFault::EmergencyStop)) {
    behavior_.dispatch(behavior::EventKind::EMERGENCY_STOP);
  } else if (runtime::has_fault(decision.faults, runtime::SafetyFault::Stall) ||
             runtime::has_fault(decision.faults, runtime::SafetyFault::HardLimit) ||
             runtime::has_fault(decision.faults, runtime::SafetyFault::Watchdog)) {
    behavior_.dispatch(behavior::EventKind::FAULT);
  }
  return decision;
}

bool Controller::control(ControlAction action, bool local_confirmation) {
  switch (action) {
    case ControlAction::Pause:
      behavior_.dispatch(behavior::EventKind::PAUSE);
      pending_motion_ = {};
      servo_.stop(true);
      return true;
    case ControlAction::Resume:
      behavior_.dispatch(behavior::EventKind::RESUME);
      return true;
    case ControlAction::Home: {
      const auto mode = behavior_.state().mode;
      if (mode == behavior::LifeMode::PAUSED || mode == behavior::LifeMode::FAULT ||
          mode == behavior::LifeMode::EMERGENCY_STOP) return false;
      const auto now = clock_ ? clock_() : 0;
      pending_motion_ = {true, 0.0F, 45.0F, now + 500};
      return true;
    }
    case ControlAction::ClearFault:
      if (!local_confirmation || !safety_.clear_latched(true)) return false;
      behavior_.dispatch(behavior::EventKind::CLEAR_EMERGENCY_STOP);
      behavior_.dispatch(behavior::EventKind::CLEAR_FAULT);
      return true;
    case ControlAction::EmergencyStop:
      behavior_.dispatch(behavior::EventKind::EMERGENCY_STOP);
      pending_motion_ = {};
      servo_.stop(true);
      return true;
  }
  return false;
}

void Controller::set_motion_from(const behavior::SemanticTarget& target,
                                 const hal::PersonObservation& person,
                                 std::uint64_t now_ms) {
  const auto mode = behavior_.state().mode;
  if (mode == behavior::LifeMode::PAUSED ||
      mode == behavior::LifeMode::FAULT ||
      mode == behavior::LifeMode::EMERGENCY_STOP) {
    pending_motion_ = {};
    return;
  }
  pending_motion_.valid = true;
  pending_motion_.expires_at_ms = now_ms + std::max<std::uint16_t>(target.duration_ms, 100);
  pending_motion_.yaw_deg = 0.0F;
  pending_motion_.pitch_deg = 45.0F;
  if (target.motion == behavior::Motion::LOOK_AT && person.present) {
    pending_motion_.yaw_deg = std::max(-60.0F, std::min(60.0F, person.x * 60.0F));
    pending_motion_.pitch_deg = std::max(15.0F, std::min(75.0F, 45.0F - person.y * 25.0F));
  } else if (target.motion == behavior::Motion::SLEEP) {
    pending_motion_.pitch_deg = 30.0F;
  }
}

void Controller::render_state(std::uint64_t now_ms) {
  hal::DisplayFrame frame;
  frame.timestamp_ms = now_ms;
  std::snprintf(frame.expression.data(), frame.expression.size(), "%s",
                mode_name(behavior_.state().mode));
  display_.render(frame);
}

void Controller::emit_state(std::uint64_t now_ms) {
  hal::TelemetryRecord record;
  record.timestamp_ms = now_ms;
  std::snprintf(record.type.data(), record.type.size(), "life.state");
  std::snprintf(record.payload.data(), record.payload.size(),
                "{\"mode\":\"%s\"}", mode_name(behavior_.state().mode));
  telemetry_.emit(record);
}

}  // namespace lifeos::phase1
