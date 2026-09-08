# RFC 0004：多站点与 4G control-plane 设计契约

- 状态：Draft for security review / design-only
- 日期：2026-08-30
- 版本：`lifeos.control-plane.v1` / `1.0.0`
- 范围：site、Edge Agent、central control plane 的身份、会话、消息、弱网、迁移和 OTA 边界
- 依据：`DESIGN.md`、`docs/rfc/0001-web-bridge-fleet-control.md`、
  `docs/web-bridge-implementation-plan.md` 第 9 节、`docs/security.md`
- 机器可读事实源：`contracts/control-plane/`

## 1. 摘要与决策

W6 只冻结了多站点/4G 的 control-plane 与 Edge-to-control 设计契约，不实现生产网络。
Edge 主动向 control plane 建立出站连接；设备继续只连接本地 Edge 或受控 endpoint。中央
控制面只处理逻辑设备、站点、Edge session、任务和审计事实，不持有远端 USB 文件描述符，
也不把浏览器 JSON 透传到设备。

本 RFC 的硬决策如下：

1. `lifeos.control-plane.v1` 与 `lifeos.v1` 完全分开。`contracts/phase1/envelope.schema.json`
   仍是当前设备 wire envelope 的唯一事实源。
2. Edge-to-control 必须使用 TLS 1.3+ 双向 mTLS。证书指纹、SAN、pairing ID 和 registry
   状态共同决定 sender 是否可信；网络可达不等于可信。
3. 每个 control session 使用新的 nonce、epoch 和双向序列空间；`seq`、`message_id`、
   device session、device seq 不能跨层复用。
4. 过期、sender 不匹配、旧 epoch、seq rollback/gap/duplicate 和撤销凭据全部 fail closed。
   任何已过期物理命令都不能在重连、迁移或 4G 恢复后执行。
5. 当前可信内网“Web 无认证”是部署例外，只约束本地浏览器来源，不能延伸到 W6。W6 必须
   另有 Web 身份、授权主体、撤销和审计。
6. 安全评审、网络实现、故障注入和 OTA/HIL 证据完成前，公网入口、远程运动、destructive
   operations 和 OTA gate 全部关闭。

本 RFC 不声称 W6、公网、4G、站点迁移或 OTA 已实现。

## 2. 范围与非目标

### 2.1 范围

- 站点与 Edge 的稳定身份及 device-to-site 归属事实。
- Edge-to-control envelope、sender/recipient、mTLS/配对和凭据生命周期。
- control session 的 nonce、epoch、方向 seq、过期和 replay 规则。
- Edge 本地 device session 与 control session 的隔离边界。
- 4G/NAT/切网/弱网的心跳、时钟漂移、消息大小、流量预算和自治策略。
- 两阶段站点迁移及旧 Edge/session/lease 的失效规则。
- OTA 风险、关闭条件和未来实现的验收门禁。

### 2.2 非目标

- 不实现 mTLS server/client、4G modem、VPN、反向代理或公网监听。
- 不实现 control-plane router、消息队列、云数据库或多站点 API。
- 不改变 Phase 1 firmware parser、`lifeos.v1` envelope 或设备 SafetyLoop。
- 不把 schema、fixture 或 limits 文件当作运行时 feature flag 或容量证明。
- 不定义浏览器的登录实现；本 RFC 只规定 W6 不能复用无认证 Web 例外。

## 3. 拓扑与信任边界

```text
W6 Web identity + authorization
              │ authenticated control-plane API
              ▼
       Central control plane
              ▲  outbound TLS 1.3+ mTLS
              │
           Edge Agent ── local paired session ── StackChan device
              │
             4G/NAT or private WAN
```

### 3.1 身份主体

| 主体 | 稳定键 | 证明 | 责任 |
| --- | --- | --- | --- |
| site | `site_id` | registry + 管理审计 | 归属和迁移边界，不是网络地址 |
| Edge Agent | `edge_id` + `site_id` | mTLS cert SAN/fingerprint + pairing | 本地发现、device session、出站连接和断网自治 |
| device | `device_id` + hardware digest | 本地配对、`lifeos.v1` hello | 最终 safety、设备状态和本地执行 |
| control plane | 固定控制面身份 | mTLS server identity + W6 Web identity | registry、路由、审计、撤销和策略 |

