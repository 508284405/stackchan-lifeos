# StackChan LifeOS 测试与验证策略

目标：证明“Agent 可恢复、协议可兼容、设备可降级、运动始终受安全边界约束”。在固件实现完成前，先用 fake device、协议回放和故障注入锁定契约。

## 1. 测试层级

| 层级 | 被测对象 | 必测内容 |
| --- | --- | --- |
| Schema | JSONL envelope、IntentPlan | 必填字段、枚举、大小、版本、未知字段 |
| 主机单元 | LangGraph 节点、策略、validator、Codex Adapter | 状态 reducer、TTL、幂等、工具 allowlist |
| 图回放 | 完整 LangGraph | 事件序列、checkpoint 恢复、interrupt/resume、重启 |
| 设备单元 | CGraph-shaped nodes、safety loop、控制器 | 限位、超时、状态转换、固定内存、边界输入 |
| Gateway 集成 | 主机↔fake device | seq、ACK、断线、背压、重复消息、版本协商 |
| ESP-IDF spike | CGraph selective-port/上游子集 | 编译、栈/heap、executor、看门狗、许可证 |
| HIL | 真实 StackChan | 舵机卡滞、急停、相机失败、触摸、断电恢复 |
| 长稳 | 主机+真实设备 | 8 h 运行、内存趋势、串口队列、Codex 重启 |

## 2. 关键性质与断言

### Agent/Graph

- 相同的有序输入和 graph 版本产生相同的策略决定（LLM 输出用录制 fixture 替代）。
- checkpoint 恢复不会重复非幂等副作用；outbox 使用 `idempotency_key`。
- `interrupt` 拒绝、超时和恢复均产生可关联的 `approval_id`、`run_id` 和审计事件。
- Codex app-server schema 改变时，Adapter contract test 失败而不是静默放行。

### 协议/Gateway

- 超限、非法 JSON、未知 schema、乱序 seq 和过期 command 均被拒绝。
- duplicate `event_id` 返回 `duplicate`，不重复动作、播报、记忆或日志副作用。
- 断线后旧 command 不能在重新 hello 前执行；普通行为过期，急停仍可本地触发。
- 背压时保留安全事件，丢弃或合并旧 observation，不无限增长队列。

### 固件/安全

- 任意 Agent payload 都不能越过硬限位、速率限制和动作 TTL。
- safety loop 在 executor 阻塞、主机断线、相机异常或 heap 压力下仍能停止运动。
- 反馈角与目标角偏差持续超时触发 `FAULT_STALL`，且不会自动无限重试。
- 启动、复位和故障恢复默认舵机停止；清故障需要明确用户动作。

## 3. 故障注入矩阵

| 注入 | 预期结果 |
| --- | --- |
| Codex 进程退出/返回 overload | Agent 降级，设备保留本地反射 |
| LangGraph 进程重启 | checkpoint 恢复，过期动作不重放 |
| USB 拔出 2 s | 设备停止普通命令并进入 idle |
| 重放旧 command | `expired` 或 `duplicate`，不运动 |
| 舵机反馈冻结 | `FAULT_STALL`，stop + torque off |
| 相机连续失败 | 能力标记 unavailable，不误判无人 |
| 发送未知行为/NaN/超长 payload | 双端拒绝，记录安全事件 |
| executor 人为阻塞 | safety loop 仍按预算运行并可急停 |
| 主机时间跳变 | TTL 仍使用设备/主机 monotonic 规则 |

## 4. 性能与 SLO 验证

每个 release candidate 采集至少 p50/p95/p99：急停、触摸反馈、Gateway ACK、Agent 首状态、结构化意图延迟；同时记录 heap、PSRAM、CPU、队列深度、舵机实际/目标角。测试报告必须注明设备型号、固件、主机、Codex CLI、LangGraph、CGraph adapter 和协议版本。

## 5. 测试数据与隐私

默认使用合成 observation 和脱敏 transcript。真实音视频只在明确授权的本地 HIL 中短时使用，不提交仓库、不写共享日志。回放 fixture 删除 token、路径、账户、设备唯一密钥和原始媒体。

## 6. 完成门禁

P0 需求必须有自动化测试或可复现 HIL 记录；没有测试证据的行为只能标记 Draft，不能写入“已支持”或 release note。
