# Web Bridge 实施与验收计划

- 状态：Proposed
- 日期：2026-08-29
- 设计输入：`docs/rfc/0001-web-bridge-fleet-control.md`
- UI 输入：`DESIGN.md`
- 当前设备数量：1
- 长期产品目标：200 台；当前不做 200 台容量验收

## 1. 执行原则

1. 先锁定领域契约和故障语义，再实现 UI。
2. 一台设备也必须走完整的 registry/session/router/command/task 路径。
3. 不在当前 `brain.api` 的全局 `LifeState` 上堆多设备条件分支。
4. 设备 wire 层只使用 `lifeos.v1`；Web API、Edge protocol 和 Codex app-server
   protocol 各自隔离。
5. 所有普通动作都可能失败、过期或被 safety block；UI 不做乐观完成。
6. 真实运动验收继续服从现有 Phase 1 硬件门禁。未通过就标记 BLOCKED。
7. 不因未来 200 台目标提前引入分布式数据库、消息集群或微服务。
8. Web 端不实现登录、用户角色或 RBAC；首版只允许明确的可信内网部署。

## 2. 总体里程碑

| 里程碑 | 交付 | 退出条件 | 当前状态 |
| --- | --- | --- | --- |
| W0 契约冻结 | RFC、UI DESIGN、API/状态机测试规格 | 评审通过且无协议/安全矛盾 | DESIGN COMPLETE |
| W1 Bridge 核心 | registry、session、command、USB adapter、持久化 | 一台 fake device 完整命令闭环 | HOST-ONLY PASS |
| W2 Web 监控 | Overview、Devices、detail、事件流 | 一台 fake/真实 USB 无运动监控通过 | FAKE PASS / USB + CONTROLLED RECONNECT PASS / BROWSER E2E PASS (FAKE) |
| W3 远程控制 | 安全命令、行为/语音、dead-man、仲裁 | fake 完整；真实运动按 HIL 门禁 | HOST LEASE PASS / WIRE NOT ENABLED |
| W4 批量与运维 | batch task、诊断、校准、升级任务框架 | 单目标批量和部分失败模拟通过 | HOST-ONLY PASS / DEVICE WIRE BLOCKED |
| W5 Wi-Fi Edge | Wi-Fi adapter、Edge Agent、出站连接 | 单站点 LAN 断线/重连/不重放通过 | CONTRACT/FAKE PASS / NETWORK NOT TESTED |
| W6 多站点/4G | 中央控制面、弱网、远端 Edge | 独立 RFC 和安全评审 | DESIGN BASELINE / IMPLEMENTATION NOT STARTED |
| W7 容量验证 | 负载模型、观测、调优 | 基于当时设备规模设定，不预写 200 PASS | METHOD BASELINE / CAPACITY NOT TESTED |

W1 当前交付范围为 `motion-disabled / fake-device / host-only`：已覆盖注册、会话、
hello（host-first 与 device-first 兼容）、seq/TTL/幂等、命令 ACK/completed、单目标
BatchTask、SQLite migration/restart recovery、late evidence、审计和最小 REST DTO。
真实 USB 只读接入、浏览器事件流、dead-man、维护 challenge 和远程运动不因 W1 的
host-only PASS 而提前启用。

W2 当前实现采用 FastAPI 托管的 React 页面（Vite 构建，源码 `web/src`、提交的构建产物 `web/dist`，界面中文默认可切换英文）：fake/API/WebSocket 的无运动
监控链路已覆盖；2026-08-30 在 `/dev/cu.usbmodem1101` 完成直接 USB JSONL
hello/status 只读 smoke，身份、协议和 safe-idle 回包通过，证据见
`artifacts/web-bridge/usb-readonly-20260830.json`；随后通过标准库 POSIX 串口适配器完成
一次 Web Bridge service → session → `control.status` → health ACK → disconnect 只读闭环，
证据见 `artifacts/web-bridge/usb-bridge-readonly-20260830.json`。当前仍未完成浏览器到真实
设备的长连接断线/重连闭环；
前端界面不暴露运动、raw envelope、maintenance 或 manual-control 操作。

W3 的 `manual_control_v1` 设计基线见 `docs/rfc/0002-manual-control-v1.md`；host lease、
设备端 parser/state machine、固定小步长映射、真实 capability handshake 和 target build
已完成，真实连续控制仍须通过显式 `--enable-manual-control` 与受监督 HIL 门禁。

