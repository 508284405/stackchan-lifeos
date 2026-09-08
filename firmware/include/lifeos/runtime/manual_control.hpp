#pragma once

#include <cstdint>

#include "lifeos/hal/hal.hpp"
#include "lifeos/protocol/protocol.hpp"
#include "lifeos/runtime/runtime.hpp"

namespace lifeos::runtime {

/** Result of the device-side manual-control lease/TTL state machine. */
enum class ManualControlResultCode : std::uint8_t {
  Accepted,
  MotionUnavailable,
  SafetyBlocked,
  LeaseRequired,
  LeaseMismatch,
  SequenceRejected,
  RateLimited,
  Expired,
  Invalid,
};

struct ManualControlResult {
  ManualControlResultCode code{ManualControlResultCode::Invalid};
  const char* detail{"manual_control_rejected"};
  bool accepted{false};
  bool release{false};
  // An input received before the device-side physical update interval is
  // acknowledged and keeps the dead-man lease alive, but does not enqueue an
  // additional ServoIo generation.
  bool coalesced{false};
  float yaw_deg{0.0F};
  float pitch_deg{45.0F};
  std::uint64_t expires_at_ms{0};
};

/**
 * Fixed-capacity device-side lease and dead-man state.
 *
 * The USB task owns command admission and the safety task calls expire(). The
 * target integrates this object under its existing critical section. It only
 * accepts normalized direction values; absolute positions are generated here
 * with fixed small steps and are still passed through FastSafetyLoop and the
 * ServoIoTask before hardware I/O.
 */
class ManualControlState final {
 public:
  static constexpr std::uint64_t kMinInputIntervalMs = 100;
  static constexpr float kStepDegrees = 2.0F;

  ManualControlResult apply(
      const protocol::ManualControlPayload& input,
      const hal::ServoPosition& measured,
      bool motion_ready,
      bool paused,
      bool emergency,
      bool fault,
      bool feedback_frozen,
      std::uint64_t now_ms) noexcept;

  /** Expire the active input on TTL or any safety/link preemption. */
  bool expire(std::uint64_t now_ms, bool safety_blocked) noexcept;

  void reset() noexcept;
  bool active() const noexcept { return lease_id_.size != 0; }

 private:
  static float clamp(float value, float low, float high) noexcept;
  void copy_lease(const protocol::BoundedText<protocol::kMaxIdBytes>& lease) noexcept;

  protocol::BoundedText<protocol::kMaxIdBytes> lease_id_{};
  std::uint64_t last_input_seq_{0};
  std::uint64_t last_input_ms_{0};
  std::uint64_t deadline_ms_{0};
  bool has_input_time_{false};
  // A held direction is an incremental intent, not a repeated request to aim
  // only one step ahead of feedback that has not yet caught up. This target is
  // device-owned, reset with the lease, and clamped before ServoIo sees it.
  hal::ServoPosition target_{};
  bool has_target_{false};
};

}  // namespace lifeos::runtime
