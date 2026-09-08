# Web Bridge Host API 契约

本目录定义浏览器到主机 Web Bridge 的 W1-W4 REST、W2 监控、W3 控制和媒体边界，机器可读事实源是
[`openapi.yaml`](openapi.yaml)。设计依据是仓库根目录的 [`DESIGN.md`](../../DESIGN.md)
和 [`docs/rfc/0001-web-bridge-fleet-control.md`](../../docs/rfc/0001-web-bridge-fleet-control.md)。

这不是设备协议。`contracts/phase1/envelope.schema.json` 仍是 `lifeos.v1` USB JSONL
wire envelope 的唯一事实源；浏览器 DTO 必须经过 Bridge 的高层命令 mapper，不能直接
成为设备消息。

## W1 范围

W1 仅冻结以下 5 类 REST 操作：

| 方法 | 路径 | 语义 | 成功状态 |
| --- | --- | --- | --- |
| `GET` | `/api/v1/health` | Bridge 与部署状态 | `200` |
| `GET` | `/api/v1/devices` | 已注册设备摘要，返回 `{ "items": [...] }` | `200` |
| `POST` | `/api/v1/devices/{device_id}/claim` | 认领已发现 candidate | `200` |
| `POST` | `/api/v1/devices/{device_id}/commands` | 创建高层离散命令 | `202` |
| `GET` | `/api/v1/commands/{command_id}` | 查询 CommandRecord 生命周期 | `200` |

claim 的 `candidate_id` 必须来自 Bridge discovery，且 candidate 的稳定
`device_id` 必须与路径一致；客户端不能借此注册或伪造设备身份。W1 的 `devices` 是
当前快照，不提供分页、筛选或候选发现 API。

## 请求字段严格性

所有 JSON body 都必须是对象，并使用 `application/json`。OpenAPI 中的请求 schema
均为 `additionalProperties: false`；未知字段、缺少必填字段、错误类型或越界值都拒绝。

### claim

请求体只有一个必填字段：

```json
{ "candidate_id": "candidate-fake-usb-01" }
```

`candidate_id` 是非空字符串，最长 96 个字符。不能额外提交 `device_id`、
`hardware_id`、`display_name`、`session_id` 或任意身份字段。

### commands

请求体字段固定为：

| 字段 | 必填 | 约束 |
| --- | --- | --- |
| `type` | 是 | 1–64 字符；允许控制命令、`emergency_stop`、`behavior.play`、`speech.play`，以及仅用于返回 feature-gated `409` 的 `manual_control` |
| `params` | 否 | 对象，默认 `{}`；只允许 `reason` 与 `local_confirmation` |
| `ttl_ms` | 否 | 整数，`1..1500`，默认 `1500`；Bridge 不接受更长 TTL |
| `idempotency_key` | 否 | 非空字符串，最长 96；按 `device_id` 作用域，用于 HTTP 重试去重 |
| `correlation_id` | 否 | 非空字符串，最长 96；用于关联请求/记录 |

`params.reason` 最长 128 个字符。`params.local_confirmation` 只有在
`control.clear_fault` 中才允许出现，且值必须严格为 `true`；它不是维护 API，也不
替代设备本地安全确认。客户端不得提交 `priority`、`source`、`command_id`、
`session_id`、`wire_type`、`schema`、`kind`、`event_id`、`seq`、`ts_ms`、`payload`
或任何其他字段。

W1 命令类型是高层 Web 名称，不是 `lifeos.v1` 类型。浏览器不能提交
`command.control` 等 raw wire type；未知或未注册的高层名称按 `422` 处理。
`manual_control` 在 W1 仅作为显式 capability gate 记录，始终返回 `409`，不发送设备消息。
`behavior.play` 与 `speech.play` 同样必须通过注册表、feature gate 和设备 capability；W3
默认关闭，禁止把请求参数当作 raw device payload。

## HTTP 状态语义

- `200`：读取成功，或 claim 返回已注册的 `DeviceSummary`。它不表示命令已执行。
- `202`：Bridge 已创建并持久化 `CommandRecord`，请求进入命令生命周期；**不表示**
  设备已接受或完成。响应中的 `state` 和后续 command query 才是事实。fake device
  可能在 HTTP 返回前完成，因此 `202` 的 body 也可能已经是 `completed`。
