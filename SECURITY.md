# 安全政策 / Security Policy

StackChan LifeOS 是仍在开发中的本地优先机器人系统。它包含 USB 设备协议、主机服务、Web
控制、相机媒体、模型 provider 和固件执行器；安全问题可能同时影响信息保密、主机权限和
实体运动。请在报告前先让设备处于安全停止状态，并按本文件进行 private disclosure。

## 支持范围 / Supported versions

仓库目前没有发布版本的长期安全支持矩阵或安全响应 SLA。维护重点是当前默认开发线的
最新代码和仍被维护者明确标注为 active 的版本；旧提交、个人 fork、未声明的镜像和经过
修改的设备配置不承诺支持。请在报告中写明 commit、固件 profile、协议版本和受影响组件。

项目的功能验收状态仍按 `PASS`、`PARTIAL`、`BLOCKED`、`NOT TESTED` 分开记录。安全报告中
不要把 host/fake PASS、CI、schema、ACK 或 build success 当作真实 provider、真实媒体、
实体运动、物理 OTA/rollback 或 200-device scale 的证明。当前阶段与已知限制见
[`docs/security.md`](docs/security.md)、[`docs/phase1-acceptance.md`](docs/phase1-acceptance.md)
和最新的验收报告。

## 报告渠道 / Private reporting

请勿把可利用细节、PoC、凭据或敏感数据直接发到公开 issue、讨论或 PR。

1. 如果仓库页面提供 GitHub Security Advisory / private vulnerability reporting，优先使用
   该渠道。
2. 否则使用维护者公开的私密联系方式；本文件不虚构固定邮箱或未配置的 GitHub 设置。
3. 如果你没有任何私密渠道，只能创建一个不含漏洞细节的最小公开请求（例如“需要安全报告
   私密渠道”），不要附 PoC、完整日志、密钥或受影响用户数据，请维护者转入私下沟通。

报告请尽量包含：

- 简短标题与影响组件（`firmware`、`brain`、`bridge`、`web`、`contracts`、`tools` 等）；
- 受影响的 commit/tag、构建 profile、协议版本和部署方式；
- 影响类型（confidentiality / integrity / availability / physical safety）与攻击前提；
- 最小、脱敏且可复现的步骤，或用于确认问题的 fixture；
- 已知缓解措施、是否已在野外暴露、希望的披露时间和联系方式。

请删除或替换 API key、Bearer token、密码、session nonce、配对凭据、设备唯一密钥、绝对
本机路径、原始 JPEG/audio、用户 transcript、完整 USB dump 和私人备份。若必须证明媒体
问题，提供脱敏元数据或最小 synthetic fixture，不上传原始媒体。

## 哪些问题属于安全范围 / In scope

以下类型优先作为安全报告处理：

- 绕过 schema、policy、session/nonce、seq、TTL、幂等或 capability gate，导致越权 command；
- LLM、sub2api、LangGraph、Web 或 Bridge 产生/转发 PWM、GPIO、I²C、原始舵机角、shell、
  URL、任意设备 envelope，或绕过 pitch 5°–85° 与设备 Safety Controller；
- USB JSONL parser、串口注入/重放、恶意 payload、内存破坏、未授权设备配对或审计绕过；
- Web Bridge、文件、checkpoint、日志、fixture 或 provider 出站请求泄露凭据、nonce、路径、
  raw media 或个人信息；
- 视频帧生命周期、viewer/lease/dead-man 失效导致旧画面继续手控，或失联后自动运动/回正；
- 固件镜像签名、摘要、硬件/协议/分区兼容性、A/B rollback、PENDING_VERIFY 或恢复信任
  被绕过，导致不可信镜像启动或不可恢复刷写；
- 默认部署把无认证 Bridge 暴露到不可信网络，或能利用该边界取得 Web 控制。

普通崩溃、功能缺陷、文档错误、性能建议和使用疑问请使用普通 issue；如果不确定，先按
安全问题最小化披露，维护者会分流。

## 安全测试边界 / Safe testing

- 默认只使用 deterministic fake provider、simulator、host tests 和本机测试设备；不要在
  普通测试中调用真实 provider 或消耗配额。
- Bridge 首版没有用户登录、token 或 RBAC；能访问监听地址的客户端可能获得完整 Web 控制。
  默认仅监听 loopback；若明确在可信内网测试，使用防火墙/VLAN/可信 Wi-Fi 与 Origin allowlist，
  不要公网暴露、端口转发或把“无认证内网模式”称为已认证。
- 不要扫描不属于你的网络或设备，不要用 brute force、fuzzing、负载攻击或 destructive
  payload 验证漏洞。只在自有/明确授权的环境中测试，并设置停止条件。
- 对真实 StackChan 的非零运动、刷写、分区/bootloader/eFuse、物理断电、卡滞或触摸测试，
  必须有设备所有者明确授权、身份和恢复备份复核、现场监督和独立记录。维护/HIL 命令必须
  由 `LIFEOS_HIL_TEST_MODE` 隔离；不要为了复现安全问题关闭安全门。
- `tools/scan_usb_devices.py --probe` 会打开 USB CDC 并触发 ESP32-S3 复位；先关闭 Bridge
  或 HIL 对该端口的占用。读到 `no-response` 不足以证明设备故障。
- 任何相机、麦克风或 transcript 数据都应短时、最小化、脱敏处理，不落库、不进 audit、
  checkpoint、provider 或公开附件。

## 已知安全限制 / Known limitations

- Web Bridge 的可信内网例外目前没有用户身份认证；它不是公网服务。CORS/Origin、主机防火墙
  和 loopback 监听是部署补偿措施，不等同于 authentication。
- `contracts/phase1/envelope.schema.json` 才是设备 wire truth；浏览器 DTO 不得直发固件，
  root Phase 2 schemas 也不能替代 Phase 1 parser。
- `manual_control_v1`、相机新鲜度、lease/dead-man、维护和 OTA 路径仍由 capability、session、
  evidence 与安全门控制；实时 `accepted`/HTTP 成功不表示实体动作完成。
- 主机/fake、固件 host test、ESP-IDF target build 和 OTA artifact 检查不等于物理回滚。
  截至当前验收记录，真实签名升级/断电 rollback 为 `NOT TESTED`，真实连续手控/媒体状态
  仍需独立实机验收；网络、mTLS、4G、多站点和 200-device scale 也不能由本地测试替代。

## 响应与披露 / Response and disclosure

维护者会在可行时确认收到报告，复现并按影响范围评估严重性，必要时提供缓解、修复或协调的
advisory。由于项目处于开发阶段，暂不承诺确认、修复或披露的时间表；请在公开披露前给维护
者合理的协调时间，并在报告中说明你的时间要求。若漏洞涉及实体安全，报告中明确“停止设备、
撤销 lease、断开网络/USB、保留最小脱敏证据”等临时措施。

安全修复的提交、测试和 release note 必须继续遵守本仓库的安全边界；不能因为修复紧急就把
真实运动、provider、媒体、OTA 或规模化能力写成未经验证的 PASS。
