# StackChan LifeOS 路线图

路线图按“先安全身体，再本机 Agent，最后主动能力”的顺序推进。每阶段有可演示的出口和不可逾越的门禁；未通过门禁不得进入下一阶段。

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

## 阶段 2：LangGraph + 本机 Codex（3–6 周）

交付：Python LangGraph graph、checkpoint/store、Device Gateway、Codex Adapter、IntentPlan validator、审批 interrupt 和可观测性。

出口：

- 用户输入能经过 `receive → context → recall → codex → validate → dispatch`。
- Codex 进程退出、限流、schema 漂移、主机重启均有降级和恢复路径。
- Agent 不能调用未授权文件/网络/设备原语；高风险工具会暂停等待批准。
- SLO 达到 PRD 中 p95/p99 目标，日志可关联 `run_id`、`event_id` 和设备故障。

## 阶段 3：感知与生命感（4–8 周）

交付：稳定目标选择、有限主动行为、情绪/表情映射、语音适配、长期记忆策略、安静时段和用户偏好。

出口：

- 连续主动行为有频率、时长和冷却上限。
- 原始媒体默认不保存；记忆写入可解释、可删除、有审计记录。
- 设备断链时体验退化为本地反射，不出现持续搜索或重复播报。

## 阶段 4：可选扩展（按需）

多设备、局域网加密 transport、NFC 场景、Home Assistant、远程诊断和云端模型均为独立 RFC。每项扩展需重新评估隐私、配对、权限和实时性；不得绕过本协议和设备 safety loop。

## 发布门禁

每个 release candidate 必须附带：

- 固件/主机/协议/Codex/CGraph 版本矩阵；
- schema 兼容与协议回放结果；
- 故障注入、急停、限位和断线报告；
- 资源预算（heap、PSRAM、CPU、串口队列）；
- 安全与隐私变更记录；
- 未验证项和回滚方案。