- `404`：目标资源不存在。claim 时是 discovery candidate 不存在；创建命令时是
  `device_id` 未注册；查询时是 `command_id` 不存在。
- `409`：请求与已知身份/能力状态冲突。claim 的路径身份不匹配、已撤销/冲突身份使用
  `ErrorResponse`；命令的 feature gate 或设备协商能力不可用时，Bridge 仍创建可审计的
  `CommandRecord`，状态为 `rejected`，并返回该记录。
- `422`：请求 schema 或高层 mapper 校验失败，包括未知字段、缺失/错误字段、TTL 越界、
  未知命令类型、非法 `params` 或不满足 `clear_fault` 本地确认。请求形状错误返回
  `ErrorResponse`；mapper 拒绝若已创建审计记录则返回 `state: rejected` 的
  `CommandRecord`。两种情况都不能向设备发送 wire command。

命令能力拒绝的 `error.code` 为 `capability_unavailable`，`error.reason` 使用 RFC 约定
的有限集合：`feature_gate_disabled`、`device_not_declared`、
`protocol_version_unsupported`、`firmware_not_ready`。W1 当前主要覆盖前两种。能力检查
必须发生在 transport dispatch 之前。

命令响应中的 `created`、`validated`、`routed`、`sent`、`accepted`、`executing`、
`completed` 以及 `rejected`、`safety_blocked`、`offline`、`timeout`、`expired` 等状态
属于 Bridge 记录。`accepted` 是设备 ACK admission，不等于 `completed`；过期后的迟到
ACK 只能成为 `late_evidence`，不能重新激活命令。

同一 `device_id` 重复使用相同 `idempotency_key` 和命令类型必须查回同一
`CommandRecord`，不得再次执行。该 key 是 HTTP 层去重键，不是第二套设备幂等协议。

## 禁止的控制面

本契约没有、也不得实现以下浏览器入口：

- `POST /serial/write` 或任何 raw serial/USB 写入接口；
- `POST /api/v1/devices/{id}/envelope` 或任何 raw `lifeos.v1` envelope 透传接口；
- 任意 shell、URL fetch、寄存器/PWM/GPIO/I2C/舵机参数写入接口；
- 让浏览器自报 `priority`、`seq`、`event_id`、设备 `device_id` 或 `session_id` 的接口。

Bridge 服务端生成 `command_id`、目标设备、session、priority、sequence 和 wire envelope。
其中 `command_id` 才是设备 envelope 的 `event_id`；浏览器的 `idempotency_key` 不得
变成另一套 wire 幂等键。CommandRecord 中的 `wire_type`/`payload` 是服务端记录，不是
可回放的请求 envelope。

## 无认证可信内网边界

W1 有意不提供 Web 登录、token、用户身份、角色或 RBAC；OpenAPI 全局 `security: []`。
任何能访问监听地址的客户端都可能获得完整 Web 控制能力，因此此模式不能称为“已认证”。

部署约束如下：

1. 默认只监听 `127.0.0.1`（或等价 loopback）。
2. 监听具体私网接口前，必须显式设置 `web_no_auth_trusted_lan=true`；不得默认绑定
   `0.0.0.0` 或其他通配地址。
3. 由主机防火墙、VLAN 或可信 Wi-Fi 限制来源，并为 CORS/WebSocket Origin 使用显式
   allowlist。网络绑定与 Origin 限制是来源约束，不是用户认证。
4. 禁止端口映射、反向代理公网暴露、公共 DNS 和公网访问。需要跨公网时，必须另行设计
   Web 身份、加密、撤销和审计主体。

设备侧配对、硬件身份、session nonce、seq、TTL、双端 schema 校验和独立 safety loop
仍然强制；无 Web 认证不能削弱这些设备安全边界。

## W2 监控扩展

W2 新增 `GET /api/v1/devices/{device_id}` 设备详情快照和 `WS /api/v1/events`。WebSocket
首条消息必须指定 `device_ids` 或 `visible_devices: true`，可带 `cursor` 和
`include_telemetry`；cursor 超出保留窗口时返回 `resync_required`，客户端重新拉 REST
快照。事件只包含 Bridge domain 字段，health telemetry 可合并，命令/session/safety
事件必须先进入审计存储。部署可以给 API 显式配置 Origin allowlist；配置后非 allowlist
来源以 WebSocket policy close 拒绝。未配置时仍必须保持 loopback 或显式可信内网边界。

