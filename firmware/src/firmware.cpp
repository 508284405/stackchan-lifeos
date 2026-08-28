#include "lifeos/firmware.hpp"

#include <algorithm>
#include <cmath>
#include <queue>
#include <unordered_set>

namespace lifeos {

namespace {
float clamp(float value, float low, float high) {
  return std::max(low, std::min(value, high));
}
}  // namespace

SafeMotion MotionSafety::evaluate(const BehaviorCommand& command,
                                  const SensorSnapshot& sensors,
                                  std::uint64_t now_ms) const {
  if (sensors.stall) {
    SafeMotion result;
    result.accepted = false;
    result.torque_enabled = false;
    result.reason = "fault.stall";
    return result;
  }
  if (command.expires_at_ms <= now_ms) {
    SafeMotion result;
    result.accepted = false;
    result.reason = "command.expired";
    return result;
  }

  const float raw_yaw = command.yaw_deg.value_or(0.0F);
  const float raw_pitch = command.pitch_deg.value_or(45.0F);
  if (raw_yaw < kYawHardMin || raw_yaw > kYawHardMax ||
      raw_pitch < kPitchMin || raw_pitch > kPitchMax) {
    SafeMotion result;
    result.accepted = false;
    result.reason = "motion.hard_limit";
    return result;
  }
  SafeMotion result;
  result.accepted = true;
  result.torque_enabled = true;
  result.yaw_deg = clamp(raw_yaw, kYawSoftMin, kYawSoftMax);
  result.pitch_deg = clamp(raw_pitch, kPitchMin, kPitchMax);
  result.reason = "motion.accepted";
  return result;
}

std::optional<BehaviorCommand> CommandArbiter::choose(
    const std::vector<BehaviorCommand>& candidates,
    std::uint64_t now_ms) const {
  std::optional<BehaviorCommand> best;
  for (const auto& candidate : candidates) {
    if (candidate.expires_at_ms <= now_ms) continue;
    if (!best || static_cast<int>(candidate.priority) >
                     static_cast<int>(best->priority)) {
      best = candidate;
    }
  }
  return best;
}

bool StaticDag::add_node(std::string name, NodeFn fn) {
  if (nodes_.size() >= kMaxNodes || nodes_.count(name) != 0 || !fn) return false;
  insertion_order_.push_back(name);
  nodes_.emplace(std::move(name), Node{std::move(fn), {}});
  return true;
}

bool StaticDag::add_dependency(const std::string& node,
                               const std::string& dependency) {
  if (nodes_.count(node) == 0 || nodes_.count(dependency) == 0) return false;
  nodes_.at(node).dependencies.push_back(dependency);
  return true;
}

bool StaticDag::compile() {
  order_.clear();
  std::unordered_map<std::string, std::size_t> indegree;
  std::unordered_map<std::string, std::vector<std::string>> outgoing;
  for (const auto& name : insertion_order_) indegree[name] = 0;
  for (const auto& [name, node] : nodes_) {
    indegree[name] = node.dependencies.size();
    for (const auto& dependency : node.dependencies) outgoing[dependency].push_back(name);
  }
  std::queue<std::string> ready;
  for (const auto& name : insertion_order_) {
    if (indegree[name] == 0) ready.push(name);
  }
  while (!ready.empty()) {
    auto name = ready.front();
    ready.pop();
    order_.push_back(name);
    for (const auto& next : outgoing[name]) {
      if (--indegree[next] == 0) ready.push(next);
    }
  }
  return order_.size() == nodes_.size();
}

bool StaticDag::run(GraphContext& context) const {
  if (order_.size() != nodes_.size()) return false;
  for (const auto& name : order_) {
    if (!nodes_.at(name).fn(context)) return false;
  }
  return true;
}

StaticDag build_body_graph() {
  StaticDag graph;
  graph.add_node("sense", [](GraphContext&) { return true; });
  graph.add_node("reflex", [](GraphContext& context) {
    if (context.sensors.stall) {
      context.candidates.push_back({"emergency_stop", Priority::Safety, 1.0F,
                                    context.sensors.timestamp_ms,
                                    context.sensors.timestamp_ms + 100,
                                    std::nullopt, std::nullopt});
    } else if (context.sensors.touch) {
      context.candidates.push_back({"happy", Priority::Reflex, 0.6F,
                                    context.sensors.timestamp_ms,
                                    context.sensors.timestamp_ms + 1000,
                                    std::nullopt, std::nullopt});
    }
    return true;
  });
  graph.add_node("arbitrate", [](GraphContext& context) {
    context.selected = CommandArbiter{}.choose(context.candidates,
                                               context.sensors.timestamp_ms);
    return true;
  });
  graph.add_node("safety", [](GraphContext& context) {
    if (!context.selected) return true;
    context.motion = MotionSafety{}.evaluate(*context.selected, context.sensors,
                                             context.sensors.timestamp_ms);
    return true;
  });
  graph.add_node("transport", [](GraphContext&) { return true; });
  graph.add_dependency("reflex", "sense");
  graph.add_dependency("arbitrate", "reflex");
  graph.add_dependency("safety", "arbitrate");
  graph.add_dependency("transport", "safety");
  graph.compile();
  return graph;
}

}  // namespace lifeos
