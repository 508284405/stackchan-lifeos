---
name: "🐛 软件问题 / Bug report"
about: "报告可复现的软件、协议、模拟器或 Web Bridge 问题"
title: "[Bug] "
labels: ""
assignees: ""
---

<!-- 安全漏洞不要公开提交可利用细节、PoC、凭据或敏感日志；请先阅读 SECURITY.md。 -->

## 问题摘要 / Summary

<!-- 一句话描述问题，以及它影响哪个 layer。 -->

## 影响范围 / Area

- [ ] `brain`
- [ ] `bridge`
- [ ] `web` / Web Console
- [ ] `firmware` host test
- [ ] `contracts` / protocol
- [ ] `simulator` / `tests` / `tools`
- [ ] docs / documentation

## 证据类型 / Evidence modality

请至少勾选一项，并不要把一种证据写成另一种能力：

- [ ] host/fake software
- [ ] real provider（请确认已获得授权；普通测试应使用 fake）
- [ ] real media
- [ ] hardware/HIL / physical device
- [ ] not tested; only static review or design concern

## 预期与实际行为 / Expected vs actual

**预期（Expected）**：

**实际（Actual）**：

**安全影响（Safety/security impact，如有）**：

## 复现步骤 / Reproduction

请提供从仓库根目录运行的最小命令、输入 fixture、配置和复现率。不要粘贴 secret、完整
环境变量、原始媒体或未经脱敏的 USB/设备日志。

```text
1.
2.
3.
```

## 环境 / Environment

- commit/tag：
- OS / Python / Node（如相关）：
- 组件版本（firmware / protocol / provider / browser）：
- fake、simulator、host、real media 或 HIL：
- 若涉及设备：board/firmware profile（请脱敏 serial path、MAC、凭据）：

## 验证结果 / Checks run

请列出实际运行过的命令和结果；例如 `make test`、`make bridge-test`、`make web-check`、
`make firmware-test`、`make schemas` 或对应单测。命令成功不等于真实设备、OTA、provider
或规模化能力通过。

```text
命令：
结果：PASS / PARTIAL / BLOCKED / NOT TESTED
```

## 附加信息 / Additional context

- 是否有 workaround：
- 相关 issue/RFC/日志链接（仅限已脱敏内容）：
- 是否涉及设备运动、相机、维护、OTA、配对、lease 或安全 gate：
- 是否确认退出时 torque off、旧 lease/命令未重放、无自动回正（如相关）：
