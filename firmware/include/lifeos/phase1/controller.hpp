#pragma once

#include <cstdint>
#include <string_view>

#include "lifeos/behavior/behavior.hpp"
#include "lifeos/hal/hal.hpp"
#include "lifeos/runtime/runtime.hpp"
#include "lifeos/protocol/protocol.hpp"

namespace lifeos::phase1 {

enum class ControlAction : std::uint8_t {
  Pause,
  Resume,
  Preflight,
  Home,
  ClearFault,
  EmergencyStop,
};

/** Host-testable Phase 1 coordinator.

    graph_tick() is the low-rate business path. safety_tick() is intentionally
    separate and must be scheduled by a higher-priority FreeRTOS task on target.
 */
class Controller final {
 public:
  Controller(hal::Touch& touch, hal::Proximity& proximity,
             hal::PerceptionSource& perception, hal::Servo& servo,
             hal::Display& display, hal::Telemetry& telemetry,
             behavior::Clock clock, behavior::Random random = {});

  bool boot();
  bool graph_tick();
  protocol::GatewayResult ingest_line(std::string_view line);
  void reset_link() { gateway_.reset_session(); }
  runtime::SafetyDecision safety_tick(bool emergency_stop = false,
                                      bool stall = false);
  bool control(ControlAction action, bool local_confirmation = false);

  behavior::LifeState state() const { return behavior_.state(); }
  const runtime::MotionRequest& pending_motion() const { return pending_motion_; }
  runtime::SafetyFault safety_faults() const { return safety_.latched_faults(); }

 private:
  void set_motion_from(const behavior::SemanticTarget& target,
                       const hal::PersonObservation& person,
                       std::uint64_t now_ms);
  void render_state(std::uint64_t now_ms);
  void emit_state(std::uint64_t now_ms);

  hal::Touch& touch_;
  hal::Proximity& proximity_;
  hal::PerceptionSource& perception_;
  hal::Servo& servo_;
  hal::Display& display_;
  hal::Telemetry& telemetry_;
  behavior::Clock clock_;
  behavior::BehaviorEngine behavior_;
  runtime::FastSafetyLoop safety_;
  protocol::Gateway gateway_;
  runtime::MotionRequest pending_motion_{};
  bool last_present_{false};
  bool touch_clear_hold_{false};
  hal::ServoPosition last_feedback_{};
  std::uint64_t last_feedback_progress_ms_{0};
  bool have_feedback_{false};
  bool stall_motion_active_{false};
  bool stall_monitoring_started_{false};
};

}  // namespace lifeos::phase1
