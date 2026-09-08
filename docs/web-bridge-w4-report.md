# Web Bridge W4 Host-only Batch 验收报告

日期：2026-08-30  
范围：BatchTask/BatchTarget 的有界 fan-out、逐目标结果、cancel 和重启恢复

## 结论

Host-only batch surface **PASS**。单设备和多目标 fake 场景均通过同一
`submit_batch`/`BatchTarget` 路径；maintenance、OTA 和真实批量运动仍未启用。
Host-only maintenance/diagnostics/rollout 框架另见
`docs/web-bridge-w4-extension-report.md`。

## 已实现

- `BatchTask`/`BatchTarget` 记录和状态机：`pending/running/completed/partial/failed/cancelled/expired`。
- SQLite migration v2 持久化 batch；Bridge 重启将未终态 task/target 标为 expired，
  不重放物理动作。
- `Bridge.submit_batch` 使用每设备 command service，并行 fan-out；单设备慢或离线
  不改变其他 target 的终态。
- `Bridge.cancel_batch` 只取消尚未进入 dispatch boundary 的 target；已 dispatch
  目标保留真实 ACK/completed/offline/timeout 结果，不声称回滚。
- REST：`POST/GET /api/v1/batch-tasks` 与 `/cancel`；OpenAPI 已同步。

## 验证

| 项目 | 状态 |
| --- | --- |
| 单目标 BatchTask 完成和 target command ID | PASS |
| completed + offline → partial | PASS |
| 取消 pending target | PASS |
| Bridge 重启不重放 batch | PASS（与 W1 recovery 测试） |
| REST batch create/query/cancel | PASS |
| 大规模 fan-out/容量/200 台 | NOT TESTED |
| maintenance challenge/diagnostics/rollout framework | PASS（host-only，wire gate 关闭） |
| real maintenance/OTA/diagnostics execution | BLOCKED / NOT TESTED |
| 真实设备批量运动 | BLOCKED by Phase 1 overall gate and USB access |