## USB 扫描与添加(usb_add 门控)

`POST /api/v1/usb-scan` 与 `POST /api/v1/usb-devices` 允许本机操作者扫描宿主机 USB
串口并把一台设备注册、连接进 Bridge,避免手填 `/dev/cu.*` 路径。约束:

- 两个端点都受 `usb_add` feature gate 控制,默认关闭;随附的
  `tools/web_bridge_browser_server.py` 与 `tools/web_bridge_real_server.py` 显式开启。
- `usb_add` 属于 high-impact gate:trusted-LAN 绑定下开启它必须同时配置非空 Origin
  allowlist(与 maintenance/rollout 同级)。
- `/usb-scan` 对每个候选端口做只读 `hello.host` 握手;已被 Bridge 活跃会话占用的
  端口报告 `in-use` 且绝不探测。打开 USB CDC 会复位 ESP32-S3 Serial/JTAG 外设,
  因此探测是破坏性最小化、且永不发送致动命令的。
- `/usb-devices` 的 `device_id`/`hardware_id` 必须来自扫描结果;重复添加已知硬件
  身份等价于重连,保持原 `display_name`。设备未在超时内完成 hello 时返回 `502`
  并拆除会话。

这不是 raw serial 写入接口:浏览器依旧不接触 wire envelope;端口打开、配对、
hello 与命令映射都发生在 Bridge 服务端。

## W1/W2 未覆盖的后续能力

W1/W2 原始范围不冻结 maintenance、firmware、factory reset、manual control/dead-man 的
操作协议、control lease、audit 或 media API；这些能力必须各自有 RFC、schema、feature gate
和验收证据。当前 W2 UI/event stream、W3 host-only lease 和 W4 host-only 运维扩展已在
独立报告中记录，不能把 host-only 证据解释为真实设备能力。

## W3 host-only 控制 spike

当前 API 还提供 gated `WS /api/v1/control`，服务器生成 connection ID 并绑定
`manual_control_v1` lease。默认 feature gate 关闭；显式打开只适用于 fake transport
测试。连接关闭会 preempt lease，输入使用 300–500 ms TTL、每 lease 严格递增
`input_seq` 和 10 Hz 上限。固件 parser、真实运动和 HIL 证据完成前不得在生产/HIL
配置启用它。另有独立 `POST /api/v1/devices/{device_id}/emergency-stop`，只接受
reason/idempotency/correlation 字段，绕过普通 host queue，但仍是 best-effort，不能
替代设备本地急停。

## Web 控制与摄像头预览

`POST /api/v1/devices/{device_id}/camera-preview` 只接受 `{ "action": "start" }` 或
`{ "action": "stop" }`，并受服务端 `media` gate 与设备 `camera` capability 双重约束。
预览参数由服务端固定为 QVGA JPEG、最多 2 fps、短时会话；浏览器通过
`GET /api/v1/devices/{device_id}/camera/stream` 接收 `multipart/x-mixed-replace`。

摄像头帧只在内存中保留每台设备的最新完整帧，不进入 SQLite、audit、event payload 或
LangGraph。USB 上的 `camera.frame.begin/chunk/end` 是低帧率 bootstrap wire event，浏览器
永远不接触它；缺块、越界、跨 session 和不完整 JPEG 都会被 Bridge 丢弃。高码率或网络媒体
必须另行设计 transport，不能扩大当前 JSONL 隧道。

## W4 host-only batch and operations surface

当前还提供 `POST /api/v1/batch-tasks`、GET 查询和 cancel。目标快照在创建时固化，
每个 target 保留自己的 command ID、状态和错误；`partial` 不回滚已完成目标。取消
只影响尚未进入 dispatch boundary 的 target，已 dispatch 的物理动作继续报告真实结果。
另有 feature-gated maintenance challenge、allowlisted diagnostics 和 rollout task API；
它们只提供 host-only 任务/证据框架，默认不发送 maintenance/OTA wire，真实设备批量运动
仍未验收。W4 的当前边界见 `docs/web-bridge-w4-extension-report.md`。
