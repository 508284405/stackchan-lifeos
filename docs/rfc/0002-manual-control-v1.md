# RFC 0002: `manual_control_v1` Dead-man 输入扩展

- 状态：Implementation candidate / target firmware build PASS；真实设备刷写与受监督 HIL 待完成
- 日期：2026-08-30
- 范围：W3 fake/host 契约、control lease、归一化输入和设备端安全门
- 不包含：本 RFC 之外的任意串口透传、真实远程运动启用、网络配置、OTA、媒体和公网访问
- 依赖：`docs/rfc/0001-web-bridge-fleet-control.md`、`docs/scs-runtime-io-design.md`

## 1. 决策摘要

当前 `lifeos.v1` 没有通用 Web 手动控制 wire command，因此不能把浏览器输入直接
塞进 `command.control` 或 `command.maintenance_motion`。本 RFC 为能力协商和后续
fake/固件实现冻结一个独立扩展：

- capability 名称：`manual_control_v1`；wire type：`command.manual_control`；
  仍使用现有 `lifeos.v1` envelope，不创建第二套设备协议。
- 输入只表达归一化方向，不表达角度、速度、电流、PWM、GPIO、I²C、寄存器或脚本。
- 每台设备同时只有一个 Web control lease；lease 绑定 WebSocket connection 和
  当前 device session，Bridge 重启、session 变化、fault、pause、maintenance 或
  preemption 后立即失效。
- 每帧独立带 `input_seq` 和 300–500 ms 的短 TTL；建议默认 400 ms、最大 500 ms，
  输入频率不超过 10 Hz。设备端仍必须用自己的硬/软限位、速率、反馈和卡滞规则。
- 生产/HIL Bridge feature gate 默认关闭。目标固件已编译 parser/state machine，但只有
  host hello 明确协商、真实设备刷写并完成受监督 HIL 后，Bridge 才可打开真实传输门禁。
  在此之前 API/UI 只返回 `capability_unavailable`，不发送 envelope。

## 2. Wire payload

`command.manual_control` 的 payload 由独立 schema
`contracts/phase1/manual-control-v1.schema.json` 约束。示例：

```json
{
  "lease_id": "lease-01J...",
  "input_seq": 17,
  "action": "input",
  "direction": {"yaw": 0.75, "pitch": -0.2},
  "ttl_ms": 400
}
```

释放帧使用同一类型，但不包含方向：

```json
{
  "lease_id": "lease-01J...",
  "input_seq": 18,
  "action": "release",
  "ttl_ms": 400
}
```

字段规则：

| 字段 | 约束 |
| --- | --- |
| `lease_id` | Bridge 生成的非空 bounded ID；设备只接受当前 session 中已协商的 lease |
| `input_seq` | 每个 lease 从 1 开始严格递增；回退、跳号、旧 lease 一律拒绝 |
| `action` | 仅 `input` 或 `release`；release 不产生新的目标 |
| `direction.yaw/pitch` | 浮点数 `[-1, 1]`；`0` 表示该轴不变；不代表角度或速度 |
| `ttl_ms` | 整数 `300..500`；设备以收到帧时的本地 monotonic 时钟计算失效 |

设备端拥有并固定保存 `max_step_deg`、`max_rate_deg_per_s`、硬限位、软限位、
反馈新鲜度和 stall threshold；这些值不能由浏览器或 Agent 写入。方向到目标的
映射必须在设备端再次经过 `SafetyGate` 和独立 `ServoIoTask`，不能在 Bridge 中
生成 raw servo command。

## 3. Lease 状态机

```text
NONE ── online + capability + no safety block ──► ACTIVE
ACTIVE ── valid input/renew ─────────────────────► ACTIVE
ACTIVE ── release ───────────────────────────────► RELEASED
ACTIVE ── TTL/max lifetime/WS close/blur ────────► EXPIRED
ACTIVE ── safety/pause/maintenance/priority ─────► PREEMPTED
ACTIVE ── device/session change or Bridge restart ► INVALID
```

Lease 至少记录：`lease_id`、`device_id`、`session_id`、`connection_id`、
`issued_at`、`expires_at`、`max_expires_at`、`last_input_seq` 和 state。续租只延长
到 `max_expires_at`，不能无限续期。没有 Web 用户身份；`connection_id` 只用于
失焦/断链撤销，不是认证主体。

## 4. Bridge 与浏览器语义