IP、域名、SIM、USB 端口、MAC 变化和连接来源都不是身份。sender 必须由 transport 认证
结果与 registry 记录共同确认；payload 中自报的 sender 不产生信任。

## 4. 身份、配对和凭据

### 4.1 mTLS 与配对

- Edge-to-control 使用 TLS 1.3 或更高版本的双向 mTLS；control plane 验证客户端证书
  链、有效期、撤销状态、SAN 和 registry 绑定，Edge 同样验证 control-plane server。
- Edge SAN 使用 `spiffe://stackchan/site/{site_id}/edge/{edge_id}` 形式。证书指纹使用
  小写 SHA-256 hex；私钥永不进入 schema、日志、checkpoint、普通审计导出或 device payload。
- 初始 pairing 必须通过本地/带外确认，生成唯一 `pairing_id`。浏览器在可信内网能访问
  Bridge 不等于 Edge 已配对，也不能生成 Edge 身份。
- 未知、未配对、expired 或 revoked 的主体在解析 payload、更新 seq 或创建路由之前拒绝。

`identity.v1.schema.json` 保存的是 registry 元数据和凭据状态，不是证书私钥或可直接用于
认证的 secret。设备可以使用 local pairing；Edge/control 的跨站点连接不得降级到仅 pairing
或 bearer token。

### 4.2 轮换与撤销

凭据状态为 `staged -> active -> revoked|expired`。轮换流程必须：

1. 生成新的 `rotation_id` 和新证书，不覆盖旧凭据记录。
2. 在短暂的受控 overlap window 内验证新证书和 sender 绑定。
3. 原子激活新指纹，再撤销旧指纹；撤销优先于重试和重连。
4. 使旧 control session、control lease 和未完成远程物理命令失效，并记录最小审计事实。

撤销列表不可因 4G 离线而回退到“最后一次已知可信”。无法取得最新撤销事实时，Edge
可以继续本地 SafetyLoop 和明确允许的本地行为，但不得接受新的远程 motion、destructive
或 OTA 命令。

## 5. Control-plane envelope

`contracts/control-plane/control-envelope.v1.schema.json` 定义严格顶层字段：

| 字段 | 规则 |
| --- | --- |
| `schema` / `contract_version` | 固定为 `lifeos.control-plane.v1` / `1.0.0` |
| `kind` / `message_type` | 只使用注册的 hello、heartbeat、command、ack、event、credential、migration 类型 |
| `message_id` | sender 生成的幂等 ID；重试复用，最长 96 字符 |
| `sender` / `recipient` | 服务端/transport 确定；不能由 payload 覆盖 |
| `issued_at_ms` / `expires_at_ms` | 审计和时效窗口；必须满足 issued < expires |
| `seq` | sender + direction + control session 独立递增序列 |
| `session` | session ID、session nonce、方向和 epoch；每次重连新建 |
| `device_session` | 可选的本地设备绑定，只含 nonce 摘要，不含原始设备 nonce |
| `payload` | 类型化、有界对象；不承载 raw serial、PWM、GPIO、I2C、shell、URL 或私钥 |

这层 envelope 不能直接发送给 ESP32-S3。Edge 必须把高层 control message 映射为已注册的
设备语义，继续经过 `lifeos.v1` schema、Bridge Policy、设备 SafetyGate 和独立 safety loop。

## 6. Session、seq、nonce 与 replay

### 6.1 双层 session 边界

| 层 | 绑定 | 序列 | nonce 规则 |
| --- | --- | --- | --- |
| Edge-control | sender、recipient、`session_id`、direction、epoch | `seq` 每方向独立，严格递增 | `session_nonce` 仅用于该 control session，重连更换 |
| Edge-device | `device_id`、Edge、device session | `device_rx_seq`/`device_tx_seq` 属于本地 `lifeos.v1` | 原始 nonce 留在 Edge/device；control-plane 只见 SHA-256 摘要 |

