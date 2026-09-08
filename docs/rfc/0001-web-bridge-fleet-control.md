# RFC 0001: Web Bridge 多设备管理与远程控制

- 状态：Draft for review
- 日期：2026-08-29
- 范围：主机桥接层、Web API、Web Console、设备注册与会话路由、Edge Agent 演进
- 不包含：本轮实现、公共互联网暴露、Web 用户账号/RBAC、200 台性能验收
- UI 决策源：`DESIGN.md`
- 实施计划：`docs/web-bridge-implementation-plan.md`

## 1. 决策摘要

1. 在浏览器与现有 Device Gateway 之间新增 Web Bridge；浏览器不能直接连接
   串口，也不能提交任意设备 envelope。
2. Web 端面向可信内网中的单一操作者，不做登录、用户账号、管理员/普通用户、
   RBAC 或多操作者租约竞争。
3. 无 Web 认证不等于无设备身份。每台设备仍须有稳定 `device_id`、硬件身份、
   配对记录、session nonce、独立 seq 与幂等窗口。
4. 初始交付必须让一台真实 USB 设备完成端到端闭环；所有多设备代码路径从第一天
   使用 registry/session/router/task 模型，避免把全局单例状态扩展成舰队系统。
5. 产品目标为 200 台设备；本阶段不验证 200 台，也不把未测容量写成 SLO。
   未来达到真实使用规模后再以观测数据建立容量门禁。
6. USB、Wi-Fi、4G 是可替换 transport。设备 wire contract 保持 `lifeos.v1`，
   不因网络传输另造第二套命令协议。
7. 支持只读监控、安全控制、行为/语音、有限手动运动、维护、升级与媒体；按阶段
   交付，但接口和权限边界一次设计完整。
8. 批量命令采用 best-effort fan-out，结果按设备持久化，允许 `partial`。
9. 远程操作者拥有全部产品功能，但不能绕过固件的硬限位、软限位、过流、卡滞、
   急停、故障锁存或独立 safety loop。
10. 首版不跨公网；未来多站点采用 Edge Agent/设备主动出站连接，不向设备开放
    公网入站端口。

## 2. 背景与问题

当前架构明确是单主机、单设备、USB CDC。`brain/api.py` 的 FastAPI 原型只有一个
全局 `LifeState`，其 `/events` 和 `/ws` 是认知事件入口，不具备设备发现、设备注册、
连接会话、命令路由、ACK 跟踪、批量任务、审计、维护或媒体能力。

直接在当前 `/ws` 上增加 `device_id` 会产生以下结构性错误：

- 多台设备共享一个状态锁和一个 `LifeState`，状态与行为可能串台；
- 浏览器协议、认知域模型和设备 wire protocol 混为一体；
- 无法独立维护每台设备的 hello、nonce、seq、TTL、ACK 和断线状态；
- 无法表达一次批量命令的逐设备部分失败；
- UI 可能把 HTTP 返回当成设备已执行；
- 重连或 Bridge 重启可能错误重放旧运动命令。

因此本 RFC 引入独立 Web Bridge 边界，同时保留 `lifeos.v1` 和设备 safety contract。

## 3. 目标与非目标

### 3.1 目标

- 一台 USB 设备也完整经过注册、会话、路由、命令、结果和审计模型。
- 多设备状态严格按 `device_id` 和 session 隔离。
- Web 页面同时承担运维控制台和可视化遥控器。
- 每个命令具有唯一 ID、短 TTL、明确生命周期和设备终态。
- 手动控制采用 dead-man 语义，浏览器失焦/断链后不会持续运动。
- 批量命令能准确展示各目标的 `accepted/completed/rejected/offline/timeout`。
- 为 Wi-Fi、4G、多个 Edge Agent 和最终 200 台目标保留稳定边界。

### 3.2 非目标

- 当前阶段不实现公网控制、云端账号、SSO、RBAC 或多租户。
- 不保证不同网络设备的毫秒级动作同步。
- 不允许浏览器发送 PWM、GPIO、I2C、原始舵机速度、电流阈值或任意脚本。
- 不把 WebSocket 当作 Codex app-server 的生产 transport。
- 不用 1 台设备或轻量 fake test 推导“已支持 200 台”。

## 4. 总体架构

### 4.1 初始单站点拓扑

```text
Trusted LAN browser
        │ HTTP + WebSocket
        ▼
┌──────────────────────── Web Bridge ─────────────────────────┐
│ Web API / event stream                                     │
│ Device Registry ─ Session Manager ─ Command/Batch Service  │
│ Policy & Arbitration ─ Audit/Telemetry ─ Media Coordinator │
└──────────────────────────┬──────────────────────────────────┘
                           │ Transport interface
                  ┌────────┴────────┐
                  │ USB CDC adapter │  initial
                  └────────┬────────┘
                           │ lifeos.v1 JSONL
                           ▼
                  M5Stack / StackChan
                           │
                  SafetyGate + SafetyLoop
```

