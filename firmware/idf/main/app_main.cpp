#include <algorithm>
#include <atomic>
#include <cmath>
#include <cstdio>
#include <cstdlib>
#include <cstring>
#include <iterator>
#include <string_view>

#include "driver/usb_serial_jtag.h"
#include "driver/usb_serial_jtag_vfs.h"
#include "esp_heap_caps.h"
#include "esp_mac.h"
#include "esp_timer.h"
#include "freertos/FreeRTOS.h"
#include "freertos/semphr.h"
#include "freertos/task.h"
#include "lifeos/hal/stackchan/stackchan.hpp"
#include "lifeos/protocol/protocol.hpp"
#include "lifeos/runtime/manual_control.hpp"
#include "lifeos/runtime/runtime.hpp"
#include "lifeos/runtime/servo_io.hpp"

#ifndef LIFEOS_MANUAL_CONTROL_V1
#define LIFEOS_MANUAL_CONTROL_V1 0
#endif

// =============================================================================
// app_main.cpp —— StackChan LifeOS 固件主程序（ESP32-S3 机身/反射层）
//
// 整体结构：
//   1. 常量与全局缓冲
//   2. 工具函数（时间 / 日志 / 序列化 / 简易 JSON 解析）
//   3. StackChanServoLink —— ServoIoCore 与 SCS/Py32 硬件的目标适配器
//   4. TargetRuntime —— 运行时核心（CGraph 行为图 + FastSafetyLoop + 舵机 I/O）
//   5. 报文应答（hello / status / error / 错误码映射）
//   6. 三个常驻任务（行为图 / 舵机 I/O / 安全回路）
//   7. app_main：启动流程与 USB 主命令循环
//
// 约定：关键流程用 ① ② ③ … 序号注释标出
// （见 begin / handle / graph_tick / safety_tick / app_main 等）。
// =============================================================================

namespace {

using lifeos::hal::ServoPosition;
using lifeos::hal::stackchan::StackChanBoard;
using CameraJpegFrame = lifeos::hal::stackchan::StackChanCamera::JpegFrame;
using lifeos::runtime::ServoIoCore;
using lifeos::runtime::ServoIoError;
using lifeos::runtime::ServoIoState;
using lifeos::runtime::ManualControlResultCode;
using lifeos::runtime::ManualControlState;

// ---- 全局常量 ----
constexpr const char* kDeviceId = "stackchan-01";          // 设备 ID（hello 上报）
constexpr const char* kFirmware = "lifeos-phase1-0.5.0";
constexpr std::uint64_t kHostTimeoutMs = 1500;             // 主机超时：无消息即判定失联(ms)
constexpr std::uint64_t kGraphPeriodMs = 100;              // 行为图节拍周期(ms)
constexpr std::uint64_t kSafetyPeriodMs = 20;              // 快速安全回路节拍周期(ms)
// 运动 TTL 必须覆盖"轻唤醒"路径（VM 稳定 + 健康检查），并在故障恢复后覆盖
// 完整自检；堵转监视器保护的是运动本身，因此 TTL 是运动截止时间，
// 而不是 ACK 截止时间。
constexpr std::uint64_t kMaintenanceMotionTtlMs = 2500;
constexpr std::uint64_t kHomeTtlMs = 2500;
constexpr std::size_t kCameraChunkBytes = 5u * 1024u;
constexpr std::uint64_t kCameraDefaultIntervalMs = 100;
// RGB565 -> JPEG conversion on the reference GC0308 takes about 150 ms at
// QVGA. Do not let the camera task busy-retry at a nominal 10 fps while the
// encoder is still catching up; that would starve control/health traffic.
constexpr std::uint64_t kCameraColorMinimumIntervalMs = 150;
constexpr std::uint32_t kCameraWriteTimeoutMs = 20;
constexpr std::uint32_t kHealthWriteTimeoutMs = 20;
constexpr std::uint32_t kUsbTxBufferBytes = 16u * 1024u;
// Diagnostic lines are best-effort.  A servo feedback/transition log must
// never hold the shared protocol writer or the high-priority ServoIoTask when
// the USB console ring is busy; ACKs, health, and camera frames have their
// own bounded protocol path below.
constexpr TickType_t kDiagnosticWriteWaitTicks = 0;

// ---- 全局缓冲与板级对象 ----
char input_line[lifeos::protocol::kMaxLineBytes + 2]{};  // USB 行接收缓冲（每行一个 JSONL 报文）
char output_line[lifeos::protocol::kMaxLineBytes + 2]{}; // 报文序列化输出缓冲
std::uint64_t output_sequence = 0;                       // 出站序号（单调递增）
StaticSemaphore_t output_mutex_storage{};
SemaphoreHandle_t output_mutex{nullptr};
std::uint64_t camera_frame_sequence = 0;
StackChanBoard board{};                                  // 板级硬件封装（舵机/扩展板/IMU/触摸/显示…）

// 主机停止读取且 TX 环写满时，向 USB-Serial/JTAG vfs 写入会阻塞，
// 从而冻结任何持有 stdout 锁的任务（连带网关 ACK 路径）。
// 因此诊断输出被门控：除非主机在线且近 kConsoleIdleDropMs 内发送过数据，
// 否则丢弃；协议 ACK 从不被门控，因为它们只出现在活跃的主机交互中。
constexpr std::uint64_t kConsoleIdleDropMs = 4000;
std::atomic<std::uint64_t> last_console_rx_ms{0};

// 当前单调时间（毫秒），作为全固件统一时间基准
std::uint64_t now_ms() {
  return static_cast<std::uint64_t>(esp_timer_get_time() / 1000);
}

// 诊断日志出口：USB 未连接或主机 4s 无通信则丢弃，避免阻塞 stdout 锁
void hil_log(const char* format, ...) {
  if (!usb_serial_jtag_is_connected()) return;
  const auto last_rx = last_console_rx_ms.load(std::memory_order_relaxed);
  const auto current = now_ms();
  if (last_rx != 0 && current > last_rx + kConsoleIdleDropMs) return;
  char line[256];
  va_list args;
  va_start(args, format);
  std::vsnprintf(line, sizeof(line), format, args);
  va_end(args);
  const bool locked = output_mutex == nullptr ||
                      xSemaphoreTake(output_mutex, kDiagnosticWriteWaitTicks) == pdTRUE;
  if (!locked) return;
  const auto length = std::strlen(line);
  if (length != 0) {
    (void)usb_serial_jtag_write_bytes(line, length, kDiagnosticWriteWaitTicks);
  }
  if (output_mutex != nullptr) xSemaphoreGive(output_mutex);
}

// 将字符串拷入定长字段（自动截断并以 NUL 终止）
template <typename Field>
void set_text(Field& field, std::string_view value) {
  const auto size = std::min<std::size_t>(value.size(), field.data.size() - 1);
  std::memcpy(field.data.data(), value.data(), size);
  field.size = size;
  field.data[size] = '\0';
}

// 发送报文：自动填充出站序号与时间戳。
// 媒体报文使用有界的 USB ring 写入；TX 拥塞时丢弃当前媒体报文，不能
// 无限等待并占住 output_mutex，否则会连带阻塞控制 ACK。
bool emit_impl(lifeos::protocol::Envelope& envelope, bool media = false,
               bool bounded = true) {
  const bool bounded_write = media || bounded;
  const bool locked = output_mutex == nullptr ||
                      xSemaphoreTake(
                          output_mutex,
                          bounded_write
                              ? pdMS_TO_TICKS(media ? kCameraWriteTimeoutMs
                                                    : kHealthWriteTimeoutMs)
                              : pdMS_TO_TICKS(kHealthWriteTimeoutMs)) == pdTRUE;
  if (!locked) return false;
  const auto next_sequence = output_sequence + 1;
  envelope.seq = next_sequence;
  envelope.ts_ms = now_ms();
  std::size_t written = 0;
  bool sent = false;
  if (lifeos::protocol::serialize(envelope, output_line, sizeof(output_line), written)) {
    const auto timeout_ms = media ? kCameraWriteTimeoutMs : kHealthWriteTimeoutMs;
    sent = usb_serial_jtag_write_bytes(output_line, written,
                                       pdMS_TO_TICKS(timeout_ms)) ==
           static_cast<int>(written);
  }
  if (sent) output_sequence = next_sequence;
  if (output_mutex != nullptr) xSemaphoreGive(output_mutex);
  return sent;
}

// Keep the value-taking convenience path for short-lived control responses;
// periodic tasks with a static envelope must use the in-place path below so
// an 8 KiB payload is not copied onto their small task stack.
bool emit(lifeos::protocol::Envelope envelope, bool media = false,
          bool bounded = true) {
  return emit_impl(envelope, media, bounded);
}

bool emit_in_place(lifeos::protocol::Envelope& envelope, bool media = false,
                   bool bounded = true) {
  return emit_impl(envelope, media, bounded);
}

// 在 payload 中查找 "name":true|false（用于识别命令子动作）
bool bool_field(std::string_view payload, std::string_view name, bool expected) {
  char needle[48]{};
  const int written = std::snprintf(needle, sizeof(needle), "\"%.*s\":%s",
                                    static_cast<int>(name.size()), name.data(),
                                    expected ? "true" : "false");
  return written > 0 && payload.find(std::string_view(needle, static_cast<std::size_t>(written))) !=
                           std::string_view::npos;
}

// 判断有限 JSON 数组中是否包含一个精确字符串；用于 hello 能力协商。
bool array_contains_text(std::string_view payload, std::string_view name,
                         std::string_view expected) {
  char needle[64]{};
  const int written = std::snprintf(needle, sizeof(needle), "\"%.*s\":[",
                                    static_cast<int>(name.size()), name.data());
  if (written <= 0) return false;
  const auto start = payload.find(std::string_view(needle, static_cast<std::size_t>(written)));
  if (start == std::string_view::npos) return false;
  const auto end = payload.find(']', start + static_cast<std::size_t>(written));
  if (end == std::string_view::npos) return false;
  char value[96]{};
  const int value_written = std::snprintf(value, sizeof(value), "\"%.*s\"",
                                          static_cast<int>(expected.size()), expected.data());
  return value_written > 0 &&
         payload.substr(start + static_cast<std::size_t>(written),
                        end - start - static_cast<std::size_t>(written))
                 .find(std::string_view(value, static_cast<std::size_t>(value_written))) !=
             std::string_view::npos;
}

// ---- 以下仅 HIL 测试模式编译：简易 JSON 字段解析（维护命令鉴权/参数）----
#if CONFIG_LIFEOS_HIL_TEST_MODE
// 精确匹配 "name":"expected"
bool text_field(std::string_view payload, std::string_view name,
                std::string_view expected) {
  char needle[96]{};
  const int written = std::snprintf(needle, sizeof(needle), "\"%.*s\":\"%.*s\"",
                                    static_cast<int>(name.size()), name.data(),
                                    static_cast<int>(expected.size()), expected.data());
  return written > 0 && payload.find(std::string_view(needle, static_cast<std::size_t>(written))) !=
                           std::string_view::npos;
}

// 提取 "name": 之后的数字串并校验为有限值
bool number_field(std::string_view payload, std::string_view name, float& output) {
  char needle[48]{};
  const int written = std::snprintf(needle, sizeof(needle), "\"%.*s\":",
                                    static_cast<int>(name.size()), name.data());
  if (written <= 0) return false;
  const auto start = payload.find(std::string_view(needle, static_cast<std::size_t>(written)));
  if (start == std::string_view::npos) return false;
  std::size_t cursor = start + static_cast<std::size_t>(written);
  while (cursor < payload.size() && (payload[cursor] == ' ' || payload[cursor] == '\t')) ++cursor;
  char number[32]{};
  std::size_t size = 0;
  while (cursor < payload.size() && size + 1 < sizeof(number) &&
         payload[cursor] != ',' && payload[cursor] != '}' && payload[cursor] != ' ' &&
         payload[cursor] != '\t') {
    number[size++] = payload[cursor++];
  }
  if (size == 0) return false;
  char* end = nullptr;
  output = std::strtof(number, &end);
  return end == number + size && std::isfinite(output);
}
#endif

// 舵机 I/O 核心日志回调 -> 复用 hil_log 门控
void servo_io_log_sink(void* /*context*/, const char* line) {
  hil_log("%s\n", line);
}

/** 目标适配器：ServoIoCore 与 SCS/Py32 硬件之间的唯一桥接层。
 *  每个方法都可能阻塞；只允许舵机 I/O 任务调用。 */
class StackChanServoLink final : public lifeos::runtime::ServoLink {
 public:
  explicit StackChanServoLink(StackChanBoard& board) : board_(board) {}

