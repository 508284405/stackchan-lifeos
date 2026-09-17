---
name: "🧪 实机/HIL 验证 / Hardware & HIL validation"
about: "记录经过授权的硬件、真实媒体或 HIL 证据；不代表自动批准危险操作"
title: "[HIL] "
labels: ""
assignees: ""
---

<!--
这是证据记录模板，不是刷写、运动、卡滞或断电测试授权。
安全漏洞不要公开披露细节，请先看 SECURITY.md。
请脱敏 serial path、MAC、设备唯一密钥、API key、nonce、raw media 和完整 USB dump。
-->

## 测试目的 / Purpose

- 场景：read-only / build / real media / manual-control / physical motion / HIL fault / OTA-recovery
- 关联 RFC、验收条目或 issue：
- 操作者与设备所有者是否明确授权（不需要公开姓名）：是 / 否
- 测试停止条件与现场监督安排：

## 设备与构建 / Device and build

- board/model（例如 StackChan K151 / CoreS3）：
- firmware version/profile：
- protocol/schema version：
- host OS/tool versions：
- 设备标识（仅填写已脱敏值，避免发布 MAC/serial/密钥）：
- 是否复核恢复备份/hash（仅写“已复核”，不要上传私密备份）：是 / 否 / N/A
- HIL gate：`LIFEOS_HIL_TEST_MODE` enabled / production / N/A

## 前置条件与命令 / Preconditions and commands

请列出实际命令与关键输出摘要。涉及实体设备时，确认没有其他 Bridge/HIL 进程占用串口；
`tools/scan_usb_devices.py --probe` 会复位 USB CDC。不要粘贴凭据或未脱敏原始日志。

```text
命令：
关键输出：
```

## 独立证据状态 / Independent evidence status

请为每类证据填写 `PASS`、`PARTIAL`、`BLOCKED` 或 `NOT TESTED`，并附最小可复核依据：

| 证据层 | 状态 | 依据/限制 |
| --- | --- | --- |
| host/fake software |  |  |
| real provider |  |  |
| real media |  |  |
| hardware/HIL / physical motion |  |  |
| OTA/recovery（如相关） |  |  |
| network/identity（如相关） |  |  |
| scale/soak（如相关） |  |  |

`accepted`、HTTP 200、ACK、目标 build 或 host/fake 测试不能单独证明实体动作完成、物理急停、
断电 rollback、真实 provider、真实媒体或 200-device scale。

## 安全观察 / Safety observations

- 是否保持 pitch 5°–85°：
- 是否保持安全故障优先、TTL/session/lease/dead-man gate：
- 断线/失焦/视频过期后是否停止并释放 torque：
- 是否不自动回正、不重放旧命令、不自动恢复运动：
- 测试结束时 torque/目标/设备状态：
- 是否发生异常、损坏、未知结果或需要恢复：

## 附件与后续 / Artifacts and follow-up

只链接已脱敏的短日志、摘要、fixture 或报告；不要上传 raw image/audio、凭据、私钥、完整
flash dump 或用户数据。记录未完成门禁、复测条件和恢复步骤。