### 4.2 未来多站点拓扑（未实现）

```text
                         Central Control Plane
                    registry / tasks / audit / Web
                         ▲              ▲
                    outbound mTLS  outbound mTLS
                         │              │
                    Edge Agent A    Edge Agent B
                    ├─ USB device   ├─ USB device
                    └─ Wi-Fi device └─ LAN device

Future 4G device ── outbound secure session ──► nearest Edge/control endpoint
```

控制面只看逻辑设备与 Edge session，不持有远端 USB 文件描述符。Edge Agent 负责本地
发现、设备 transport、短时命令队列、心跳和断线上报。跨站点协议另行版本化，但其中
承载的设备语义必须由 `lifeos.v1` mapper 产生，不能把浏览器 JSON 透传到固件。

## 5. 组件边界

| 组件 | 责任 | 明确不负责 |
| --- | --- | --- |
| Web Console | 展示状态、采集明确操作、显示逐设备结果 | 判断设备安全、生成原始 envelope |
| Web API | 输入校验、命令/任务查询、事件订阅 | 直接读写串口 |
| Device Registry | 稳定身份、名称、站点、能力与期望配置 | 实时 session I/O |
| Discovery Service | 枚举 USB/网络候选、读取 hello 身份 | 自动信任未知设备 |
| Session Manager | 每设备 hello、nonce、seq、heartbeat、freshness | 跨设备共享 seq 或 ACK |
| Command Service | 创建命令、TTL、幂等、状态流转 | 绕过 Policy/SafetyGate |
| Batch Service | 固化目标快照、fan-out、聚合结果 | 事务式全体回滚 |
| Arbitration | 来源优先级、手动租约、抢占原因 | 覆盖设备本地安全状态 |
| Transport Adapter | 字节帧、连接生命周期、背压 | 改写业务权限或动作语义 |
| Audit Store | 不可变操作、结果和安全事件 | 保存密钥或原始媒体 |
| Media Coordinator | 媒体会话协商与生命周期 | 把媒体塞入 JSONL 控制通道 |
| Edge Agent | 远端站点发现、session 与出站连接 | 提供公开设备入站端口 |

实现时 `brain/api.py` 的认知事件 API 与 Web Bridge API 分开部署或分开 router；不能
继续共享一个模块级 `LifeState`。LangGraph state 按设备/thread 管理，设备连接状态
由 Bridge 自己的 session 模型管理。

当前 Phase 1 ESP-IDF 固件实际使用 host-first hello（Bridge 先发 `hello.host`，设备回
`hello.device`）；为兼容协议示例和 fake transport，W1 Bridge 也接受 device-first hello。
两种顺序都必须在 capability 交集和身份校验完成后才进入 `ONLINE`，不得把检测到串口
直接视为在线。

## 6. 核心领域模型

### 6.1 DeviceRecord

| 字段 | 说明 |
| --- | --- |
| `device_id` | 稳定 UUID；系统关联键，不随 USB 口/IP/名称变化 |
| `hardware_id` | eFuse/MAC/设备序列等硬件身份摘要 |
| `display_name` | 操作者可修改名称 |
| `site_id` | 初始为 `local`，未来指向 Edge site |
| `transport_hint` | 最近使用的 USB/Wi-Fi/Edge transport，不是身份 |
| `firmware_version` | 最近 hello 报告值 |
| `protocol_version` | 当前协商协议 |
| `capabilities` | 设备声明与 Bridge allowlist 的交集 |
| `desired_config_version` | 期望配置版本 |
| `last_seen_at` | 最近可信心跳时间 |
| `lifecycle_state` | registered/revoked/maintenance 等 |

### 6.2 DeviceSession

| 字段 | 说明 |
| --- | --- |
| `session_id` | 每次连接新建，不复用旧 session |
| `device_id` | 所属设备 |
| `edge_id` | `local` 或远端 Edge Agent |
| `transport_id` | 当前物理/网络连接实例 |
| `nonce` | hello 协商值，不写普通日志 |
| `rx_seq/tx_seq` | 每设备、每 session 独立序列 |
| `state` | session 状态机 |
| `connected_at/last_heartbeat_at` | freshness |
| `capabilities` | 本 session 协商能力 |
| `active_control_lease` | 可空；只有手动连续控制使用 |

### 6.3 CommandRecord

