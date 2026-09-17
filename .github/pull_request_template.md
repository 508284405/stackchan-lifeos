## 变更摘要 / Summary

<!-- 说明解决的问题、用户影响和不在范围内的内容。关联 issue/RFC/ADR。 -->

## 影响范围 / Affected layers

- [ ] firmware
- [ ] brain
- [ ] bridge
- [ ] web / Web Console
- [ ] contracts / protocol
- [ ] simulator / tests / tools
- [ ] docs / community files

## 证据状态 / Evidence status

请分别填写，不要用一个总的 PASS 覆盖不同证据层。允许的状态：`PASS`、`PARTIAL`、
`BLOCKED`、`NOT TESTED`。

| 证据层 | 状态 | 实际依据、命令或限制 |
| --- | --- | --- |
| host/fake software |  |  |
| real provider |  |  |
| real media |  |  |
| hardware/HIL / physical motion |  |  |
| OTA/recovery（如相关） |  |  |
| network/identity（如相关） |  |  |
| scale/soak（如相关） |  |  |

## 验证命令 / Checks run

<!-- 对 docs-only 改动可填 N/A，但请说明原因。 -->

```text
make test:
make bridge-test / make web-check / make firmware-test（如相关）：
make phase1-acceptance / make phase2-3-acceptance / make phase4-acceptance（如相关）：
make schemas / 其他精确命令：
结果与未运行项目：
```

## 安全与隐私清单 / Safety and privacy checklist

- [ ] 未让 LLM、sub2api、LangGraph、浏览器或 Bridge 产生 PWM、GPIO、I²C、原始舵机角、shell、
      URL 或任意设备 envelope。
- [ ] 保持 pitch 5°–85°、SafetyGate、TTL、session/seq/nonce、capability、lease/dead-man 和
      断线停止语义；视频过期/未知年龄不会继续手控，恢复不会自动运动。
- [ ] 未放宽 `LIFEOS_HIL_TEST_MODE`，未绕过签名/摘要/兼容性/回滚门禁。
- [ ] 未进行未经授权的非零运动、刷写、分区/bootloader/eFuse、实体断电或机械卡滞操作；
      如确有授权测试，已在上面的 hardware/HIL 行单独记录。
- [ ] 未提交 API key、token、密码、nonce、配对凭据、signing private key、设备唯一密钥、
      绝对本机路径、raw image/audio、完整 USB dump 或未脱敏用户数据。
- [ ] 已检查 `git diff` / `git diff --check`，没有无关用户改动或生成缓存。

## 契约、文档与失败路径 / Contracts and failure paths

- [ ] 若改变 wire/API/schema，已同步 `contracts/`、parser、simulator、tests 和相关文档；
      `contracts/phase1/envelope.schema.json` 仍是 `lifeos.v1` 唯一 wire truth。
- [ ] 已覆盖或说明拒绝、过期、重复、乱序、断线、重连、背压、故障、回滚和部分失败行为。
- [ ] 已更新相关 acceptance/report；未验证能力仍标为 `PARTIAL`、`BLOCKED` 或 `NOT TESTED`。
- [ ] `accepted`/ACK/HTTP 成功没有被描述成 `completed` 或真实设备验收。

## 备注 / Notes

<!-- 说明 reviewer 需要关注的风险、迁移、已知限制和后续工作。 -->
