#pragma once

#include <cstdint>
#include <functional>
#include <optional>
#include <string>
#include <unordered_map>
#include <vector>

namespace lifeos {

enum class Priority : std::uint8_t {
  Idle = 0,
  Proactive = 10,
  Agent = 20,
  User = 30,
  Reflex = 40,
  Safety = 50,
};

struct SensorSnapshot {
  std::uint64_t timestamp_ms{0};
  bool touch{false};
  bool person_present{false};
  bool shaken{false};
  bool stall{false};
  float brightness{0.5F};
};

struct BehaviorCommand {
  std::string name{"still"};
  Priority priority{Priority::Idle};
  float intensity{0.0F};
  std::uint64_t issued_at_ms{0};
  std::uint64_t expires_at_ms{0};
  std::optional<float> yaw_deg;
  std::optional<float> pitch_deg;
};

struct SafeMotion {
  bool accepted{false};
  bool torque_enabled{true};
  float yaw_deg{0.0F};
  float pitch_deg{45.0F};
  std::string reason;
};

class MotionSafety final {
 public:
  static constexpr float kYawSoftMin = -75.0F;
  static constexpr float kYawSoftMax = 75.0F;
  static constexpr float kYawHardMin = -90.0F;
  static constexpr float kYawHardMax = 90.0F;
  static constexpr float kPitchMin = 5.0F;
  static constexpr float kPitchMax = 85.0F;

  SafeMotion evaluate(const BehaviorCommand& command,
                      const SensorSnapshot& sensors,
                      std::uint64_t now_ms) const;
};

class CommandArbiter final {
 public:
  std::optional<BehaviorCommand> choose(
      const std::vector<BehaviorCommand>& candidates,
      std::uint64_t now_ms) const;
};

struct GraphContext {
  SensorSnapshot sensors;
  std::vector<BehaviorCommand> candidates;
  std::optional<BehaviorCommand> selected;
  SafeMotion motion;
};

class StaticDag final {
 public:
  using NodeFn = std::function<bool(GraphContext&)>;

  bool add_node(std::string name, NodeFn fn);
  bool add_dependency(const std::string& node, const std::string& dependency);
  bool compile();
  bool run(GraphContext& context) const;
  const std::vector<std::string>& order() const { return order_; }

 private:
  static constexpr std::size_t kMaxNodes = 16;
  struct Node {
    NodeFn fn;
    std::vector<std::string> dependencies;
  };
  std::unordered_map<std::string, Node> nodes_;
  std::vector<std::string> insertion_order_;
  std::vector<std::string> order_;
};

StaticDag build_body_graph();

}  // namespace lifeos

