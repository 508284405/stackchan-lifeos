# StackChan LifeOS 路线图

路线图按“先安全身体，再本地主机编排，最后主动能力”的顺序推进。每阶段有可演示的出口和不可逾越的门禁；未通过门禁不得进入下一阶段。

## 当前状态（2026-09-02）

- 阶段 0：PASS。Codex CLI schema/握手契约、CGraph ESP-IDF spike、硬件
  身份和安全边界均有仓库证据。
- 阶段 1：软件实现、ESP32-S3 production/HIL build、板级初始化和 SCS 执行器
  闭环子门禁 PASS；整体出口仍为 **BLOCKED/PARTIAL**。设备侧急停为 1 ms，
  但端到端观测约 209.6 ms；触摸、8 小时 soak、真实机械卡滞、断线独立时序和
  传感器读取/capture 仍未测试。详情见 `docs/scs-runtime-io-acceptance.md` 和
  `docs/phase1-acceptance.md`。
- 在阶段 1 整体出口闭合前，不进入阶段 2 的设备主动能力扩展；阶段 2 的 host-only
  provider 契约、mock、图回放，以及 Web Bridge 的 motion-disabled/fake-device 工作
  可独立推进。
- 阶段 2：host-only provider、LangGraph 恢复/审批/幂等 dispatch、脱敏、降级和离线
  contract 已完成并通过确定性验收；真实 Sub2API smoke 仍未测试，详见
  `docs/phase2-3-acceptance.md`。
- 阶段 3：host-only 目标选择、主动行为上限、情绪/语音语义、偏好和审计记忆已完成；
  真实相机、麦克风、音频 transport 和机械追踪仍未测试，详见
  `docs/phase3-perception-life-plan.md`。

## 阶段 0：证据与 Spike（1–2 周）

交付：硬件清单、ESP-IDF 基线、协议 schema、主机 fake device、Codex app-server adapter 最小连接、CGraph selective-port 设计。

门禁：

- 确认具体 StackChan/CoreS3 版本、舵机反馈和相机初始化路径。
- Codex schema 从实际 CLI 版本生成并通过握手/断线测试；不依赖 websocket。
- CGraph 上游 ESP-IDF spike 明确成功或失败；失败时保留 CGraph-shaped 实现，不偷偷引入未验证上游。
- 安全 threat model、急停和权限矩阵评审完成。

## 阶段 1：身体与本地反射（2–4 周）

交付：ESP-IDF 固件、CGraph-shaped 静态有界 DAG、独立 safety loop、显示/触摸/IMU/舵机/相机适配器、USB JSONL gateway。

出口：

- 触摸暂停、回正、故障清除可用。
- 主机断开、帧卡死、舵机卡滞和越界命令均能安全停止。
- 连续运行 8 小时无崩溃、无硬限位越界、无未界定动态内存增长。
- 协议回放、固件单元测试和 HIL 安全测试通过。

## 阶段 2：LangGraph + sub2api（3–6 周）

交付：Python LangGraph graph、checkpoint/store、Device Gateway、`Sub2APIProvider`、
IntentPlan validator、审批 interrupt 和可观测性。实施拆分与验收矩阵见
`docs/phase2-sub2api-plan.md`。

出口：

- 用户输入能经过 `receive → context → recall → sub2api → validate → dispatch`。
- sub2api 鉴权失败、限流、5xx、超时、响应 schema 漂移和主机重启均有降级与恢复路径。
- 远端只接收脱敏、有界推理上下文；API key 不进入 checkpoint、日志或设备协议。
- 生产路径不启动或自动回退本机 Codex app-server；关闭 provider 后只保留确定性测试
  provider 和设备本地反射。
- Agent 不能调用未授权文件/网络/设备原语；高风险工具会暂停等待批准。
- SLO 达到 PRD 中 p95/p99 目标，日志可关联 `run_id`、`event_id` 和设备故障。

## 阶段 3：感知与生命感（4–8 周）

交付：稳定目标选择、有限主动行为、情绪/表情映射、语音适配、长期记忆策略、安静时段和用户偏好。

当前 host-only 任务与验收矩阵见 `docs/phase3-perception-life-plan.md`；真实媒体和机械能力仍需独立门禁。

出口：

- 连续主动行为有频率、时长和冷却上限。
- 原始媒体默认不保存；记忆写入可解释、可删除、有审计记录。
- 设备断链时体验退化为本地反射，不出现持续搜索或重复播报。

## 阶段 4：可选扩展（按需）

多设备、局域网加密 transport、NFC 场景、Home Assistant、远程诊断和云端模型均为独立 RFC。每项扩展需重新评估隐私、配对、权限和实时性；不得绕过本协议和设备 safety loop。

Web Bridge 多设备管理与远程控制已完成 W1–W4 的 host/fake 基线，并冻结了 W5 Edge
和 W6 control-plane 的设计契约；W7 已有可重复的容量方法基线。阶段 4 整体仍为
**PARTIAL**：Web Bridge 的长连接 USB 断线/重连和浏览器真实 E2E、真实维护/OTA、真实
Wi-Fi/mTLS/4G、多站点生产实现和真实容量/200 台验收尚未通过；直接 USB 与一次性 Bridge
hello/status 只读闭环已通过。
产品目标为 200 台，但在真实容量测试前不声明已支持 200 台。
Web 端首版为可信内网单人无认证模式，禁止公网暴露。逐项证据见
`docs/phase4-acceptance.md` 和 `docs/web-bridge-implementation-plan.md`。

本目标已补齐 Web typed 控制和真实连续 MJPEG 视频的 host/fake/UI 交付；production 实机
35 秒、10.57 fps、371 个完整 JPEG 和浏览器实时画面已 **PASS**。真实 Bridge 的手动 UI/
lease 已通过，实体运动、急停和机械 HIL 仍保持 **NOT TESTED/BLOCKED**，详见
`docs/web-control-camera-report.md`。

该扩展的主机 fake、无运动管理和 UI 工作可以独立推进；任何真实远程运动验收仍受当前
阶段 1 执行器 HIL 门禁约束，不能用 Web 软件测试替代硬件安全证据。

## 发布门禁

每个 release candidate 必须附带：

- 固件/主机/协议/sub2api endpoint/model/CGraph 版本矩阵；
- schema 兼容与协议回放结果；
- 故障注入、急停、限位和断线报告；
- 资源预算（heap、PSRAM、CPU、串口队列）；
- 安全与隐私变更记录；
- 未验证项和回滚方案。
