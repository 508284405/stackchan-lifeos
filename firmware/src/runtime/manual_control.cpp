#include "lifeos/runtime/manual_control.hpp"

#include <algorithm>
#include <cmath>
#include <cstring>
#include <limits>

namespace lifeos::runtime {

float ManualControlState::clamp(float value, float low, float high) noexcept {
  return std::max(low, std::min(value, high));
}

void ManualControlState::copy_lease(
    const protocol::BoundedText<protocol::kMaxIdBytes>& lease) noexcept {
  lease_id_ = {};
  lease_id_.size = std::min<std::size_t>(lease.size, protocol::kMaxIdBytes);
  std::memcpy(lease_id_.data.data(), lease.data.data(), lease_id_.size);
  lease_id_.data[lease_id_.size] = '\0';
}

void ManualControlState::reset() noexcept {
  lease_id_ = {};
  last_input_seq_ = 0;
  last_input_ms_ = 0;
  deadline_ms_ = 0;
  has_input_time_ = false;
  target_ = {};
  has_target_ = false;
}

bool ManualControlState::expire(std::uint64_t now_ms,
                                bool safety_blocked) noexcept {
  if (!active()) return false;
  if (!safety_blocked && now_ms < deadline_ms_) return false;
  reset();
  return true;
}

ManualControlResult ManualControlState::apply(
    const protocol::ManualControlPayload& input,
    const hal::ServoPosition& measured,
    bool motion_ready,
    bool paused,
    bool emergency,
    bool fault,
    bool feedback_frozen,
    std::uint64_t now_ms) noexcept {
  ManualControlResult result;
  if (paused || emergency || fault || feedback_frozen) {
    result.code = ManualControlResultCode::SafetyBlocked;
    result.detail = "manual_control_safety_blocked";
    return result;
  }
  if (!motion_ready) {
    result.code = ManualControlResultCode::MotionUnavailable;
    result.detail = "motion_unavailable";
    return result;
  }
  if (input.lease_id.size == 0) {
    result.code = ManualControlResultCode::LeaseRequired;
    result.detail = "manual_lease_required";
    return result;
  }
  if (input.lease_id.size > protocol::kMaxIdBytes) {
    result.code = ManualControlResultCode::Invalid;
    result.detail = "manual_lease_invalid";
    return result;
  }

  // A device-side TTL expiry invalidates the old lease. A fresh lease may
  // start again at input_seq=1; a late frame from the old lease cannot revive
  // its previous target.
  if (active() && now_ms >= deadline_ms_) reset();

  if (!active()) {
    if (input.input_seq != 1) {
      result.code = ManualControlResultCode::SequenceRejected;
      result.detail = "manual_sequence_must_start_at_one";
      return result;
    }
    copy_lease(input.lease_id);
  } else {
    if (input.lease_id.view() != lease_id_.view()) {
      result.code = ManualControlResultCode::LeaseMismatch;
      result.detail = "manual_lease_mismatch";
      return result;
    }
    if (last_input_seq_ == std::numeric_limits<std::uint64_t>::max() ||
        input.input_seq != last_input_seq_ + 1) {
      result.code = ManualControlResultCode::SequenceRejected;
      result.detail = "manual_sequence_not_consecutive";
      return result;
    }
  }

  if (input.action == protocol::ManualAction::Input) {
    if (!input.has_direction || !std::isfinite(input.yaw) ||
        !std::isfinite(input.pitch) || input.yaw < -1.0F || input.yaw > 1.0F ||
        input.pitch < -1.0F || input.pitch > 1.0F) {
      result.code = ManualControlResultCode::Invalid;
      result.detail = "manual_direction_invalid";
      return result;
    }
    if (has_input_time_ &&
        (now_ms < last_input_ms_ ||
         now_ms - last_input_ms_ < kMinInputIntervalMs)) {
      // USB/CDC may deliver an ACK-paced host's next input immediately after
      // the preceding ACK. Preserve its sequence and short dead-man window,
      // but coalesce it instead of treating it as a broken lease. Do not move
      // last_input_ms_: the next input at the physical update interval will
      // advance the target normally.
      last_input_seq_ = input.input_seq;
      deadline_ms_ = now_ms + input.ttl_ms;
      result.yaw_deg = target_.yaw_deg;
      result.pitch_deg = target_.pitch_deg;
      result.expires_at_ms = deadline_ms_;
      result.code = ManualControlResultCode::Accepted;
      result.detail = "manual_control_coalesced";
      result.accepted = true;
      result.coalesced = true;
      return result;
    }
    if (!std::isfinite(measured.yaw_deg) || !std::isfinite(measured.pitch_deg)) {
      result.code = ManualControlResultCode::Invalid;
      result.detail = "manual_feedback_invalid";
      return result;
    }

    // These are internal semantic targets, never values supplied by the
    // browser. A held direction advances the prior device-owned target, so
    // the head keeps moving while feedback is still converging. Re-basing on
    // the same lagging feedback every frame made a long right hold repeatedly
    // command only one tiny step. The hard limits remain enforced again by
    // FastSafetyLoop/HAL.
    if (!has_target_) {
      target_ = measured;
      has_target_ = true;
    }
    target_.yaw_deg = clamp(
        target_.yaw_deg + input.yaw * kStepDegrees,
        FastSafetyLoop::kYawSoftMin, FastSafetyLoop::kYawSoftMax);
    target_.pitch_deg = clamp(
        target_.pitch_deg + input.pitch * kStepDegrees,
        FastSafetyLoop::kPitchMin, FastSafetyLoop::kPitchMax);
    result.yaw_deg = target_.yaw_deg;
    result.pitch_deg = target_.pitch_deg;
    result.expires_at_ms = now_ms + input.ttl_ms;
    last_input_seq_ = input.input_seq;
    last_input_ms_ = now_ms;
    deadline_ms_ = result.expires_at_ms;
    has_input_time_ = true;
    result.code = ManualControlResultCode::Accepted;
    result.detail = "manual_control_input";
    result.accepted = true;
    return result;
  }

  if (input.action == protocol::ManualAction::Release) {
    last_input_seq_ = input.input_seq;
    reset();
    result.code = ManualControlResultCode::Accepted;
    result.detail = "manual_control_release";
    result.accepted = true;
    result.release = true;
    return result;
  }

  result.code = ManualControlResultCode::Invalid;
  result.detail = "manual_action_invalid";
  return result;
}

}  // namespace lifeos::runtime
