# Phase 0 安全与边界评审记录

日期：2026-08-29
评审类型：仓库实现与可复现测试的工程评审
范围：`docs/security.md`、`docs/protocol.md`、`firmware/src/runtime`、`firmware/src/protocol`、`firmware/idf`、Codex adapter seam

## 评审结论

结论：PASS（阶段 0 安全边界与实现门禁已形成）；真实执行器安全仍由阶段 1 HIL 记录证明，不能由本记录单独推导。

| 检查项 | 结果 | 证据 |
| --- | --- | --- |
| 不可信输入经过 schema、序列、TTL 和幂等检查 | PASS | `firmware/src/protocol/protocol.cpp`、`contracts/phase1/envelope.schema.json`、Phase 1 replay tests |
| 普通控制动作使用注册表，未知动作拒绝 | PASS | `validate_command_payload()`；`firmware/tests/protocol/test_protocol.cpp` |
| 急停/故障锁存优先于普通行为 | PASS | `firmware/src/runtime/runtime.cpp`、`firmware/src/behavior/behavior.cpp` |
| NaN/Inf 不得进入执行器 | PASS | `FastSafetyLoop` 的 finite 检查及 runtime 单测 |
| 清故障需要本地确认 | PASS | 协议层 `local_confirmation=true` 检查；目标 HIL 清故障另有编译开关 |
| Codex 原生协议不作为设备协议 | PASS | `contracts/codex/` 与 `contracts/phase1/` 分离；Codex 只通过 provider/transport seam 使用 |
| Codex schema 与本机 CLI 版本/hash 绑定 | PASS | `tools/codex_protocol_contract.py`、`contracts/codex/manifest.json` |
| Codex app-server 最小 live 握手与断开可重复 | PASS | `python3 tools/codex_app_server_probe.py --repeat 2 --timeout 8`；两次 `initialize`/`initialized`/close 均返回 `userAgent`，未启动 thread/turn |
| CGraph 上游不绕过 ESP-IDF spike 进入安全路径 | PASS | `docs/cgraph-spike-report.md`、`docs/adr/0002-cgraph.md` |
| 原始媒体默认不保存、不下发 | PASS | `docs/security.md`、协议 hello 的 `raw_media=false` |

## 已知边界

- 阶段 0 评审不替代真实舵机、卡滞、触摸、急停时延和断电 HIL。
- `command.maintenance_motion` 与 `command.maintenance_fault` 只在 `LIFEOS_HIL_TEST_MODE` 编译开关下存在；生产配置必须保持关闭。
- 真实 Codex app-server 的无工具握手只作为本机 CLI contract 证据；Agent turn、审批和持久化属于阶段 2。
