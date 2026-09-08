#include "lifeos/protocol/protocol.hpp"

#include <cassert>
#include <cstdio>
#include <iostream>

using namespace lifeos::protocol;

namespace {

const char* hello =
    "{\"schema\":\"lifeos.v1\",\"kind\":\"hello\",\"type\":\"hello.host\","
    "\"event_id\":\"hello-1\",\"device_id\":\"stackchan-01\",\"seq\":1,\"ts_ms\":1,\"payload\":{}}";

const char* frame(const char* event_id, std::uint64_t seq, const char* payload) {
  static char line[1024];
  std::snprintf(line, sizeof(line),
                "{\"schema\":\"lifeos.v1\",\"kind\":\"command\","
                "\"type\":\"command.manual_control\",\"event_id\":\"%s\","
                "\"device_id\":\"stackchan-01\",\"seq\":%llu,\"ts_ms\":1,\"payload\":%s}",
                event_id, static_cast<unsigned long long>(seq), payload);
  return line;
}

}  // namespace

int main() {
  Gateway gateway;
  assert(gateway.ingest(hello, 1).accepted);

  const char* input = frame(
      "manual-1", 2,
      "{\"lease_id\":\"lease-1\",\"input_seq\":1,\"action\":\"input\","
      "\"direction\":{\"yaw\":0.75,\"pitch\":-0.2},\"ttl_ms\":400}");
  auto parsed = parse(input);
  assert(parsed);
  ManualControlPayload payload;
  assert(parse_manual_control_payload(parsed.envelope, payload, 10) == ParseError::None);
  assert(payload.action == ManualAction::Input);
  assert(payload.has_direction && payload.yaw == 0.75F && payload.pitch == -0.2F);
  assert(gateway.ingest(input, 10).accepted);

  const char* release = frame(
      "manual-2", 3,
      "{\"lease_id\":\"lease-1\",\"input_seq\":2,\"action\":\"release\",\"ttl_ms\":400}");
  parsed = parse(release);
  assert(parse_manual_control_payload(parsed.envelope, payload, 11) == ParseError::None);
  assert(payload.action == ManualAction::Release && !payload.has_direction);
  assert(gateway.ingest(release, 11).accepted);

  const char* out_of_range = frame(
      "manual-bad-1", 4,
      "{\"lease_id\":\"lease-1\",\"input_seq\":3,\"action\":\"input\","
      "\"direction\":{\"yaw\":1.1,\"pitch\":0},\"ttl_ms\":400}");
  parsed = parse(out_of_range);
  assert(parse_manual_control_payload(parsed.envelope, payload, 12) == ParseError::InvalidPayload);
  assert(!gateway.ingest(out_of_range, 12).accepted);

  const char* bad_ttl = frame(
      "manual-bad-2", 5,
      "{\"lease_id\":\"lease-1\",\"input_seq\":4,\"action\":\"release\",\"ttl_ms\":200}");
  parsed = parse(bad_ttl);
  assert(parse_manual_control_payload(parsed.envelope, payload, 13) == ParseError::InvalidPayload);
  assert(!gateway.ingest(bad_ttl, 13).accepted);

  const char* raw_field = frame(
      "manual-bad-3", 6,
      "{\"lease_id\":\"lease-1\",\"input_seq\":5,\"action\":\"input\","
      "\"direction\":{\"yaw\":0,\"pitch\":0},\"ttl_ms\":400,\"yaw_deg\":10}");
  parsed = parse(raw_field);
  assert(parse_manual_control_payload(parsed.envelope, payload, 14) == ParseError::InvalidPayload);
  assert(!gateway.ingest(raw_field, 14).accepted);
  std::cout << "manual_control_v1 protocol tests passed\n";
}