  bool vm_enabled() const override { return board_.expander().vm_enabled(); }
  bool set_vm_enabled(bool enabled) override {
    return board_.expander().set_vm_enabled(enabled);
  }
  bool enable_torque(bool enabled) override {
    return board_.servo().enable_torque_pair(enabled);
  }
  bool write_goal(const ServoPosition& position, std::uint16_t& yaw_raw,
                  std::uint16_t& pitch_raw) override {
    return board_.servo().write_goal_pair(position, yaw_raw, pitch_raw);
  }
  bool read_feedback(ServoPosition& position, std::uint16_t& yaw_raw,
                     std::uint16_t& pitch_raw) override {
    return board_.servo().read_feedback_pair(position, yaw_raw, pitch_raw);
  }
  bool wake_light() override { return board_.servo().wake_light(); }
  bool power_on_full() override { return board_.servo().power_on(); }
  bool hardware_ready() const override { return board_.motion_ready(); }

 private:
  StackChanBoard& board_;
};

// =============================================================================
// TargetRuntime —— 固件运行时核心
//   - graph_:  CGraph 行为图（touch / sensors / heartbeat 三个节点，100ms 节拍）
//   - safety_: FastSafetyLoop（20ms 快速安全回路，独立于 DAG 运行）
//   - core_:   ServoIoCore（舵机协议交互 / 断电 / 反馈读取）
//   - gateway_: 报文网关（解析 / 去重 / seq 校验）
//   跨任务共享的状态由 guard_（portMUX）保护，见 snapshot()。
// =============================================================================
class TargetRuntime final {
 public:
  struct CommandOutcome {
    bool accepted{false};
    lifeos::protocol::ErrorCode code{lifeos::protocol::ErrorCode::Internal};
    const char* detail{"command_rejected"};
  };

  struct Snapshot {
    bool host_seen{false};
    bool link_lost{false};
    bool paused{false};
    bool emergency_stop{false};
    bool fault{false};
    bool feedback_frozen{false};
    bool torque_enabled{false};
    bool motion_ready{false};
    ServoPosition position{};
    lifeos::runtime::SafetyFault safety_faults{lifeos::runtime::SafetyFault::None};
    ServoIoState io_state{ServoIoState::PowerOff};
    ServoIoError last_io_error{ServoIoError::None};
    std::uint32_t command_generation{0};
    std::uint64_t feedback_age_ms{0};
    std::uint64_t command_applied_ms{0};
  };

  explicit TargetRuntime(StackChanBoard& board)
      : board_(board), link_(board), graph_executor_(graph_) {}

  // 运行时启动流程：
  // ① 建图：注册 touch / sensors / heartbeat 节点并编译；失败则置 fault
  // ② 给安全回路一个初始心跳，避免启动期被判定失联
  // ③ 临界区内初始化共享快照（运动就绪、缓存位置、心跳时间戳）
  void begin() {
    const bool graph_ready =
        graph_.add_node("touch", &TargetRuntime::graph_touch_node) &&
        graph_.add_node("sensors", &TargetRuntime::graph_sensors_node) &&
        graph_.add_node("heartbeat", &TargetRuntime::graph_heartbeat_node) &&
        graph_.compile();
    graph_compiled_ = graph_ready;
    const auto current = now_ms();
    safety_.heartbeat(current);
    portENTER_CRITICAL(&guard_);
    manual_control_.reset();
    graph_heartbeat_ms_ = current;
    safety_heartbeat_seen_ms_ = current;
    motion_ready_ = board_.motion_ready();
    position_ = board_.servo().cached_position();
    if (!graph_ready) fault_ = true;
    portEXIT_CRITICAL(&guard_);
  }

  // 舵机 I/O 任务入口：先挂日志回调，再按"运动就绪 + VM 使能"初始化核心
  void servo_io_begin() {
    core_.set_logger(&servo_io_log_sink, nullptr);
    core_.init(board_.motion_ready(), board_.expander().vm_enabled());
  }

  // 舵机 I/O 步进：与硬件交互（读反馈/写目标/断链检测），仅由专用任务调用
  void servo_io_step() { core_.io_step(link_, now_ms()); }

  // 报文入口：交由网关完成解析 / 去重 / seq 校验
  lifeos::protocol::GatewayResult ingest_line(std::string_view line,
                                              std::uint64_t current_ms) {
    return gateway_.ingest(line, current_ms);
  }

