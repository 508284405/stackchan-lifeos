# LifeOS 设备协议

状态：Draft  
版本：`lifeos.v1`

本协议是 LifeOS 自有的设备—主机协议，不是 Codex app-server 协议，也不是 CGraph API。它面向低带宽、有序但可能断线的 USB CDC/串行链路；未来局域网传输必须复用相同 envelope 和安全规则。

## 1. 传输与 envelope

每行一个 UTF-8 JSON 对象；禁止嵌入换行。设备和主机都必须拒绝超过 16 KiB 的单条消息，接收方应先按字节上限读取再解析 JSON。

```json
{
  "schema": "lifeos.v1",
  "kind": "event",
  "type": "observation.sensor",
  "event_id": "01J00000000000000000000000",
  "correlation_id": "01J00000000000000000000001",
  "device_id": "stackchan-01",
  "seq": 42,
  "ts_ms": 1730000000123,
  "payload": {}
}
```

字段规则：

| 字段 | 必填 | 规则 |
| --- | --- | --- |
| `schema` | 是 | 当前固定 `lifeos.v1`；未知版本拒绝 |
| `kind` | 是 | `event`、`command`、`ack`、`error`、`hello` |
| `type` | 是 | 小写点分命名；由本协议注册表定义 |
| `event_id` | 是 | 主机或设备生成的全局唯一 ID；重试复用原 ID |
| `correlation_id` | 否 | 关联命令、审批或 Agent run |
| `device_id` | hello 后必填 | 与配对记录完全匹配 |
| `seq` | 是 | 单发送方单调递增；重连后从 hello 协商 |
| `ts_ms` | 是 | 发送方时间；不能作为安全超时唯一依据 |
| `payload` | 是 | 类型化对象；禁止任意代码、路径或模板执行 |

接收方先校验 schema、kind、大小、字段类型和枚举，再按 `event_id` 做幂等处理。未知 payload 字段可忽略；未知必需字段或未知命令类型必须返回 `error.unsupported`。

## 2. Hello 与能力协商

设备连接后先发 `hello.device`，主机返回 `hello.host`。在协商完成前只允许 hello、配对和错误消息。

```json
{
  "schema": "lifeos.v1",
  "kind": "hello",
  "type": "hello.device",
  "event_id": "01J00000000000000000000002",
  "device_id": "stackchan-01",
  "seq": 1,
  "ts_ms": 1730000000200,
  "payload": {
    "firmware": "0.1.0",
    "protocol_versions": ["lifeos.v1"],
    "capabilities": ["touch", "imu", "display", "servo_yaw", "servo_pitch"],
    "safety": {"hard_stop": true, "max_command_age_ms": 1500}
  }
}
```

`hello.host.payload` 至少包含主机协议版本、允许的 observation 类型、命令能力、session nonce 和是否启用原始媒体（默认 `false`）。能力是交集，不得由主机强行开启设备未声明的能力。

## 3. 事件类型

### 3.1 设备事件

| 类型 | payload 核心字段 | 频率/限制 |
| --- | --- | --- |
| `observation.sensor` | `source`, `values`, `quality` | ≤10 Hz；只传数值摘要 |
| `observation.touch` | `zone`, `gesture`, `duration_ms` | 每次触摸一次 |
| `observation.voice` | `transcript?`, `locale?`, `confidence?` | 默认仅传经用户同意的文本 |
| `state.changed` | `from`, `to`, `reason` | 每次变化 |
| `motion.completed` | `action_id`, `result`, `actual` | 每个动作一次 |
| `health.report` | `heap`, `uptime_ms`, `task_heartbeats`, `faults` | 1 Hz |
| `fault.raised` | `code`, `severity`, `latched`, `detail` | 每个故障一次 |

`observation.sensor` 不得默认包含图片、音频或可逆的生物特征。若未来启用媒体，必须单独能力协商、短时授权和加密传输，且不能改变安全路径。

### 3.2 主机事件

| 类型 | payload 核心字段 |
| --- | --- |
| `agent.status` | `run_id`, `phase`, `message?` |
| `agent.intent` | `run_id`, `intent_plan` |
| `approval.requested` | `approval_id`, `risk`, `summary`, `expires_at` |
| `approval.resolved` | `approval_id`, `decision`, `actor` |

