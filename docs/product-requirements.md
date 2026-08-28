# StackChan LifeOS 产品需求文档

状态：Draft  
版本：0.1  
目标：让 StackChan 在本机 Agent 驱动下持续感知、表达并安全地与人互动

## 1. 产品定义

LifeOS 是一个本地优先的 Physical AI 桌面伙伴。StackChan 负责身体和反射，主机负责 LangGraph 状态图与规划，Codex CLI 作为本机 Agent runtime。系统的价值不是“能回答问题”，而是让设备在无人交互时仍有可解释、克制、可暂停的生命感。

## 2. 用户与场景

| 用户 | 任务 | 首要成功标准 |
| --- | --- | --- |
| 开发者 | 调试传感器、图、协议和模型调用 | 可复现日志、模拟器和故障注入 |
| 调试者 | 校准舵机、查看健康、恢复故障 | 不需要改代码即可暂停/回正/清故障 |
| 桌面用户 | 交谈、触摸、观察主动行为 | 行为自然，随时知道设备在做什么 |

典型场景：唤醒问候、触摸反馈、感知有人靠近、短时闲置行为、用户提问、主动提醒（需策略和审批）、设备故障降级。

## 3. 目标与非目标

### 3.1 V1 目标

- 单设备通过 USB 与单主机通信。
- 设备拥有本地反射：眨眼、表情、触摸、姿态和安全运动。
- 主机以 LangGraph 编排 observation、记忆、Agent 规划和行为下发。
- 通过本机 Codex app-server 提供推理/工具能力；所有工具显式 allowlist。
- 使用统一、版本化、可审计的 JSONL envelope。
- 默认不保存原始图像和音频，不向云端上传设备数据。

### 3.2 非目标

- ESP32 上运行通用 LLM、身份识别或任意 Python/JS。
- 无人时持续追踪或无限扫描。
- 默认执行发消息、购买、门锁、家电高风险操作。
- 将 Codex 原生 app-server 协议当作设备公共协议。
- 首版承诺多设备编排、云端部署或移动端应用。

## 4. 功能需求

### P0：必须

| ID | 需求 | 验收 |
| --- | --- | --- |
| FR-01 | 设备启动自检并进入安全状态 | 传感器/舵机异常时不运动并显示错误 |
| FR-02 | 接收 observation 并生成结构化状态 | 每条事件有 `event_id`、`device_id`、schema 版本 |
| FR-03 | LangGraph 支持 thread 恢复和有限记忆 | 重启后可恢复未完成会话，不重复非幂等副作用 |
| FR-04 | Codex Adapter 支持健康检查、超时和降级 | Codex 退出后设备仍可本地待机 |
| FR-05 | IntentPlan 双端 schema 校验 | 非法动作被拒绝且记录原因 |
| FR-06 | 设备端安全门、急停、软硬限位、卡滞处理 | 主机失联或命令异常时停止运动 |
| FR-07 | 用户可暂停、回正、查看状态、清故障 | 触摸和 USB 控制都产生确认事件 |
| FR-08 | 结构化 telemetry 和错误码 | 能定位链路、图节点、驱动和安全故障 |

### P1：应该

- 多目标感知的稳定选择和短时丢失保持。
- 语音输入/输出的本机或可配置提供商适配。
- 主动行为策略：频率上限、安静时段、冷却时间和用户暂停。
- 需要用户确认的 Agent 工具通过 LangGraph interrupt 恢复。
- UI 显示当前状态、来源（本地反射/Agent）和倒计时。

### P2：可以

- NFC 场景卡、局域网多设备、Home Assistant、可插拔记忆后端。
- 视觉摘要而非原始帧上传。
- 远程诊断（显式配对、短时授权、默认只读）。

## 5. 体验状态

`BOOT`、`IDLE`、`LISTENING`、`THINKING`、`EXPRESSING`、`PAUSED`、`DEGRADED`、`FAULT`、`EMERGENCY_STOP` 为产品级状态。设备状态优先级高于 Agent 情绪：`FAULT` 和 `EMERGENCY_STOP` 不得被 `happy` 等表达覆盖。

## 6. 非功能需求与 SLO

SLO 适用于 V1 单设备、USB 连接、常规室内环境；不适用于网络断开、供电不足、机械损坏或 Codex 服务端限流。

| 指标 | 目标 |
| --- | --- |
| 安全急停响应 | p95 ≤50 ms，p99 ≤100 ms |
| 触摸到本地反馈 | p95 ≤150 ms |
| 主机事件接收确认 | p95 ≤300 ms |
| 用户请求到首个可见状态 | p95 ≤1 s |
| Agent 请求到结构化意图 | p95 ≤3 s；超时 10 s 后降级 |
| 运动命令执行 | 100% 经过设备端安全门；0 次越过硬限位 |
| 主机进程可用性 | 月度 99%，不含人工升级窗口 |
| 设备连续运行 | 8 h 演示无崩溃、无内存单调增长 |
| 数据默认保留 | 原始音视频 0 天；日志仅保留本机配置的滚动窗口 |

## 7. 验收场景

1. 主机断电：设备在超时后停止运动，进入本地 idle，恢复主机后重新配对。
2. Codex 不可用：对话失败显示可理解状态，触摸和安全功能继续工作。
3. Agent 输出未知动作：Gateway 与设备均拒绝，舵机目标不变化。
4. 重复事件：按 `event_id` 幂等处理，不重复播报或写记忆。
5. 用户拒绝审批：图从 interrupt 恢复到可解释的取消状态，不执行工具。
6. 目标丢失：短时保持，达到期限后停止搜索，不无限摆头。
7. 舵机卡滞：触发 `FAULT_STALL`，停止发送目标并提示人工清故障。

## 8. 版本策略

产品版本、内部 graph 版本、设备固件版本和 Codex CLI 版本独立记录。协议只在兼容矩阵中声明支持范围；升级任一项前先运行回放、schema、故障注入和安全测试。

## 9. 参考资料

- [M5Stack StackChan 官方文档](https://docs.m5stack.com/en/StackChan)
- [LangGraph 官方概览](https://docs.langchain.com/oss/python/langgraph/overview)
- [OpenAI Codex app-server](https://github.com/openai/codex/blob/main/codex-rs/app-server/README.md)
- [CGraph](https://github.com/ChunelFeng/CGraph)