设备 seq 不能成为 control seq，control message ID 不能冒充设备 `event_id`。中央控制面
记录的是 device session 的不透明绑定事实；它不能在没有新的本地 hello、能力交集和身份
校验时恢复设备 online。

### 6.2 接收顺序

接收端按以下顺序处理：

1. TLS/mTLS peer、证书状态、SAN/fingerprint、sender/site/recipient 绑定。
2. schema、版本、消息大小和字段白名单。
3. 当前 session、nonce、epoch、direction 和 device binding。
4. issued/expiry、时钟漂移和流量预算。
5. message ID 幂等与 seq：同一 `(sender, session_id, direction)` 必须是 `last_seq + 1`。
   duplicate、rollback 和 gap 拒绝；不得跳过 gap 执行后续物理消息。
6. payload capability、policy、safety 和本地 device session 状态。

失败在对应边界停止，不把拒绝消息交给更低层执行器。拒绝和异常只保留脱敏原因；不能把
nonce、私钥、完整媒体或原始 payload 写入普通日志。

### 6.3 Replay 保留

每个 sender/session 至少保留最后 seq、已见 `message_id` 和 24 小时 replay tombstone。旧
session 的消息即使仍在 expiry 内也拒绝；新 session 不能复用旧 nonce 或旧 seq 起点。迟到
ACK 只能成为 `late_evidence`，不能把 `TIMEOUT`/`EXPIRED` 命令重新变成可执行状态。

## 7. 弱网、时钟、大小和流量预算

`contracts/control-plane/limits.v1.json` 冻结以下设计默认值；这些值不是实测容量、SLO 或
运营商承诺：

| 项目 | 默认上限/阈值 | 处理 |
| --- | ---: | --- |
| 单条 envelope | 16 KiB | 超限在 payload 处理前拒绝 |
| payload | 8 KiB | 超限拒绝，不截断控制消息 |
| 最大 message age | 120 s | 过期不入队、不重放 |
| heartbeat interval | 15 s | Edge 发送新鲜性信号 |
| heartbeat timeout | 45 s | degraded/offline，撤销远程 lease |
| 最大时钟漂移 | 30 s | 保留本地安全/观测，拒绝高影响远程动作 |
| replay tombstone | 24 h | 旧 message ID/session 不能复活 |
| Edge 稳态预算 | 256 KiB/分钟 | 超额 backpressure；不挤掉安全事件 |
| Edge 突发预算 | 64 KiB | 超额拒绝低优先级消息 |
| telemetry 子预算 | 128 KiB/分钟 | coalesce/latest-value，可丢旧样本 |
| control 子预算 | 64 KiB/分钟 | 命令/安全消息不被 telemetry 淘汰 |
| OTA rollout 预算 | 0 字节 | 未完成签名/恢复/评审前不可用 |

绝对时间只用于审计；安全 TTL 使用接收端单调时钟计算。NAT、4G 网络切换、TCP 长连接
恢复或 Edge 进程重启都必须进行新的 mTLS hello、nonce 和 epoch 协商。不得通过重传旧包
来“恢复” session。

## 8. 断网自治与动作语义

control session 断开时 Edge 必须：

- 拒绝新的远程命令、远程 motion、destructive operation 和 OTA；不建立离线远程动作队列。
- 继续提供设备本地 SafetyLoop、本地急停和显式允许的本地安全/行为策略。
- 使 control lease 和 session-bound 连续控制失效；已过期动作永不补发。
- 对网络急停只报告 sent/accepted/not-delivered 的真实状态；不能声称设备已停止。设备本地
  急停始终优先。

重连后只同步 registry、session、设备状态、任务终态和审计事实。批量任务逐设备报告
`offline`/`timeout`/`partial`，不能把“重连成功”解释为旧动作已执行。

## 9. 4G 与 OTA 风险

4G 引入 NAT、地址变化、运营商断链、漫游费用、带宽抖动、丢包、网络切换和时钟异常，
但不改变身份、seq、TTL 或 SafetyGate。默认禁止原始音视频；未来媒体必须单独能力协商、
短时授权、端到端加密、降码率和预算核算，不得塞入控制 envelope。

