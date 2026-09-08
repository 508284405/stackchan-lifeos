#pragma once

#include <atomic>
#include <cstdint>
#include <cstdio>

#include "lifeos/hal/hal.hpp"
#include "lifeos/runtime/runtime.hpp"

namespace lifeos::runtime {

/** Servo I/O owner state machine states. */
enum class ServoIoState : std::uint8_t {
  PowerOff,
  PoweringOn,
  ReadyTorqueOff,
  ApplyingCommand,
  Tracking,
  Stopping,
  Faulted,
};

const char* servo_io_state_name(ServoIoState state) noexcept;

/** Last servo I/O error observed by the owner task. */
enum class ServoIoError : std::uint8_t {
  None,
  PowerUnavailable,
  SelfTestFailed,
  TorqueEnableFailed,
  PositionWriteFailed,
  FeedbackUnavailable,
  Cancelled,
  EmergencyStop,
};

const char* servo_io_error_name(ServoIoError error) noexcept;

// Named safety thresholds. Host-tested; do not inline magic numbers elsewhere.
inline constexpr std::uint64_t kFeedbackFreshnessMs = 400;
inline constexpr std::uint32_t kFeedbackMinFailuresForLatch = 3;
inline constexpr float kStallTargetErrorDeg = 3.0F;
inline constexpr float kStallProgressDeltaDeg = 0.5F;
inline constexpr std::uint64_t kStallNoProgressWindowMs = 700;

struct ServoCommand {
  std::uint32_t generation{0};
  float yaw_deg{0.0F};
  float pitch_deg{45.0F};
  std::uint64_t expires_at_ms{0};
};

struct ServoFeedbackSnapshot {
  std::uint64_t sampled_at_ms{0};
  std::uint64_t command_applied_ms{0};
  std::uint32_t command_generation{0};
  float yaw_deg{0.0F};
  float pitch_deg{45.0F};
  bool valid{false};
  bool torque_enabled{false};
  bool vm_enabled{false};
  std::uint32_t consecutive_failures{0};
  bool post_wake_grace{false};
  ServoIoState io_state{ServoIoState::PowerOff};
  ServoIoError last_error{ServoIoError::None};
};

enum class OutputKind : std::uint8_t { None, Motion, Wake, Stop, Reset };

struct OutputRequest {
  OutputKind kind{OutputKind::None};
  bool power_cut{false};
  std::uint32_t generation{0};
  float yaw_deg{0.0F};
  float pitch_deg{45.0F};
};

struct FeedbackHealth {
  bool valid{false};
  bool fresh{false};
  bool degraded{false};
  bool stale_latch{false};
};

FeedbackHealth evaluate_feedback_health(const ServoFeedbackSnapshot& snapshot,
                                        std::uint64_t now_ms) noexcept;

/** The only door to SCS UART / servo power hardware. Implemented by the
 *  target adapter and by test fakes. All calls may block; only the servo I/O
 *  owner task (or a test) may invoke them. */
class ServoLink {
 public:
  virtual ~ServoLink() = default;
  virtual bool vm_enabled() const = 0;
  virtual bool set_vm_enabled(bool enabled) = 0;
  virtual bool enable_torque(bool enabled) = 0;
  virtual bool write_goal(const hal::ServoPosition& position,
                          std::uint16_t& yaw_raw, std::uint16_t& pitch_raw) = 0;
  virtual bool read_feedback(hal::ServoPosition& position,
                             std::uint16_t& yaw_raw, std::uint16_t& pitch_raw) = 0;
  virtual bool wake_light() = 0;
  virtual bool power_on_full() = 0;
  virtual bool hardware_ready() const = 0;
};

/** Generation-keyed stall bookkeeping used by the safety tick. Progress is
 *  only credited from applied commands; the window starts at the time the
 *  position write was acknowledged. */
class StallMonitor final {
 public:
  void begin(std::uint32_t generation, const hal::ServoPosition& target,
             std::uint64_t applied_ms) noexcept;
  void reset() noexcept;
  bool monitoring(std::uint32_t generation) const noexcept {
    return active_ && generation_ == generation;
  }
  void on_feedback(std::uint32_t generation, const hal::ServoPosition& feedback,
                   std::uint64_t now_ms) noexcept;
  bool stalled(std::uint32_t generation, const hal::ServoPosition& feedback,
               std::uint64_t now_ms) const noexcept;
  std::uint64_t progress_age_ms(std::uint64_t now_ms) const noexcept {
    return last_progress_ms_ <= now_ms ? now_ms - last_progress_ms_ : 0;
  }