  // 命令处理入口：
  // ① 更新"主机活跃"标记，供安全回路做 1.5s 失联判定
  // ② 按类型分发：hello / 紧急停止 / 控制类 / 维护类（HIL）
  CommandOutcome handle(const lifeos::protocol::Envelope& envelope,
                        std::uint64_t current_ms) {
    portENTER_CRITICAL(&guard_);
    host_seen_ = true;
    last_host_ms_ = current_ms;
    portEXIT_CRITICAL(&guard_);

    if (envelope.kind == lifeos::protocol::Kind::Hello) {
      if (envelope.type.view() == "hello.host") {
        const bool media_enabled = bool_field(envelope.payload.view(), "media_enabled", true);
        const bool manual_enabled =
            array_contains_text(envelope.payload.view(), "capabilities", "manual_control_v1");
        portENTER_CRITICAL(&guard_);
        manual_control_.reset();
        manual_control_enabled_ = manual_enabled;
        media_enabled_ = media_enabled;
        if (!media_enabled_) camera_preview_enabled_ = false;
        portEXIT_CRITICAL(&guard_);
      }
      return {true, lifeos::protocol::ErrorCode::Internal, "hello"};
    }
    if (envelope.kind == lifeos::protocol::Kind::Event) {
      if (envelope.type.view() == "host.heartbeat") {
        const bool media_enabled = bool_field(envelope.payload.view(), "media_enabled", true);
        portENTER_CRITICAL(&guard_);
        media_enabled_ = media_enabled;
        if (!media_enabled_) camera_preview_enabled_ = false;
        portEXIT_CRITICAL(&guard_);
      }
      return {true, lifeos::protocol::ErrorCode::Internal, "event"};
    }
    if (envelope.kind != lifeos::protocol::Kind::Command) {
      return {true, lifeos::protocol::ErrorCode::Internal, "event"};
    }

    const auto type = envelope.type.view();
    const auto payload = envelope.payload.view();
    if (type == "command.emergency_stop") {
      // 紧急停止流程（最高优先级，不等待 SCS ACK）：
      // ① 原子提升紧急/停止代次并取消所有未完成命令
      // ② 直接切断 VM_EN 电源
      // ③ 锁存紧急状态并置故障标志
      // ④ 舵机 I/O 任务随后完成剩余清理
      core_.mark_emergency();
      cut_vm_power("emergency");
      portENTER_CRITICAL(&guard_);
      manual_control_.reset();
      emergency_stop_ = true;
      fault_ = true;
      portEXIT_CRITICAL(&guard_);
      return {true, lifeos::protocol::ErrorCode::Internal, "emergency_stop"};
    }
    if (type == "command.control") {
      if (payload.find("\"action\":\"status\"") != std::string_view::npos) {
        return {true, lifeos::protocol::ErrorCode::Internal, "status"};
      }
      if (payload.find("\"action\":\"pause\"") != std::string_view::npos) {
        portENTER_CRITICAL(&guard_);
        manual_control_.reset();
        paused_ = true;
        portEXIT_CRITICAL(&guard_);
        core_.cancel_all();
        // 暂停策略：立即释放力矩并切断电机电源，
        // 使"已暂停"状态可立即确认无力矩，无需等舵机 I/O 任务排空停止序列。
        cut_vm_power("pause");
        return {true, lifeos::protocol::ErrorCode::Internal, "pause"};
      }
      if (payload.find("\"action\":\"resume\"") != std::string_view::npos) {
        portENTER_CRITICAL(&guard_);
        const bool blocked = emergency_stop_ || fault_;
        if (!blocked) paused_ = false;
        portEXIT_CRITICAL(&guard_);
        if (blocked) return {false, lifeos::protocol::ErrorCode::FaultLatched, "safety_latched"};
        return {true, lifeos::protocol::ErrorCode::Internal, "resume"};
      }
      if (payload.find("\"action\":\"preflight\"") != std::string_view::npos) {
        // Zero-motion direction preflight: SafetyTask asks the sole Servo I/O
        // owner to wake the rail and confirm feedback while torque remains off.
        portENTER_CRITICAL(&guard_);
        const bool blocked = emergency_stop_ || fault_ || paused_;
        if (!blocked) manual_control_.reset();
        portEXIT_CRITICAL(&guard_);
        if (blocked) {
          return {false, lifeos::protocol::ErrorCode::SafetyBlocked,
                  "preflight_blocked"};
        }
        core_.request_preflight();
        return {true, lifeos::protocol::ErrorCode::Internal, "preflight"};
      }
      if (payload.find("\"action\":\"home\"") != std::string_view::npos) {
        // 回中：紧急停止/故障/暂停时拒绝；否则提交 (0°,45°) 运动，TTL 2.5s
        portENTER_CRITICAL(&guard_);
        const bool blocked = emergency_stop_ || fault_ || paused_;
        if (!blocked) manual_control_.reset();
        portEXIT_CRITICAL(&guard_);
        if (blocked) return {false, lifeos::protocol::ErrorCode::SafetyBlocked, "home_blocked"};
        core_.submit(0.0F, 45.0F, current_ms + kHomeTtlMs);
        return {true, lifeos::protocol::ErrorCode::Internal, "home"};
      }
      if (payload.find("\"action\":\"clear_fault\"") != std::string_view::npos) {
        // 清除故障：必须携带本地在场确认，否则拒绝；置位后由安全回路执行解除
        if (!bool_field(payload, "local_confirmation", true)) {
          return {false, lifeos::protocol::ErrorCode::Unauthorized, "local_confirmation_required"};
        }
        portENTER_CRITICAL(&guard_);
        manual_control_.reset();
        clear_requested_ = true;
        portEXIT_CRITICAL(&guard_);
        return {true, lifeos::protocol::ErrorCode::Internal, "clear_fault"};
      }
    }
    if (type == "command.manual_control") {
#if LIFEOS_MANUAL_CONTROL_V1
      bool manual_enabled = false;
      portENTER_CRITICAL(&guard_);
      manual_enabled = manual_control_enabled_;
      portEXIT_CRITICAL(&guard_);
      if (!manual_enabled) {
        return {false, lifeos::protocol::ErrorCode::Unauthorized,
                "manual_control_not_negotiated"};
      }
      lifeos::protocol::ManualControlPayload input;
      if (lifeos::protocol::parse_manual_control_payload(envelope, input, current_ms) !=
          lifeos::protocol::ParseError::None) {
        return {false, lifeos::protocol::ErrorCode::InvalidSchema,
                "manual_control_invalid"};
      }
      bool paused = false;
      bool emergency = false;
      bool fault = false;
      bool feedback_frozen = false;
      bool motion_ready = false;
      ServoPosition measured{};
      portENTER_CRITICAL(&guard_);
      paused = paused_;
      emergency = emergency_stop_;
      fault = fault_ || safety_latched_;
      feedback_frozen = feedback_frozen_;
      motion_ready = motion_ready_;
      measured = position_;
      const auto manual = manual_control_.apply(
          input, measured, motion_ready, paused, emergency, fault,
          feedback_frozen, current_ms);
      portEXIT_CRITICAL(&guard_);

      if (!manual.accepted) {
        switch (manual.code) {
          case ManualControlResultCode::MotionUnavailable:
            return {false, lifeos::protocol::ErrorCode::Unsupported, manual.detail};
          case ManualControlResultCode::SafetyBlocked:
            return {false, lifeos::protocol::ErrorCode::SafetyBlocked, manual.detail};
          case ManualControlResultCode::LeaseRequired:
          case ManualControlResultCode::LeaseMismatch:
            return {false, lifeos::protocol::ErrorCode::Unauthorized, manual.detail};
          case ManualControlResultCode::SequenceRejected:
          case ManualControlResultCode::RateLimited:
            return {false, lifeos::protocol::ErrorCode::RateLimited, manual.detail};
          case ManualControlResultCode::Expired:
            return {false, lifeos::protocol::ErrorCode::Expired, manual.detail};
          case ManualControlResultCode::Invalid:
            return {false, lifeos::protocol::ErrorCode::InvalidSchema, manual.detail};
          case ManualControlResultCode::Accepted:
            break;
        }
        return {false, lifeos::protocol::ErrorCode::Internal, manual.detail};
      }
      if (manual.release) {
        core_.cancel_all();
        return {true, lifeos::protocol::ErrorCode::Internal, manual.detail};
      }
      if (manual.coalesced) {
        // The device has acknowledged the newest dead-man sequence but keeps
        // the existing ServoIo target until its 100 ms physical update gate
        // opens. This prevents USB ACK timing from breaking a held direction.
        return {true, lifeos::protocol::ErrorCode::Internal, manual.detail};
      }
      if (core_.submit(manual.yaw_deg, manual.pitch_deg, manual.expires_at_ms) == 0) {
        portENTER_CRITICAL(&guard_);
        manual_control_.reset();
        portEXIT_CRITICAL(&guard_);
        return {false, lifeos::protocol::ErrorCode::InvalidSchema,
                "manual_target_invalid"};
      }
      return {true, lifeos::protocol::ErrorCode::Internal, manual.detail};
#else
      return {false, lifeos::protocol::ErrorCode::Unsupported,
              "manual_control_disabled"};
#endif
    }
    if (type == "command.camera_preview") {
      lifeos::protocol::CameraPreviewPayload preview;
      if (lifeos::protocol::parse_camera_preview_payload(envelope, preview) !=
          lifeos::protocol::ParseError::None) {
        return {false, lifeos::protocol::ErrorCode::InvalidSchema, "camera_preview_invalid"};
      }
      portENTER_CRITICAL(&guard_);
      const bool media_enabled = media_enabled_;
      portEXIT_CRITICAL(&guard_);
      if (!media_enabled) {
        return {false, lifeos::protocol::ErrorCode::Unauthorized, "media_disabled"};
      }
      if (board_.camera().status().state != lifeos::hal::CapabilityState::Available) {
        return {false, lifeos::protocol::ErrorCode::Unsupported, "camera_unavailable"};
      }
      portENTER_CRITICAL(&guard_);
      if (preview.action.view() == "start") {
        camera_preview_enabled_ = true;
        // duration_ms=0 is the explicit continuous-session sentinel. The
        // host watchdog still terminates the stream on USB/session loss.
        camera_preview_deadline_ms_ = preview.duration_ms == 0
                                          ? 0
                                          : current_ms + preview.duration_ms;
        camera_preview_interval_ms_ =
            std::max<std::uint32_t>(
                static_cast<std::uint32_t>(kCameraColorMinimumIntervalMs),
                static_cast<std::uint32_t>(1000 / preview.fps));
      } else {
        camera_preview_enabled_ = false;
        camera_preview_deadline_ms_ = 0;
      }
      portEXIT_CRITICAL(&guard_);
      return {true, lifeos::protocol::ErrorCode::Internal,
              preview.action.view() == "start" ? "camera_preview_start" : "camera_preview_stop"};
    }
    if (type == "command.maintenance_motion") {
      // 维护运动（仅 HIL 模式）：鉴权 + 数字参数校验，提交带 TTL 的运动
#if CONFIG_LIFEOS_HIL_TEST_MODE
      if (!text_field(payload, "authorization", "maintainer") ||
          !text_field(payload, "test_mode", "phase1")) {
        return {false, lifeos::protocol::ErrorCode::Unauthorized, "maintenance_authorization_required"};
      }
      float yaw = 0.0F;
      float pitch = 45.0F;
      if (!number_field(payload, "yaw_deg", yaw) || !number_field(payload, "pitch_deg", pitch)) {
        return {false, lifeos::protocol::ErrorCode::InvalidSchema, "motion_number_required"};
      }
      portENTER_CRITICAL(&guard_);
      const bool blocked = emergency_stop_ || fault_ || paused_;
      portEXIT_CRITICAL(&guard_);
      if (blocked) return {false, lifeos::protocol::ErrorCode::SafetyBlocked, "motion_blocked"};
      // 角度硬限由安全门负责；准入只要求有限数字，
      // 以便 HIL 演练 FastSafetyLoop 的锁存路径。
      core_.submit(yaw, pitch, current_ms + kMaintenanceMotionTtlMs);
      return {true, lifeos::protocol::ErrorCode::Internal, "maintenance_motion"};
#else
      return {false, lifeos::protocol::ErrorCode::Unauthorized, "maintenance_motion_disabled"};
#endif
    }
    if (type == "command.maintenance_fault") {
      // 维护故障注入（仅 HIL 模式）：反馈冻结 / 瞬时故障，用于安全测试
#if CONFIG_LIFEOS_HIL_TEST_MODE
      if (!text_field(payload, "authorization", "maintainer") ||
          !text_field(payload, "test_mode", "phase1")) {
        return {false, lifeos::protocol::ErrorCode::Unauthorized, "maintenance_authorization_required"};
      }
      if (!text_field(payload, "kind", "feedback_frozen") &&
          !text_field(payload, "kind", "stall") &&
          !text_field(payload, "kind", "feedback_transient")) {
        return {false, lifeos::protocol::ErrorCode::Unsupported, "fault_kind_unsupported"};
      }
      if (text_field(payload, "kind", "feedback_transient")) {
        core_.inject_transient_failures(2);
      } else {
        core_.inject_feedback_freeze(true);
        portENTER_CRITICAL(&guard_);
        feedback_frozen_ = true;
        portEXIT_CRITICAL(&guard_);
      }
      return {true, lifeos::protocol::ErrorCode::Internal, "feedback_frozen"};
#else
      return {false, lifeos::protocol::ErrorCode::Unauthorized, "maintenance_fault_disabled"};
#endif
    }
    return {false, lifeos::protocol::ErrorCode::Unsupported, "unsupported_action"};
  }