| 字段 | 说明 |
| --- | --- |
| `command_id` | 全局唯一，也是幂等关联键 |
| `device_id/session_id` | 固定目标；session 变化后不自动迁移 |
| `source` | web/manual/batch/agent/maintenance/system |
| `type` | 注册表中的命令类型 |
| `payload` | 经主机 schema 校验的有界数据 |
| `priority` | 来源与安全规则决定，不信任浏览器自报 |
| `issued_at/expires_at` | Bridge 单调/墙钟记录与短 TTL |
| `state` | 命令状态机 |
| `correlation_id` | 关联 Web 请求、批量任务或 Agent run |
| `result/error` | 结构化终态；保留设备 ACK 原因的安全子集 |

### 6.4 BatchTask/BatchTarget

创建批量任务时立即固化目标设备快照，后加入筛选集合的设备不会收到旧任务。

```text
BatchTask
  task_id, command_template, created_at, expires_at, aggregate_state
  targets[] -> device_id, command_id, state, result, finished_at
```

`aggregate_state` 允许 `pending/running/completed/partial/failed/cancelled/expired`。
`partial` 是明确终态，不触发已成功设备的补偿或回滚。取消只阻止尚未 dispatch 的目标；
已被设备接受的动作依照该命令自己的中止语义处理。

每个批量任务具有全局 deadline，每个目标拥有独立 TTL、重试预算和终态：fan-out 前已离线
记为 `offline`；发送后掉线依据目标命令进入 `timeout/offline`；只允许 `busy`、
`rate_limited` 和临时 transport error 使用同一 `command_id` 独立重试。任务取消后，未发送
目标记为 `cancelled`，已发送/接受目标继续报告自身真实终态，聚合层不能覆盖它们。

## 7. 状态机

### 7.1 设备会话

```text
DISCOVERED
   └─ claim/local confirmation ─► REGISTERED
REGISTERED
   └─ transport connected ──────► NEGOTIATING
NEGOTIATING
   ├─ hello/capability valid ───► ONLINE
   ├─ invalid identity/schema ──► REJECTED
   └─ timeout/transport lost ───► OFFLINE
ONLINE
   ├─ stale heartbeat ──────────► DEGRADED
   ├─ transport lost ───────────► OFFLINE
   └─ maintenance requested ────► MAINTENANCE
DEGRADED ─ fresh hello/session ─► ONLINE
DEGRADED ─ stale timeout/lost ──► OFFLINE
MAINTENANCE
   ├─ completed/cancelled ──────► ONLINE
   ├─ transport lost ──────────► OFFLINE
   └─ safety fault ────────────► DEGRADED
OFFLINE  ─ new connection ──────► NEGOTIATING
REJECTED ─ new explicit claim ─► REGISTERED
ANY      ─ identity revoked ────► REVOKED
```

`ONLINE` 必须表示 hello 完成、设备身份匹配、协议已协商且心跳新鲜；检测到 USB 口不等于
在线。重连总是建立新 session，旧 session 的普通命令不迁移、不重放。

进入 `OFFLINE/REVOKED` 必须撤销 control lease，并使该 session 的非终态物理命令进入
`OFFLINE/EXPIRED`。Bridge 重启后所有连接从 `NEGOTIATING` 重新 hello；持久化记录不能让
session 直接恢复为 `ONLINE`。`hello.host` 协商新的 session nonce 和双方 seq 起点；Edge
只转发 frame，不改变设备发送方身份，也不能把另一条 Edge/control 序列当作设备 seq。

### 7.2 命令生命周期

```text
CREATED → VALIDATED → ROUTED → SENT → ACCEPTED → EXECUTING → COMPLETED
    │         │          │       │        │           │
    └─────────┴──────────┴───────┴────────┴───────────┴─► terminal failure

terminal failure:
REJECTED | SAFETY_BLOCKED | OFFLINE | TIMEOUT | EXPIRED | PREEMPTED | CANCELLED
```

- HTTP `202` 只表示 Bridge 创建了 `CommandRecord`，不是设备接受。
- `ACCEPTED` 来自设备 `ack.command`；不等于执行完成。
- 支持完成事件的动作以 `COMPLETED` 为正常终态；仅 admission 语义的命令可以把
  `ACCEPTED` 定义为文档化终态。
- `TIMEOUT/EXPIRED` 后到达的 ACK 记录为 late evidence，但不能把已过期动作重新激活。

设备 `ack.command.payload.status` 映射为：`accepted → ACCEPTED`，
`clamped → ACCEPTED` 并记录实际限制，`completed` 或以 command `event_id` 为
`correlation_id` 的 `motion.completed → COMPLETED`，`duplicate` 查回同一 `event_id` 的
已有状态而不是新执行，`rejected → REJECTED`；错误码 `safety_blocked/fault_latched`
分别进入 `SAFETY_BLOCKED` 或带 fault 原因的拒绝终态。
ACK 超时可以按协议允许条件复用同一 `event_id` 重试；late ACK 只追加 evidence，原
`TIMEOUT/EXPIRED` 终态保持不变。

## 8. 控制仲裁与安全

### 8.1 固定优先级