本目标新增的 Web 控制与摄像头预览基线见 `docs/rfc/0005-web-control-camera-preview.md`。
离散安全控制可以进入 Web Console；连续手动控制仍受 `manual_control_v1` gate 和真实
transport/HIL 门禁。摄像头支持显式 start/stop、QVGA JPEG、最多 10 fps、持续 MJPEG、单设备
最新帧和 HTTP 输出；不录制、不持久化、不进入认知链路。USB 上的 begin/chunk/end 是当前
单设备连续视频实现，不代表未来 Wi-Fi/4G 的 H.264/WebRTC 高码率媒体协议。

W4 已补齐 host-only maintenance challenge、脱敏诊断导出和单目标 rollout task 框架；
默认 gate 仍关闭，真实 maintenance/OTA wire、签名/分片/boot confirmation/A-B rollback
和实机 HIL 尚未实现。W5 的 `lifeos.edge.v1` contract/fake 基线见
`docs/rfc/0003-wifi-edge.md`；W6 的 `lifeos.control-plane.v1` design-only 基线见
`docs/rfc/0004-multisite-4g.md`；W7 的可重复方法见 `docs/web-bridge-capacity-plan.md`。

## 3. W0：契约与测试规格

### 工作项

- 定义 Bridge domain models：DeviceRecord、DeviceSession、CommandRecord、
  BatchTask、BatchTarget、ControlLease、AuditEvent。
- 为 session 和 command 状态机建立机器可读枚举及迁移规则。
- 定义 `/api/v1` OpenAPI，并明确禁止 raw serial/envelope API。
- 冻结统一错误结构和优先级：schema `422`、not found `404`、feature/capability `409`；
  capability 拒绝仍创建可审计的 REJECTED CommandRecord。
- 定义浏览器 event stream schema、snapshot + cursor 恢复协议。
- 建立控制优先级和每种命令的 TTL/重试/终态表。
- 定义 Web command mapper：Bridge `command_id` 必须成为设备 envelope `event_id`，
  浏览器 idempotency key 不能形成第二套 wire 幂等语义。
- 完整定义 session timeout/maintenance/revocation、ACK/error/late evidence、batch
  deadline/retry/cancel 和 Edge sender/seq 状态迁移。
- 建立 USB transport interface，使 Wi-Fi/Edge adapter 后续可替换。
- 在 `docs/testing.md` 增加 Web Bridge 测试矩阵。

### 验证

- schema 正反例；未知字段/命令/priority 拒绝。
- 状态机不可达转换测试。
- `lifeos.v1` mapper contract test，证明 Web DTO 不能直接成为 wire message。
- 设计审查确认 Web 无认证风险已明确记录。
- 启动门禁测试：默认 loopback；无显式 trusted-LAN 配置时拒绝内网监听和 destructive
  feature；非私网绑定、空 Origin allowlist 时拒绝启用 maintenance/manual/upgrade/media。
- 急停 API contract/fault tests：重复幂等、普通队列满仍走优先通道、设备离线明确
  not-delivered、设备 ACK 与已停止状态分离、设备本地急停永远优先。

## 4. W1：Bridge 核心与一台 fake device

### 建议模块边界

```text
bridge/
  domain/       records, enums, state transitions
  registry/     device persistence and discovery claims
  sessions/     hello, nonce, seq, heartbeat, freshness
  commands/     validation, routing, TTL, idempotency, results
  batches/      target snapshot, fan-out, aggregation
  arbitration/  source priority and control lease
  transports/   interface, USB JSONL, fake transport
  persistence/  repositories and migrations
  audit/        append/query/export
  api/          REST/WebSocket DTO boundary
```

最终路径应服从仓库 Python packaging 决策；本计划定义责任，不强制现在创建目录。

### 工作项

- 实现有迁移的本地持久化，保证 Bridge 重启可恢复 registry/audit/task 终态。
- 实现 USB discovery，但检测到端口只产生 candidate，不直接标记 online。
- 实现 per-device session worker；禁止全局 session 锁包围 I/O。
- 实现 hello/capability/nonce/seq/heartbeat/duplicate/TTL 映射。
- 对 manual control、网络配置、升级等现有 wire registry 未覆盖能力建立显式协议扩展
  门禁；未协商 capability 时服务端和 UI 都拒绝。
