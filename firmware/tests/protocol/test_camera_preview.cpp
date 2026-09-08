#include "lifeos/protocol/protocol.hpp"

#include <cassert>
#include <iostream>

using namespace lifeos::protocol;

namespace {

const char* hello =
    "{\"schema\":\"lifeos.v1\",\"kind\":\"hello\",\"type\":\"hello.host\","
    "\"event_id\":\"hello-1\",\"device_id\":\"stackchan-01\",\"seq\":1,"
    "\"ts_ms\":1,\"payload\":{}}";

const char* start =
    "{\"schema\":\"lifeos.v1\",\"kind\":\"command\","
    "\"type\":\"command.camera_preview\",\"event_id\":\"camera-1\","
    "\"device_id\":\"stackchan-01\",\"seq\":2,\"ts_ms\":2,"
    "\"payload\":{\"action\":\"start\",\"fps\":10,\"duration_ms\":0}}";

const char* stop =
    "{\"schema\":\"lifeos.v1\",\"kind\":\"command\","
    "\"type\":\"command.camera_preview\",\"event_id\":\"camera-2\","
    "\"device_id\":\"stackchan-01\",\"seq\":3,\"ts_ms\":3,"
    "\"payload\":{\"action\":\"stop\"}}";

}  // namespace

int main() {
  Gateway gateway;
  assert(gateway.ingest(hello, 1).accepted);

  auto parsed = parse(start);
  assert(parsed);
  CameraPreviewPayload payload;
  assert(parse_camera_preview_payload(parsed.envelope, payload) == ParseError::None);
  assert(payload.action.view() == "start");
  assert(payload.fps == 10 && payload.duration_ms == 0);
  assert(gateway.ingest(start, 2).accepted);

  parsed = parse(stop);
  assert(parsed);
  assert(parse_camera_preview_payload(parsed.envelope, payload) == ParseError::None);
  assert(payload.action.view() == "stop");
  assert(gateway.ingest(stop, 3).accepted);

  const char* bad_fps =
      "{\"schema\":\"lifeos.v1\",\"kind\":\"command\","
      "\"type\":\"command.camera_preview\",\"event_id\":\"camera-bad\","
      "\"device_id\":\"stackchan-01\",\"seq\":4,\"ts_ms\":4,"
      "\"payload\":{\"action\":\"start\",\"fps\":11,\"duration_ms\":0}}";
  parsed = parse(bad_fps);
  assert(parsed);
  assert(parse_camera_preview_payload(parsed.envelope, payload) == ParseError::InvalidPayload);
  assert(!gateway.ingest(bad_fps, 4).accepted);

  std::cout << "camera preview protocol tests passed\n";
}