OTA 只有在以下证据全部存在后才可单独打开 gate：

- 固件签名验证和信任根保护。
- device hardware、partition、protocol、bootloader 兼容性检查。
- pause/home/torque-off 等设备安全前置条件。
- A/B 或等价回滚、boot confirmation、断电/断网恢复和失败阈值。
- rollout/canary/批次的逐设备审计、幂等、expiry 和人工停止路径。
- 真实设备、弱网、重启、失败恢复和 HIL 证据。

在此之前，control-plane 只允许登记 OTA intent 为 rejected/design-only，不发送分片、URL、
任意固件或网络配置到设备。

## 10. 站点迁移

`site-migration.v1.schema.json` 定义 `prepare`、`commit`、`abort` 三种阶段。迁移请求必须
绑定 source/target site、source/target Edge、device snapshot、单调 `migration_epoch`、
短 expiry 和本地确认。

- `prepare` 只创建待迁移记录，不改变当前归属或发送设备动作。
- `commit` 需要匹配的 migration ID/epoch、目标身份可用和本地确认；成功后原 Edge 凭据、
  control session、lease 和未完成远程动作失效。
- 目标 Edge 必须重新配对并完成新的 mTLS/session/本地 device hello；IP、SIM 或迁移消息本身
  不能恢复 online。
- `abort` 只结束未 commit 的迁移；不能把已完成的物理动作回滚成未发生。
- 迁移不复制 control seq、device seq、nonce 或活跃动作；中央控制面保留 source/target
  审计关联。

## 11. W6 Web 身份边界与 gates

W1/W2 的可信内网无 Web 认证决定只适用于单操作者、受网络隔离的本地 Web Bridge。它不是
用户身份、不是 Edge 身份、不是 mTLS，也不提供公网安全。W6 的中央 control-plane API
必须在未来设计中单独定义 Web authentication、authorization、actor、session、撤销、
审计主体和 break-glass 规则；当前 RFC 不假定任何既有无认证入口可复用。

`contracts/control-plane/gates.v1.json` 的设计期状态必须保持：

```text
status = design_only
web_authentication_required = true
public_network_ingress = false
remote_motion = false
destructive_operations = false
ota = false
```

这些 gate 由安全评审和实现验收共同打开，不能只因 schema 通过、mTLS 握手成功或设备
在线就打开。普通 Web/API 隐藏按钮也不是 gate；服务端、Edge 和设备都必须 fail closed。

## 12. 版本与兼容

- `lifeos.control-plane.v1` 是独立于 `lifeos.v1` 的 wire family；两者不能互相冒充。
- 同一 major 只能新增兼容的可选字段；删除字段、改变默认安全行为、改变 seq/replay 或
  放宽身份边界必须升 major。
- 每条实现发布记录 control-plane contract、Edge build、firmware/protocol、identity epoch、
  schema hash 和安全评审结论。
- control-plane envelope 的未知顶层字段、未知 message type、错误 contract version 和
  不符合大小/类型约束的输入必须拒绝；payload 需要各 message type 的后续严格 schema。

## 13. 验收与未完成项

当前纯契约验证位于 `tests/bridge/test_control_plane_contract.py`，只读取 schema/fixture，
不连接网络、不发送真实设备命令、不执行 4G、OTA 或迁移。它覆盖：

- draft 2020-12、唯一 `$id`、严格 root 和 valid identity/migration/limits/gates；
- 未知 envelope 字段；
- 结构合法但已过期的 envelope；
- registry 证书指纹不匹配的 sender；
- 同 session 的 seq replay 和 message ID replay；
- 序列化后超过 16 KiB 的 envelope。

W6 进入实现前仍必须完成：正式安全评审、Web 身份设计、mTLS/撤销服务、Edge agent、弱网
故障注入、NAT/切网验证、真实设备 session/replay 回放、OTA 签名/回滚/boot confirmation
和 HIL。任何一项未完成，相关 gate 保持关闭。