  // 行为图节拍（100ms，core 1）：
  // ① 首次编译失败 -> 锁存故障并退出本次节拍
  // ② 执行 DAG；任一节点失败 -> 锁存故障并取消所有命令
  void graph_tick(std::uint64_t current_ms) {
    graph_now_ms_ = current_ms;
    if (!graph_compiled_ && !graph_.compile()) {
      portENTER_CRITICAL(&guard_);
      fault_ = true;
      portEXIT_CRITICAL(&guard_);
      return;
    }
    graph_compiled_ = true;
    if (!graph_executor_.execute(this)) {
      portENTER_CRITICAL(&guard_);
      fault_ = true;
      core_.cancel_all();
      portEXIT_CRITICAL(&guard_);
    }
  }

  // 快速安全回路节拍（20ms，core 0，优先级最高）：
  // ① 临界区拷贝共享状态（暂停/紧急/故障/心跳/主机时间）
  // ② 更新 safety 心跳，判定主机失联（1.5s 无消息）
  // ③ 组装 SafetyGate 输入并调用安全门
  // ④ 首次检测到堵转时输出诊断日志
  // ⑤ 清故障请求被受理 -> 解除故障/紧急/暂停并复位注入
  // ⑥ 安全回路已锁存但共享标志未同步 -> 提升为故障
  // ⑦ 更新对外快照（位置/力矩/IO 状态/反馈年龄等）
  // ⑧ 失联时取消所有命令
  void safety_tick(std::uint64_t current_ms) {
    bool paused = false;
    bool emergency = false;
    bool fault = false;
    bool feedback_frozen = false;
    bool safety_latched = false;
    bool clear_requested = false;
    std::uint64_t heartbeat = 0;
    std::uint64_t last_host = 0;
    bool host_seen = false;
    portENTER_CRITICAL(&guard_);
    paused = paused_;
    emergency = emergency_stop_;
    fault = fault_;
    feedback_frozen = feedback_frozen_;
    safety_latched = safety_latched_;
    clear_requested = clear_requested_;
    heartbeat = graph_heartbeat_ms_;
    last_host = last_host_ms_;
    host_seen = host_seen_;
    portEXIT_CRITICAL(&guard_);

    const bool link_lost = host_seen && current_ms > last_host + kHostTimeoutMs;

    bool manual_expired = false;
    portENTER_CRITICAL(&guard_);
    manual_expired = manual_control_.expire(
        current_ms,
        paused || emergency || fault || feedback_frozen || safety_latched || link_lost);
    portEXIT_CRITICAL(&guard_);
    if (manual_expired) core_.cancel_all();

    if (heartbeat != safety_heartbeat_seen_ms_) {
      safety_.heartbeat(heartbeat);
      safety_heartbeat_seen_ms_ = heartbeat;
    }

    lifeos::runtime::SafetyGateInputs inputs;
    inputs.now_ms = current_ms;
    inputs.paused = paused;
    inputs.emergency = emergency;
    inputs.fault_latched = fault;
    inputs.feedback_frozen = feedback_frozen;
    inputs.link_lost = link_lost;
    inputs.clear_requested = clear_requested;
    const auto gate = lifeos::runtime::safety_gate(core_, safety_, stall_, inputs);

    if (gate.stall || safety_.latched()) {
      bool manual_preempted = false;
      portENTER_CRITICAL(&guard_);
      manual_preempted = manual_control_.expire(current_ms, true);
      portEXIT_CRITICAL(&guard_);
      if (manual_preempted) core_.cancel_all();
    }

    // ④ 堵转状态变化时只记录首次日志，避免刷屏
    if (gate.stall && !stall_logged_) {
      hil_log(
          "safety stall generation=%u source=%s target_error=%.2f "
          "progress_age_ms=%llu feedback_age_ms=%llu failures=%u\n",
          static_cast<unsigned>(gate.active_command.generation), gate.stall_source,
          static_cast<double>(gate.target_error_deg),
          static_cast<unsigned long long>(gate.progress_age_ms),
          static_cast<unsigned long long>(gate.feedback_age_ms),
          static_cast<unsigned>(core_.feedback().consecutive_failures));
      stall_logged_ = true;
    } else if (!gate.stall) {
      stall_logged_ = false;
    }

    // ⑤ 清故障请求获安全门受理：解除故障/紧急/暂停与故障注入
    if (gate.cleared) {
      core_.inject_feedback_freeze(false);
      portENTER_CRITICAL(&guard_);
      clear_requested_ = false;
      fault_ = false;
      emergency_stop_ = false;
      paused_ = false;
      feedback_frozen_ = false;
      main_cut_pending_ = false;
      portEXIT_CRITICAL(&guard_);
      paused = false;
      emergency = false;
      fault = false;
      feedback_frozen = false;
    }

    // ⑥ 安全回路已锁存但共享标志尚未同步 -> 提升为故障
    if (safety_.latched() && !fault) {
      portENTER_CRITICAL(&guard_);
      fault_ = true;
      portEXIT_CRITICAL(&guard_);
      fault = true;
    }

    const auto snapshot = core_.feedback();
    const auto position = snapshot.valid
                              ? ServoPosition{snapshot.yaw_deg, snapshot.pitch_deg}
                              : position_;
    portENTER_CRITICAL(&guard_);
    position_ = position;
    if (main_cut_pending_ && !snapshot.vm_enabled) main_cut_pending_ = false;
    torque_enabled_ = snapshot.torque_enabled && snapshot.vm_enabled && !main_cut_pending_;
    motion_ready_ = board_.motion_ready();
    io_state_ = snapshot.io_state;
    last_io_error_ = snapshot.last_error;
    command_generation_ = snapshot.command_generation;
    command_applied_ms_ = snapshot.command_applied_ms;
    feedback_age_ms_ =
        snapshot.valid && snapshot.sampled_at_ms <= current_ms
            ? current_ms - snapshot.sampled_at_ms
            : 0;
    safety_faults_ = safety_.latched_faults();
    safety_latched_ = safety_.latched();
    portEXIT_CRITICAL(&guard_);

    // ⑧ 主机失联：取消所有未完成命令
    if (link_lost) {
      core_.cancel_all();
      portENTER_CRITICAL(&guard_);
      camera_preview_enabled_ = false;
      camera_preview_deadline_ms_ = 0;
      portEXIT_CRITICAL(&guard_);
    }

#if CONFIG_LIFEOS_HIL_TEST_MODE
    // HIL 诊断探针：证明安全任务保持 20ms 节拍，且控制台路径未被卡死
    if (current_ms - last_hb_log_ms_ >= 500) {
      last_hb_log_ms_ = current_ms;
      hil_log("safety hb uptime_ms=%llu stall=%d io=%s torque=%d failures=%u\n",
              static_cast<unsigned long long>(current_ms), gate.stall ? 1 : 0,
              lifeos::runtime::servo_io_state_name(io_state_),
              torque_enabled_ ? 1 : 0,
              static_cast<unsigned>(core_.feedback().consecutive_failures));
    }
#endif
  }

