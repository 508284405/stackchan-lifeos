#pragma once

#include <array>
#include <cstdint>
#include <functional>

namespace lifeos::behavior {

enum class LifeMode : std::uint8_t {
  BOOT, IDLE, ATTENTIVE, ACTING, SLEEPING, PAUSED, DEGRADED, FAULT, EMERGENCY_STOP
};
enum class EventKind : std::uint8_t {
  TICK, BOOT_COMPLETE, TOUCH, PERSON_PRESENT, PERSON_LEFT,
  ACTION_STARTED, ACTION_FINISHED, WAKE, SLEEP, FAULT, CLEAR_FAULT,
  PAUSE, RESUME, CAPABILITY_DEGRADED, CAPABILITY_RESTORED,
  EMERGENCY_STOP, CLEAR_EMERGENCY_STOP
};

struct LifeEvent {
  EventKind kind{EventKind::TICK};
  std::uint64_t at_ms{0};
};

struct LifeState {
  LifeMode mode{LifeMode::BOOT};
  std::uint64_t now_ms{0};
  std::uint64_t last_interaction_ms{0};
  std::uint64_t last_transition_ms{0};
  bool person_present{false};
  bool fault_latched{false};
  bool emergency_stop_latched{false};
  bool capability_degraded{false};
  bool paused{false};
  bool touch_recent{false};
  std::uint32_t interaction_count{0};
};

// Pure, deterministic state transition. The event timestamp is the only time input.
LifeState reduce(LifeState state, const LifeEvent& event,
                 std::uint64_t sleep_after_ms = 300000);

enum class Motion : std::uint8_t {
  STILL, BLINK, HAPPY, LOOK_AT, IDLE_SWAY, SLEEP, WAKE
};

struct SemanticTarget {
  Motion motion{Motion::STILL};
  std::uint8_t intensity{0}; // 0..100; semantic intensity, not a hardware value.
  std::uint16_t duration_ms{0};
  std::uint32_t target_id{0}; // opaque perceptual target identifier for LOOK_AT.
};

struct BehaviorCandidate {
  SemanticTarget target{};
  std::uint8_t priority{0};
  std::uint64_t expires_at_ms{0};
};

template <std::size_t Capacity>
struct CandidateList {
  std::array<BehaviorCandidate, Capacity> values{};
  std::size_t size{0};

  bool add(const BehaviorCandidate& candidate) {
    if (size >= Capacity) return false;
    values[size++] = candidate;
    return true;
  }
};

template <std::size_t Capacity>
const BehaviorCandidate* choose_highest(const CandidateList<Capacity>& candidates,
                                        std::uint64_t now_ms) {
  const BehaviorCandidate* best = nullptr;
  for (std::size_t i = 0; i < candidates.size; ++i) {
    const auto& candidate = candidates.values[i];
    if (candidate.expires_at_ms <= now_ms) continue;
    if (best == nullptr || candidate.priority > best->priority) best = &candidate;
  }
  return best;
}

struct TimelineStep {
  SemanticTarget target{};
  std::uint32_t offset_ms{0};
};

template <std::size_t Capacity>
struct Timeline {
  std::array<TimelineStep, Capacity> steps{};
  std::size_t size{0};
  std::uint64_t start_ms{0};
  std::uint32_t duration_ms{0};

  bool add(SemanticTarget target, std::uint32_t offset_ms) {
    if (size >= Capacity) return false;
    steps[size++] = TimelineStep{target, offset_ms};
    const auto end = offset_ms + target.duration_ms;
    if (end > duration_ms) duration_ms = end;
    return true;
  }
};

Timeline<4> blink_timeline(std::uint64_t start_ms);
Timeline<4> touch_happy_timeline(std::uint64_t start_ms);
Timeline<4> look_at_timeline(std::uint64_t start_ms, std::uint32_t target_id);
Timeline<4> idle_timeline(std::uint64_t start_ms, std::uint32_t random_value);
Timeline<4> sleep_timeline(std::uint64_t start_ms);

using Clock = std::function<std::uint64_t()>;
using Random = std::function<std::uint32_t()>;

class BehaviorEngine final {
 public:
  explicit BehaviorEngine(Clock clock, Random random = {});
  LifeState state() const { return state_; }
  void dispatch(EventKind kind);
  CandidateList<8> candidates() const;

 private:
  Clock clock_;
  Random random_;
  LifeState state_{};
};

} // namespace lifeos::behavior
