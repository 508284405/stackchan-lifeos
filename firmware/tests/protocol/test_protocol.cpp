#include "lifeos/protocol/protocol.hpp"

#include <cassert>
#include <cstdio>
#include <cstring>
#include <iostream>
#include <string>

using namespace lifeos::protocol;

int main() {
  constexpr const char* command =
      "{\"schema\":\"lifeos.v1\",\"kind\":\"command\",\"type\":\"command.control\","
      "\"event_id\":\"evt-1\",\"command_id\":\"cmd-1\",\"device_id\":\"stackchan-01\",\"seq\":7,\"ts_ms\":1000,"
      "\"payload\":{\"action\":\"pause\"}}\n";
  auto parsed = parse(command);
  assert(parsed && parsed.envelope.kind == Kind::Command);
  assert(parsed.envelope.command_id.view() == "cmd-1");
  assert(parsed.envelope.payload.view() == "{\"action\":\"pause\"}");

  auto bad_schema = parse("{\"schema\":\"lifeos.v2\"}");
  assert(!bad_schema && bad_schema.error == ParseError::InvalidSchema);
  auto malformed = parse(
      "{\"schema\":\"lifeos.v1\",\"kind\":\"event\",\"type\":\"x.y\","
      "\"event_id\":\"e\",\"device_id\":\"stackchan-01\",\"seq\":1,\"ts_ms\":1,\"payload\":{},}");
  assert(!malformed);
  std::string oversized(kMaxLineBytes + 1, 'x');
  assert(parse(oversized).error == ParseError::TooLarge);
  auto no_command_id = parse(
      "{\"schema\":\"lifeos.v1\",\"kind\":\"command\",\"type\":\"x.y\","
      "\"event_id\":\"e\",\"device_id\":\"stackchan-01\",\"seq\":1,\"ts_ms\":1,\"payload\":{}}");
  assert(no_command_id && no_command_id.envelope.event_id.view() == "e");
  auto invalid_type = parse(
      "{\"schema\":\"lifeos.v1\",\"kind\":\"event\",\"type\":\"BadType\","
      "\"event_id\":\"e2\",\"device_id\":\"stackchan-01\",\"seq\":1,\"ts_ms\":1,\"payload\":{}}");
  assert(!invalid_type && invalid_type.error == ParseError::InvalidField);

  SequenceValidator sequence;
  assert(sequence.accept(10));
  assert(sequence.accept(11));
  assert(!sequence.accept(11));
  assert(!sequence.accept(13));
  sequence.reset();
  assert(sequence.accept(0));
  assert(ttl_valid(1000, 2000, 1500));
  assert(!ttl_valid(1000, 2000, 2000));
  assert(!ttl_valid(1000, 3000, 2601));
  assert(!ttl_valid(2000, 1000, 2000));

  DuplicateCommandGuard duplicates;
  assert(duplicates.remember("cmd-1") == ParseError::None);
  assert(duplicates.remember("cmd-1") == ParseError::DuplicateCommand);
  for (int i = 0; i < 15; ++i) {
    char id[16]; std::snprintf(id, sizeof(id), "cmd-%d", i + 2);
    assert(duplicates.remember(id) == ParseError::None);
  }
  assert(duplicates.remember("cmd-overflow") == ParseError::None);

  Envelope ack = make_ack(parsed.envelope, AckStatus::Accepted, false, 8, 1001);
  char output[kMaxLineBytes + 1]{}; std::size_t written = 0;
  assert(serialize(ack, output, sizeof(output), written));
  auto roundtrip = parse({output, written});
  assert(roundtrip && roundtrip.envelope.kind == Kind::Ack);
  assert(roundtrip.envelope.event_id.view() != "evt-1");
  assert(roundtrip.envelope.correlation_id.view() == "evt-1");
  auto error = make_error(parsed.envelope, ErrorCode::Expired, 9, 1002);
  assert(serialize(error, output, sizeof(output), written));
  assert(parse({output, written}).envelope.kind == Kind::Error);
  Envelope escaped = parsed.envelope;
  const char escaped_id[] = "evt-\\\"quoted";
  escaped.event_id.size = sizeof(escaped_id) - 1;
  std::memcpy(escaped.event_id.data.data(), escaped_id, escaped.event_id.size);
  assert(serialize(escaped, output, sizeof(output), written));
  auto escaped_roundtrip = parse({output, written});
  assert(escaped_roundtrip && escaped_roundtrip.envelope.event_id.view() == escaped_id);

  BoundedQueue<int, 2> queue;
  assert(queue.push(1) && queue.push(2) && !queue.push(3));
  int value = 0; assert(queue.pop(value) && value == 1);
  ObservationMailbox<int> observations;
  observations.publish(1); observations.publish(2);
  assert(observations.consume(value) && value == 2);
  assert(!observations.consume(value));
  LineFramer framer;
  int frames = 0;
  ParseError last_error = ParseError::None;
  auto collect = [&](ParseResult result) { ++frames; last_error = result.error; };
  framer.feed(std::string_view(command).substr(0, 19), collect);
  assert(framer.buffered() == 19 && frames == 0);
  framer.feed(std::string_view(command).substr(19), collect);
  assert(frames == 1 && last_error == ParseError::None);
  framer.feed("{}\n", collect);
  assert(frames == 2 && last_error == ParseError::MissingField);
  std::string overlong(kMaxLineBytes + 1, 'x');
  overlong.push_back('\n');
  overlong += command;
  framer.feed(overlong, collect);
  assert(frames == 3 && last_error == ParseError::None && !framer.discarding());

  Gateway gateway;
  auto before_hello = gateway.ingest(command, 1000);
  assert(!before_hello.accepted && before_hello.error == ParseError::InvalidSchema);
  const char* hello =
      "{\"schema\":\"lifeos.v1\",\"kind\":\"hello\",\"type\":\"hello.host\","
      "\"event_id\":\"hello-1\",\"device_id\":\"stackchan-01\",\"seq\":6,\"ts_ms\":1000,\"payload\":{}}";
  assert(gateway.ingest(hello, 1000).accepted);
  auto accepted = gateway.ingest(command, 1000);
  assert(accepted.accepted && !accepted.duplicate);
  auto duplicate = gateway.ingest(command, 1000);
  assert(duplicate.accepted && duplicate.duplicate);
  gateway.reset_session();
  assert(!gateway.hello_complete());
  std::cout << "lifeos protocol tests passed\n";
}