```text
设备本地急停
  > 图外 SafetyLoop / 故障锁存
  > Web 急停
  > 设备本地暂停
  > Web 手动控制租约
  > 维护任务
  > 批量离散命令
  > LangGraph Agent 行为
  > 设备自主行为
```

只有一个 Web 操作者，因此不解决人与人之间的租约竞争；仍必须解决 Web 手动控制、
批量任务、Agent 和本地自主行为的竞争。抢占产生 `PREEMPTED` 终态和审计事件。

### 8.2 Dead-man 手动控制

1. 浏览器请求一台设备的短期 control lease。
2. Bridge 确认设备在线、具备能力、未 fault/paused/maintenance，并暂停低优先级行为。
3. 浏览器按住控件后最多以 10 Hz 发送归一化方向或受限目标，不发送 PWM/电流。
4. 每帧带递增 input sequence 和 300–500 ms TTL；乱序帧拒绝。
5. 浏览器 `pointerup/keyup/blur/visibilitychange/WebSocket close` 立即发送 release；
   即使 release 丢失，TTL/heartbeat 也使控制自动失效。
6. Edge/Bridge 把输入映射为设备已注册的维护运动动作；固件再次执行角度、步长、
   速率、反馈和卡滞检查。
7. 失效后停止接受连续目标，进入安全 idle；是否释放扭矩由设备安全状态决定。

批量实时摇杆不支持。需要多设备动作时，发送带短 TTL 的预定义行为任务，并接受网络
条件导致的非精确同步。

当前 Phase 1 固件没有通用 Web 手动控制 wire command。该能力必须作为版本化协议扩展
（暂名 `manual_control_v1`）实现，并在 `hello.device.capabilities` 中显式声明；未声明能力
时 Bridge 和 UI 都必须禁用该控件。协议扩展仍只接收有界绝对目标或归一化方向及短 TTL，
不得暴露 PWM、电流、寄存器或任意速度流。

### 8.3 远程完全权限的边界

Web Console 可以发起：暂停、恢复、回正、急停、行为、语音、维护运动、校准、清故障、
重启、网络配置、日志导出、固件升级和恢复出厂。但“完全权限”不包括：

- 禁用或放宽固件绝对硬限位、过流/过温、卡滞、watchdog 和急停；
- 普通命令清除设备本地急停或故障锁存；
- 未签名固件、任意 shell、任意 URL/路径、任意寄存器或串口透传；
- 将过期/离线运动排队后自动执行。

清故障、校准、恢复出厂和升级属于 maintenance command，必须显示影响并记录审计。
Web 没有 maintainer 用户角色；权限来自单人内网部署，但设备仍按现有安全契约要求本地
触摸或进入 maintenance mode。Bridge 只能等待带 session/nonce 的本地确认事件，不能
用 Web 点击或浏览器字段伪造确认；确认过期、设备重连或 session 变化后必须重新确认。

当前 `lifeos.v1` 尚未注册可证明本地确认的 wire flow。实施维护写操作前必须增加版本化
扩展：Bridge 发送 `command.maintenance_prepare`，payload 绑定 `operation`、一次性
`challenge_id` 和短 expiry；设备显示/提示后只接受固件定义的本地触摸动作，并在同一
session 以 `maintenance.confirmed` 事件返回 `operation/challenge_id/result/valid_for_ms`。
实际维护命令必须引用该 challenge，设备端再次验证一次性、operation、session 和有效期。
challenge 在使用、超时、重连、取消或故障后失效。该扩展必须同步更新协议注册表、schema、
固件 parser/state machine、回放与 HIL；完成前相关 API 返回 `capability_unavailable`。

## 9. 功能面

### 9.1 监控

- 设备身份、名称、站点、transport、固件/协议版本和能力。
- 产品状态、在线 freshness、当前行为、活动命令、暂停/故障/急停。
- heap、uptime、task heartbeat、舵机目标/反馈、安全故障等有界 telemetry。
- UI 只订阅可见设备摘要；选中设备才订阅更详细数据。

### 9.2 安全与离散控制

- `status/pause/resume/home/emergency_stop`。
- 急停独立优先，重复请求幂等。
- `resume/home/clear_fault` 必须返回设备实际拒绝原因，不能前端乐观成功。

### 9.3 行为、表达和语音

- 只允许设备 hello 声明且 Bridge 注册表允许的行为。
- 文本朗读限制长度、语言、voice 和 TTL；断线不排队。
- 表情/行为具有 intensity、duration 和数量上限。
- Agent 与 Web 使用同一高层行为注册表，但 Web 手动操作优先级更高。

### 9.4 维护

- 校准过程是显式 session，进入时暂停普通行为，退出/断线时回安全状态。
- 网络配置先写候选配置并验证，再切换；失败保留旧连接配置。
- 诊断包只包含 allowlist 字段、版本、最近错误和脱敏日志。
- 恢复出厂是明确的不可逆任务；设计保留二次 consequence UI，但不以用户密码确认。