Agent 的自然语言、reasoning、主机路径和工具原始输出不能直接下发设备；只能转成 `intent_plan`。

## 4. 命令类型与 schema

### 4.1 通用命令

```json
{
  "schema": "lifeos.v1",
  "kind": "command",
  "type": "command.control",
  "event_id": "01J00000000000000000000003",
  "correlation_id": "01J00000000000000000000004",
  "device_id": "stackchan-01",
  "seq": 9,
  "ts_ms": 1730000000300,
  "payload": {"action": "pause", "reason": "user_touch"}
}
```

`action` 仅允许 `pause`、`resume`、`home`、`status`、`clear_fault`。`emergency_stop` 是独立命令，设备必须在本地立即执行，不等待 ACK。

### 4.2 IntentPlan

```json
{
  "run_id": "01J00000000000000000000005",
  "expires_at_ms": 1730000005300,
  "source": "langgraph",
  "speech": {"text": "你回来啦", "voice": "default"},
  "emotion": {"name": "happy", "intensity": 0.6, "duration_ms": 1800},
  "behaviors": [
    {"name": "greet", "intensity": 0.6, "duration_ms": 1200}
  ],
  "requires_approval": false,
  "idempotency_key": "01J00000000000000000000005"
}
```

约束：

- `expires_at_ms` 超时即拒绝；设备用单调时钟计算年龄，不能信任主机墙上时钟。
- `name` 来自注册表，例如 `blink`、`greet`、`look_at_target`、`sleep`；未知行为拒绝。
- `intensity` 限制在 `[0,1]`；`duration_ms` 和行为数量有设备端上限。
- 计划不得包含坐标、PWM、速度、电流阈值、shell、URL、文件路径或任意表达式。
- 设备可以将计划降级为安全子集；降级必须回 ACK，注明 `accepted`, `clamped` 或 `rejected`。

### 4.3 运动命令

Agent 不直接发送运动命令。仅由本地行为库将 `look_at_target` 等行为解析为设备内部动作；内部动作同样经过 `SafetyGate`。协议层如需调试运动，只允许维护权限下的绝对角度、软限位和最大步长字段，并默认关闭。

## 5. ACK、错误与重试

```json
{
  "schema": "lifeos.v1",
  "kind": "ack",
  "type": "ack.command",
  "event_id": "01J00000000000000000000006",
  "correlation_id": "01J00000000000000000000003",
  "device_id": "stackchan-01",
  "seq": 10,
  "ts_ms": 1730000000350,
  "payload": {"status": "accepted", "idempotent": false}
}
```

状态：`accepted`、`clamped`、`completed`、`duplicate`、`rejected`。错误码：`invalid_schema`、`unsupported`、`unauthorized`、`expired`、`busy`、`safety_blocked`、`fault_latched`、`rate_limited`、`internal`。

只有 `busy`、`rate_limited`、临时链路错误可重试；重试必须复用 `event_id`/`idempotency_key` 并采用指数退避。`safety_blocked` 和 `fault_latched` 必须等待状态变化或人工清除。

## 6. 顺序、背压与断线

- 单方向 `seq` 严格递增；乱序和回退只记录一次并拒绝。
- observation mailbox 深度为 1，最新样本覆盖旧样本；command mailbox 只保留有限条且按优先级处理。
- 急停、暂停和故障事件优先于普通 intent；普通 intent 在断线或过期时全部作废。
- 主机连续 `1500 ms` 未收到设备心跳时，设备侧 safety loop 不再接受普通主机命令；设备进入本地 idle。
- 恢复连接需重新 hello 和 nonce 协商，不能自动重放过期动作。

## 7. 版本与兼容矩阵

协议遵循 `major.minor`：同一 major 内新增可选字段保持兼容；改变语义、删除字段或改变安全默认值必须升 major。每次固件/主机发布都记录：协议版本、固件版本、LangGraph graph 版本、Codex CLI 版本、CGraph adapter 版本。

## 8. 参考资料

- [OpenAI Codex app-server 协议说明](https://github.com/openai/codex/blob/main/codex-rs/app-server/README.md)（仅用于主机 Adapter，非设备协议）
- [LangGraph 流式事件](https://docs.langchain.com/oss/python/langgraph/streaming)