- 实现 command repository、dispatcher 和 late ACK 处理。
- 实现可编程 fake transport，支持断线、乱序、重复、延迟、拒绝和 safety block。

### 退出条件

- 一台 fake device：discover → claim → hello → online → command → ACK →
  completed → audit 全链路 PASS。
- 重复命令不重复执行；错 `device_id/session_id/seq` 不串台。
- Bridge 重启使旧非终态运动命令 expired，不重放。
- USB/协议错误不会使 Bridge 进程崩溃或阻塞其他服务任务。

## 5. W2：Web 监控与无运动实机闭环

### 工作项

- 实现 `DESIGN.md` 中 Overview、Devices、Device detail 和 System 基础页面。
- 实现 REST snapshot、分页设备列表和范围化 WebSocket subscription。
- 实现 SafetyBanner、freshness、capability、command timeline、telemetry 和 fault UI。
- 显示明确的无认证内网模式与绑定地址。
- 使用一个真实 USB 设备完成只读发现/认领/hello/status/health/断线/重连。

### 退出条件

- 页面区分 detected、negotiating、online、degraded、offline 和 rejected。
- WebSocket 断开后 UI 标记 stale，并通过 snapshot 恢复。
- HTTP `202` 不显示成设备 completed。
- 一台真实设备的身份、固件、协议、能力和 health 与串口证据一致。
- 不发送运动命令也能完成本里程碑。

## 6. W3：完整远程控制

### W3.1 离散安全控制

- status、pause、resume、home、emergency_stop。
- 每个动作定义 capability、TTL、前置状态、ACK 终态和恢复建议。
- emergency stop 独立优先、幂等，并抢占普通行为。
- 提供独立急停 endpoint/优先队列，并清楚报告 sent、device accepted 与 offline/not-delivered；
  远程请求不能冒充设备本地急停保证。

### W3.2 行为、表情和语音

- 行为来自设备/Bridge 注册表，支持强度和时长上限。
- 文本朗读设置长度、语言、voice 和 TTL；离线立即失败。
- Web 操作抢占 LangGraph/自主行为时产生可见 PREEMPTED 结果。

### W3.3 Dead-man 手动控制

- 获取一台设备 control lease；不需要多用户身份，但仍需要 lease ID。
- 输入最大 10 Hz、逐帧 seq、TTL 300–500 ms。
- release、blur、visibilitychange、WebSocket close 和 heartbeat timeout 全覆盖。
- 设备反馈冻结、越界、fault、pause、maintenance 时立即 block/preempt。
- lease 绑定 WebSocket connection 和 device session，具有最大期限；Bridge 重启不恢复。

### 退出条件

- fake device 故障注入矩阵全部 PASS。
- 浏览器失焦/断链后控制在 500 ms 内失效。
- 远程命令不能改变硬安全常量或提交原始硬件参数。
- 真实运动只在 `docs/phase1-acceptance.md` 门禁通过后运行；否则明确 BLOCKED。

## 7. W4：批量、诊断与维护

### W4.1 BatchTask

- 创建时固化目标列表。
- fan-out 有界并发，单设备慢/离线不阻塞其他目标。
- 全局 deadline + 每目标独立 TTL/重试；取消只影响未 dispatch 目标。
- 聚合 `completed/partial/failed/cancelled/expired`。
- UI 显示每目标 command ID、状态和原因。
- 单台设备也通过 BatchTask 执行，证明不是另写单设备捷径。

### W4.2 维护和诊断

- 校准 session、网络候选配置、重启、清故障、日志与诊断包。
- 恢复出厂和固件升级使用 consequence UI 和不可变审计。
- 诊断导出执行字段 allowlist，不包含 nonce、密钥或默认原始媒体。
- 需要设备本地确认的维护操作等待 session-bound confirmation event；Web 无用户角色不等于
  可以伪造或跳过本地确认。
- 在任何维护写操作前实现并验证 `maintenance_prepare` → device local touch →
  `maintenance.confirmed` → execute 的一次性 challenge 协议；同步 schema、firmware、
  回放和 HIL，未完成时 feature gate 保持关闭。

### W4.3 固件 rollout