### 9.5 固件升级

- 只接受签名镜像和声明兼容的硬件/bootloader/partition/protocol 组合。
- 升级前暂停、回正并释放扭矩；无法确认安全姿态则拒绝升级。
- 优先使用 A/B 分区或等价回滚；启动后通过 hello、版本、健康和无运动自检。
- 批量升级使用 canary/分批/失败阈值；首版只有一台设备时仍走同一 rollout task。
- 升级前命令和 lease 全部失效，重启后不重放。

### 9.6 媒体

- 文本/状态/控制仍走 Web API 和 `lifeos.v1`。
- 实时音视频使用独立媒体 transport；不能作为 JSONL payload。
- 默认不录制；媒体 session 有显式开始、可见指示和短时生命周期。
- 首版可先提供低帧率诊断画面；双向语音和实时视频在硬件/带宽通过独立验收后启用。

单设备 USB 低帧率诊断预览的具体 bootstrap 例外、固定 QVGA/JPEG 预算和 Bridge
最新帧出口见 `docs/rfc/0005-web-control-camera-preview.md`；该例外不改变高码率
视频必须使用独立 media transport 的长期决策。

以上是完整产品能力边界，不表示当前 wire/固件已经支持。manual control、网络配置、
固件升级、恢复出厂和媒体分别需要协议 schema、固件 parser/executor、回滚与 HIL 证据；
在对应扩展完成前 API 必须返回 capability unavailable，不能透传任意 payload。

## 10. Web API 草案

API 使用 `/api/v1`；所有写操作产生 `command_id` 或 `task_id`。Web 端不做认证，部署必须
限制在显式可信内网。JSON Schema/OpenAPI 是浏览器 API 的事实源，但不能复用为固件
wire schema。

| Method | Path | 语义 |
| --- | --- | --- |
| GET | `/api/v1/health` | Bridge 进程、存储和 adapter 健康 |
| GET | `/api/v1/devices` | 分页查询设备摘要 |
| GET | `/api/v1/devices/{id}` | 设备身份、能力和当前 session |
| POST | `/api/v1/devices/{id}/claim` | 认领发现设备 |
| PATCH | `/api/v1/devices/{id}` | 名称、站点和非安全元数据 |
| GET | `/api/v1/devices/{id}/telemetry` | 有界历史/聚合 telemetry |
| POST | `/api/v1/devices/{id}/commands` | 创建注册表中的离散命令 |
| POST | `/api/v1/devices/{id}/emergency-stop` | 独立优先、幂等的尽力急停请求 |
| GET | `/api/v1/commands/{command_id}` | 查询命令生命周期 |
| POST | `/api/v1/devices/{id}/control-leases` | 获取手动控制 lease |
| POST | `/api/v1/control-leases/{lease_id}/renew` | 在最大期限内续租 |
| DELETE | `/api/v1/control-leases/{lease_id}` | 主动释放 lease |
| POST | `/api/v1/control-leases/{lease_id}/inputs` | 备用 HTTP 输入；正常走 WS |
| POST | `/api/v1/batch-tasks` | 对固化目标集创建 best-effort 任务 |
| GET | `/api/v1/batch-tasks/{task_id}` | 聚合与逐设备结果 |
| POST | `/api/v1/maintenance-tasks` | 校准、升级、恢复等长任务 |
| GET | `/api/v1/audit` | 分页审计查询 |
| POST | `/api/v1/diagnostic-bundles` | 创建脱敏诊断包 |
| WS | `/api/v1/events` | UI 状态、安全、命令和任务事件 |

禁止提供：`POST /serial/write`、`POST /devices/{id}/envelope`、任意 shell、任意 URL fetch、
任意寄存器写入或浏览器自报 priority。

能力拒绝使用 HTTP `409`，同时创建终态为 `REJECTED` 的审计 CommandRecord：

```json
{
  "command_id": "cmd-01J...",
  "state": "rejected",
  "code": "capability_unavailable",
  "reason": "feature_gate_disabled",
  "required_capability": "manual_control_v1"
}
```

`reason` 只允许 `feature_gate_disabled`、`device_not_declared`、
`protocol_version_unsupported` 或 `firmware_not_ready`。校验顺序固定为：请求 schema →
服务端 feature gate → session 协商 capability → 设备 lifecycle/safety。未知命令/schema
使用 `422`；设备不存在使用 `404`；capability 不满足不得先进入 transport 再失败。

急停 endpoint 绕过普通命令队列但不绕过设备 wire 校验；它是“尽力送达”而不是到达保证。
设备/session 离线时返回 `OFFLINE` 并记录未送达，不能声称设备已经停止。若本地 Edge 与
设备仍连通而中央 Web 断开，Edge 可以继续处理已经收到的急停；设备本地急停始终是唯一
不依赖网络到达的最高优先路径。

