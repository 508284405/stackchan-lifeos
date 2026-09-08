# Web Bridge W3 Host-only Lease 验收报告

日期：2026-09-02  
范围：`ControlLease`、dead-man 语义、设备端 `manual_control_v1` 实现和真实传输门禁

## 结论

Host-side lease/input 语义 **PASS**；设备端 parser/state machine、真实 production capability
handshake、UI 和真实 lease acquire/release **PASS**；实体运动输入与运动 HIL **NOT TESTED**。
当前 Bridge 通过显式 flag 打开 gate，但本轮没有发送 `command.manual_control`。

## 已实现

- `bridge/domain/records.py`：`ControlLease` 与 `LeaseState`，包含 device/session/
  WebSocket connection binding、短 TTL、最大生命周期和 input sequence。
- `bridge/leases.py`：单设备单 lease、renew 上限、release、TTL expiry、session/
  connection 校验、10 Hz 限制、归一化 `yaw/pitch ∈ [-1,1]` 校验。
- `bridge/service.py`：显式 `manual_control_v1` gate、lease audit、`command.manual_control`
  mapper/dispatcher、session 断开失效；真实 USB 还要求显式 verified transport 标记。
- `bridge/api.py`：gated `/api/v1/control` WebSocket；服务器生成 connection ID，
  不接受浏览器 priority、seq、device envelope 或 raw servo 字段；generic
  `manual_control` command endpoint 即使 gate 显式打开也要求 control lease；独立
  `POST /api/v1/devices/{id}/emergency-stop` 走高优先级 hook。
- `bridge/intent.py`：行为注册表、强度/时长上限、speech 文本/voice allowlist，统一
  映射为高层 `command.intent`；behavior/speech gates 默认关闭。
- `contracts/phase1/manual-control-v1.schema.json` 与
  `docs/rfc/0002-manual-control-v1.md`：payload、安全边界和 HIL 前置条件。
- `firmware/include/lifeos/protocol/protocol.hpp` / `protocol.cpp`：在显式
  `LIFEOS_MANUAL_CONTROL_V1=1` 的 target build 中提供 payload parser；`TargetRuntime` 增加
  lease、seq、TTL、固定小步长、SafetyGate/ServoIo 映射和 host hello capability 协商。

## 验证状态

| 项目 | 状态 |
| --- | --- |
| lease acquire/renew/release/preempt/expiry | PASS |
| device/session/connection 绑定 | PASS |
| 300–500 ms TTL、最大 lease lifetime | PASS |
| 10 Hz input limit、strict input sequence | PASS |
| normalized direction、raw field/NaN/range rejection | PASS |
| WebSocket close 自动 preempt | PASS |
| 独立 emergency-stop endpoint、幂等、普通命令 preempt | PASS |
| 默认 feature gate/非 fake transport 拒绝且不发送 wire command | PASS |
| fake enabled input ACK/completion | PASS |
| C++ manual_control_v1 parser positive/negative cases | PASS |
| behavior/speech allowlist、TTL、fake mapper | PASS |
| Web behavior preempts lower-priority Agent intent (fake) | PASS |
| firmware parser / fixed mailbox / SafetyGate integration | production/HIL target build PASS；实机 capability handshake PASS |
| real USB capability handshake / manual UI / lease acquire-release | PASS；未发送实体输入 |
| real motion / emergency-stop timing / mechanical HIL | NOT TESTED；仍受 Phase 1 总出口约束 |
| Agent/LangGraph real dispatch and device behavior/speech execution | NOT IMPLEMENTED |

## 下一门禁

下一步完成失焦/断链、TTL、故障、限位和受监督实体 HIL，验证方向、反馈、急停和机械卡滞。
真实运动继续受 `docs/phase1-acceptance.md` 的整体出口约束；`--enable-manual-control` 只应
在现场有人值守、机械范围清空且实体急停可立即操作时使用。