- 签名、硬件/partition/protocol 兼容校验。
- pause/home/torque-off 前置条件。
- rollout task、canary/批次模型、失败阈值和回滚接口。
- 一台设备仍作为大小为 1 的 rollout，不能绕过安全前置条件。
- W4 先实现任务框架；OTA 分片、hash/signature、boot confirmation、A/B rollback 和网络
  配置 wire contract 必须由后续协议 RFC/固件实现后才能启用对应 feature gate。

### 退出条件

- 单目标 batch PASS。
- fake 多目标场景准确产生 partial 并保持逐设备终态。
- 取消任务不把已执行动作宣称为回滚。
- Bridge 重启能恢复维护任务记录，但不重放物理动作。

## 8. W5：Wi-Fi 与 Edge Agent

在开始前新增 Wi-Fi/Edge ADR/RFC，至少决定：设备发现、mTLS/配对、证书轮换、地址变化、
弱网心跳、最大消息、流量预算、断网自治、Edge 升级和本地故障处理。
该 RFC 必须产出机器可读 Edge envelope/schema，明确 sender identity、device session ID、
双向 seq 起点、Edge reconnect 和跨 Edge 迁移；这些契约冻结前不得实现网络转发。

### 工作项

- 从 USB adapter 抽取已经通过的 transport contract，而不是复制 command service。
- Edge Agent 主动连接控制面；设备只连接本地 Edge/受控 endpoint。
- Edge 断开控制面时，拒绝新的远程动作并让设备按本地策略运行。
- 重连只同步 registry/session/task 事实，不重放过期动作。

## 9. W6：多站点与4G

这是公网/广域网能力，必须重新引入 Web 身份、加密、撤销、设备/Edge 凭据轮换、弱网和
数据成本设计。当前“Web 无认证”决定不得自动延伸到这一阶段。

4G 需要额外验证：NAT、长连接恢复、流量上限、网络切换、时钟漂移、离线窗口、OTA
失败恢复和媒体禁用/降码率策略。

## 10. W7：朝 200 台目标验证

当前不要求 200 台测试。本阶段只登记未来工作，不给出虚假 PASS：

- 依据真实使用增长建立 representative simulator 和流量分布。
- 分别测 registered、online、active-control、telemetry 和 batch fan-out 数量。
- 采集 CPU、RSS、event-loop lag、DB latency、queue depth、event drop 和恢复时间。
- 找到单实例容量后再决定是否需要多进程、外部数据库或消息总线。
- 发布报告明确硬件数、模拟数、持续时间和未测条件。

## 11. 测试矩阵

| 层 | 重点 |
| --- | --- |
| Domain unit | 状态转换、TTL、幂等、priority、batch 聚合 |
| Repository | migration、重启恢复、并发更新、audit append |
| Transport contract | USB/fake 的 hello、seq、ACK、断线、背压 |
| API contract | OpenAPI、输入上限、未知命令、状态语义 |
| Browser E2E | 一台设备完整流、stale/reconnect、dead-man、partial UI |
| Fault injection | ACK 丢失、乱序、设备替换、Bridge 重启、反馈冻结 |
| Firmware/HIL | safety gate、急停、限位、卡滞、断线 torque behavior |
| Security/deployment | bind address、Origin allowlist、无认证风险提示、无 raw API |

## 12. 发布与回滚

- W1/W2 可在 motion-disabled 配置下发布和验证。
- 新 Bridge 与现有脑端 API 使用独立入口；初期保留旧路径作为只读对照，不能双写设备。
- 数据库 migration 必须可备份并提供前一版本读取/恢复方案。
- 功能开关按 transport、manual control、maintenance、firmware、media 分开。
- 关闭某功能时服务端也拒绝命令，不能只隐藏 UI。
- 回滚 Bridge 后设备保持本地安全；所有 active lease 立即失效。

## 13. 交付报告格式

每个里程碑报告必须包含：

- Changed files / schema versions / migration versions。
- Tested：具体 fake、真实设备、浏览器和 HIL 命令及结果。
- PASS/BLOCKED/NOT TESTED 明细。
- 设备型号、端口、固件、协议、Bridge build 和备份/恢复状态。
- 已知风险、未测试规模和下一门禁。

禁止将“一台设备架构可扩展”写成“200 台已支持”，也禁止将 Web ACK 写成执行器完成。
