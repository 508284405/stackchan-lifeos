# StackChan LifeOS 安全与隐私设计

安全目标：即使主机、Agent、链路或输入数据失效，设备仍不会执行越权动作、泄露默认媒体或持续施力。安全设计分为主机权限、协议完整性、设备安全和数据治理四层。

## 1. 信任边界

```text
不可信：用户文本、语音转写、相机内容、NFC、串口输入、sub2api 输出、第三方工具
        ↓ schema + policy + TTL + audit
主机：LangGraph / Gateway / Sub2APIProvider / 本地工具执行器
        ↕ HTTPS + Bearer key + 脱敏有界 context
外部：sub2api / 上游模型（不可信推理边界）
        ↓ 配对会话 + 双端校验 + 有界 command
设备：CGraph 行为图
        ↓ 独立 safety loop
执行器：舵机、音频、灯光、显示
```

sub2api 与上游模型是不可信的远程推理边界，不自动等同于设备管理员，也不能直接执行
本机工具。LangGraph 是本地编排层，不自动获得文件、网络或外设权限。设备固件是最终
运动安全执行者。

## 2. 威胁与控制

| 威胁 | 控制 |
| --- | --- |
| prompt injection 诱导工具或运动 | 工具 allowlist、结构化 IntentPlan、策略节点、审批 interrupt |
| 恶意/错误 Agent 输出 | 双端 schema、行为注册表、TTL、强度/时长/数量上限 |
| 串口注入或重放 | 配对 nonce、会话序列、event_id 幂等、断线重新 hello |
| 主机文件/命令越权 | 远端不直接执行工具；本地工具 allowlist、参数 schema、审批与幂等 outbox |
| provider 凭据泄露 | OS secret/env 注入、日志/checkpoint 脱敏、轮换测试；不下发设备 |
| 推理数据越界外发 | 字段 allowlist、长度预算、raw media/路径/nonce/wire envelope 拒绝发送 |
| 设备被物理接入 | USB 配对确认、维护模式、清故障需本地动作；不把 USB 当作天然可信 |
| 舵机失控/卡滞 | 图外 safety loop、硬/软限位、反馈超时、急停、扭矩释放 |
| 原始媒体泄露 | 默认不传/不存；日志只存数值摘要；显式授权和短时保留 |
| 依赖供应链漂移 | 锁定版本、hash、schema fixture、SBOM/许可证和升级审查 |
| 日志泄露密钥或个人信息 | 字段 allowlist、脱敏、滚动保留、访问审计 |

## 3. 身份、配对与授权

V1 使用本机单设备配对模型：主机首次配对须显示设备标识并要求用户在设备上确认；生成随机会话 nonce 和本地凭据，凭据不进入 LangGraph state、日志或设备显示。每次连接重新协商会话，序列号从协商值继续。

权限分级：

- `observer`：读状态、健康和脱敏 telemetry。
- `operator`：暂停、恢复、回正、设置已允许参数。
- `maintainer`：校准和清故障；必须本地确认并产生审计事件。
- `agent`：只能发 IntentPlan 中的低风险行为；不能发原始运动、shell、网络或记忆删除命令。
- `emergency`：设备本地触摸/硬件路径优先；无需等待主机 ACK。

任何高风险外部动作（发消息、家电、门锁、购买、远程诊断写操作）默认禁用；启用时由 LangGraph interrupt 等待明确批准，并设置短期 expiry。

## 4. sub2api 与 LangGraph 安全边界

- `Sub2APIProvider` 只调用显式配置的 HTTPS endpoint；非回环 HTTP、跨域重定向和未批准
  endpoint 默认拒绝。
- API key 通过运行时 secret/env 注入，不进入 graph state、checkpoint、日志、trace、
  fixture 或设备 payload。
- 出站请求使用字段 allowlist 和大小预算，过滤 raw media、环境变量、绝对路径、nonce、
  设备 wire envelope、reasoning 和工具原始输出。
