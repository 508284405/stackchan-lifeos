---
name: "💡 功能建议 / Feature request"
about: "提出可验证的新能力、改进或设计讨论"
title: "[Feature] "
labels: ""
assignees: ""
---

<!-- 安全问题、凭据或可利用细节请改走 SECURITY.md，不要公开。 -->

## 用户问题 / Problem

谁遇到什么问题？当前 workaround 是什么？请区分已验证事实、假设和期望（host-only、fake、
real provider、real media、hardware/HIL）。

## 建议 / Proposal

请描述行为、输入/输出、错误和降级路径，而不是只描述 UI 按钮或内部实现。

## 范围与兼容性 / Scope and compatibility

- 影响 layer：`firmware` / `brain` / `bridge` / `web` / `contracts` / `simulator` / docs
- 是否改变 `lifeos.v1`、JSON Schema、capability、session、seq、TTL 或安全默认值：
- 是否需要迁移、rollback 或数据生命周期变更：
- 是否涉及网络、公网身份、OTA、媒体、实体运动或规模化：

## 安全与隐私 / Safety and privacy

请说明如何保持以下边界：

- LLM、浏览器和 host 只发高层语义，不产生 PWM、GPIO、I²C、原始舵机角、shell、URL 或任意
  设备 envelope；
- pitch 5°–85%、SafetyGate、TTL、session/lease、dead-man、断线停止和不自动回正；
- `LIFEOS_HIL_TEST_MODE` 对 maintenance/HIL-only 命令的隔离；
- raw image/audio、凭据、nonce、路径和用户数据不进入日志、audit、checkpoint 或 provider；
- 新固件只有在签名、摘要、兼容性和可恢复条件成立时才允许进入升级路径。

## 验收标准 / Acceptance criteria

请给出可测试的 success、failure、timeout、reconnect 和 rollback 条件，并说明每一项预期
证据类型。对于真实设备/OTA/规模化，请明确它们是独立门禁，不由 fake、host build 或 ACK
替代。

```text
- [ ]
- [ ]
- [ ]
```

## 其他方案 / Alternatives

说明考虑过的方案、取舍和为什么当前方案更适合本地优先与安全边界。
