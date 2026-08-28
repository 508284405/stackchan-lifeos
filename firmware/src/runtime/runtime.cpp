#include "lifeos/runtime/runtime.hpp"

#include <algorithm>

namespace lifeos::runtime {

bool StaticGraph::add_node(const char* name, NodeFunction function) noexcept {
  if (compiled_ || node_count_ == kMaxGraphNodes || name == nullptr ||
      function == nullptr) {
    return false;
  }
  nodes_[node_count_] = Node{name, function, 0};
  ++node_count_;
  return true;
}

bool StaticGraph::add_dependency(std::size_t node,
                                 std::size_t dependency) noexcept {
  if (compiled_ || node >= node_count_ || dependency >= node_count_ ||
      node == dependency) {
    return false;
  }
  nodes_[node].dependencies = static_cast<std::uint16_t>(
      nodes_[node].dependencies | (std::uint16_t{1} << dependency));
  return true;
}

bool StaticGraph::compile() noexcept {
  order_count_ = 0;
  std::uint16_t completed = 0;
  while (order_count_ < node_count_) {
    bool progressed = false;
    for (std::size_t index = 0; index < node_count_; ++index) {
      const std::uint16_t bit = static_cast<std::uint16_t>(std::uint16_t{1} << index);
      if ((completed & bit) == 0 &&
          (nodes_[index].dependencies & completed) == nodes_[index].dependencies) {
        order_[order_count_++] = index;
        completed = static_cast<std::uint16_t>(completed | bit);
        progressed = true;
      }
    }
    if (!progressed) {
      order_count_ = 0;
      compiled_ = false;
      return false;
    }
  }
  compiled_ = true;
  return true;
}

bool StaticGraph::run(void* context) const noexcept {
  if (!compiled_ || context == nullptr) return false;
  for (std::size_t index = 0; index < order_count_; ++index) {
    if (!nodes_[order_[index]].function(context)) return false;
  }
  return true;
}

float FastSafetyLoop::clamp(float value, float low, float high) noexcept {
  return std::max(low, std::min(value, high));
}

SafetyDecision FastSafetyLoop::tick(const SafetySample& sample,
                                    const MotionRequest& request) noexcept {
  SafetyDecision decision;
  SafetyFault faults = SafetyFault::None;
  if (sample.emergency_stop) faults |= SafetyFault::EmergencyStop;
  if (sample.stall) faults |= SafetyFault::Stall;
  if (sample.yaw_limit || sample.pitch_limit) faults |= SafetyFault::HardLimit;
  if (!heartbeat_seen_ || sample.now_ms < last_heartbeat_ms_ ||
      sample.now_ms - last_heartbeat_ms_ > watchdog_timeout_ms_) {
    faults |= SafetyFault::Watchdog;
  }
  if (!request.valid || request.expires_at_ms <= sample.now_ms) {
    faults |= request.valid ? SafetyFault::CommandExpired
                            : SafetyFault::InvalidCommand;
  } else if (request.yaw_deg < kYawHardMin || request.yaw_deg > kYawHardMax ||
             request.pitch_deg < kPitchMin || request.pitch_deg > kPitchMax) {
    faults |= SafetyFault::HardLimit;
  }

  const bool critical = has_fault(faults, SafetyFault::EmergencyStop) ||
                        has_fault(faults, SafetyFault::Watchdog) ||
                        has_fault(faults, SafetyFault::Stall) ||
                        has_fault(faults, SafetyFault::HardLimit);
  if (critical) {
    SafetyFault latchable = SafetyFault::None;
    if (has_fault(faults, SafetyFault::EmergencyStop)) latchable |= SafetyFault::EmergencyStop;
    if (has_fault(faults, SafetyFault::Watchdog)) latchable |= SafetyFault::Watchdog;
    if (has_fault(faults, SafetyFault::Stall)) latchable |= SafetyFault::Stall;
    if (has_fault(faults, SafetyFault::HardLimit)) latchable |= SafetyFault::HardLimit;
    latched_faults_ |= latchable;
  }
  decision.faults = faults | latched_faults_;
  // TTL and malformed requests stop output for this tick, but are not
  // permanent faults; a subsequent valid request may resume safely.
  decision.torque_enabled = !latched() && faults == SafetyFault::None;
  if (decision.torque_enabled && request.valid &&
      request.expires_at_ms > sample.now_ms &&
      request.yaw_deg >= kYawHardMin && request.yaw_deg <= kYawHardMax &&
      request.pitch_deg >= kPitchMin && request.pitch_deg <= kPitchMax) {
    decision.accepted = true;
    decision.yaw_deg = clamp(request.yaw_deg, kYawSoftMin, kYawSoftMax);
    decision.pitch_deg = clamp(request.pitch_deg, kPitchMin, kPitchMax);
  }
  return decision;
}

bool FastSafetyLoop::clear_latched(bool local_confirmation) noexcept {
  if (!local_confirmation) return false;
  latched_faults_ = SafetyFault::None;
  return true;
}

}  // namespace lifeos::runtime