  // 对外快照：临界区内一次性拷贝全部共享状态，供 hello/status 应答使用
  Snapshot snapshot() const {
    Snapshot result;
    portENTER_CRITICAL(const_cast<portMUX_TYPE*>(&guard_));
    result.host_seen = host_seen_;
    result.link_lost = host_seen_ && now_ms() > last_host_ms_ + kHostTimeoutMs;
    result.paused = paused_;
    result.emergency_stop = emergency_stop_;
    result.fault = fault_ || safety_latched_;
    result.feedback_frozen = feedback_frozen_;
    result.torque_enabled = torque_enabled_;
    result.motion_ready = motion_ready_;
    result.position = position_;
    result.safety_faults = safety_faults_;
    result.io_state = io_state_;
    result.last_io_error = last_io_error_;
    result.command_generation = command_generation_;
    result.feedback_age_ms = feedback_age_ms_;
    result.command_applied_ms = command_applied_ms_;
    portEXIT_CRITICAL(const_cast<portMUX_TYPE*>(&guard_));
    return result;
  }

  bool camera_preview_active(std::uint64_t current_ms,
                             std::uint32_t& interval_ms) {
    portENTER_CRITICAL(&guard_);
    if (camera_preview_enabled_ &&
        ((camera_preview_deadline_ms_ != 0 &&
          current_ms >= camera_preview_deadline_ms_) ||
         !media_enabled_)) {
      camera_preview_enabled_ = false;
      camera_preview_deadline_ms_ = 0;
    }
    const bool active = camera_preview_enabled_;
    interval_ms = camera_preview_interval_ms_;
    portEXIT_CRITICAL(&guard_);
    return active;
  }

  bool capture_camera_frame(CameraJpegFrame& frame) {
    return board_.camera().capture_jpeg(frame);
  }

  void release_camera_frame(CameraJpegFrame& frame) {
    board_.camera().release_jpeg(frame);
  }

  void display_tick(std::uint64_t current_ms) {
    render(current_ms);
  }

  lifeos::protocol::GatewayResult gateway(std::string_view line,
                                           std::uint64_t current_ms) {
    return ingest_line(line, current_ms);
  }

 private:
  // ---- 行为图节点（由 graph_tick 驱动；只产生语义状态，不直接碰硬件）----
  static bool graph_touch_node(void* context) {
    return static_cast<TargetRuntime*>(context)->sample_touch();
  }

  static bool graph_sensors_node(void* context) {
    return static_cast<TargetRuntime*>(context)->sample_sensors();
  }

  static bool graph_heartbeat_node(void* context) {
    return static_cast<TargetRuntime*>(context)->publish_graph_heartbeat();
  }

  // 触摸节点：
  // ① 读取失败 -> 锁存故障并取消命令
  // ② 长按 >= 1.5s 且处于故障/紧急状态 -> 请求清除故障（保持锁存直至松开）
  // ③ 非保持态短触 -> 进入暂停
  bool sample_touch() {
    lifeos::hal::TouchSample sample;
    if (!board_.touch().read(sample)) {
      portENTER_CRITICAL(&guard_);
      fault_ = true;
      core_.cancel_all();
      portEXIT_CRITICAL(&guard_);
      // A failed optional sensor must not prevent the heartbeat node from
      // running. The fault flag still blocks motion through SafetyGate, while
      // keeping the failure classified as a sensor fault rather than turning
      // the independent 250ms graph watchdog into a secondary latch.
      return true;
    } else if (sample.touched) {
      portENTER_CRITICAL(&guard_);
      const bool clear = sample.duration_ms >= 1500 && (fault_ || emergency_stop_);
      if (clear) clear_requested_ = true;
      if (clear) touch_clear_hold_ = true;
      else if (!touch_clear_hold_ && !fault_ && !emergency_stop_) paused_ = true;
      portEXIT_CRITICAL(&guard_);
    } else {
      portENTER_CRITICAL(&guard_);
      touch_clear_hold_ = false;
      portEXIT_CRITICAL(&guard_);
    }

    return true;
  }

  // 传感器采样节点：IMU/距离；摄像头采集属于独立的低优先级预览任务，
  // 不能在图心跳路径中等待 DVP/JPEG frame，否则会触发 250ms watchdog。
  bool sample_sensors() {
    lifeos::hal::ImuSample imu_sample;
    (void)board_.imu().read(imu_sample);
    lifeos::hal::ProximitySample proximity_sample;
    (void)board_.proximity().read(proximity_sample);
    return true;
  }

  // 心跳节点：只发布"行为图活动"时间戳（喂给安全回路）。显示刷新属于
  // 独立低优先级任务，不能让 SPI 全屏绘制阻塞 graph watchdog。
  bool publish_graph_heartbeat() {
    portENTER_CRITICAL(&guard_);
    graph_heartbeat_ms_ = graph_now_ms_;
    portEXIT_CRITICAL(&guard_);
    return true;
  }

  // 快速切断 VM 电源（失败重试一次）；挂起"主切断待确认"标志，
  // 由安全回路在 VM 确实断电后复位，避免力矩状态误报
  void cut_vm_power(const char* reason) {
    const auto started = now_ms();
    bool ok = board_.expander().cut_power_fast();
    if (!ok) ok = board_.expander().cut_power_fast();
    portENTER_CRITICAL(&guard_);
    main_cut_pending_ = true;
    portEXIT_CRITICAL(&guard_);
    hil_log("safety power_cut reason=%s elapsed_ms=%u ok=%d\n", reason,
            static_cast<unsigned>(now_ms() - started), ok ? 1 : 0);
  }

