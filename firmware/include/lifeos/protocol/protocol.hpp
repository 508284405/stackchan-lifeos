#pragma once

#include <array>
#include <cstddef>
#include <cstdint>
#include <string_view>

namespace lifeos::protocol {

inline constexpr std::string_view kSchema = "lifeos.v1";
inline constexpr std::size_t kMaxLineBytes = 16u * 1024u;
inline constexpr std::size_t kMaxIdBytes = 96;
inline constexpr std::size_t kMaxTypeBytes = 64;
inline constexpr std::size_t kMaxDeviceIdBytes = 64;
inline constexpr std::size_t kMaxPayloadBytes = 8u * 1024u;
inline constexpr std::size_t kMaxErrorBytes = 64;

enum class Kind : std::uint8_t { Event, Command, Ack, Error, Hello };
enum class ParseError : std::uint8_t {
  None, TooLarge, InvalidJson, InvalidSchema, MissingField, InvalidField,
  UnsupportedKind, InvalidPayload, SequenceRejected, DuplicateCommand, QueueFull,
};
enum class AckStatus : std::uint8_t {
  Accepted, Clamped, Completed, Duplicate, Rejected,
};
enum class ErrorCode : std::uint8_t {
  InvalidSchema, Unsupported, Unauthorized, Expired, Busy, SafetyBlocked,
  FaultLatched, RateLimited, Internal,
};

template <std::size_t N>
struct BoundedText {
  std::array<char, N + 1> data{};
  std::size_t size{0};
  std::string_view view() const { return {data.data(), size}; }
};

struct Envelope {
  Kind kind{Kind::Event};
  BoundedText<kMaxTypeBytes> type;
  BoundedText<kMaxIdBytes> event_id;
  BoundedText<kMaxIdBytes> command_id;
  BoundedText<kMaxIdBytes> correlation_id;
  BoundedText<kMaxDeviceIdBytes> device_id;
  std::uint64_t seq{0};
  std::uint64_t ts_ms{0};
  BoundedText<kMaxPayloadBytes> payload;
};

struct ParseResult {
  ParseError error{ParseError::None};
  Envelope envelope;
  explicit operator bool() const { return error == ParseError::None; }
};

struct GatewayResult {
  ParseError error{ParseError::None};
  bool accepted{false};
  bool duplicate{false};
  Envelope envelope{};
  Envelope response{};
};

ParseResult parse(std::string_view line);
bool serialize(const Envelope& envelope, char* output, std::size_t capacity,
               std::size_t& written);
const char* parse_error_name(ParseError error);

// Uses the local monotonic clock value for expiry. Wall-clock timestamps are
// metadata only and must not be used to bypass this check.
bool ttl_valid(std::uint64_t issued_at_ms, std::uint64_t expires_at_ms,
               std::uint64_t now_ms, std::uint64_t max_age_ms = 1500);

class LineFramer final {
 public:
  template <typename Callback>
  void feed(std::string_view bytes, Callback&& callback) {
    for (const char byte : bytes) {
      if (byte == '\n') {
        if (discarding_) reset();
        else { callback(parse({buffer_.data(), size_})); reset(); }
        continue;
      }
      if (discarding_) continue;
      if (size_ == kMaxLineBytes) { discarding_ = true; continue; }
      buffer_[size_++] = byte;
    }
  }
  bool discarding() const { return discarding_; }
  std::size_t buffered() const { return size_; }
 private:
  void reset() { size_ = 0; discarding_ = false; }
  std::array<char, kMaxLineBytes> buffer_{};
  std::size_t size_{0};
  bool discarding_{false};
};

class SequenceValidator final {
 public:
  bool accept(std::uint64_t sequence);
  void reset() { initialized_ = false; last_ = 0; }
 private:
  bool initialized_{false};
  std::uint64_t last_{0};
};

class DuplicateCommandGuard final {
 public:
  static constexpr std::size_t kCapacity = 16;
  bool contains(std::string_view id) const;
  ParseError remember(std::string_view command_id);
  void reset();
 private:
  std::array<BoundedText<kMaxIdBytes>, kCapacity> ids_{};
  std::size_t count_{0};
  std::size_t next_{0};
};

/** Stateful session gate placed after parsing and before command dispatch. */
class Gateway final {
 public:
  GatewayResult ingest(std::string_view line, std::uint64_t now_ms);
  void reset_session();
  bool hello_complete() const { return hello_complete_; }
  std::string_view device_id() const { return device_id_.view(); }

 private:
  bool hello_complete_{false};
  BoundedText<kMaxDeviceIdBytes> device_id_{};
  SequenceValidator sequence_{};
  DuplicateCommandGuard duplicate_{};
};

Envelope make_ack(const Envelope& command, AckStatus status, bool idempotent,
                  std::uint64_t sequence, std::uint64_t now_ms);
// `detail` carries a machine-readable reason token (e.g. parse_error_name)
// next to the coarse `code` so hosts can distinguish rejection causes.
Envelope make_error(const Envelope& source, ErrorCode code,
                    std::uint64_t sequence, std::uint64_t now_ms,
                    std::string_view detail = {});

template <typename T, std::size_t N>
class BoundedQueue final {
 public:
  bool push(const T& item) {
    if (size_ == N) return false;
    items_[tail_] = item;
    tail_ = (tail_ + 1) % N;
    ++size_;
    return true;
  }
  bool pop(T& item) {
    if (size_ == 0) return false;
    item = items_[head_];
    head_ = (head_ + 1) % N;
    --size_;
    return true;
  }
  std::size_t size() const { return size_; }
  bool full() const { return size_ == N; }
 private:
  static_assert(N > 0, "bounded queues must have positive capacity");
  std::array<T, N> items_{};
  std::size_t head_{0}, tail_{0}, size_{0};
};

template <typename T>
class ObservationMailbox final {
 public:
  void publish(const T& value) { value_ = value; present_ = true; }
  bool consume(T& value) {
    if (!present_) return false;
    value = value_;
    present_ = false;
    return true;
  }
  bool present() const { return present_; }
 private:
  T value_{};
  bool present_{false};
};

}  // namespace lifeos::protocol