ControlLease 至少包含 `lease_id`、`device_id`、`session_id`、`issued_at`、`expires_at`、
`max_expires_at`、`last_input_seq` 和 `state`。它不需要用户 owner，但只绑定创建它的
WebSocket connection ID；连接关闭、设备 session 变化、Bridge 重启、fault、maintenance
或 preemption 均立即失效。

### 10.1 创建离散命令示例

```json
POST /api/v1/devices/stackchan-01/commands
{
  "type": "control.home",
  "idempotency_key": "web-01J...",
  "ttl_ms": 1500,
  "params": {}
}
```

```json
HTTP 202
{
  "command_id": "cmd-01J...",
  "device_id": "stackchan-01",
  "state": "created",
  "expires_at": "2026-08-29T12:00:01.500Z"
}
```

### 10.2 批量结果示例

```json
{
  "task_id": "task-01J...",
  "state": "partial",
  "summary": {"completed": 1, "offline": 1, "rejected": 0},
  "targets": [
    {"device_id": "stackchan-01", "state": "completed"},
    {"device_id": "stackchan-02", "state": "offline"}
  ]
}
```

### 10.3 Web command 到 `lifeos.v1` 映射

Bridge 内部 `command_id` 是设备 wire envelope 的唯一 `event_id`；不能再生成第二个设备
幂等键。浏览器提供的 `idempotency_key` 只用于让重复 HTTP 请求查回同一个
`CommandRecord`。映射如下：

| Web/Bridge 字段 | `lifeos.v1` 字段 | 规则 |
| --- | --- | --- |
| `CommandRecord.command_id` | `event_id` | 重试复用，设备幂等事实源 |
| `CommandRecord.correlation_id` | `correlation_id` | 关联 batch/Agent/Web 请求 |
| 路由目标 | `device_id` | 由服务端写入，不信任 payload 自报 |
| 当前 session counter | `seq` | 每设备每 session 独立生成 |
| Bridge 时间 | `ts_ms` | 不能作为设备安全超时唯一依据 |
| `issued_at/expires_at` | command payload | 服务端计算；浏览器只能请求受限 TTL |
| 注册的 Web command | `type` + `payload` | 由 mapper allowlist 转换 |

例如 Web `control.home` 映射为现有 `command.control` 加
`{"action":"home", ...TTL fields...}`；行为/语音映射为通过 schema 和 capability 注册的
高层 IntentPlan。manual control、网络配置和升级若现有注册表没有对应 wire type，必须先
完成协议版本决策、schema、固件 parser、回放和 HIL，不能用任意 payload 提前透传。

## 11. WebSocket 事件模型

WebSocket 只向 UI 推送 Bridge domain events：

- `device.summary.changed`
- `device.session.changed`
- `device.safety.changed`
- `command.state.changed`
- `batch.target.changed`
- `batch.state.changed`
- `control.lease.changed`
- `telemetry.sampled`
- `maintenance.progress`

每条事件包含 `event_id/type/occurred_at/device_id?/correlation_id?/payload`。客户端重连后先
重新获取 REST snapshot，再以 cursor 订阅增量；不能假定 WebSocket 消息永久无丢失。
高频 telemetry 可合并/丢弃旧值，命令与安全事件必须持久化后再通知。

订阅请求必须指定 `device_ids`、`site_ids` 或 `visible_devices` filter 之一；缺省不订阅高频
telemetry。cursor 只承诺同一 Bridge event log 保留窗口内可恢复；cursor 过期返回
`resync_required`，客户端重新拉 snapshot。安全和命令事件按各设备持久化顺序投递；不承诺
不同设备间的全局时序。telemetry 按指标配置窗口采样/coalesce，并携带采样时间和 freshness。

## 12. 离线、重启与背压

- 运动、语音、行为、恢复、清故障离线时立即终止为 `OFFLINE`，不排队。
- 急停请求可记录为 safety intent；设备重新上线仍以本地 safe boot/torque off 为准，
  不把旧 Web 请求当作新的运动命令。
- 配置和升级可以是持久任务，但必须绑定期望版本、兼容条件和 expiry。
- Bridge/Edge 重启后恢复命令记录，但所有非终态运动命令标为 `EXPIRED`；不自动发送。
- observation/telemetry 使用 latest-value/coalescing；命令与安全事件使用有限有界队列，
  满载时拒绝低优先级新任务而不是挤掉急停。

## 13. 数据与审计

建议本地持久化边界：