 private:
  static float abs_error(float a, float b) noexcept {
    return a > b ? a - b : b - a;
  }
  bool active_{false};
  std::uint32_t generation_{0};
  hal::ServoPosition target_{};
  hal::ServoPosition last_feedback_{};
  std::uint64_t last_progress_ms_{0};
  bool have_feedback_{false};
};

using ServoIoLogFn = void (*)(void* context, const char* line);

/** Deterministic core shared by the servo I/O owner task, the safety task and
 *  the USB main task. Single-writer seqlocks isolate the three data slots; a
 *  CAS-max atomic implements generation cancellation. The USB main task calls
 *  submit/mark_emergency only, the safety task calls the take/feedback/publish
 *  group, and the owner task calls io_step. */
class ServoIoCore final {
 public:
  // ---- producer API (USB main task; O(nanoseconds), never touches a link) --
  std::uint32_t submit(float yaw_deg, float pitch_deg,
                       std::uint64_t expires_at_ms) noexcept;
  void cancel_up_to(std::uint32_t generation) noexcept;
  void cancel_all() noexcept {
    cancel_up_to(current_generation());
    preflight_requested_.store(false, std::memory_order_release);
  }
  /** Request a zero-motion VM/feedback preflight. The SafetyTask is the only
   *  consumer and the servo I/O task remains the only hardware owner. */
  void request_preflight() noexcept {
    preflight_requested_.store(true, std::memory_order_release);
  }
  void mark_emergency() noexcept;
  void inject_feedback_freeze(bool enabled) noexcept;
  void inject_transient_failures(std::uint32_t count) noexcept;
  std::uint32_t current_generation() const noexcept {
    return generation_counter_.load(std::memory_order_acquire);
  }

  // ---- safety API (safety task only) --------------------------------------
  ServoCommand take_command(std::uint64_t now_ms) const noexcept;
  bool take_preflight() noexcept {
    return preflight_requested_.exchange(false, std::memory_order_acq_rel);
  }
  ServoFeedbackSnapshot feedback() const noexcept;
  void publish_output(const OutputRequest& request) noexcept;
  void clear_fault_reset() noexcept { cancel_all(); }

  // ---- owner API (servo I/O task only) ------------------------------------
  void init(bool hardware_ready, bool vm_enabled) noexcept;
  void io_step(ServoLink& link, std::uint64_t now_ms) noexcept;
  ServoIoState io_state() const noexcept { return state_; }
  void set_logger(ServoIoLogFn fn, void* context) noexcept {
    log_fn_ = fn;
    log_ctx_ = context;
  }

 private:
  static constexpr std::size_t kLogLineBytes = 160;

  void logf(const char* format, ...) noexcept;
  void publish_state_update(ServoIoError error) noexcept;
  void publish_feedback(const hal::ServoPosition& position,
                        std::uint64_t now_ms) noexcept;
  bool sample_feedback(ServoLink& link, std::uint64_t now_ms,
                       std::uint64_t period_ms,
                       std::uint64_t log_interval_ms) noexcept;
  void enter_stopping(bool power_cut, bool faulted, ServoIoError error) noexcept;
  void finish_stopping() noexcept;

  // mailbox (writer: submit; reader: safety)
  std::atomic<std::uint32_t> mailbox_seq_{0};
  ServoCommand mailbox_{};
  // output request (writer: safety; reader: owner)
  std::atomic<std::uint32_t> output_seq_{0};
  OutputRequest output_{};
  // feedback snapshot (writer: owner; reader: safety/status)
  std::atomic<std::uint32_t> snapshot_seq_{0};
  ServoFeedbackSnapshot snapshot_{};

  std::atomic<std::uint32_t> generation_counter_{0};
  std::atomic<std::uint32_t> cancel_gen_{0};
  std::atomic<std::uint32_t> emergency_gen_{0};
  std::atomic<bool> preflight_requested_{false};
  std::atomic<bool> freeze_inject_{false};
  std::atomic<std::uint32_t> transient_failures_{0};

  // owner-private state machine
  ServoIoState state_{ServoIoState::PowerOff};
  ServoIoError last_error_{ServoIoError::None};
  std::uint32_t active_generation_{0};
  std::uint64_t command_applied_ms_{0};
  bool torque_on_{false};
  bool vm_on_{false};
  bool hardware_ready_{false};
  bool full_test_next_{false};
  bool stopping_power_cut_{false};
  bool stopping_faulted_{false};
  std::uint8_t wake_stage_{0};
  std::uint8_t wake_attempts_{0};
  std::uint64_t wake_deadline_ms_{0};
  std::uint64_t next_feedback_due_ms_{0};
  std::uint64_t last_feedback_log_ms_{0};
  std::uint32_t consecutive_failures_{0};
  bool post_wake_grace_{false};
  std::uint64_t post_wake_grace_until_ms_{0};
  std::uint32_t seen_emergency_gen_{0};
  ServoIoLogFn log_fn_{nullptr};
  void* log_ctx_{nullptr};
};

struct SafetyGateInputs {
  std::uint64_t now_ms{0};
  bool paused{false};
  bool emergency{false};
  bool fault_latched{false};
  bool feedback_frozen{false};
  bool link_lost{false};
  bool clear_requested{false};
};

struct SafetyGateResult {
  OutputRequest output{};
  bool stall{false};
  const char* stall_source{"none"};
  bool progress_stall{false};
  bool cleared{false};
  bool command_applied{false};
  float target_error_deg{0.0F};
  FeedbackHealth health{};
  ServoCommand active_command{};
  std::uint64_t progress_age_ms{0};
  std::uint64_t feedback_age_ms{0};
};

/** The complete 20 Hz safety evaluation: command gating, feedback health,
 *  stall monitoring, FastSafetyLoop latching and output publication. Runs on
 *  the safety task on target; host tests drive it with a fake link. */
SafetyGateResult safety_gate(ServoIoCore& core, FastSafetyLoop& safety,
                             StallMonitor& monitor, const SafetyGateInputs& inputs) noexcept;

}  // namespace lifeos::runtime
