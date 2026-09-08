#include "lifeos/runtime/servo_io.hpp"

#include <cmath>
#include <cstdarg>
#include <cstring>
#include <utility>

namespace lifeos::runtime {
namespace {

constexpr std::uint64_t kIdleFeedbackPeriodMs = 100;
constexpr std::uint64_t kTrackingFeedbackPeriodMs = 30;
constexpr std::uint64_t kFeedbackLogMinIntervalMs = 250;
// Idle diagnostics are much sparser than in-motion ones: the console TX ring
// must never fill while no host is reading.
constexpr std::uint64_t kIdleFeedbackLogIntervalMs = 2000;
constexpr std::uint64_t kWakeSettleMs = 300;
// Measured on the reference StackChan: SCS servos answer their first
// register read 0.5-1.0 s after VM_EN rises, so the light wake probes with
// spaced retries instead of declaring a fault after a single settle.
constexpr std::uint64_t kWakeRetryIntervalMs = 200;
constexpr std::uint64_t kWakeConfirmIntervalMs = 100;
constexpr std::uint8_t kWakeMaxAttempts = 6;
// Measured on the reference StackChan: a servo that slept for minutes answers
// its first reads intermittently even after a successful wake probe. Feedback
// failure-count latching is deferred through this window; the independent
// 700 ms no-progress monitor still covers a genuinely dead servo.
constexpr std::uint64_t kPostWakeGraceMs = 1200;

template <typename Writer>
void seqlock_write(std::atomic<std::uint32_t>& seq, Writer&& writer) {
  const auto value = seq.load(std::memory_order_relaxed);
  seq.store(value + 1, std::memory_order_relaxed);
  std::atomic_thread_fence(std::memory_order_release);
  writer();
  std::atomic_thread_fence(std::memory_order_release);
  seq.store(value + 2, std::memory_order_release);
}

template <typename Payload>
bool seqlock_read(const std::atomic<std::uint32_t>& seq, Payload&& reader) {
  for (int attempt = 0; attempt < 8; ++attempt) {
    const auto before = seq.load(std::memory_order_acquire);
    if ((before & 1u) != 0) continue;
    reader();
    std::atomic_thread_fence(std::memory_order_acquire);
    if (seq.load(std::memory_order_relaxed) == before) return true;
  }
  return false;
}

float abs_delta(float a, float b) noexcept { return a > b ? a - b : b - a; }

}  // namespace

const char* servo_io_state_name(ServoIoState state) noexcept {
  switch (state) {
    case ServoIoState::PowerOff: return "power_off";
    case ServoIoState::PoweringOn: return "powering_on";
    case ServoIoState::ReadyTorqueOff: return "ready_torque_off";
    case ServoIoState::ApplyingCommand: return "applying_command";
    case ServoIoState::Tracking: return "tracking";
    case ServoIoState::Stopping: return "stopping";
    case ServoIoState::Faulted: return "faulted";
  }
  return "unknown";
}

const char* servo_io_error_name(ServoIoError error) noexcept {
  switch (error) {
    case ServoIoError::None: return "none";
    case ServoIoError::PowerUnavailable: return "power_unavailable";
    case ServoIoError::SelfTestFailed: return "self_test_failed";
    case ServoIoError::TorqueEnableFailed: return "torque_enable_failed";
    case ServoIoError::PositionWriteFailed: return "position_write_failed";
    case ServoIoError::FeedbackUnavailable: return "feedback_unavailable";
    case ServoIoError::Cancelled: return "cancelled";
    case ServoIoError::EmergencyStop: return "emergency_stop";
  }
  return "unknown";
}

FeedbackHealth evaluate_feedback_health(const ServoFeedbackSnapshot& snapshot,
                                        std::uint64_t now_ms) noexcept {
  FeedbackHealth health;
  health.valid = snapshot.valid;
  // Feedback health is only judged while the owner task is actually sampling;
  // with the rail down the failure counter is a frozen leftover and must not
  // keep the STALL latch alive.
  const bool sampling =
      snapshot.io_state == ServoIoState::ReadyTorqueOff ||
      snapshot.io_state == ServoIoState::ApplyingCommand ||
      snapshot.io_state == ServoIoState::Tracking;
  health.fresh = sampling && snapshot.valid && snapshot.sampled_at_ms <= now_ms &&
                 now_ms - snapshot.sampled_at_ms <= kFeedbackFreshnessMs;
  health.degraded = sampling && snapshot.consecutive_failures > 0;
  health.stale_latch = sampling &&
                       snapshot.consecutive_failures >= kFeedbackMinFailuresForLatch &&
                       !health.fresh && !snapshot.post_wake_grace;
  return health;
}

void StallMonitor::begin(std::uint32_t generation, const hal::ServoPosition& target,
                         std::uint64_t applied_ms) noexcept {
  active_ = true;
  generation_ = generation;
  target_ = target;
  last_progress_ms_ = applied_ms;
  have_feedback_ = false;
  last_feedback_ = {};
}

void StallMonitor::reset() noexcept {
  active_ = false;
  generation_ = 0;
  have_feedback_ = false;
}

void StallMonitor::on_feedback(std::uint32_t generation,
                               const hal::ServoPosition& feedback,
                               std::uint64_t now_ms) noexcept {
  if (!active_ || generation_ != generation) return;
  const bool moved = !have_feedback_ ||
                     abs_delta(feedback.yaw_deg, last_feedback_.yaw_deg) >= kStallProgressDeltaDeg ||
                     abs_delta(feedback.pitch_deg, last_feedback_.pitch_deg) >= kStallProgressDeltaDeg;
  // The first sample only seeds the reference angle so the servo's initial
  // jump cannot be credited; the window itself is anchored at applied_ms.
  if (moved && have_feedback_) last_progress_ms_ = now_ms;
  last_feedback_ = feedback;
  have_feedback_ = true;
}

bool StallMonitor::stalled(std::uint32_t generation,
                           const hal::ServoPosition& feedback,
                           std::uint64_t now_ms) const noexcept {
  if (!monitoring(generation)) return false;
  if (abs_delta(target_.yaw_deg, feedback.yaw_deg) < kStallTargetErrorDeg &&
      abs_delta(target_.pitch_deg, feedback.pitch_deg) < kStallTargetErrorDeg) {
    return false;
  }
  return now_ms >= last_progress_ms_ &&
         now_ms - last_progress_ms_ >= kStallNoProgressWindowMs;
}

std::uint32_t ServoIoCore::submit(float yaw_deg, float pitch_deg,
                                  std::uint64_t expires_at_ms) noexcept {
  if (!std::isfinite(yaw_deg) || !std::isfinite(pitch_deg)) return 0;
  const auto generation =
      generation_counter_.fetch_add(1, std::memory_order_relaxed) + 1;
  ServoCommand command;
  command.generation = generation;
  command.yaw_deg = yaw_deg;
  command.pitch_deg = pitch_deg;
  command.expires_at_ms = expires_at_ms;
  seqlock_write(mailbox_seq_, [&command, this]() { mailbox_ = command; });
  return generation;
}

void ServoIoCore::cancel_up_to(std::uint32_t generation) noexcept {
  auto current = cancel_gen_.load(std::memory_order_relaxed);
  while (current < generation &&
         !cancel_gen_.compare_exchange_weak(current, generation,
                                            std::memory_order_release,
                                            std::memory_order_relaxed)) {
  }
}

void ServoIoCore::mark_emergency() noexcept {
  preflight_requested_.store(false, std::memory_order_release);
  const auto generation =
      generation_counter_.fetch_add(1, std::memory_order_relaxed) + 1;
  cancel_up_to(generation);
  emergency_gen_.fetch_add(1, std::memory_order_release);
}

void ServoIoCore::inject_feedback_freeze(bool enabled) noexcept {
  freeze_inject_.store(enabled, std::memory_order_release);
}

void ServoIoCore::inject_transient_failures(std::uint32_t count) noexcept {
  transient_failures_.store(count, std::memory_order_release);
}

ServoCommand ServoIoCore::take_command(std::uint64_t now_ms) const noexcept {
  ServoCommand command;
  const bool consistent = seqlock_read(
      mailbox_seq_, [&command, this]() { command = mailbox_; });
  if (!consistent) return {};
  const auto cancel = cancel_gen_.load(std::memory_order_acquire);
  if (command.generation == 0 || command.generation <= cancel ||
      command.expires_at_ms <= now_ms) {
    return {};
  }
  return command;
}

ServoFeedbackSnapshot ServoIoCore::feedback() const noexcept {
  ServoFeedbackSnapshot snapshot;
  seqlock_read(snapshot_seq_, [&snapshot, this]() { snapshot = snapshot_; });
  return snapshot;
}

void ServoIoCore::publish_output(const OutputRequest& request) noexcept {
  seqlock_write(output_seq_, [&request, this]() { output_ = request; });
}

void ServoIoCore::logf(const char* format, ...) noexcept {
  if (log_fn_ == nullptr) return;
  char line[kLogLineBytes];
  va_list args;
  va_start(args, format);
  std::vsnprintf(line, sizeof(line), format, args);
  va_end(args);
  log_fn_(log_ctx_, line);
}

void ServoIoCore::publish_state_update(ServoIoError error) noexcept {
  ServoFeedbackSnapshot update;
  seqlock_read(snapshot_seq_, [&update, this]() { update = snapshot_; });
  update.command_generation = active_generation_;
  update.command_applied_ms = command_applied_ms_;
  update.consecutive_failures = consecutive_failures_;
  update.post_wake_grace = post_wake_grace_;
  update.io_state = state_;
  update.last_error = error;
  update.torque_enabled = torque_on_;
  update.vm_enabled = vm_on_;
  seqlock_write(snapshot_seq_, [&update, this]() { snapshot_ = update; });
}

void ServoIoCore::publish_feedback(const hal::ServoPosition& position,
                                   std::uint64_t now_ms) noexcept {
  ServoFeedbackSnapshot update;
  seqlock_read(snapshot_seq_, [&update, this]() { update = snapshot_; });
  update.sampled_at_ms = now_ms;
  update.command_generation = active_generation_;
  update.command_applied_ms = command_applied_ms_;
  update.yaw_deg = position.yaw_deg;
  update.pitch_deg = position.pitch_deg;
  update.valid = true;
  update.torque_enabled = torque_on_;
  update.vm_enabled = vm_on_;
  update.consecutive_failures = 0;
  update.post_wake_grace = post_wake_grace_;
  update.io_state = state_;
  update.last_error = ServoIoError::None;
  seqlock_write(snapshot_seq_, [&update, this]() { snapshot_ = update; });
}

void ServoIoCore::init(bool hardware_ready, bool vm_enabled) noexcept {
  hardware_ready_ = hardware_ready;
  vm_on_ = hardware_ready && vm_enabled;
  torque_on_ = false;
  active_generation_ = 0;
  command_applied_ms_ = 0;
  consecutive_failures_ = 0;
  seen_emergency_gen_ = emergency_gen_.load(std::memory_order_acquire);
  state_ = hardware_ready_ ? (vm_on_ ? ServoIoState::ReadyTorqueOff
                                     : ServoIoState::PowerOff)
                           : ServoIoState::Faulted;
  last_error_ = state_ == ServoIoState::Faulted ? ServoIoError::SelfTestFailed
                                                : ServoIoError::None;
  logf("servo_io power state=%s", servo_io_state_name(state_));
  publish_state_update(last_error_);
}

void ServoIoCore::enter_stopping(bool power_cut, bool faulted,
                                 ServoIoError error) noexcept {
  if (state_ == ServoIoState::Stopping) {
    stopping_power_cut_ = stopping_power_cut_ || power_cut;
    stopping_faulted_ = stopping_faulted_ || faulted;
    return;
  }
  state_ = ServoIoState::Stopping;
  stopping_power_cut_ = power_cut;
  stopping_faulted_ = faulted;
  last_error_ = error;
  publish_state_update(error);
}

void ServoIoCore::finish_stopping() noexcept {
  active_generation_ = 0;
  command_applied_ms_ = 0;
  consecutive_failures_ = 0;
  if (stopping_faulted_) {
    state_ = ServoIoState::Faulted;
    hardware_ready_ = false;
    torque_on_ = false;
    vm_on_ = false;
    logf("servo_io power state=%s error=%s", servo_io_state_name(state_),
         servo_io_error_name(last_error_));
    publish_state_update(last_error_);
    return;
  }
  state_ = vm_on_ ? ServoIoState::ReadyTorqueOff : ServoIoState::PowerOff;
  last_error_ = ServoIoError::None;
  logf("servo_io power state=%s", servo_io_state_name(state_));
  publish_state_update(ServoIoError::None);
}

bool ServoIoCore::sample_feedback(ServoLink& link, std::uint64_t now_ms,
                                  std::uint64_t period_ms,
                                  std::uint64_t log_interval_ms) noexcept {
  if (!vm_on_ || !hardware_ready_ || now_ms < next_feedback_due_ms_) return false;
  const auto started_ms = now_ms;
  hal::ServoPosition feedback;
  std::uint16_t yaw_raw = 0;
  std::uint16_t pitch_raw = 0;
  bool failed = false;
  if (transient_failures_.load(std::memory_order_acquire) > 0) {
    transient_failures_.fetch_sub(1, std::memory_order_release);
    failed = true;
  } else if (freeze_inject_.load(std::memory_order_acquire)) {
    failed = true;
  } else {
    failed = !link.read_feedback(feedback, yaw_raw, pitch_raw);
  }
  next_feedback_due_ms_ = now_ms + period_ms;
  if (failed) {
    ++consecutive_failures_;
    last_error_ = ServoIoError::FeedbackUnavailable;
    ServoFeedbackSnapshot update;
    seqlock_read(snapshot_seq_, [&update, this]() { update = snapshot_; });
    update.consecutive_failures = consecutive_failures_;
    update.last_error = last_error_;
    update.io_state = state_;
    update.torque_enabled = torque_on_;
    update.vm_enabled = vm_on_;
    update.command_generation = active_generation_;
    update.command_applied_ms = command_applied_ms_;
    seqlock_write(snapshot_seq_, [&update, this]() { snapshot_ = update; });
    logf("servo_io feedback failed consecutive=%u age_ms=%llu",
         static_cast<unsigned>(consecutive_failures_),
         static_cast<unsigned long long>(
             update.sampled_at_ms <= now_ms ? now_ms - update.sampled_at_ms : 0));
    return false;
  }
  consecutive_failures_ = 0;
  if (now_ms - last_feedback_log_ms_ >= log_interval_ms) {
    last_feedback_log_ms_ = now_ms;
    logf("servo_io feedback ok generation=%u raw=%u/%u angle=%.2f/%.2f elapsed_ms=%u",
         static_cast<unsigned>(active_generation_),
         static_cast<unsigned>(yaw_raw), static_cast<unsigned>(pitch_raw),
         static_cast<double>(feedback.yaw_deg),
         static_cast<double>(feedback.pitch_deg),
         static_cast<unsigned>(now_ms - started_ms));
  }
  publish_feedback(feedback, now_ms);
  return true;
}

void ServoIoCore::io_step(ServoLink& link, std::uint64_t now_ms) noexcept {
  post_wake_grace_ = now_ms < post_wake_grace_until_ms_;
  // Emergency always wins: drop the active generation and reconcile power
  // state. The emergency handler has already cut VM_EN directly; this is the
  // owner-side cleanup and a best-effort backup cut.
  const auto emergency = emergency_gen_.load(std::memory_order_acquire);
  if (emergency != seen_emergency_gen_) {
    seen_emergency_gen_ = emergency;
    cancel_up_to(current_generation());
    active_generation_ = 0;
    command_applied_ms_ = 0;
    if (link.vm_enabled()) (void)link.set_vm_enabled(false);
    torque_on_ = false;
    vm_on_ = false;
    consecutive_failures_ = 0;
    state_ = ServoIoState::PowerOff;
    logf("servo_io power state=%s reason=emergency", servo_io_state_name(state_));
    publish_state_update(ServoIoError::EmergencyStop);
    return;
  }

  // A cancelled generation never executes another blocking transaction; the
  // next step is always the stop sequence.
  const auto cancel = cancel_gen_.load(std::memory_order_acquire);
  if (active_generation_ != 0 && cancel >= active_generation_ &&
      state_ != ServoIoState::Stopping && state_ != ServoIoState::PowerOff &&
      state_ != ServoIoState::Faulted) {
    enter_stopping(false, false, ServoIoError::Cancelled);
    return;
  }

  OutputRequest request;
  seqlock_read(output_seq_, [&request, this]() { request = output_; });
  const auto request_cancel = cancel_gen_.load(std::memory_order_acquire);

  switch (state_) {
    case ServoIoState::PowerOff: {
      if (request.kind == OutputKind::Wake) {
        active_generation_ = 0;
        command_applied_ms_ = 0;
        wake_stage_ = 0;
        wake_attempts_ = 0;
        state_ = ServoIoState::PoweringOn;
        logf("servo_io preflight state=%s", servo_io_state_name(state_));
        publish_state_update(ServoIoError::None);
        return;
      }
      if (request.kind == OutputKind::Motion && request.generation != 0 &&
          request.generation > request_cancel) {
        active_generation_ = request.generation;
        command_applied_ms_ = 0;
        wake_stage_ = 0;
        wake_attempts_ = 0;
        logf("servo_io command accepted generation=%u target=%.2f/%.2f wake=%s",
             static_cast<unsigned>(request.generation), static_cast<double>(request.yaw_deg),
             static_cast<double>(request.pitch_deg),
             full_test_next_ ? "full" : "light");
        state_ = ServoIoState::PoweringOn;
        logf("servo_io power state=%s", servo_io_state_name(state_));
        publish_state_update(ServoIoError::None);
      }
      return;
    }

    case ServoIoState::PoweringOn: {
      if (wake_stage_ == 0) {
        if (full_test_next_) {
          if (!link.power_on_full()) {
            enter_stopping(true, true, ServoIoError::SelfTestFailed);
            return;
          }
          full_test_next_ = false;
          hardware_ready_ = true;
          vm_on_ = true;
          torque_on_ = false;
          state_ = ServoIoState::ReadyTorqueOff;
          logf("servo_io power state=%s mode=full", servo_io_state_name(state_));
          publish_state_update(ServoIoError::None);
          return;
        }
        if (!link.vm_enabled()) {
          if (!link.set_vm_enabled(true)) {
            ++wake_attempts_;
            if (wake_attempts_ < kWakeMaxAttempts) return;
            enter_stopping(true, true, ServoIoError::PowerUnavailable);
            return;
          }
          wake_deadline_ms_ = now_ms + kWakeSettleMs;
        }
        wake_stage_ = 1;
        return;
      }
      if (wake_deadline_ms_ > now_ms) return;
      hal::ServoPosition feedback;
      std::uint16_t yaw_raw = 0;
      std::uint16_t pitch_raw = 0;
      if (!link.read_feedback(feedback, yaw_raw, pitch_raw)) {
        ++wake_attempts_;
        if (wake_attempts_ < kWakeMaxAttempts) {
          wake_deadline_ms_ = now_ms + kWakeRetryIntervalMs;
          return;
        }
        enter_stopping(true, true, ServoIoError::FeedbackUnavailable);
        return;
      }
      if (wake_stage_ == 1) {
        // A just-powered SCS servo answers intermittently for the first few
        // hundred milliseconds; require a second spaced read before trusting
        // the pair as ready, otherwise tracking starts with a failure streak.
        wake_stage_ = 2;
        wake_deadline_ms_ = now_ms + kWakeConfirmIntervalMs;
        publish_feedback(feedback, now_ms);
        return;
      }
      if (!link.enable_torque(false)) {
        // A wake that cannot confirm torque-off must not continue to motion.
        enter_stopping(true, true, ServoIoError::TorqueEnableFailed);
        return;
      }
      torque_on_ = false;
      hardware_ready_ = true;
      vm_on_ = true;
      wake_attempts_ = 0;
      post_wake_grace_until_ms_ = now_ms + kPostWakeGraceMs;
      state_ = ServoIoState::ReadyTorqueOff;
      logf("servo_io power state=%s mode=light", servo_io_state_name(state_));
      publish_feedback(feedback, now_ms);
      return;
    }

    case ServoIoState::ReadyTorqueOff: {
      if (request.kind == OutputKind::Wake) {
        next_feedback_due_ms_ = now_ms;
        sample_feedback(link, now_ms, kIdleFeedbackPeriodMs,
                        kIdleFeedbackLogIntervalMs);
        return;
      }
      if (request.kind == OutputKind::Stop) {
        if (request.power_cut && (vm_on_ || torque_on_)) {
          enter_stopping(true, false, ServoIoError::None);
        } else if (torque_on_) {
          enter_stopping(false, false, ServoIoError::None);
        } else {
          // Safety emits a non-cut Stop while idle. Keep sampling in the
          // torque-off state so a successful zero-motion preflight stays
          // fresh long enough for the browser to unlock manual input.
          sample_feedback(link, now_ms, kIdleFeedbackPeriodMs,
                          kIdleFeedbackLogIntervalMs);
        }
        return;
      }
      if (request.kind == OutputKind::Motion && request.generation != 0 &&
          request.generation > request_cancel) {
        active_generation_ = request.generation;
        command_applied_ms_ = 0;
        logf("servo_io command accepted generation=%u target=%.2f/%.2f",
             static_cast<unsigned>(request.generation), static_cast<double>(request.yaw_deg),
             static_cast<double>(request.pitch_deg));
        state_ = ServoIoState::ApplyingCommand;
        publish_state_update(ServoIoError::None);
        return;
      }
      sample_feedback(link, now_ms, kIdleFeedbackPeriodMs, kIdleFeedbackLogIntervalMs);
      return;
    }

    case ServoIoState::ApplyingCommand: {
      if (request.kind == OutputKind::Stop || request.generation == 0 ||
          request.generation <= request_cancel) {
        enter_stopping(request.power_cut, false, ServoIoError::Cancelled);
        return;
      }
      if (request.kind != OutputKind::Motion) return;
      if (request.generation != active_generation_) {
        // A newer generation supersedes the one still being applied.
        active_generation_ = request.generation;
        command_applied_ms_ = 0;
        logf("servo_io command accepted generation=%u target=%.2f/%.2f",
             static_cast<unsigned>(request.generation), static_cast<double>(request.yaw_deg),
             static_cast<double>(request.pitch_deg));
      }
      if (!torque_on_) {
        const auto started_ms = now_ms;
        if (!link.enable_torque(true)) {
          enter_stopping(true, true, ServoIoError::TorqueEnableFailed);
          return;
        }
        torque_on_ = true;
        logf("servo_io torque enable ok=1 elapsed_ms=%u",
             static_cast<unsigned>(now_ms - started_ms));
        publish_state_update(ServoIoError::None);
        return;
      }
      const auto started_ms = now_ms;
      hal::ServoPosition goal{request.yaw_deg, request.pitch_deg};
      std::uint16_t yaw_raw = 0;
      std::uint16_t pitch_raw = 0;
      if (!link.write_goal(goal, yaw_raw, pitch_raw)) {
        enter_stopping(true, true, ServoIoError::PositionWriteFailed);
        return;
      }
      command_applied_ms_ = now_ms;
      state_ = ServoIoState::Tracking;
      next_feedback_due_ms_ = now_ms;
      logf("servo_io position write yaw=%.2f pitch=%.2f raw=%u/%u elapsed_ms=%u",
           static_cast<double>(goal.yaw_deg), static_cast<double>(goal.pitch_deg),
           static_cast<unsigned>(yaw_raw), static_cast<unsigned>(pitch_raw),
           static_cast<unsigned>(now_ms - started_ms));
      logf("servo_io command applied generation=%u applied_ms=%llu",
           static_cast<unsigned>(active_generation_),
           static_cast<unsigned long long>(command_applied_ms_));
      publish_state_update(ServoIoError::None);
      return;
    }

    case ServoIoState::Tracking: {
      if (request.kind == OutputKind::Stop) {
        enter_stopping(request.power_cut, false, ServoIoError::None);
        return;
      }
      if (request.kind == OutputKind::Motion && request.generation != 0) {
        if (request.generation <= request_cancel) {
          enter_stopping(false, false, ServoIoError::Cancelled);
          return;
        }
        if (request.generation != active_generation_) {
          active_generation_ = request.generation;
          command_applied_ms_ = 0;
          logf("servo_io command accepted generation=%u target=%.2f/%.2f",
               static_cast<unsigned>(request.generation),
             static_cast<double>(request.yaw_deg),
               static_cast<double>(request.pitch_deg));
          state_ = ServoIoState::ApplyingCommand;
          publish_state_update(ServoIoError::None);
          return;
        }
      }
      sample_feedback(link, now_ms, kTrackingFeedbackPeriodMs, kFeedbackLogMinIntervalMs);
      return;
    }

    case ServoIoState::Stopping: {
      if (torque_on_) {
        const auto started_ms = now_ms;
        const bool ok = link.enable_torque(false);
        torque_on_ = false;
        logf("servo_io torque disable ok=%d elapsed_ms=%u", ok ? 1 : 0,
             static_cast<unsigned>(now_ms - started_ms));
        publish_state_update(ServoIoError::None);
        return;
      }
      if (vm_on_ && (stopping_power_cut_ || request.power_cut)) {
        const auto started_ms = now_ms;
        const bool ok = link.set_vm_enabled(false);
        vm_on_ = false;
        logf("servo_io vm cut ok=%d elapsed_ms=%u", ok ? 1 : 0,
             static_cast<unsigned>(now_ms - started_ms));
        publish_state_update(ServoIoError::None);
        return;
      }
      finish_stopping();
      return;
    }

    case ServoIoState::Faulted: {
      if (request.kind == OutputKind::Reset) {
        state_ = ServoIoState::PowerOff;
        full_test_next_ = true;
        last_error_ = ServoIoError::None;
        logf("servo_io power state=%s recovery=full_test",
             servo_io_state_name(state_));
        publish_state_update(ServoIoError::None);
      }
      return;
    }
  }
}

SafetyGateResult safety_gate(ServoIoCore& core, FastSafetyLoop& safety,
                             StallMonitor& monitor,
                             const SafetyGateInputs& inputs) noexcept {
  SafetyGateResult result;
  const bool latched = safety.latched();

  // Paused, emergency, link loss and latched faults cancel every outstanding
  // generation; nothing resumes automatically after the condition clears.
  if (inputs.paused || inputs.emergency || inputs.link_lost || latched) {
    core.cancel_all();
  }

  result.active_command =
      (inputs.paused || inputs.emergency || inputs.link_lost || latched)
          ? ServoCommand{}
          : core.take_command(inputs.now_ms);
  const bool preflight_requested = core.take_preflight();
  const bool motion_candidate = result.active_command.generation != 0;
  const auto snapshot = core.feedback();
  result.health = evaluate_feedback_health(snapshot, inputs.now_ms);
  result.feedback_age_ms =
      snapshot.valid && snapshot.sampled_at_ms <= inputs.now_ms
          ? inputs.now_ms - snapshot.sampled_at_ms
          : 0;

  bool progress_stall = false;
  if (motion_candidate) {
    const bool applied =
        snapshot.valid &&
        snapshot.command_generation == result.active_command.generation &&
        snapshot.command_applied_ms != 0;
    result.command_applied = applied;
    if (applied) {
      const hal::ServoPosition target{result.active_command.yaw_deg,
                                      result.active_command.pitch_deg};
      const hal::ServoPosition measured{snapshot.yaw_deg, snapshot.pitch_deg};
      if (!monitor.monitoring(result.active_command.generation)) {
        monitor.begin(result.active_command.generation, target,
                      snapshot.command_applied_ms);
      }
      monitor.on_feedback(result.active_command.generation, measured, inputs.now_ms);
      progress_stall =
          monitor.stalled(result.active_command.generation, measured, inputs.now_ms);
      result.progress_age_ms = monitor.progress_age_ms(inputs.now_ms);
      result.target_error_deg =
          std::fmax(abs_delta(target.yaw_deg, measured.yaw_deg),
                    abs_delta(target.pitch_deg, measured.pitch_deg));
    } else {
      monitor.reset();
    }
  } else {
    monitor.reset();
  }
  result.progress_stall = progress_stall;

  const bool io_faulted = snapshot.io_state == ServoIoState::Faulted;
  result.stall =
      inputs.feedback_frozen || progress_stall || result.health.stale_latch || io_faulted;
  result.stall_source = inputs.feedback_frozen  ? "frozen"
                        : progress_stall        ? "progress"
                        : result.health.stale_latch ? "stale"
                        : io_faulted            ? "io_faulted"
                                                : "none";

  runtime::MotionRequest request;
  request.valid = motion_candidate;
  request.yaw_deg = result.active_command.yaw_deg;
  request.pitch_deg = result.active_command.pitch_deg;
  request.expires_at_ms = result.active_command.expires_at_ms;
  runtime::SafetySample sample;
  sample.now_ms = inputs.now_ms;
  sample.measured_yaw_deg = snapshot.yaw_deg;
  sample.measured_pitch_deg = snapshot.pitch_deg;
  sample.emergency_stop = inputs.emergency;
  sample.stall = result.stall;
  const auto decision = safety.tick(sample, request);

  // Fault clearing requires trustworthy feedback while the servo rail is
  // live; with the rail down the last known position is the best available
  // evidence and clearing is allowed (recovery re-runs the full self-test).
  const bool vm_active =
      snapshot.io_state == ServoIoState::PoweringOn ||
      snapshot.io_state == ServoIoState::ReadyTorqueOff ||
      snapshot.io_state == ServoIoState::ApplyingCommand ||
      snapshot.io_state == ServoIoState::Tracking;
  if (inputs.clear_requested && result.health.valid && !inputs.link_lost &&
      (result.health.fresh || !vm_active)) {
    safety.clear_latched(true);
    core.clear_fault_reset();
    monitor.reset();
    result.cleared = true;
    result.output = OutputRequest{OutputKind::Reset, false, 0, 0.0F, 45.0F};
    core.publish_output(result.output);
    return result;
  }

  if (preflight_requested && !motion_candidate && !inputs.paused &&
      !inputs.emergency && !inputs.link_lost && !latched) {
    result.output = OutputRequest{OutputKind::Wake, false, 0, 0.0F, 45.0F};
    core.publish_output(result.output);
    return result;
  }

  if (decision.accepted && motion_candidate) {
    result.output = OutputRequest{OutputKind::Motion, false,
                                  result.active_command.generation,
                                  decision.yaw_deg, decision.pitch_deg};
  } else {
    const bool power_cut = safety.latched() || inputs.paused || inputs.emergency ||
                           inputs.link_lost || io_faulted;
    result.output = OutputRequest{OutputKind::Stop, power_cut, 0, 0.0F, 45.0F};
  }
  core.publish_output(result.output);
  return result;
}

}  // namespace lifeos::runtime