  // 表情渲染：按优先级 EMERGENCY_STOP > FAULT > PAUSED > DEGRADED > IDLE
  void render(std::uint64_t current_ms) {
    const auto state = snapshot();
    const char* expression = "IDLE";
    if (state.emergency_stop) expression = "EMERGENCY_STOP";
    else if (state.fault) expression = "FAULT";
    else if (state.paused) expression = "PAUSED";
    else if (!state.motion_ready) expression = "DEGRADED";
    lifeos::hal::DisplayFrame frame;
    frame.timestamp_ms = current_ms;
    std::snprintf(frame.expression.data(), frame.expression.size(), "%s", expression);
    (void)board_.display().render(frame);
  }

  StackChanBoard& board_;
  StackChanServoLink link_;
  ServoIoCore core_{};
  lifeos::runtime::StaticGraph graph_{};
  lifeos::runtime::StaticExecutor graph_executor_;
  lifeos::runtime::FastSafetyLoop safety_{};
  lifeos::runtime::StallMonitor stall_{};
  lifeos::protocol::Gateway gateway_{};
  ManualControlState manual_control_{};
  portMUX_TYPE guard_ = portMUX_INITIALIZER_UNLOCKED;
  std::uint64_t graph_heartbeat_ms_{0};
  std::uint64_t safety_heartbeat_seen_ms_{0};
  std::uint64_t last_host_ms_{0};
  std::uint64_t graph_now_ms_{0};
  ServoPosition position_{};
  lifeos::runtime::SafetyFault safety_faults_{lifeos::runtime::SafetyFault::None};
  ServoIoState io_state_{ServoIoState::PowerOff};
  ServoIoError last_io_error_{ServoIoError::None};
  std::uint32_t command_generation_{0};
  std::uint64_t feedback_age_ms_{0};
  std::uint64_t command_applied_ms_{0};
  bool host_seen_{false};
  bool paused_{false};
  bool emergency_stop_{false};
  bool fault_{false};
  bool feedback_frozen_{false};
  bool clear_requested_{false};
  bool touch_clear_hold_{false};
  bool graph_compiled_{false};
  bool safety_latched_{false};
  bool torque_enabled_{false};
  bool motion_ready_{false};
  bool main_cut_pending_{false};
  bool stall_logged_{false};
  bool media_enabled_{false};
  bool manual_control_enabled_{false};
  bool camera_preview_enabled_{false};
  std::uint64_t camera_preview_deadline_ms_{0};
  std::uint32_t camera_preview_interval_ms_{static_cast<std::uint32_t>(kCameraDefaultIntervalMs)};
  std::uint64_t last_hb_log_ms_{0};
};

TargetRuntime runtime(board);

// 应答 hello：回填设备信息（固件/板型/MAC/能力/运动状态），correlation 关联请求
void emit_hello(const lifeos::protocol::Envelope& request) {
  const auto state = runtime.snapshot();
  const bool camera_ready =
      board.camera().status().state == lifeos::hal::CapabilityState::Available;
  const char* camera_capability = camera_ready ? "\"camera\"," : "";
#if LIFEOS_MANUAL_CONTROL_V1
  const char* manual_control_capability =
      state.motion_ready ? "\"manual_control_v1\"," : "";
  const char* manual_preflight_capability =
      state.motion_ready ? "\"manual_preflight_v1\"," : "";
#else
  const char* manual_control_capability = "";
  const char* manual_preflight_capability = "";
#endif
  lifeos::protocol::Envelope response;
  response.kind = lifeos::protocol::Kind::Hello;
  set_text(response.type, "hello.device");
  set_text(response.event_id, "device-hello");
  set_text(response.correlation_id, request.event_id.view());
  set_text(response.device_id, kDeviceId);
  char mac_text[24]{};
  std::uint8_t mac[6]{};
  esp_read_mac(mac, ESP_MAC_WIFI_STA);
  std::snprintf(mac_text, sizeof(mac_text), "%02x:%02x:%02x:%02x:%02x:%02x",
                mac[0], mac[1], mac[2], mac[3], mac[4], mac[5]);
  char payload[768]{};
  std::snprintf(payload, sizeof(payload),
                "{\"firmware\":\"%s\",\"board\":\"StackChan/CoreS3\","
                "\"mac\":\"%s\",\"protocol_versions\":[\"lifeos.v1\"],"
                "\"capabilities\":[\"status\",\"protocol\",\"safety\",\"motion\","
                "\"health\",\"touch\",\"imu\",%s%s%s\"display\"],"
                "\"motion_enabled\":%s,\"torque_enabled\":%s,\"camera_ready\":%s,"
                "\"heap_free\":%u,"
                "\"psram_free\":%u}",
                kFirmware, mac_text, camera_capability, manual_control_capability,
                manual_preflight_capability,
                state.motion_ready ? "true" : "false",
                state.torque_enabled ? "true" : "false",
                camera_ready ? "true" : "false",
                static_cast<unsigned>(esp_get_free_heap_size()),
                static_cast<unsigned>(heap_caps_get_free_size(MALLOC_CAP_SPIRAM)));
  set_text(response.payload, payload);
  emit(response);
}

// 1 Hz device heartbeat for Bridge freshness. It contains only the same
// bounded health snapshot as status; it never carries media or control input.
void emit_health_report() {
  const auto state = runtime.snapshot();
  if (!state.host_seen || state.link_lost || !usb_serial_jtag_is_connected()) return;
  // Envelope owns an 8 KiB bounded payload. Keep the periodic telemetry
  // buffers out of the 4 KiB health task stack; otherwise the first report
  // corrupts the task frame and the next delay call panics in FreeRTOS.
  static lifeos::protocol::Envelope response;
  response = {};
  response.kind = lifeos::protocol::Kind::Event;
  set_text(response.type, "health.report");
  static char event_id[48]{};
  std::snprintf(event_id, sizeof(event_id), "health-%llu",
                static_cast<unsigned long long>(now_ms()));
  set_text(response.event_id, event_id);
  set_text(response.device_id, kDeviceId);
  static char payload[1024]{};
  const int payload_size = std::snprintf(
      payload, sizeof(payload),
      "{\"firmware\":\"%s\",\"motion_enabled\":%s,\"torque_enabled\":%s,"
      "\"camera_ready\":%s,\"paused\":%s,\"fault\":%s,"
      "\"feedback_frozen\":%s,\"safety_faults\":%u,\"link_lost\":%s,"
      "\"yaw_deg\":%.2f,\"pitch_deg\":%.2f,\"io_state\":\"%s\","
      "\"last_io_error\":\"%s\",\"command_generation\":%u,"
      "\"command_applied_ms\":%llu,\"feedback_age_ms\":%llu,"
      "\"heap_free\":%u,\"psram_free\":%u,\"uptime_ms\":%llu}",
      kFirmware, state.motion_ready ? "true" : "false",
      state.torque_enabled ? "true" : "false",
      board.camera().status().state == lifeos::hal::CapabilityState::Available ? "true" : "false",
      state.paused ? "true" : "false", state.fault ? "true" : "false",
      state.feedback_frozen ? "true" : "false",
      static_cast<unsigned>(state.safety_faults), state.link_lost ? "true" : "false",
      static_cast<double>(state.position.yaw_deg),
      static_cast<double>(state.position.pitch_deg),
      lifeos::runtime::servo_io_state_name(state.io_state),
      lifeos::runtime::servo_io_error_name(state.last_io_error),
      static_cast<unsigned>(state.command_generation),
      static_cast<unsigned long long>(state.command_applied_ms),
      static_cast<unsigned long long>(state.feedback_age_ms),
      static_cast<unsigned>(esp_get_free_heap_size()),
      static_cast<unsigned>(heap_caps_get_free_size(MALLOC_CAP_SPIRAM)),
      static_cast<unsigned long long>(now_ms()));
  if (payload_size <= 0 || static_cast<std::size_t>(payload_size) >= sizeof(payload)) return;
  set_text(response.payload, {payload, static_cast<std::size_t>(payload_size)});
  // Health is periodic telemetry, not a control ACK. It must never hold the
  // shared output mutex indefinitely when the host has stopped reading; a
  // blocked health task would otherwise prevent the next hello/status reply.
  (void)emit_in_place(response, false, true);
}

// 应答 status：以运行时快照渲染完整状态 JSON（Completed + 幂等）
void emit_status(const lifeos::protocol::Envelope& request) {
  const auto state = runtime.snapshot();
  const bool camera_ready =
      board.camera().status().state == lifeos::hal::CapabilityState::Available;
  auto response = lifeos::protocol::make_ack(
      request, lifeos::protocol::AckStatus::Completed, true, 0, now_ms());
  char payload[768]{};
  std::snprintf(payload, sizeof(payload),
                "{\"status\":\"completed\",\"idempotent\":true,"
                "\"firmware\":\"%s\",\"motion_enabled\":%s,\"torque_enabled\":%s,"
                "\"camera_ready\":%s,\"paused\":%s,\"fault\":%s,"
                "\"feedback_frozen\":%s,"
                "\"safety_faults\":%u,\"link_lost\":%s,"
                "\"yaw_deg\":%.2f,\"pitch_deg\":%.2f,"
                "\"io_state\":\"%s\",\"last_io_error\":\"%s\","
                "\"command_generation\":%u,\"command_applied_ms\":%llu,"
                "\"feedback_age_ms\":%llu,\"heap_free\":%u,"
                "\"psram_free\":%u,\"uptime_ms\":%llu}",
                kFirmware, state.motion_ready ? "true" : "false",
                state.torque_enabled ? "true" : "false", camera_ready ? "true" : "false",
                state.paused ? "true" : "false",
                state.fault ? "true" : "false", state.feedback_frozen ? "true" : "false",
                static_cast<unsigned>(state.safety_faults), state.link_lost ? "true" : "false",
                static_cast<double>(state.position.yaw_deg),
                static_cast<double>(state.position.pitch_deg),
                lifeos::runtime::servo_io_state_name(state.io_state),
                lifeos::runtime::servo_io_error_name(state.last_io_error),
                static_cast<unsigned>(state.command_generation),
                static_cast<unsigned long long>(state.command_applied_ms),
                static_cast<unsigned long long>(state.feedback_age_ms),
                static_cast<unsigned>(esp_get_free_heap_size()),
                static_cast<unsigned>(heap_caps_get_free_size(MALLOC_CAP_SPIRAM)),
                static_cast<unsigned long long>(now_ms()));
  set_text(response.payload, payload);
  emit(response);
}

std::size_t base64_encode(const std::uint8_t* input, std::size_t length,
                          char* output, std::size_t capacity) {
  static constexpr char alphabet[] =
      "ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789+/";
  if (input == nullptr || output == nullptr || capacity == 0) return 0;
  std::size_t source = 0;
  std::size_t target = 0;
  while (source + 2 < length) {
    if (target + 4 >= capacity) return 0;
    const std::uint32_t value = (static_cast<std::uint32_t>(input[source]) << 16) |
                                (static_cast<std::uint32_t>(input[source + 1]) << 8) |
                                input[source + 2];
    output[target++] = alphabet[(value >> 18) & 0x3f];
    output[target++] = alphabet[(value >> 12) & 0x3f];
    output[target++] = alphabet[(value >> 6) & 0x3f];
    output[target++] = alphabet[value & 0x3f];
    source += 3;
  }
  if (source < length) {
    if (target + 4 >= capacity) return 0;
    const std::size_t remaining = length - source;
    const std::uint32_t value = static_cast<std::uint32_t>(input[source]) << 16 |
                                (remaining == 2
                                     ? static_cast<std::uint32_t>(input[source + 1]) << 8
                                     : 0);
    output[target++] = alphabet[(value >> 18) & 0x3f];
    output[target++] = alphabet[(value >> 12) & 0x3f];
    output[target++] = remaining == 2 ? alphabet[(value >> 6) & 0x3f] : '=';
    output[target++] = '=';
  }
  output[target] = '\0';
  return target;
}

void emit_camera_frame(const CameraJpegFrame& frame) {
  if (frame.data == nullptr || frame.size < 2 || frame.size > 64u * 1024u ||
      frame.width != 320 || frame.height != 240) {
    return;
  }
  const std::uint64_t frame_number = ++camera_frame_sequence;
  const std::uint32_t chunk_count = static_cast<std::uint32_t>(
      (frame.size + kCameraChunkBytes - 1) / kCameraChunkBytes);
  if (chunk_count == 0 || chunk_count > 32) return;

  char frame_id[48]{};
  std::snprintf(frame_id, sizeof(frame_id), "camera-%llu",
                static_cast<unsigned long long>(frame_number));
  // Envelope and payload are deliberately static: an Envelope owns an 8 KiB
  // bounded payload, so keeping begin/chunk/end locals together can exceed
  // the 16 KiB camera task stack even though each event is individually small.
  static lifeos::protocol::Envelope envelope;
  static char payload[lifeos::protocol::kMaxPayloadBytes + 1];
  envelope = {};
  envelope.kind = lifeos::protocol::Kind::Event;
  set_text(envelope.type, "camera.frame.begin");
  char begin_event_id[64]{};
  std::snprintf(begin_event_id, sizeof(begin_event_id), "camera-begin-%s", frame_id);
  set_text(envelope.event_id, begin_event_id);
  set_text(envelope.correlation_id, frame_id);
  set_text(envelope.device_id, kDeviceId);
  const int begin_size = std::snprintf(
      payload, sizeof(payload),
      "{\"frame_id\":\"%s\",\"format\":\"jpeg\",\"width\":%u,"
      "\"height\":%u,\"size\":%u,\"chunk_count\":%u}",
      frame_id, static_cast<unsigned>(frame.width), static_cast<unsigned>(frame.height),
      static_cast<unsigned>(frame.size), static_cast<unsigned>(chunk_count));
  if (begin_size <= 0 || static_cast<std::size_t>(begin_size) >= sizeof(payload)) return;
  set_text(envelope.payload, {payload, static_cast<std::size_t>(begin_size)});
  if (!emit_in_place(envelope, true)) return;

  static char encoded[(kCameraChunkBytes + 2) / 3 * 4 + 1]{};
  static char chunk_payload[lifeos::protocol::kMaxPayloadBytes + 1]{};
  for (std::uint32_t index = 0; index < chunk_count; ++index) {
    const std::size_t offset = static_cast<std::size_t>(index) * kCameraChunkBytes;
    const std::size_t length = std::min(kCameraChunkBytes, frame.size - offset);
    const auto encoded_size = base64_encode(frame.data + offset, length, encoded, sizeof(encoded));
    if (encoded_size == 0) return;
    envelope = {};
    envelope.kind = lifeos::protocol::Kind::Event;
    set_text(envelope.type, "camera.frame.chunk");
    char event_id[64]{};
    std::snprintf(event_id, sizeof(event_id), "camera-chunk-%llu-%u",
                  static_cast<unsigned long long>(frame_number),
                  static_cast<unsigned>(index));
    set_text(envelope.event_id, event_id);
    set_text(envelope.correlation_id, frame_id);
    set_text(envelope.device_id, kDeviceId);
    const int chunk_size = std::snprintf(
        chunk_payload, sizeof(chunk_payload),
        "{\"frame_id\":\"%s\",\"index\":%u,\"chunk_count\":%u,\"data\":\"%s\"}",
        frame_id, static_cast<unsigned>(index), static_cast<unsigned>(chunk_count), encoded);
    if (chunk_size <= 0 || static_cast<std::size_t>(chunk_size) >= sizeof(chunk_payload)) return;
    set_text(envelope.payload, {chunk_payload, static_cast<std::size_t>(chunk_size)});
    if (!emit_in_place(envelope, true)) return;
  }

  envelope = {};
  envelope.kind = lifeos::protocol::Kind::Event;
  set_text(envelope.type, "camera.frame.end");
  char end_event_id[64]{};
  std::snprintf(end_event_id, sizeof(end_event_id), "camera-end-%llu",
                static_cast<unsigned long long>(frame_number));
  set_text(envelope.event_id, end_event_id);
  set_text(envelope.correlation_id, frame_id);
  set_text(envelope.device_id, kDeviceId);
  const int end_size = std::snprintf(payload, sizeof(payload),
                                     "{\"frame_id\":\"%s\",\"size\":%u}",
                                     frame_id, static_cast<unsigned>(frame.size));
  if (end_size <= 0 || static_cast<std::size_t>(end_size) >= sizeof(payload)) return;
  set_text(envelope.payload, {payload, static_cast<std::size_t>(end_size)});
  (void)emit_in_place(envelope, true);
}

// 应答失败：发送 error 报文；无 event_id 的报文不回应
void emit_error(const lifeos::protocol::GatewayResult& result,
                lifeos::protocol::ErrorCode code, std::string_view detail) {
  if (result.envelope.event_id.size == 0) return;
  emit(lifeos::protocol::make_error(result.envelope, code, 0, now_ms(), detail));
}

// 网关解析错误 -> 协议错误码映射
lifeos::protocol::ErrorCode parse_error_code(lifeos::protocol::ParseError error) {
  using lifeos::protocol::ErrorCode;
  switch (error) {
    case lifeos::protocol::ParseError::UnsupportedKind: return ErrorCode::Unsupported;
    case lifeos::protocol::ParseError::SequenceRejected:
    case lifeos::protocol::ParseError::InvalidField: return ErrorCode::Unauthorized;
    case lifeos::protocol::ParseError::InvalidPayload: return ErrorCode::InvalidSchema;
    case lifeos::protocol::ParseError::QueueFull: return ErrorCode::Busy;
    case lifeos::protocol::ParseError::None: return ErrorCode::Internal;
    default: return ErrorCode::InvalidSchema;
  }
}

// ---- 常驻任务 ----
// 行为图任务：core 1，优先级 5，每 100ms 执行一次 DAG 节拍
void graph_task(void* argument) {
  auto* target = static_cast<TargetRuntime*>(argument);
  while (true) {
    target->graph_tick(now_ms());
    vTaskDelay(pdMS_TO_TICKS(kGraphPeriodMs));
  }
}

// 显示任务：core 0，低优先级；SPI 全屏/局部绘制绝不占用 graph heartbeat 路径。
void display_task(void* argument) {
  auto* target = static_cast<TargetRuntime*>(argument);
  while (true) {
    target->display_tick(now_ms());
    // 50 ms keeps eye/mouth animation responsive; only the small animated
    // feature region is repainted after the initial full-screen frame.
    vTaskDelay(pdMS_TO_TICKS(50));
  }
}

// 快速安全回路任务：core 0，优先级 20（最高），20ms 严格周期
void safety_task(void* argument) {
  auto* target = static_cast<TargetRuntime*>(argument);
  TickType_t last_wake = xTaskGetTickCount();
  while (true) {
    target->safety_tick(now_ms());
    vTaskDelayUntil(&last_wake, pdMS_TO_TICKS(kSafetyPeriodMs));
  }
}

// 舵机 I/O 任务：core 1，优先级 12，~1ms 循环执行硬件交互（唯一碰硬件的任务）
void servo_io_task(void* argument) {
  auto* target = static_cast<TargetRuntime*>(argument);
  target->servo_io_begin();
  while (true) {
    target->servo_io_step();
    vTaskDelay(1);
  }
}

// 摄像头预览任务：core 1，低优先级，默认每 100ms 采集一帧 JPEG；持续到显式停止或失联。
// 只在收到明确的 start 且 host hello 已启用 media 后运行。
void camera_task(void* argument) {
  auto* target = static_cast<TargetRuntime*>(argument);
  while (true) {
    std::uint32_t interval_ms = static_cast<std::uint32_t>(kCameraDefaultIntervalMs);
    const auto started = now_ms();
    if (!target->camera_preview_active(started, interval_ms)) {
      vTaskDelay(pdMS_TO_TICKS(100));
      continue;
    }
    CameraJpegFrame frame;
    if (target->capture_camera_frame(frame)) {
      emit_camera_frame(frame);
      target->release_camera_frame(frame);
    }
    const auto elapsed = now_ms() - started;
    const auto delay_ms = elapsed < interval_ms ? interval_ms - elapsed : 1;
    vTaskDelay(pdMS_TO_TICKS(delay_ms));
  }
}

// 设备健康事件任务：core 1，低优先级，1 Hz；Bridge 用它判断 session freshness。
void health_task(void* /*argument*/) {
  TickType_t last_wake = xTaskGetTickCount();
  while (true) {
    emit_health_report();
    vTaskDelayUntil(&last_wake, pdMS_TO_TICKS(1000));
  }
}

}  // 匿名命名空间结束
// ESP-IDF 标准入口，相当于 main
extern "C" void app_main() {
  // ── 启动流程（按序号执行）─────────────────────────────────
  // ① 关闭 stdout 缓冲；把 TX 环扩大到 16KiB（容纳一个最大 base64 媒体行），
  //    否则主机停止读取时日志任务会被阻塞、并通过共享 stdout 锁连带
  //    卡死网关 ACK 路径；RX 环 1024B
  setvbuf(stdout, nullptr, _IONBF, 0);
  output_mutex = xSemaphoreCreateMutexStatic(&output_mutex_storage);
  ESP_ERROR_CHECK(output_mutex != nullptr ? ESP_OK : ESP_FAIL);
  usb_serial_jtag_driver_config_t usb_config = {
      .tx_buffer_size = kUsbTxBufferBytes,
      .rx_buffer_size = 1024,
  };
  ESP_ERROR_CHECK(usb_serial_jtag_driver_install(&usb_config));
  usb_serial_jtag_vfs_use_driver();
  // ② 把"最近主机通信"初始化为当前时间，让启动诊断先可输出，
  //    直到第一次主机交互后才按 4s 空闲门控（见 kConsoleIdleDropMs）
  last_console_rx_ms.store(now_ms(), std::memory_order_relaxed);

  // ③ 初始化板级硬件与运行时（建图、复位共享状态、初始心跳）
  const bool hardware_ready = board.begin();
  runtime.begin();
  hil_log("LIFEOS_HIL_READY %s motion=%s board=%s\n", kFirmware,
          hardware_ready ? "enabled" : "disabled", hardware_ready ? "ready" : "degraded");
  // ④ 创建六个常驻任务（见上方任务注释）：
  //    行为图(core1, prio5) / 摄像头(core1, prio3) /
  //    显示(core0, prio2) / 舵机I/O(core1, prio12) /
  //    健康事件(core1, prio1) / 安全回路(core0, prio20)
  xTaskCreatePinnedToCore(graph_task, "lifeos_graph", 8192, &runtime, 5, nullptr, 1);
  xTaskCreatePinnedToCore(camera_task, "lifeos_camera", 16384, &runtime, 3, nullptr, 1);
  // The SPI interrupt is installed on the app-main core. Keep its waiting
  // task on that same core to avoid a cross-core FreeRTOS event-list wakeup
  // race observed on the CoreS3 display path.
  xTaskCreatePinnedToCore(display_task, "lifeos_display", 8192, &runtime, 2, nullptr, 0);
  xTaskCreatePinnedToCore(health_task, "lifeos_health", 4096, nullptr, 1, nullptr, 1);
  xTaskCreatePinnedToCore(servo_io_task, "lifeos_servo_io", 6144, &runtime, 12, nullptr, 1);
  xTaskCreatePinnedToCore(safety_task, "lifeos_safety", 8192, &runtime, 20, nullptr, 0);

  // ── 主命令循环（⑤）：逐行解析 USB JSONL 并分发 ─────────────
  std::size_t length = 0;
  std::uint8_t byte = 0;
  while (true) {
    // ① 逐字节读取一行：\r 忽略、\n 结束；同时刷新"最近主机通信"
    // ② 超长行丢弃并吞到行尾，防止缓冲错位
    if (usb_serial_jtag_read_bytes(&byte, 1, portMAX_DELAY) <= 0) continue;
    last_console_rx_ms.store(now_ms(), std::memory_order_relaxed);
    if (byte == '\r') continue;
    if (byte != '\n') {
      if (length < std::size(input_line) - 1) input_line[length++] = static_cast<char>(byte);
      else {
        while (usb_serial_jtag_read_bytes(&byte, 1, portMAX_DELAY) > 0 && byte != '\n') {}
        length = 0;
      }
      continue;
    }
    const auto line = std::string_view(input_line, length);
    const auto started_at = now_ms();
    // ③ 网关解析：seq / 去重 / 协议校验
    const auto result = runtime.gateway(line, started_at);
    // ④ 分派：解析失败 -> 错误应答；重复 -> 重发缓存应答；
    //    hello -> 设备握手；其它 -> 命令处理 + 回执
    if (!result.accepted) {
      emit_error(result, parse_error_code(result.error), lifeos::protocol::parse_error_name(result.error));
    } else if (result.duplicate) {
      if (result.response.event_id.size != 0) emit(result.response);
    } else if (result.envelope.kind == lifeos::protocol::Kind::Hello) {
      (void)runtime.handle(result.envelope, now_ms());
      emit_hello(result.envelope);
    } else if (result.envelope.kind == lifeos::protocol::Kind::Event) {
      // Host heartbeats refresh the local link watchdog without creating an
      // ACK stream that would compete with the camera media path.
      (void)runtime.handle(result.envelope, now_ms());
    } else {
      // ⑤ 命令处理回执：accepted -> ACK(Accepted)；status -> 完整状态；拒绝 -> error
      const auto outcome = runtime.handle(result.envelope, now_ms());
      if (!outcome.accepted) emit_error(result, outcome.code, outcome.detail);
      else if (std::string_view(outcome.detail) == "status") emit_status(result.envelope);
      else emit(lifeos::protocol::make_ack(result.envelope,
                                             lifeos::protocol::AckStatus::Accepted,
                                             false, 0, now_ms()));
      const auto type_view = result.envelope.type.view();
      hil_log("gateway command accepted type=%.*s detail=%s latency_ms=%u\n",
              static_cast<int>(type_view.size()), type_view.data(), outcome.detail,
              static_cast<unsigned>(now_ms() - started_at));
    }
    length = 0;
  }
}