- sub2api 返回值始终视为不可信输入，经结构化 schema、策略、TTL 和行为注册表校验后，
  才能进入 Gateway。
- 首版不向远端声明可直接执行的 tools；工具意图由本地 LangGraph 决策，实际调用使用固定
  allowlist，禁止将用户文本直接拼接成 shell、URL 或路径。
- 401/403、429、5xx、timeout、网络错误、畸形 JSON 和中断 stream 均 fail closed；不自动
  回退本机 Codex，也不扩大权限。
- LangGraph 节点中的外部副作用采用 outbox、幂等键、超时和可撤销状态；checkpoint 不保存秘密。
- Agent 失败、限流或超时时，图转入 `DEGRADED_LOCAL`，不降级为“执行更多权限”。

### 4.1 Web Bridge 可信内网部署例外

Web Bridge 首版由单人在可信内网操作，产品决定不实现 Web 登录、token、用户角色或
RBAC。任何能访问 Bridge 监听地址的客户端都可能获得完整 Web 控制能力，因此该模式
不得表述为“已认证”，也不得直接映射、反向代理或端口转发到公网。

补偿性部署边界是：默认只监听回环地址；启用内网时显式选择私网接口；使用主机防火墙、
VLAN 或可信 Wi-Fi 限制访问；CORS/WebSocket Origin 使用 allowlist；UI 持续标识
“无认证内网模式”。这些措施不是用户认证。未来任何跨公网能力必须新增身份、加密、撤销
与审计主体设计。

该例外只适用于 Web 操作者身份，不取消设备/Edge 配对、硬件身份、session nonce、seq、
TTL、幂等、双端 schema 校验或设备 safety loop。详细边界见
`docs/rfc/0001-web-bridge-fleet-control.md`。

## 5. 设备安全不变量

以下不变量必须在固件单元测试、HIL 和代码审查中持续成立：

1. 所有运动目标经过软限位、硬限位、速率、动作 TTL 和状态门。
2. safety loop 不依赖 LangGraph、sub2api、USB、Wi-Fi、相机或 CGraph executor。
3. 急停、故障锁存和用户暂停不能被普通 Agent command 清除。
4. 复位、断线、心跳超时和非法命令不会导致持续施力。
5. 原始图像/音频不写入持久化存储，除非用户已授权且有单独的保留策略。

## 6. 数据生命周期

| 数据 | 默认 | 例外 |
| --- | --- | --- |
| 原始图像/音频 | 不传、不存 | 明确授权的短时本地调试 |
| 传感器摘要 | 内存有界窗口 | 为诊断写入脱敏滚动日志 |
| transcript | 当前 thread 窗口 | 用户启用会话历史 |
| provider 推理上下文 | 默认不发送 | 显式启用后发送脱敏、有界文本/结构化摘要；不含 raw media、路径、nonce 或设备 wire envelope |
| 长期记忆 | 不自动写入 | 用户可见、可删除、策略允许 |
| 安全/审计事件 | 本机滚动保存 | 维护者按权限导出 |
| 凭据/nonce | OS/设备安全存储 | 永不进日志、图状态或协议 payload |

用户应能查看、暂停和删除长期记忆；删除操作本身要审计，但审计记录不复制被删除的内容。

## 7. 安全事件响应

严重级别：`info`（能力变化）、`warning`（降级/拒绝）、`critical`（急停/越界尝试/凭据异常）。`critical` 事件锁存设备故障、停止普通动作并保留最小诊断字段。修复流程：隔离 → 导出脱敏日志 → 验证固件/主机版本 → 清故障/回滚 → 复测急停与协议回放。

## 8. 参考资料

- [sub2api 官方仓库](https://github.com/Wei-Shaw/sub2api)
- [LangGraph 持久化与恢复](https://docs.langchain.com/oss/python/langgraph/persistence)
- [LangGraph 中断与人工确认](https://docs.langchain.com/oss/python/langgraph/interrupts)
- [CGraph 官方仓库](https://github.com/ChunelFeng/CGraph)
