# StackChan LifeOS W6 control-plane contract

状态：Design-only / Draft for security review

本目录冻结 `lifeos.control-plane.v1` 的多站点 Edge-to-control 设计边界。它是控制面
协议，不是浏览器 API，也不是 `lifeos.v1` 设备 wire envelope。当前没有生产网络转发、
4G 连接、mTLS 服务或 OTA 实现；这些 schema 和 fixture 不能单独打开任何能力。

## 文件

| 文件 | 用途 |
| --- | --- |
| `control-envelope.v1.schema.json` | Edge 与 control plane 之间的严格 envelope |
| `identity.v1.schema.json` | site、Edge、device 的身份、配对和凭据元数据 |
| `site-migration.v1.schema.json` | 两阶段站点/Edge 迁移 payload |
| `limits.v1.schema.json` / `limits.v1.json` | 固定消息、时钟、session、流量和断网自治预算 |
| `gates.v1.schema.json` / `gates.v1.json` | W6 设计期安全 gate；所有高影响 gate 为关闭 |
| `fixtures/` | 确定性正例及 schema/语义拒绝用例 |

## 信任与身份

- Edge-to-control 必须使用 TLS 1.3 或更高版本的双向 mTLS。证书 SAN 使用
  `spiffe://stackchan/site/{site_id}/edge/{edge_id}` 形式；sender 的 entity、site 和
  SHA-256 证书指纹必须同时命中 control-plane registry。schema 只保存指纹和 SAN，
  不保存私钥、token 或配对秘密。
- 初始配对必须是带外/本地确认流程，生成唯一 `pairing_id`；浏览器可见的“可信内网无
  Web 认证”不是 Edge 身份，也不能替代 mTLS 或配对。未知、过期或撤销的 sender 在
  payload、seq 和路由处理前拒绝。
- 凭据生命周期为 `staged -> active -> revoked|expired`。轮换使用新的
  `rotation_id` 和短暂重叠窗口；激活新证书后撤销旧指纹。撤销事件必须 fail closed，
  并使相关控制 session、lease 和未完成远程动作失效。

## Envelope 与 session 边界

- `sender` 是实际发送方，不由 payload 自报；`recipient` 是 control plane 或 Edge。
  一个 control session 由 `(session_id, session_nonce, epoch, direction)` 绑定。
- `seq` 只属于一个 sender、direction 和 control session，按协商起点严格递增；重复、
  回退和无法填补的 gap 都拒绝并审计。新的 nonce/session 才能重新开始计数；旧 session
  的消息不能迁移到新 session。
- `device_session` 只携带 Edge 本地 `lifeos.v1` session 的 ID、Edge/device 绑定和
  nonce 摘要。原始设备 nonce 不跨越 control-plane 边界，设备 seq 与 control seq
  绝不复用。Edge 只能转发已经过本地身份和能力校验的高层事实。
- `issued_at_ms` 和 `expires_at_ms` 用于审计和有界时效；接收端以单调时钟计算年龄，
  不把发送方墙上时钟当作安全超时。超过 30 秒漂移时保持安全/观测能力，拒绝远程运动、
  destructive 和 OTA。过期消息永不排队或在重连后执行。
- 单条 envelope 最大 16 KiB，payload 最大 8 KiB。超限在 JSON payload 处理前拒绝；
  控制事件和安全事件不能被 telemetry 挤掉。

## 弱网、4G 和断网自治

`limits.v1.json` 是设计预算，不是实测容量或运营商承诺：每个 Edge 稳态上限 256 KiB/
分钟、突发 64 KiB；其中 telemetry 128 KiB/分钟、控制面 64 KiB/分钟。心跳目标为 15 秒，
45 秒无新鲜心跳进入 degraded/offline。NAT、网络切换和长连接恢复只能创建新的 mTLS
session；不能复用旧 nonce、seq 或重放未完成命令。

Control session 断开时：

1. 拒绝新的远程命令、远程运动和 OTA；不建立离线远程动作队列。
2. 允许设备本地 SafetyLoop 和已批准的本地安全/行为策略继续运行。
3. 急停以设备本地路径为最高优先级；网络急停只报告 best-effort 送达，不冒充已停止。
4. 恢复后只同步 registry、session、状态和审计事实；所有旧物理命令重新判定为 expired，
   不自动执行。

4G 只扩大传输风险，不降低任何设备 safety gate。默认不承载原始媒体；媒体如未来设计，
   必须单独协商、限时授权、加密并计入预算。OTA 必须有签名、硬件/partition/protocol
   兼容性校验、A/B 或等价回滚、boot confirmation、断点/失败恢复和不可变审计；在这些
   证据完成前 `ota_bytes_per_rollout` 为 0 且 OTA gate 关闭。

## 站点迁移与 replay 防护

站点迁移使用 `prepare -> commit`（或 `abort`）两阶段 payload，绑定 source/target site、
source/target Edge、单调 `migration_epoch`、设备快照、短 expiry 和本地确认。commit 前
不得改变归属；commit 后旧 Edge 凭据/session/lease 立即失效，设备必须在 target Edge
重新配对并 hello。迁移不会复制活跃命令、control seq 或设备 nonce，也不把 IP、SIM 或
USB 端口当作身份。

接收端至少维护：当前 sender certificate 状态、session nonce/epoch、每方向最后 seq、
message_id 幂等记录和 24 小时 replay tombstone。任何 sender mismatch、旧 epoch、seq
replay、旧 message_id 或 expired envelope 都 fail closed；late evidence 只能进入审计，
不能复活命令。

## W6 gate 与当前状态

当前 Web Bridge 是单操作者可信内网、无 Web 登录/RBAC 的设计例外。这个例外只限制浏览器
来源，绝不延伸到 W6：任何多站点、公网、4G 或远程 Edge 操作者面都必须先有独立 Web
身份、授权主体、mTLS/配对、撤销、审计和安全评审。

在安全评审通过、网络实现、故障注入和 HIL/OTA 证据完成前，以下 gate 必须关闭：

- `public_network_ingress`
- `remote_motion`
- `destructive_operations`
- `ota`

`gates.v1.json` 是设计基线，不是运行时配置。它不证明 W6、公网、4G、迁移或 OTA 已实现。
版本规则遵循 `major.minor.patch`：安全默认、字段语义或 replay 规则改变时升 major；同一
major 只新增兼容可选字段。任何实现必须先通过本目录 fixture/replay 套件，再单独通过安全
评审和网络/HIL/OTA 门禁。
