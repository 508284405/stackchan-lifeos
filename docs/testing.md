# StackChan LifeOS 测试与验证策略

目标：证明“Agent 可恢复、协议可兼容、设备可降级、运动始终受安全边界约束”。在固件实现完成前，先用 fake device、协议回放和故障注入锁定契约。

## 1. 测试层级

| 层级 | 被测对象 | 必测内容 |
| --- | --- | --- |
| Schema | JSONL envelope、IntentPlan | 必填字段、枚举、大小、版本、未知字段 |
| 主机单元 | LangGraph 节点、策略、validator、Sub2APIProvider | 状态 reducer、TTL、幂等、工具 allowlist、错误映射、出站脱敏 |
| 图回放 | 完整 LangGraph | 事件序列、checkpoint 恢复、interrupt/resume、重启 |
| 设备单元 | CGraph-shaped nodes、safety loop、控制器 | 限位、超时、状态转换、固定内存、边界输入 |
| Gateway 集成 | 主机↔fake device | seq、ACK、断线、背压、重复消息、版本协商 |
| ESP-IDF spike | CGraph selective-port/上游子集 | 编译、栈/heap、executor、看门狗、许可证 |
| HIL | 真实 StackChan | 舵机卡滞、急停、相机失败、触摸、断电恢复 |
| 长稳 | 主机+真实设备 | 8 h 运行、内存趋势、串口队列、provider 降级/恢复 |

## 2. 关键性质与断言

### Agent/Graph

- 相同的有序输入和 graph 版本产生相同的策略决定（LLM 输出用录制 fixture 替代）。
- checkpoint 恢复不会重复非幂等副作用；outbox 使用 `idempotency_key`。
- `interrupt` 拒绝、超时和恢复均产生可关联的 `approval_id`、`run_id` 和审计事件。
- sub2api/OpenAI Responses payload 改变、缺字段或类型漂移时，Provider contract test 失败
  而不是静默放行。
- 测试确认 raw media、路径、环境变量、nonce、设备 wire envelope 和 API key 不进入出站
  request、checkpoint、日志或 fixture。
- 默认测试使用 mock/deterministic provider；真实 sub2api 测试必须显式 opt-in。

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
| sub2api 401/403 | fail closed，报告配置/鉴权错误，不重试风暴、不回退本机 Codex |
| sub2api 429/5xx/timeout/network | 有界退避后 Agent 降级，设备保留本地反射 |
| 畸形 JSON/缺字段/partial stream | 丢弃本次结果，不 dispatch，不写长期记忆 |
| LangGraph 进程重启 | checkpoint 恢复，过期动作不重放 |
| USB 拔出 2 s | 设备停止普通命令并进入 idle |
| 重放旧 command | `expired` 或 `duplicate`，不运动 |
| 舵机反馈冻结 | `FAULT_STALL`，stop + torque off |
| 相机连续失败 | 能力标记 unavailable，不误判无人 |
| 发送未知行为/NaN/超长 payload | 双端拒绝，记录安全事件 |
| executor 人为阻塞 | safety loop 仍按预算运行并可急停 |
| 主机时间跳变 | TTL 仍使用设备/主机 monotonic 规则 |

## 4. 性能与 SLO 验证

每个 release candidate 采集至少 p50/p95/p99：急停、触摸反馈、Gateway ACK、Agent 首状态、结构化意图延迟；同时记录 heap、PSRAM、CPU、队列深度、舵机实际/目标角。测试报告必须注明设备型号、固件、主机、LangGraph、Sub2APIProvider contract、endpoint/model、CGraph adapter 和协议版本；不得记录 API key。

## Web Bridge 扩展测试基线

Web Bridge 首版以一台设备完整闭环为验收目标，不要求或暗示 200 台容量已经验证：

当前 W1 host-only 回归入口为 `PYTHONDONTWRITEBYTECODE=1 make bridge-test`，并已纳入
`make test`；它不连接真实设备、不执行真实运动。

- 一台 fake device 覆盖 discover、claim、hello、session、command、ACK、完成与审计；
- 一台真实 USB 设备先覆盖身份、status、health、断线和重连的无运动路径；
- 同一设备作为单目标批量任务时仍走 BatchTask/BatchTarget；
- 重复、乱序、过期、错 session、Bridge 重启和 late ACK 不导致动作重放；
- dead-man 在页面失焦、WebSocket 断开或心跳停止后 500 ms 内失效；
- Agent、批量、维护和手动控制按固定优先级抢占并返回原因；
- UI 分开显示 created、accepted、completed、rejected、offline、timeout 和
  safety-blocked；
- 摄像头预览必须先通过 media gate 和 camera capability，再验证 begin/chunk/end 的
  JPEG 完整性、单设备最新帧、MJPEG 输出、断线清理和不落盘；测试不能把 raw JPEG
  写入 audit、SQLite 或认知 provider。
- Web 无认证模式验证监听地址、Origin allowlist 和风险提示，但不得报告为认证测试通过；
- 真实运动、急停、限位和卡滞继续服从 Phase 1 HIL，未通过时标记 BLOCKED/NOT TESTED。

完整矩阵见 `docs/web-bridge-implementation-plan.md`。

W3 当前只验收 host-only lease/input、急停和 semantic intent spike：`ControlLease` 的
session/connection 绑定、300–500 ms TTL、10 Hz、strict `input_seq`、release/断链、急停
抢占、behavior/speech allowlist 和默认 feature gate。没有 firmware parser、真实运动或
HIL 证据前，不得将此测试写成 manual control/远程行为已支持。

Web 控制与摄像头预览的 fake/API/UI 以及当前 `lifeos-phase1-0.5.0` 实机 JPEG/MJPEG、
health 和 motion/manual capability handshake 验收见 `docs/web-control-camera-report.md`；
真实连续手控、实体运动和安全 HIL 仍需独立受监督验收。

阶段 4 后续 host-only 验收还包括：

- W4 maintenance challenge 必须绑定当前 device session，一次性确认校验
  `operation/result/valid_for_ms`；断线、重连和 Bridge 重启使未完成任务过期。诊断导出只
  允许固定字段并提供完整性哈希；rollout 的签名、兼容性和 OTA wire gate 默认关闭。
- W5 `lifeos.edge.v1` contract/fake 覆盖 sender/device/session 绑定、双向 seq、重复/乱序、
  heartbeat、断线拒绝新动作和重连不重放；真实 Wi-Fi、TLS/mTLS 和 Edge Agent 另行验收。
- W6 control-plane schema/fixture 覆盖 sender、epoch、expiry、replay、消息大小和设计期
  gates；真实多站点、4G、证书轮换、迁移和公网身份不能由 schema 通过替代。
- W7 容量 runner 必须有输入上限、确定性输出、telemetry coalesce/drop、batch partial 和
  recovery 语义；RSS/CPU/真实 DB latency 不能用逻辑 workload 单位伪造，200 台保持
  NOT TESTED，直到正式 load/soak 有设备数和持续时间证据。

## 5. 测试数据与隐私

默认使用合成 observation 和脱敏 transcript。真实音视频只在明确授权的本地 HIL 中短时使用，不提交仓库、不写共享日志。回放 fixture 删除 token、路径、账户、设备唯一密钥和原始媒体。

## 6. 完成门禁

P0 需求必须有自动化测试或可复现 HIL 记录；没有测试证据的行为只能标记 Draft，不能写入“已支持”或 release note。