- Device registry、期望配置、任务和命令终态：持久化数据库。
- 安全/操作审计：默认 180 天，可配置滚动保留。
- 高频 telemetry：默认 7 天或容量上限，按设备/指标降采样。
- 故障摘要：默认 365 天。
- 原始音视频：默认不保存。
- nonce、设备私钥、Edge 私钥：安全存储；不进入普通数据库导出和日志。

首个实现可使用单进程本地数据库，但 repository/service 接口不得把 SQLite 锁或进程内
对象暴露给领域层，为未来中心数据库和消息总线保留替换边界。

## 14. 网络与安全决定

### 14.1 已接受决定：Web 无认证

首版是可信内网中的单人系统，明确不提供 Web 登录、token、session 身份、用户角色或
RBAC。这意味着任何能访问监听地址的客户端都可能拥有完整远程控制能力。这是部署风险，
不是设备安全证明。

最低部署约束仍包括：

- 默认监听 `127.0.0.1`；内网监听必须显式选择具体私网接口，不能默认 `0.0.0.0`；
- 文档要求由主机防火墙/VLAN/可信 Wi-Fi 限制来源；
- CORS 和 WebSocket Origin 使用显式 allowlist，防止普通网页直接调用；
- UI 显示“无认证内网模式”，不产生虚假的安全感；
- 不允许端口映射、反向代理公网暴露或公共 DNS；一旦需要跨公网，必须新 RFC 引入认证、
  加密、撤销和审计主体。

实现还必须提供显式启动配置 `web_no_auth_trusted_lan=true`。未设置时只允许 loopback；
设置后启动日志和 UI banner 必须显示绑定地址与风险。maintenance、firmware、factory reset、
manual control、media 各有独立服务端 feature gate，默认关闭；关闭时 API 返回
`capability_unavailable`，不能只在 UI 隐藏。启动检查发现非私网绑定、通配反向代理配置或
Origin allowlist 为空时，拒绝启用 destructive feature gates。

CORS/Origin 和网络绑定是请求来源约束，不是用户认证，不能表述为“已认证”。

### 14.2 设备和 Edge 身份仍强制

设备/Edge 的配对凭据、nonce、seq、TLS/mTLS 属于防串台、防冒充和链路完整性，不能因
Web 单人模式而删除。否则错误 USB 设备或伪造网络节点可接收另一台设备的命令。

## 15. 扩展与容量策略

产品目标为 200 台，但当前只有一台设备，且用户明确不要求 200 台模拟验收。因此：

- 所有查询从第一版分页，事件订阅按设备/站点/filter 范围化；
- 设备 session actor/worker 独立，任何一台慢设备不能持有全局锁；
- 命令、批量目标和 telemetry 使用稳定存储接口；
- transport adapter 不保存全局业务状态；
- 后续以真实设备数、可信模拟器和观测到的 CPU/内存/队列指标确定并发限制；
- 只有执行正式 load/soak 后，才能写“支持 N 台”的验证结论。

当前验收结论只允许：“架构目标 200 台；一台设备端到端闭环已验证/未验证”。

## 16. 验收标准

### 16.1 设计验收

- Web domain、认知 domain 与 device wire protocol 边界清楚。
- 每设备 registry/session/seq/nonce/ACK/TTL 状态独立。
- 命令与批量任务状态机有终态和失败语义。
- dead-man、抢占、断线、重启和离线不重放规则明确。
- 无 Web 认证风险及部署限制明确记录。

### 16.2 首版软件验收：一台设备

- 一台 fake device 完成发现、注册、hello、状态、命令、ACK、完成和审计闭环。
- 浏览器能显示离线、在线、过期、拒绝、safety blocked 和 completed。
- 把同一台设备作为单目标批量任务时，仍走 BatchTask/BatchTarget 并正确聚合。
- 重复 `idempotency_key` 不重复执行。
- WebSocket 断开/页面失焦后，手动控制在 500 ms 内过期。
- Bridge 重启后不重放旧运动、语音或行为命令。
- Agent、批量任务和手动控制冲突按固定优先级处理。
- 非 allowlist Origin、超大 payload、未知命令和浏览器伪造 priority 被拒绝。

### 16.3 首版实机验收：一台 USB 设备

- 核验硬件身份、端口、固件、协议和设备显示身份后完成认领。
- 无运动路径先通过：hello、status、health、断线、重连和审计。
- 只有当前 Phase 1 真实舵机 HIL 通过后，才能验证 home、dead-man、行为运动、急停、
  卡滞和故障恢复。
- 运动 HIL 未通过时明确标记 `BLOCKED/NOT TESTED`，不影响 Web 只读/无运动软件验收，
  但不得宣称远程运动功能完成。

### 16.4 后续容量验收

暂不设 200 台通过门槛。达到多设备实施阶段后另建测试规格，按真实目标并发验证注册、
在线 session、批量 fan-out、事件订阅、Bridge 重启恢复和 telemetry 背压。

## 17. 失败场景与期望行为

