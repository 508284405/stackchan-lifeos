# 行为准则 / Code of Conduct

StackChan LifeOS 采用 Contributor Covenant 2.1 的核心原则，并结合机器人、硬件和安全
软件项目的风险制定本规则。参与 issue、Pull Request、review、讨论、代码/文档提交、线上
活动或代表本项目的公开交流，即表示你同意遵守本准则。

## 我们期待的行为 / Expected behavior

- 尊重不同背景、经验、语言和观点；使用包容、清晰、专业的语言。
- 针对代码、证据和设计讨论问题，不攻击个人；在不确定时先询问并给出可复现依据。
- 诚实区分 `host-only`、`fake`、`real provider`、`real media` 和 `hardware/HIL` 证据，
  不夸大未验证的真实设备、OTA、网络或规模化能力。
- 尊重设备安全门禁、隐私和 responsible disclosure；发现风险时先保护用户和设备，再公开
  必要的最小信息。
- 尊重他人时间和维护边界，及时说明阻塞、未知项、复现条件和可能的副作用。

## 不可接受的行为 / Unacceptable behavior

- 骚扰、歧视、仇恨言论、威胁、跟踪、羞辱、性化或未经同意的个人联系，以及发布他人
  的个人信息（doxxing）。
- 故意提交恶意代码、后门、凭据、恶意 payload、未经授权的网络扫描，或把用户输入直接
  拼接为 shell/URL/路径。
- 诱导或指导他人绕过 `SafetyGate`、`LIFEOS_HIL_TEST_MODE`、配对、session/TTL、签名验证、
  设备停止或其他安全控制；在未授权时对实体设备运动、刷写或断电。
- 把 fake、ACK、构建、文档或单元测试伪装成真实运动、真实 provider、物理 OTA/回滚或
  200-device scale 验收。
- 在公开场合披露安全漏洞的可利用细节、API key、token、nonce、设备密钥、原始媒体或
  未脱敏日志；通过压倒性发帖、恶意 review 或其他方式妨碍社区协作。

## 适用范围 / Scope

本准则适用于项目仓库、issue、PR、code review、讨论区、项目关联的聊天/会议、发布内容，
以及以项目维护者、贡献者或参与者身份进行的公开活动。维护者可以对明显影响社区安全、
包容性或信任的项目外行为采取适当措施，但会尽量基于可核实事实处理。

## 执行 / Enforcement

维护者负责解释和执行本准则，可根据严重程度和重复情况采取提醒、要求修改或撤回内容、
暂时限制参与、永久限制参与等措施。涉及利益冲突的维护者应回避处理；涉及安全漏洞时，
优先转入 [SECURITY.md](SECURITY.md) 的私密报告流程。维护决定应尽量说明适用规则、范围
和后续步骤，同时保护报告者的隐私。

## 如何报告 / Reporting

请不要在公开 issue/PR 中写出骚扰者的敏感个人信息或安全漏洞细节。优先使用仓库当前提供
的 GitHub private reporting / Security Advisory 渠道，或使用维护者公开的私密联系方式。
本仓库文件不虚构固定邮箱；如果你看不到可用的私密渠道，可以只提交不含细节的公开请求，
例如“需要行为准则报告私密渠道”，等待维护者转为私下沟通。报告中请提供事件时间、相关
位置、事实和希望的保密范围；不要附 API key、token、原始媒体或完整敏感日志。

恶意、报复性或明知虚假的报告也违反本准则；善意报告未知问题、误报或无法确认的风险不会
受到处罚。

## 归属 / Attribution

本文件参考 [Contributor Covenant 2.1](https://www.contributor-covenant.org/version/2/1/code_of_conduct/)
编写，并针对 StackChan LifeOS 的硬件安全、证据分级和负责任披露要求作了项目化补充。
