# Web Bridge W6 多站点/4G 设计与安全评审基线

- 状态：DESIGN ONLY / NOT IMPLEMENTED
- 日期：2026-08-30
- 范围：site/Edge identity、Edge-to-control envelope、session/replay、弱网/4G、站点迁移和 OTA 安全边界
- 依据：`DESIGN.md`、`docs/rfc/0001-web-bridge-fleet-control.md`、`docs/web-bridge-implementation-plan.md` 第 9 节、`docs/security.md`

## 结论

W6 仅冻结了 `contracts/control-plane/` 下的 `lifeos.control-plane.v1` 设计契约和确定性
fixture。仓库没有新增生产网络转发、mTLS 服务、4G 接入、站点迁移执行器或 OTA 实现；
因此不得声称 W6、公网或 4G 已实现。

当前可信内网的 Web 无认证例外不延伸到 W6。多站点、公网、4G 或远程 Edge 操作者面必须
另有 Web 身份/授权主体，并同时具备 mTLS/配对、凭据撤销、审计、弱网故障和安全评审证据。

在安全评审和实现证据完成前，`gates.v1.json` 明确关闭：

- `public_network_ingress`
- `remote_motion`
- `destructive_operations`
- `ota`

## 冻结的契约

| 契约 | 设计约束 |
| --- | --- |
| Identity | sender 必须绑定 registry 中的 entity/site/证书指纹；未知、过期或撤销 sender fail closed |
| mTLS/配对 | Edge-to-control 使用 TLS 1.3+ 双向 mTLS；初始 pairing 走本地/带外确认；私钥和秘密不进 schema |
| Control session | `(sender, session_id, nonce, epoch, direction)` 隔离；每方向 seq 严格递增，旧 session 不迁移 |
| Device session | 只传 device session ID 和 nonce 摘要；原始设备 nonce、device seq 不跨边界或复用 |
| Replay | message ID 幂等、24 小时 tombstone、seq rollback/gap/duplicate 拒绝；过期消息不排队 |
| Weak network | 15 秒心跳、45 秒超时、30 秒时钟漂移门禁；NAT/切网只建立新 session |
| Budget | 单消息 16 KiB、payload 8 KiB；Edge 稳态 256 KiB/分钟、突发 64 KiB；OTA 预算为 0 |
| Autonomy | 断 control 时拒绝新远程动作，保留设备本地 SafetyLoop 和本地安全策略 |
| Migration | `prepare -> commit/abort`，绑定站点/Edge/设备快照、epoch、expiry 和本地确认；commit 后旧 session/lease 失效 |
| OTA | 必须签名、兼容校验、回滚、boot confirmation、失败恢复和审计；证据齐全前 gate 关闭 |

## 文件与验证范围

新增：

- `contracts/control-plane/*.schema.json`
- `contracts/control-plane/limits.v1.json`
- `contracts/control-plane/gates.v1.json`
- `contracts/control-plane/fixtures/*.json`
- `tests/bridge/test_control_plane_contract.py`

测试只做纯 schema 子集和 fixture 语义验证，不连接网络、不调用真实设备、不发送运动命令、
不执行 OTA。覆盖未知字段、过期 envelope、错误 sender 证书绑定、同 session seq/message
replay 和 16 KiB 大小上限。

## 状态门禁

| 项目 | 状态 |
| --- | --- |
| W6 schema/fixture 设计基线 | PASS（以本报告对应测试命令为准） |
| 安全评审 | NOT TESTED / 待评审 |
| control-plane 生产实现 | NOT IMPLEMENTED |
| 多站点/公网/4G | NOT IMPLEMENTED |
| 站点迁移执行 | NOT IMPLEMENTED |
| 远程 motion/destructive/OTA | BLOCKED；gate 保持关闭 |
| 真实网络、弱网、NAT、切网、时钟漂移 | NOT TESTED |
| OTA 签名、回滚、boot confirmation、HIL | NOT TESTED |
| 200 台容量 | NOT TESTED；不由 W6 契约推导 |
