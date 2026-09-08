# Web Bridge W1 Core 验收报告

日期：2026-08-30  
范围：`motion-disabled / fake-device / host-only`

## 结论

W1 Core **PASS**。一个可编程 fake device 已完成：

```text
discover → claim → hello/session → online → command → ACK → completed → audit
```

W1 不等于真实远程运动、完整 Web Console、dead-man、维护 challenge 或 200 台容量
支持；这些仍按 `docs/web-bridge-implementation-plan.md` 留在后续门禁。

## 交付

- `bridge/domain/`：设备、session、command、batch、audit 记录、枚举和严格状态迁移。
- `bridge/persistence.py`：SQLite migration v2；设备/session/command/batch/audit 持久化；
  Bridge 重启后 session 不恢复为 online，未完成物理命令和 batch 目标过期。
- `bridge/registry.py`：discovery candidate 与已认领设备分离；hello capability 使用
  Bridge allowlist 交集；硬件身份可校验。
- `bridge/sessions.py`：每设备独立 session、nonce、rx/tx seq、16 项重复窗口和 freshness。
  nonce 保留在运行时，不写入普通 SQLite session blob 或 audit。
- `bridge/transports/`：依赖无关的 JSONL/USB stream framing、16 KiB 上限、fake transport；
  fake 支持 host-first/device-first hello、延迟、ACK 丢失、拒绝、重复和 completion 开关。
- `bridge/service.py`：Web command allowlist/mapper、TTL、来源优先级、per-device dispatch、
  ACK/completion/late evidence、离线和重启语义、单目标/多目标 best-effort BatchTask；
  对当前固件固定 `ack.event_id` 的兼容去重以 `event_id+correlation_id` 为键，其他事件仍
  使用严格 event-id 窗口。
- `bridge/api.py`：最小 `/api/v1` REST DTO；默认 loopback；无 raw serial/envelope 路由。
- `tests/bridge/`：闭环、host-first、幂等、跨设备隔离、乱序、故障、TTL/late ACK、重启、
  nonce、batch、API 和部署绑定测试。
- `contracts/web-bridge/`：浏览器 API 的独立 OpenAPI 契约；不作为 `lifeos.v1` wire message。

## 验证

| 项目 | 结果 |
| --- | --- |
| W1 Bridge/API/transport tests | PASS（W1 定向断言；当前完整 `tests/bridge` 回归 89 tests） |
| Fake discover/claim/hello/command/ACK/completed/audit | PASS |
| host-first 与 device-first hello | PASS |
| 重复 idempotency key 不重复执行 | PASS |
| 跨设备 device/session/seq 隔离 | PASS |
| ACK 丢失、拒绝、late evidence、TTL | PASS |
| Bridge restart 不恢复旧 session、不重放命令 | PASS |
| 单目标 BatchTask 与 partial target 聚合 | PASS |
| API 默认 loopback、raw envelope 缺失、priority/feature gate | PASS |
| 既有 `brain`/firmware/protocol/Phase 1 回归 | 待最终合并命令确认 |
| 真实 USB Bridge 无运动监控 | NOT TESTED（W2） |
| 浏览器 WebSocket/event stream/UI | NOT TESTED（W2） |
| dead-man/manual_control_v1 | NOT TESTED（W3，feature gate 默认关闭） |
| 真实远程运动 | BLOCKED by Phase 1 overall gate |

建议使用：

```sh
PYTHONDONTWRITEBYTECODE=1 PYTEST_ADDOPTS='-p no:cacheprovider' make test
```

## 安全与兼容边界

- 浏览器提交的是 `control.status/pause/resume/home/clear_fault` 等注册命令；原始
  `command.*`、串口写入、任意 envelope 和浏览器自报 priority 均不接受。
- `CommandRecord.command_id` 是设备 envelope 的 `event_id`；浏览器
  `idempotency_key` 只查回同一记录，不形成第二套设备幂等键。
- 当前 ESP-IDF 固件采用 host-first hello；Bridge 也兼容 device-first fake/旧 transport。
- Web 无认证仅适用于显式可信内网配置；默认只允许 loopback。W1 没有启用
  `manual_control_v1`、maintenance、升级或媒体能力。
- 未进行设备刷写、擦除、恢复或任何物理运动操作。

## 下一门禁

W2 需要先冻结前端框架/打包方式，再实现 Overview、Devices、detail、REST snapshot 和
WebSocket snapshot+cursor；真实 USB 只做身份、status、health、断线/重连无运动闭环。
Phase 1 的独立剩余门禁见 `docs/phase1-acceptance.md`，不能被 W1 fake 测试替代。
