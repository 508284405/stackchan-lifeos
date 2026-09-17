#include "lifeos/runtime/manual_control.hpp"

#include <cassert>
#include <iostream>

using lifeos::hal::ServoPosition;
using lifeos::protocol::BoundedText;
using lifeos::protocol::ManualAction;
using lifeos::protocol::ManualControlPayload;
using lifeos::runtime::ManualControlResultCode;
using lifeos::runtime::ManualControlState;

namespace {

template <std::size_t N>
void set_text(BoundedText<N>& output, const char* value) {
  output.size = 0;
  while (output.size < N && value[output.size] != '\0') {
    output.data[output.size] = value[output.size];
    ++output.size;
  }
  output.data[output.size] = '\0';
}

ManualControlPayload input(const char* lease, std::uint64_t sequence,
                           float yaw, float pitch, std::uint64_t ttl = 400,
                           std::uint64_t capture_ts_ms = 1000) {
  ManualControlPayload value;
  set_text(value.lease_id, lease);
  value.input_seq = sequence;
  value.action = ManualAction::Input;
  value.yaw = yaw;
  value.pitch = pitch;
  value.ttl_ms = ttl;
  set_text(value.video_frame_id, "frame-1");
  value.video_capture_ts_ms = capture_ts_ms;
  value.has_direction = true;
  value.has_video_proof = true;
  return value;
}

}  // namespace

int main() {
  ManualControlState state;
  const ServoPosition home{0.0F, 45.0F};

  const auto first = state.apply(input("lease-1", 1, 1.0F, -1.0F), home,
                                 true, false, false, false, false, 1000);
  assert(first.code == ManualControlResultCode::Accepted);
  assert(first.accepted && first.yaw_deg == 2.0F && first.pitch_deg == 43.0F);
  assert(first.expires_at_ms == 1400);

  const auto too_fast = state.apply(input("lease-1", 2, 1.0F, 0.0F), home,
                                    true, false, false, false, false, 1050);
  assert(too_fast.code == ManualControlResultCode::Accepted);
  assert(too_fast.accepted && too_fast.coalesced);
  assert(too_fast.yaw_deg == 2.0F && too_fast.pitch_deg == 43.0F);

  const auto second = state.apply(input("lease-1", 3, 1.0F, 0.0F), home,
                                  true, false, false, false, false, 1100);
  assert(second.code == ManualControlResultCode::Accepted);
  // Feedback has not moved yet, but a held direction must keep advancing the
  // device-owned target instead of repeatedly commanding only +2 degrees.
  assert(second.yaw_deg == 4.0F && second.pitch_deg == 43.0F);

  const auto mismatch = state.apply(input("lease-2", 4, 0.0F, 0.0F), home,
                                    true, false, false, false, false, 1200);
  assert(mismatch.code == ManualControlResultCode::LeaseMismatch);

  const auto blocked = state.apply(input("lease-1", 4, 0.0F, 0.0F), home,
                                   true, true, false, false, false, 1200);
  assert(blocked.code == ManualControlResultCode::SafetyBlocked);

  ManualControlState clamped_state;
  const auto clamped = clamped_state.apply(input("lease-3", 1, 1.0F, 1.0F),
                                           ServoPosition{74.0F, 84.0F}, true, false,
                                           false, false, false, 1200);
  assert(clamped.code == ManualControlResultCode::Accepted);
  assert(clamped.yaw_deg == 75.0F && clamped.pitch_deg == 85.0F);

  assert(state.expire(1600, false));
  const auto late = state.apply(input("lease-1", 5, 0.0F, 0.0F), home, true,
                                false, false, false, false, 1601);
  assert(late.code == ManualControlResultCode::SequenceRejected);

  const auto start_again = state.apply(input("lease-2", 1, 0.0F, 0.0F, 400, 1700), home,
                                        true, false, false, false, false, 1700);
  assert(start_again.accepted);
  ManualControlPayload release;
  set_text(release.lease_id, "lease-2");
  release.input_seq = 2;
  release.action = ManualAction::Release;
  release.ttl_ms = 400;
  const auto released = state.apply(release, home, true, false, false, false,
                                    false, 1800);
  assert(released.accepted && released.release && !state.active());

  ManualControlState stale_video_state;
  const auto stale_video = stale_video_state.apply(
      input("lease-video", 1, 1.0F, 0.0F, 500, 1000), home, true,
      false, false, false, false, 1501);
  assert(stale_video.code == ManualControlResultCode::Expired);

  ManualControlState video_deadline_state;
  const auto video_bounded = video_deadline_state.apply(
      input("lease-video", 1, 1.0F, 0.0F, 500, 1200), home, true,
      false, false, false, false, 1400);
  assert(video_bounded.accepted && video_bounded.expires_at_ms == 1700);

  std::cout << "manual control runtime tests passed\n";
}
