# Web Bridge W4 host-only extension report

日期：2026-08-30

## 结果

- **PASS**：maintenance challenge 具备 `prepare → session-bound maintenance.confirmed → execute` 边界；challenge 一次性、过期和 session 不匹配均拒绝。默认 gate 关闭。
- **PASS**：诊断导出只包含固定 allowlist，并提供 canonical SHA-256 完整性校验；不导出 nonce、密钥、token、路径或原始媒体。
- **PASS**：maintenance/rollout task 使用有限状态机、consequence 和不可变审计；专用 SQLite migration（schema v3）持久化任务，重启将未完成任务标记 expired，物理动作不重放。
- **PASS**：rollout 仅支持单目标框架；签名、硬件、partition、protocol 前置条件及 firmware gate 未满足时结构化拒绝。
- **BLOCKED**：真实维护 wire、OTA 分片/签名校验、boot confirmation、A/B rollback 尚未实现，未启用任何真实维护或 OTA。
- **NOT TESTED**：真实 StackChan 设备维护、实机 OTA、长时间 rollout/断电恢复 HIL。

## 验证

W4 专项测试（maintenance/diagnostics/rollout）→ **10 passed**；当前完整
`tests/bridge` 回归 → **89 passed**。另行执行 OpenAPI YAML 解析与 W4 路径/DTO 检查 → **PASS**。