| 场景 | 期望行为 |
| --- | --- |
| 浏览器关闭/失焦 | dead-man 失效；离散已接受命令按自身语义完成或被抢占 |
| WebSocket 丢失 | UI 进入 stale；连续控制失效；REST snapshot 重建状态 |
| Bridge 崩溃 | 设备超时进入本地 idle；重启不重放旧动作 |
| USB 拔出 | session offline；活动命令终止；设备本地 safety loop 接管 |
| 错设备占用旧 USB 口 | 硬件/device identity 不符，拒绝 session |
| 设备 ACK 丢失 | 命令超时；只按同一 event_id 的协议规则有限重试 |
| 批量中一台离线 | 其目标为 offline，其他设备继续，任务终态 partial |
| 维护期间收到 Agent 行为 | 低优先级拒绝或 preempted，不影响维护 session |
| 急停期间收到 resume/home | safety blocked；Web 显示故障/急停解除条件 |
| 固件升级后 hello 失败 | rollout 失败并尝试受支持的回滚，不发送旧命令 |
| 未可信内网客户端访问 | 当前无 Web 认证，可能获得全部权限；由部署网络隔离承担风险 |

## 18. 可观测性

日志和指标至少关联：`device_id`、`session_id`、`command_id`、`task_id`、
`correlation_id`、transport、firmware、protocol 和 Bridge build。

关键指标：在线 freshness、hello failure、command latency、ACK/completion latency、按错误码
拒绝数、过期数、late ACK、batch partial rate、manual lease timeout、队列深度、telemetry
drop/coalesce、Edge reconnect 和 audit persistence failure。

任何审计持久化失败都不得被记为命令“已审计”；对于维护/升级等高影响操作，无法持久化
审计时应拒绝创建任务。

## 19. 兼容与迁移

- `contracts/phase1/envelope.schema.json` 继续作为当前设备 wire 事实源。
- 根目录主机 schema、Web OpenAPI 和 Edge/control protocol 分别版本化，不互相冒充。
- `brain.models.LifeState.device_id` 从单实例默认值迁移为显式设备上下文；不能只把它改成
  list。认知 checkpoint 以设备/thread 隔离。
- 当前 `brain.api.create_app()` 的单例 state 只作为原型；Web Bridge 不在该全局锁上扩建。
- USB adapter 先复用当前 JSONL/hello/ACK 规则，未来 Wi-Fi/4G adapter 必须通过相同
  contract/replay suite。

## 20. 后续 RFC

- Wi-Fi transport 与设备网络配置。
- 多站点 Edge/control 协议、断网自治和凭据轮换。
- 4G 数据预算、弱网策略和 OTA 恢复。
- 实时音视频 transport、隐私和带宽。
- 公网访问与 Web 身份认证。
- 200 台容量规格与性能测试。

## 21. 被拒绝方案

- 在现有 `brain/api.py /ws` 上直接增加 `device_id`：无法隔离 session 和命令状态。
- 浏览器直接写串口或提交任意 envelope：绕过主机策略与审计。
- 每台设备维护独立 Web 服务：升级、审计和批量控制碎片化，Wi-Fi/4G 暴露面扩大。
- 离线动作队列：重连后执行过期物理动作不可接受。
- 批量命令全有或全无：物理设备无法可靠分布式回滚，且掩盖逐设备现实状态。
- 为“未来无限设备”现在引入微服务/消息集群：缺少负载证据，增加单设备交付复杂度。

## 22. 当前未验证项

- W1 host-only Web Bridge core（registry/session/command/SQLite/fake transport/最小 REST DTO）
  已实现并通过本地 fake 闭环；W2 fake/UI/event stream 已补齐，真实 USB 无运动闭环仍受
  端口权限阻塞。
- W3 目前已有 gated host-only lease/dead-man、semantic behavior/speech mapper 和高优先级
  急停 spike；`manual_control_v1` 固件执行、真实运动和 HIL 尚未完成。
- W4 batch host-only create/query/cancel、maintenance challenge、脱敏诊断和 rollout
  task 框架已实现；真实 maintenance/OTA wire、batch 真实运动和 HIL 仍未完成。
- W5 `lifeos.edge.v1` host/fake contract baseline 已实现；真实 Wi-Fi、mTLS、Edge Agent
  和网络转发仍未实现。
- W6 `lifeos.control-plane.v1` schema/fixture/design baseline 已冻结；多站点、4G、mTLS
  生产实现和安全评审仍未完成。
- W7 已有 deterministic host/fake 容量方法基线；真实 wall-clock/load/soak、200 台只是
  产品目标，没有性能或稳定性证据。
- 媒体和签名升级尚无本仓库目标实现证据。
- 当前真实舵机链路 HIL 仍受硬件无回包问题阻塞；远程运动未通过实机验收。
