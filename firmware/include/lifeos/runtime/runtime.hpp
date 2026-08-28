#pragma once

#include <array>
#include <cstddef>
#include <cstdint>

namespace lifeos::runtime {

constexpr std::size_t kMaxGraphNodes = 16;

using NodeFunction = bool (*)(void* context);

/** A bounded, single-threaded DAG. All storage is owned by this object. */
class StaticGraph final {
 public:
  bool add_node(const char* name, NodeFunction function) noexcept;
  bool add_dependency(std::size_t node, std::size_t dependency) noexcept;
  bool compile() noexcept;
  bool run(void* context) const noexcept;
  std::size_t node_count() const noexcept { return node_count_; }
  std::size_t order_at(std::size_t index) const noexcept { return order_[index]; }

 private:
  struct Node {
    const char* name{nullptr};
    NodeFunction function{nullptr};
    std::uint16_t dependencies{0};
  };
  std::array<Node, kMaxGraphNodes> nodes_{};
  std::array<std::size_t, kMaxGraphNodes> order_{};
  std::size_t node_count_{0};
  std::size_t order_count_{0};
  bool compiled_{false};
};

class StaticExecutor final {
 public:
  explicit StaticExecutor(const StaticGraph& graph) noexcept : graph_(graph) {}
  bool execute(void* context) const noexcept { return graph_.run(context); }

 private:
  const StaticGraph& graph_;
};

enum class SafetyFault : std::uint16_t {
  None = 0,
  EmergencyStop = 1u << 0,
  Watchdog = 1u << 1,
  Stall = 1u << 2,
  HardLimit = 1u << 3,
  CommandExpired = 1u << 4,
  InvalidCommand = 1u << 5,
};

constexpr SafetyFault operator|(SafetyFault lhs, SafetyFault rhs) noexcept {
  return static_cast<SafetyFault>(static_cast<std::uint16_t>(lhs) |
                                  static_cast<std::uint16_t>(rhs));
}
constexpr SafetyFault& operator|=(SafetyFault& lhs, SafetyFault rhs) noexcept {
  lhs = lhs | rhs;
  return lhs;
}
constexpr bool has_fault(SafetyFault value, SafetyFault flag) noexcept {
  return (static_cast<std::uint16_t>(value) & static_cast<std::uint16_t>(flag)) != 0;
}

struct MotionRequest {
  bool valid{false};
  float yaw_deg{0.0F};
  float pitch_deg{45.0F};
  std::uint64_t expires_at_ms{0};
};

struct SafetySample {
  std::uint64_t now_ms{0};
  float measured_yaw_deg{0.0F};
  float measured_pitch_deg{45.0F};
  bool emergency_stop{false};
  bool stall{false};
  bool yaw_limit{false};
  bool pitch_limit{false};
};

struct SafetyDecision {
  bool torque_enabled{false};
  bool accepted{false};
  float yaw_deg{0.0F};
  float pitch_deg{45.0F};
  SafetyFault faults{SafetyFault::None};
};

class FastSafetyLoop final {
 public:
  static constexpr float kYawSoftMin = -75.0F;
  static constexpr float kYawSoftMax = 75.0F;
  static constexpr float kYawHardMin = -90.0F;
  static constexpr float kYawHardMax = 90.0F;
  static constexpr float kPitchMin = 5.0F;
  static constexpr float kPitchMax = 85.0F;
  static constexpr std::uint64_t kDefaultWatchdogTimeoutMs = 250;

  explicit FastSafetyLoop(
      std::uint64_t watchdog_timeout_ms = kDefaultWatchdogTimeoutMs) noexcept
      : watchdog_timeout_ms_(watchdog_timeout_ms) {}

  // Called by the graph executor only after a completed, healthy tick.
  void heartbeat(std::uint64_t now_ms) noexcept { last_heartbeat_ms_ = now_ms; heartbeat_seen_ = true; }
  SafetyDecision tick(const SafetySample& sample,
                      const MotionRequest& request) noexcept;
  bool latched() const noexcept { return latched_faults_ != SafetyFault::None; }
  SafetyFault latched_faults() const noexcept { return latched_faults_; }
  // Fault clearing is intentionally explicit and requires a local confirmation.
  bool clear_latched(bool local_confirmation) noexcept;

 private:
  static float clamp(float value, float low, float high) noexcept;
  std::uint64_t watchdog_timeout_ms_;
  std::uint64_t last_heartbeat_ms_{0};
  SafetyFault latched_faults_{SafetyFault::None};
  bool heartbeat_seen_{false};
};

}  // namespace lifeos::runtime
