# RFC 0003: Wi-Fi / Edge Agent host contract baseline

- 状态：W5 host-only contract/fake baseline
- 日期：2026-08-30
- 依赖：RFC 0001；`docs/web-bridge-implementation-plan.md` §8
- 实现边界：`bridge/edge.py`；未接入 `bridge/service.py`、`bridge/api.py`

## 决策

1. Edge 主动连接控制面；真实网络、Wi-Fi discovery 和设备网络配置不在本 RFC 的实现范围。
2. 控制面与 Edge 使用独立、版本化的 `lifeos.edge.v1` envelope，机器可读事实源为
   [`contracts/edge/envelope.schema.json`](../../contracts/edge/envelope.schema.json)。它不替代
   `lifeos.v1`，也不接受浏览器 DTO、任意 raw envelope 或 raw hardware fields。
3. 每帧强制携带 `sender.kind/id`、`edge_id`、`device_id`、`session_id`。一个 session 只绑定
   一个 Edge 与一个设备；身份或 session 不匹配即拒绝，不允许串台。
4. 双向 sequence space 独立：control-plane→Edge 和 Edge→control-plane 每个新 session 都从
   `seq=0` 开始，严格连续；重连建立新 `session_id` 和新计数域。
5. hello 可携带 nonce，但 nonce 是运行时秘密，不进入日志、审计导出或普通存储。本 fake 使用
   固定测试 nonce，仅为 deterministic fixture，真实实现必须使用安全随机值和安全存储。
6. 最大 frame 为 16 KiB（JSON UTF-8 编码）；heartbeat 目标间隔 15 s，45 s 无新 heartbeat
   后为 offline，中间为 degraded。degraded/offline 拒绝新的远程动作。
7. 断线期间不排队新远程动作；重连只允许后续 adapter 同步事实（registry/session/task），
   不重放旧 command。旧 command 绑定旧 session，即使仍未过期也不得跨 Edge/session 迁移。
8. 背压是显式失败：有限队列满时拒绝普通新动作；不得用丢弃急停或覆盖旧动作的方式“自愈”。
   本 baseline 暂不实现 emergency-stop adapter，但不削弱设备本地 safety 优先级。

## Fake/contract API

`FakeEdgeTransport` 只提供内存 callback transport，无 socket、Wi-Fi、TLS 或新增依赖；可注入
断线、重复、乱序、过期、背压。`EdgeSession` 负责身份/session/seq/heartbeat 状态，不负责
Web API 路由或设备 `lifeos.v1` 映射。动作入口是高层 `action + params`，禁止 `command.*`、
浏览器字段和原始硬件参数透传。

## 状态与故障语义

```text
OFFLINE -> NEGOTIATING -> ONLINE <-> DEGRADED -> OFFLINE
                         └───────────────(identity/sequence violation)──> REJECTED
```

重复帧和乱序帧不会推进 Edge RX sequence；旧 session 的 command 只保留为事实记录，重连不
发送。过期 command 由 `expires_at_ms` 判定，不能因重连重新激活。heartbeat 恢复只能使当前
有效 session 回到 ONLINE，不能恢复旧动作。

## 验收边界

- PASS：schema 正例/反例、版本与 sender identity、device/session binding、双向 seq 起点、
  断线拒绝新动作、重连不重放、重复/乱序/过期/背压注入、heartbeat 状态机、16 KiB 上限。
- PASS：host-only deterministic fake 可作为后续 Bridge adapter 的 transport seam；API 未集成
  是有意边界。
- NOT TESTED：真实 Wi-Fi/网络、mTLS 握手与证书轮换、真实 Edge Agent、4G/NAT/弱网成本、
  discovery、Edge OTA、跨进程持久化、容量或 200 台规模。

这些 NOT TESTED 项必须在 W6 或独立安全/部署 RFC 中重新设计和验收，不能由本 fake 的 PASS
推导网络安全或生产可用性。