W3 API 使用 `/api/v1/devices/{id}/control-leases` 创建 lease、
`/api/v1/control-leases/{lease_id}/renew` 续租、DELETE 释放；正常输入走
`WS /api/v1/events` 所属的已绑定控制连接，HTTP input 只是故障回退。

浏览器必须在 `pointerup`、`keyup`、`blur`、`visibilitychange` 和 WebSocket close
时发送 release；release 丢失时，Bridge 自己的 500 ms watchdog 和设备 TTL 必须让
输入失效。输入失效后不能继续提交旧 lease 的目标，也不能把离线输入排队到重连后执行。

固定优先级仍为：

```text
local emergency > SafetyLoop/fault > Web emergency > local pause
> manual lease > maintenance > batch > Agent/autonomy
```

抢占必须产生 `PREEMPTED` CommandRecord 和 audit event。远程急停仍是尽力送达，
不能冒充设备本地急停。

## 5. 设备端处理顺序

设备 parser 按以下顺序处理，每一步失败都不产生运动：

1. 校验 `lifeos.v1` envelope、payload schema、大小和有限数值。
2. 校验当前 hello/session、设备身份、`manual_control_v1` capability、lease ID
   和严格 `input_seq`。
3. 以设备本地 monotonic 时钟校验 TTL；超时帧不能恢复旧目标。
4. 读取独立 safety snapshot；fault、pause、emergency、反馈冻结、维护状态直接
   safety-blocked/preempted。
5. 将归一化方向映射到 bounded target，执行硬/软限位、rate、feedback 和 stall
   检查，再写入 servo mailbox；safety loop 不等待 Web、Bridge 或 UART。
6. release/过期/断链清空连续输入并回安全 idle；torque/VM 策略遵循设备当前安全状态。

## 6. 验证门禁

### Host/fake

- schema 正反例：未知字段、NaN、越界方向、TTL、`input_seq`、lease/session/device
  不匹配全部拒绝。
- lease 获取、续租上限、release、TTL、最大期限、WebSocket close/blur 和 Bridge
  restart 均使旧输入失效。
- fake transport 支持延迟、乱序、重复、ACK 丢失、fault/pause/preemption；不执行
  raw hardware field。
- 10 Hz 上限、单 lease、priority/preemption、`PREEMPTED` 和 audit 证据可回放。

### Firmware/replay/HIL

- parser/state machine、固定容量 mailbox、generation cancellation 和
  `ServoIoTask` 单 UART owner 有 host tests。
- simulator/回放覆盖 input seq、TTL、断链、fault、越界和 no-auto-resume。
- 首次实机只允许 motion-disabled capability/handshake/status 验证；运动验收必须在
  `docs/phase1-acceptance.md` 的整体出口闭合后，使用受监督 HIL 并重新核验设备身份、
  backup hash 和镜像 hash。

## 7. Feature-gate 与迁移

`manual_control_v1` 在 production、HIL 和 Web API 默认关闭。启用前必须同时提交：

- payload schema 和协议注册表更新；
- firmware parser、lease expiry、SafetyGate/ServoIo integration；
- host mapper、fake/replay/contract tests；
- 实机无运动 handshake、受监督运动、失焦/断链、fault、急停、限位和恢复报告。

在这些证据齐全前，API 返回：

```json
{
  "code": "capability_unavailable",
  "reason": "feature_gate_disabled",
  "required_capability": "manual_control_v1"
}
```

当前仓库已实现 `bridge/leases.py`、gated `/api/v1/control`、设备端 parser/state machine
和固定小步长映射；production/HIL target build 已通过，默认配置仍不会发送
`command.manual_control`。真实设备当前仍运行旧镜像，刷写、实机失焦/断链、故障、限位和
受监督运动 HIL 尚未完成，因此不能把 target build 当作真实能力验收。

## 8. 被拒绝方案

- 浏览器发送绝对 yaw/pitch、速度、电流或 raw servo fields：绕过设备安全常量。
- 把输入拼成 `command.maintenance_motion`：维护命令权限和语义不等价。
- 只依赖 Bridge TTL：设备断链时必须由本地安全路径独立失效。
- 用 Web release 作为唯一停止机制：release 丢包时必须由 watchdog/设备 TTL 收敛。
- 在当前尚无 parser/HIL 证据时打开 capability：会把设计草案误写成已支持功能。
