#include "lifeos/protocol/protocol.hpp"

#include <algorithm>
#include <charconv>
#include <cmath>
#include <cstdio>
#include <cstdlib>
#include <cstring>
#include <limits>

namespace lifeos::protocol {
namespace {

#ifndef LIFEOS_MANUAL_CONTROL_V1
#define LIFEOS_MANUAL_CONTROL_V1 0
#endif

struct Span {
  std::size_t begin{0};
  std::size_t end{0};
};

bool whitespace(char c) {
  return c == ' ' || c == '\t' || c == '\r' || c == '\n';
}

bool string_end(std::string_view value, std::size_t start, std::size_t& end) {
  if (start >= value.size() || value[start] != '"') return false;
  for (std::size_t cursor = start + 1; cursor < value.size(); ++cursor) {
    if (value[cursor] == '\\') {
      if (++cursor >= value.size()) return false;
      continue;
    }
    if (value[cursor] == '"') {
      end = cursor + 1;
      return true;
    }
  }
  return false;
}

bool value_end(std::string_view value, std::size_t start, std::size_t& end) {
  if (start >= value.size()) return false;
  if (value[start] == '"') return string_end(value, start, end);
  if (value[start] == '{' || value[start] == '[') {
    const char opening = value[start];
    const char closing = opening == '{' ? '}' : ']';
    int depth = 0;
    for (std::size_t cursor = start; cursor < value.size(); ++cursor) {
      if (value[cursor] == '"') {
        std::size_t ignored = 0;
        if (!string_end(value, cursor, ignored)) return false;
        cursor = ignored - 1;
        continue;
      }
      if (value[cursor] == opening) ++depth;
      if (value[cursor] == closing && --depth == 0) {
        end = cursor + 1;
        return true;
      }
    }
    return false;
  }
  end = start;
  while (end < value.size() && value[end] != ',' && value[end] != '}') ++end;
  while (end > start && whitespace(value[end - 1])) --end;
  return end > start;
}

bool field(std::string_view object, std::string_view name, Span& output) {
  if (object.size() < 2 || object.front() != '{' || object.back() != '}') return false;
  bool found = false;
  std::size_t cursor = 1;
  while (cursor < object.size() - 1) {
    while (cursor < object.size() && whitespace(object[cursor])) ++cursor;
    if (cursor >= object.size() - 1) return false;
    std::size_t key_end = 0;
    if (!string_end(object, cursor, key_end)) return false;
    const auto key = object.substr(cursor + 1, key_end - cursor - 2);
    cursor = key_end;
    while (cursor < object.size() && whitespace(object[cursor])) ++cursor;
    if (cursor >= object.size() || object[cursor++] != ':') return false;
    while (cursor < object.size() && whitespace(object[cursor])) ++cursor;
    std::size_t value_end_at = 0;
    if (!value_end(object, cursor, value_end_at)) return false;
    if (key == name) {
      output = {cursor, value_end_at};
      found = true;
    }
    cursor = value_end_at;
    while (cursor < object.size() && whitespace(object[cursor])) ++cursor;
    if (cursor == object.size() - 1) return found;
    if (cursor >= object.size() || object[cursor++] != ',') return false;
    while (cursor < object.size() && whitespace(object[cursor])) ++cursor;
    if (cursor >= object.size() - 1) return false;
  }
  return found;
}

template <std::size_t N>
bool object_keys_allowed(std::string_view object,
                         const std::array<std::string_view, N>& allowed) {
  if (object.size() < 2 || object.front() != '{' || object.back() != '}') return false;
  std::array<std::string_view, N> seen{};
  std::size_t seen_count = 0;
  std::size_t cursor = 1;
  while (cursor < object.size() - 1) {
    while (cursor < object.size() && whitespace(object[cursor])) ++cursor;
    if (cursor >= object.size() - 1) return false;
    std::size_t key_end = 0;
    if (!string_end(object, cursor, key_end)) return false;
    const auto key = object.substr(cursor + 1, key_end - cursor - 2);
    bool accepted = false;
    for (const auto candidate : allowed) {
      if (candidate == key) {
        accepted = true;
        break;
      }
    }
    if (!accepted || seen_count >= N ||
        std::find(seen.begin(), seen.begin() + static_cast<std::ptrdiff_t>(seen_count), key) !=
            seen.begin() + static_cast<std::ptrdiff_t>(seen_count)) {
      return false;
    }
    seen[seen_count++] = key;
    cursor = key_end;
    while (cursor < object.size() && whitespace(object[cursor])) ++cursor;
    if (cursor >= object.size() || object[cursor++] != ':') return false;
    while (cursor < object.size() && whitespace(object[cursor])) ++cursor;
    std::size_t value_end_at = 0;
    if (!value_end(object, cursor, value_end_at)) return false;
    cursor = value_end_at;
    while (cursor < object.size() && whitespace(object[cursor])) ++cursor;
    if (cursor == object.size() - 1) return seen_count > 0;
    if (cursor >= object.size() || object[cursor++] != ',') return false;
  }
  return seen_count > 0;
}

template <std::size_t N>
bool copy_text(std::string_view source, Span span, BoundedText<N>& output) {
  if (span.end <= span.begin + 1 || source[span.begin] != '"' ||
      source[span.end - 1] != '"' || span.end - span.begin - 2 > N) return false;
  const auto text = source.substr(span.begin + 1, span.end - span.begin - 2);
  std::size_t decoded_size = 0;
  for (std::size_t index = 0; index < text.size(); ++index) {
    unsigned char decoded = static_cast<unsigned char>(text[index]);
    if (decoded == '\\') {
      if (++index >= text.size()) return false;
      const char escaped = text[index];
      if (escaped == '"' || escaped == '\\' || escaped == '/') decoded = static_cast<unsigned char>(escaped);
      else if (escaped == 'n') decoded = '\n';
      else if (escaped == 'r') decoded = '\r';
      else if (escaped == 't') decoded = '\t';
      else if (escaped == 'u' && index + 4 < text.size() && text[index + 1] == '0' && text[index + 2] == '0') {
        auto hex_value = [](char c) -> int {
          if (c >= '0' && c <= '9') return c - '0';
          if (c >= 'a' && c <= 'f') return c - 'a' + 10;
          if (c >= 'A' && c <= 'F') return c - 'A' + 10;
          return -1;
        };
        const int high = hex_value(text[index + 3]);
        const int low = hex_value(text[index + 4]);
        if (high < 0 || low < 0) return false;
        decoded = static_cast<unsigned char>((high << 4) | low);
        index += 4;
      } else {
        return false;
      }
    }
    if (decoded < 0x20 && decoded != '\n' && decoded != '\r' && decoded != '\t') return false;
    if (decoded_size >= N) return false;
    output.data[decoded_size++] = static_cast<char>(decoded);
  }
  output.size = decoded_size;
  output.data[output.size] = '\0';
  return true;
}

bool number(std::string_view source, Span span, std::uint64_t& output) {
  const auto value = source.substr(span.begin, span.end - span.begin);
  const auto parsed = std::from_chars(value.data(), value.data() + value.size(), output);
  return parsed.ec == std::errc{} && parsed.ptr == value.data() + value.size();
}

#if LIFEOS_MANUAL_CONTROL_V1
bool decimal(std::string_view source, Span span, float& output) {
  const auto value = source.substr(span.begin, span.end - span.begin);
  if (value.empty() || value.size() >= 32) return false;
  char buffer[32]{};
  std::memcpy(buffer, value.data(), value.size());
  char* end = nullptr;
  output = std::strtof(buffer, &end);
  return end == buffer + value.size() && std::isfinite(output);
}
#endif

bool copy_object(std::string_view source, Span span,
                 BoundedText<kMaxPayloadBytes>& output) {
  if (span.end <= span.begin || span.end - span.begin > kMaxPayloadBytes ||
      source[span.begin] != '{' || source[span.end - 1] != '}') return false;
  const auto value = source.substr(span.begin, span.end - span.begin);
  std::memcpy(output.data.data(), value.data(), value.size());
  output.size = value.size();
  output.data[output.size] = '\0';
  return true;
}

bool parse_kind(std::string_view value, Kind& output) {
  if (value == "event") output = Kind::Event;
  else if (value == "command") output = Kind::Command;
  else if (value == "ack") output = Kind::Ack;
  else if (value == "error") output = Kind::Error;
  else if (value == "hello") output = Kind::Hello;
  else return false;
  return true;
}

bool valid_type(std::string_view value) {
  if (value.empty() || value.size() > kMaxTypeBytes) return false;
  bool segment_has_char = false;
  bool first_segment = true;
  for (std::size_t index = 0; index < value.size(); ++index) {
    const char c = value[index];
    if (c == '.') {
      if (!segment_has_char) return false;
      segment_has_char = false;
      first_segment = false;
      continue;
    }
    const bool lower = c >= 'a' && c <= 'z';
    const bool digit = c >= '0' && c <= '9';
    if (!(lower || digit || c == '_')) return false;
    if (!segment_has_char) {
      if (first_segment && !lower) return false;
    }
    segment_has_char = true;
  }
  return segment_has_char &&
         value.find('.') != std::string_view::npos;
}

const char* kind_name(Kind kind) {
  switch (kind) {
    case Kind::Event: return "event";
    case Kind::Command: return "command";
    case Kind::Ack: return "ack";
    case Kind::Error: return "error";
    case Kind::Hello: return "hello";
  }
  return "";
}

template <std::size_t N>
bool append(char*& cursor, char* end, std::string_view value) {
  (void)N;
  if (static_cast<std::size_t>(end - cursor) < value.size()) return false;
  std::memcpy(cursor, value.data(), value.size());
  cursor += value.size();
  return true;
}

template <typename... Values>
bool append_all(char*& cursor, char* end, Values... values) {
  return (append<0>(cursor, end, values) && ...);
}

template <std::size_t N>
bool quoted(char*& cursor, char* end, std::string_view value) {
  (void)N;
  static constexpr char hex[] = "0123456789abcdef";
  if (!append<0>(cursor, end, "\"")) return false;
  for (const char raw : value) {
    const auto c = static_cast<unsigned char>(raw);
    if (c == '"') {
      if (!append<0>(cursor, end, "\\\"")) return false;
    } else if (c == '\\') {
      if (!append<0>(cursor, end, "\\\\")) return false;
    } else if (c == '\n') {
      if (!append<0>(cursor, end, "\\n")) return false;
    } else if (c == '\r') {
      if (!append<0>(cursor, end, "\\r")) return false;
    } else if (c == '\t') {
      if (!append<0>(cursor, end, "\\t")) return false;
    } else if (c < 0x20) {
      char escaped[6] = {'\\', 'u', '0', '0', hex[c >> 4], hex[c & 0x0f]};
      if (!append<0>(cursor, end, {escaped, sizeof(escaped)})) return false;
    } else {
      const char single[1] = {raw};
      if (!append<0>(cursor, end, std::string_view(single, 1))) return false;
    }
  }
  return append<0>(cursor, end, "\"");
}

template <std::size_t N>
void set_text(BoundedText<N>& output, std::string_view value) {
  const auto size = std::min(value.size(), N);
  std::memcpy(output.data.data(), value.data(), size);
  output.size = size;
  output.data[size] = '\0';
}

}  // namespace

ParseResult parse(std::string_view line) {
  ParseResult result;
  while (!line.empty() && whitespace(line.front())) line.remove_prefix(1);
  while (!line.empty() && whitespace(line.back())) line.remove_suffix(1);
  if (line.size() > kMaxLineBytes) {
    result.error = ParseError::TooLarge;
    return result;
  }
  Span span;
  BoundedText<kMaxTypeBytes> schema;
  if (!field(line, "schema", span) || !copy_text(line, span, schema)) {
    result.error = ParseError::MissingField;
    return result;
  }
  if (schema.view() != kSchema) {
    result.error = ParseError::InvalidSchema;
    return result;
  }
  BoundedText<kMaxTypeBytes> kind_text;
  if (!field(line, "kind", span) || !copy_text(line, span, kind_text) ||
      !parse_kind(kind_text.view(), result.envelope.kind)) {
    result.error = ParseError::UnsupportedKind;
    return result;
  }
  if (!field(line, "type", span) || !copy_text(line, span, result.envelope.type) ||
      !valid_type(result.envelope.type.view())) {
    result.error = ParseError::InvalidField;
    return result;
  }
  if (!field(line, "event_id", span) || !copy_text(line, span, result.envelope.event_id) ||
      result.envelope.event_id.size == 0) {
    result.error = ParseError::MissingField;
    return result;
  }
  const bool has_command_id = field(line, "command_id", span);
  if (has_command_id && !copy_text(line, span, result.envelope.command_id)) {
    result.error = ParseError::InvalidField;
    return result;
  }
  if (field(line, "correlation_id", span) && !copy_text(line, span, result.envelope.correlation_id)) {
    result.error = ParseError::InvalidField;
    return result;
  }
  if (!field(line, "device_id", span) || !copy_text(line, span, result.envelope.device_id) ||
      result.envelope.device_id.size == 0) {
    result.error = ParseError::MissingField;
    return result;
  }
  if (!field(line, "seq", span) || !number(line, span, result.envelope.seq) ||
      !field(line, "ts_ms", span) || !number(line, span, result.envelope.ts_ms) ||
      !field(line, "payload", span) || !copy_object(line, span, result.envelope.payload)) {
    result.error = ParseError::InvalidField;
    return result;
  }
  return result;
}

ParseError parse_manual_control_payload(const Envelope& envelope,
                                        ManualControlPayload& output,
                                        std::uint64_t now_ms) {
#if !LIFEOS_MANUAL_CONTROL_V1
  (void)envelope;
  (void)output;
  (void)now_ms;
  return ParseError::UnsupportedKind;
#else
  if (envelope.kind != Kind::Command || envelope.type.view() != "command.manual_control") {
    return ParseError::UnsupportedKind;
  }
  const auto payload = envelope.payload.view();
  if (!object_keys_allowed(
          payload,
          std::array<std::string_view, 7>{"lease_id", "input_seq", "action", "direction", "ttl_ms",
                                          "video_frame_id", "video_capture_ts_ms"})) {
    return ParseError::InvalidPayload;
  }
  Span span;
  if (!field(payload, "lease_id", span) || !copy_text(payload, span, output.lease_id) ||
      output.lease_id.size == 0 || !field(payload, "input_seq", span) ||
      !number(payload, span, output.input_seq) || output.input_seq == 0 ||
      !field(payload, "ttl_ms", span) || !number(payload, span, output.ttl_ms) ||
      output.ttl_ms < 300 || output.ttl_ms > 500) {
    return ParseError::InvalidPayload;
  }
  BoundedText<16> action;
  if (!field(payload, "action", span) || !copy_text(payload, span, action)) {
    return ParseError::InvalidPayload;
  }
  if (action.view() == "input") {
    output.action = ManualAction::Input;
    if (!field(payload, "video_frame_id", span) ||
        !copy_text(payload, span, output.video_frame_id) ||
        output.video_frame_id.size == 0 ||
        !field(payload, "video_capture_ts_ms", span) ||
        !number(payload, span, output.video_capture_ts_ms)) {
      return ParseError::InvalidPayload;
    }
    output.has_video_proof = true;
    if (!field(payload, "direction", span) || span.end <= span.begin + 1 ||
        payload[span.begin] != '{' || payload[span.end - 1] != '}') {
      return ParseError::InvalidPayload;
    }
    const auto direction = payload.substr(span.begin, span.end - span.begin);
    if (!object_keys_allowed(direction, std::array<std::string_view, 2>{"yaw", "pitch"})) {
      return ParseError::InvalidPayload;
    }
    Span yaw_span{}, pitch_span{};
    if (!field(direction, "yaw", yaw_span) || !decimal(direction, yaw_span, output.yaw) ||
        !field(direction, "pitch", pitch_span) || !decimal(direction, pitch_span, output.pitch) ||
        output.yaw < -1.0F || output.yaw > 1.0F || output.pitch < -1.0F || output.pitch > 1.0F) {
      return ParseError::InvalidPayload;
    }
    output.has_direction = true;
    return ParseError::None;
  }
  if (action.view() == "release") {
    output.action = ManualAction::Release;
    Span direction_span{};
    if (field(payload, "direction", direction_span) ||
        field(payload, "video_frame_id", direction_span) ||
        field(payload, "video_capture_ts_ms", direction_span)) {
      return ParseError::InvalidPayload;
    }
    output.has_direction = false;
    output.has_video_proof = false;
    (void)now_ms;
    return ParseError::None;
  }
  return ParseError::InvalidPayload;
#endif
}

ParseError parse_camera_preview_payload(const Envelope& envelope,
                                        CameraPreviewPayload& output) {
  if (envelope.kind != Kind::Command || envelope.type.view() != "command.camera_preview") {
    return ParseError::UnsupportedKind;
  }
  const auto payload = envelope.payload.view();
  if (!object_keys_allowed(
          payload,
          std::array<std::string_view, 3>{"action", "fps", "duration_ms"})) {
    return ParseError::InvalidPayload;
  }
  Span span;
  if (!field(payload, "action", span) || !copy_text(payload, span, output.action)) {
    return ParseError::InvalidPayload;
  }
  if (output.action.view() == "stop") {
    if (field(payload, "fps", span) || field(payload, "duration_ms", span)) {
      return ParseError::InvalidPayload;
    }
    return ParseError::None;
  }
  if (output.action.view() != "start" ||
      !field(payload, "fps", span) || !number(payload, span, output.fps) ||
      output.fps < 1 || output.fps > 10 ||
      !field(payload, "duration_ms", span) || !number(payload, span, output.duration_ms) ||
      (output.duration_ms != 0 &&
       (output.duration_ms < 1000 || output.duration_ms > 30000))) {
    return ParseError::InvalidPayload;
  }
  return ParseError::None;
}

bool serialize(const Envelope& envelope, char* output, std::size_t capacity,
               std::size_t& written) {
  written = 0;
  if (!output || envelope.event_id.size == 0 || envelope.type.size == 0 ||
      envelope.device_id.size == 0 || envelope.payload.size == 0 ||
      !valid_type(envelope.type.view())) return false;
  char* cursor = output;
  char* end = output + std::min(capacity, kMaxLineBytes + 1);
  // One buffer per number: two string_views into shared scratch would alias,
  // and the later to_chars overwrites the earlier view's digits before they
  // are appended (observed on hardware as seq emitting ts_ms leading digits).
  char seq_text[24];
  char ts_text[24];
  auto number_text = [](char* buffer, std::size_t capacity, std::uint64_t value) {
    const auto converted = std::to_chars(buffer, buffer + capacity, value);
    return std::string_view(buffer, static_cast<std::size_t>(converted.ptr - buffer));
  };
  if (!append_all(cursor, end, "{\"schema\":\"lifeos.v1\",\"kind\":\"",
                  kind_name(envelope.kind), "\",\"type\":") ||
      !quoted<0>(cursor, end, envelope.type.view()) ||
      !append_all(cursor, end, ",\"event_id\":") ||
      !quoted<0>(cursor, end, envelope.event_id.view())) return false;
  if (envelope.command_id.size &&
      (!append_all(cursor, end, ",\"command_id\":") ||
       !quoted<0>(cursor, end, envelope.command_id.view()))) return false;
  if (envelope.correlation_id.size &&
      (!append_all(cursor, end, ",\"correlation_id\":") ||
       !quoted<0>(cursor, end, envelope.correlation_id.view()))) return false;
  if (!append_all(cursor, end, ",\"device_id\":") ||
      !quoted<0>(cursor, end, envelope.device_id.view()) ||
      !append_all(cursor, end, ",\"seq\":", number_text(seq_text, sizeof(seq_text), envelope.seq),
                  ",\"ts_ms\":", number_text(ts_text, sizeof(ts_text), envelope.ts_ms),
                  ",\"payload\":", envelope.payload.view(), "}\n")) return false;
  written = static_cast<std::size_t>(cursor - output);
  return written <= capacity;
}

bool SequenceValidator::accept(std::uint64_t sequence) {
  if (initialized_ && (last_ == std::numeric_limits<std::uint64_t>::max() ||
                       sequence == 0 || sequence != last_ + 1)) return false;
  initialized_ = true;
  last_ = sequence;
  return true;
}

bool ttl_valid(std::uint64_t issued_at_ms, std::uint64_t expires_at_ms,
               std::uint64_t now_ms, std::uint64_t max_age_ms) {
  if (expires_at_ms <= issued_at_ms || now_ms >= expires_at_ms || now_ms < issued_at_ms) return false;
  return now_ms - issued_at_ms <= max_age_ms;
}

bool control_action_allowed(std::string_view action) {
  return action == "pause" || action == "resume" || action == "home" ||
         action == "status" || action == "preflight" || action == "clear_fault";
}

bool json_true(std::string_view object, std::string_view name) {
  Span value;
  return field(object, name, value) && object.substr(value.begin, value.end - value.begin) == "true";
}

ParseError validate_command_payload(const Envelope& envelope) {
  const auto payload = envelope.payload.view();
  if (envelope.type.view() == "command.emergency_stop") return ParseError::None;
  if (envelope.type.view() == "command.control") {
    Span action_span;
    BoundedText<32> action;
    if (!field(payload, "action", action_span) || !copy_text(payload, action_span, action) ||
        !control_action_allowed(action.view())) {
      return ParseError::UnsupportedKind;
    }
    if (action.view() == "clear_fault" && !json_true(payload, "local_confirmation")) {
      return ParseError::InvalidPayload;
    }
    return ParseError::None;
  }
  if (envelope.type.view() == "command.intent" ||
      envelope.type.view() == "command.maintenance_motion" ||
      envelope.type.view() == "command.maintenance_fault") {
    return ParseError::None;
  }
  if (envelope.type.view() == "command.camera_preview") {
    CameraPreviewPayload ignored;
    return parse_camera_preview_payload(envelope, ignored);
  }
#if LIFEOS_MANUAL_CONTROL_V1
  if (envelope.type.view() == "command.manual_control") {
    ManualControlPayload ignored;
    return parse_manual_control_payload(envelope, ignored, 0);
  }
#endif
  return ParseError::UnsupportedKind;
}

ParseError DuplicateCommandGuard::remember(std::string_view id) {
  if (id.empty() || id.size() > kMaxIdBytes) return ParseError::InvalidField;
  for (std::size_t index = 0; index < count_; ++index) {
    if (ids_[index].view() == id) return ParseError::DuplicateCommand;
  }
  auto& slot = ids_[count_ < kCapacity ? count_ : next_];
  std::memcpy(slot.data.data(), id.data(), id.size());
  slot.size = id.size();
  slot.data[slot.size] = '\0';
  if (count_ < kCapacity) ++count_;
  else next_ = (next_ + 1) % kCapacity;
  return ParseError::None;
}

bool DuplicateCommandGuard::contains(std::string_view id) const {
  for (std::size_t index = 0; index < count_; ++index) {
    if (ids_[index].view() == id) return true;
  }
  return false;
}

void DuplicateCommandGuard::reset() {
  count_ = 0;
  next_ = 0;
}

Envelope make_ack(const Envelope& command, AckStatus status, bool idempotent,
                  std::uint64_t sequence, std::uint64_t now_ms) {
  Envelope result;
  result.kind = Kind::Ack;
  set_text(result.type, "ack.command");
  const int id_size = std::snprintf(result.event_id.data.data(), result.event_id.data.size(),
                                    "ack-%llu", static_cast<unsigned long long>(sequence));
  result.event_id.size = id_size > 0 ? static_cast<std::size_t>(id_size) : 0;
  set_text(result.correlation_id, command.event_id.view());
  result.device_id = command.device_id;
  result.seq = sequence;
  result.ts_ms = now_ms;
  static constexpr const char* statuses[] = {"accepted", "clamped", "completed", "duplicate", "rejected"};
  char payload[128];
  std::snprintf(payload, sizeof(payload), "{\"status\":\"%s\",\"idempotent\":%s}",
                statuses[static_cast<int>(status)], idempotent ? "true" : "false");
  set_text(result.payload, payload);
  return result;
}

Envelope make_error(const Envelope& source, ErrorCode code,
                    std::uint64_t sequence, std::uint64_t now_ms,
                    std::string_view detail) {
  static constexpr const char* names[] = {"invalid_schema", "unsupported", "unauthorized", "expired", "busy", "safety_blocked", "fault_latched", "rate_limited", "internal"};
  Envelope result = make_ack(source, AckStatus::Rejected, false, sequence, now_ms);
  result.kind = Kind::Error;
  set_text(result.type, "error.protocol");
  char payload[160];
  if (detail.empty()) {
    std::snprintf(payload, sizeof(payload), "{\"code\":\"%s\"}", names[static_cast<int>(code)]);
  } else {
    std::snprintf(payload, sizeof(payload), "{\"code\":\"%s\",\"detail\":\"%.*s\"}",
                  names[static_cast<int>(code)],
                  static_cast<int>(detail.size()), detail.data());
  }
  set_text(result.payload, payload);
  return result;
}

GatewayResult Gateway::ingest(std::string_view line, std::uint64_t now_ms) {
  GatewayResult result;
  const auto parsed = parse(line);
  if (!parsed) {
    result.error = parsed.error;
    return result;
  }
  const auto& envelope = parsed.envelope;
  result.envelope = envelope;
  if (hello_complete_ && envelope.device_id.view() != device_id_.view()) {
    result.error = ParseError::InvalidField;
    return result;
  }
  if (!hello_complete_ && envelope.kind != Kind::Hello) {
    result.error = ParseError::InvalidSchema;
    return result;
  }
  // A structurally valid hello always (re)establishes the session. The
  // protocol hands sequence negotiation to hello after a reconnect, and a
  // host that lands on a live gateway — the CDC open reset is not
  // guaranteed — has no other way back in. Only session bookkeeping is
  // cleared; safety state is untouched.
  if (envelope.kind == Kind::Hello && envelope.type.view() != "hello.device" &&
      envelope.type.view() != "hello.host") {
    result.error = ParseError::UnsupportedKind;
    return result;
  }
  if (envelope.kind == Kind::Hello) {
    reset_session();
  }
  if (duplicate_.contains(envelope.event_id.view())) {
    result.accepted = true;
    result.duplicate = true;
    if (envelope.kind == Kind::Command) {
      result.response = make_ack(envelope, AckStatus::Duplicate, true, envelope.seq + 1, now_ms);
    }
    return result;
  }
  if (!sequence_.accept(envelope.seq)) {
    result.error = ParseError::SequenceRejected;
    return result;
  }
  if (envelope.kind == Kind::Hello) {
    device_id_ = envelope.device_id;
    hello_complete_ = true;
  }
  if (envelope.kind == Kind::Command) {
    Span issued_span{}, expires_span{};
    const auto payload = envelope.payload.view();
    std::uint64_t issued = 0, expires = 0;
    const bool has_issued = field(payload, "issued_at_ms", issued_span);
    const bool has_expires = field(payload, "expires_at_ms", expires_span);
    if (has_issued || has_expires) {
      if (!has_issued || !has_expires || !number(payload, issued_span, issued) ||
          !number(payload, expires_span, expires) || !ttl_valid(issued, expires, now_ms)) {
        result.error = ParseError::InvalidField;
        return result;
      }
    }
    const auto payload_error = validate_command_payload(envelope);
    if (payload_error != ParseError::None) {
      result.error = payload_error;
      return result;
    }
  }
  const auto remembered = duplicate_.remember(envelope.event_id.view());
  if (remembered != ParseError::None) {
    result.error = remembered;
    return result;
  }
  result.accepted = true;
  if (envelope.kind == Kind::Command) {
    result.response = make_ack(envelope, AckStatus::Accepted, false,
                               envelope.seq + 1, now_ms);
  }
  return result;
}

void Gateway::reset_session() {
  hello_complete_ = false;
  device_id_ = {};
  sequence_.reset();
  duplicate_.reset();
}

const char* parse_error_name(ParseError error) {
  static constexpr const char* names[] = {"none", "too_large", "invalid_json", "invalid_schema", "missing_field", "invalid_field", "unsupported_kind", "invalid_payload", "sequence_rejected", "duplicate_command", "queue_full"};
  return names[static_cast<int>(error)];
}

}  // namespace lifeos::protocol
