# 阶段 2 任务计划：LangGraph + sub2api

- 状态：Host-only implementation complete；真实 provider release gate 未测试
- 日期：2026-08-30
- 预计周期：3–6 周
- 上游门禁：阶段 1 整体出口仍为 `BLOCKED/PARTIAL`；阶段 2 可完成 host-only
  契约、mock 和图回放，但不得借此启用未验收的真实运动。
- 架构决定：以 sub2api 的 OpenAI Responses 兼容 API 替代本机
  `codex app-server`；不保留本机 Codex runtime 作为生产 fallback。
- 当前 host-only 验收证据见 [`docs/phase2-3-acceptance.md`](phase2-3-acceptance.md)。
  未注入凭据时不运行真实 smoke；这不阻塞 deterministic/mock contract，但不等同于真实
  endpoint/model 已通过验收。

## 1. 目标与边界

阶段 2 在主机上完成可恢复的 LangGraph Agent：

```text
receive → normalize → context → recall → sub2api → validate
        → [approval interrupt] → dispatch → observe → checkpoint
```

sub2api 只提供推理网关。以下能力仍由 LifeOS 本机进程负责：

- LangGraph graph、thread/checkpoint、long-term store 和恢复；
- prompt/data redaction、IntentPlan schema、Policy Validator、工具 allowlist；
- 人工审批 interrupt、outbox、幂等、副作用执行和审计；
- Device Gateway、`lifeos.v1` 映射、设备 SafetyGate 和图外 safety loop。

明确不做：

- 不启动、探测或依赖本机 `codex app-server` 进程；
- 不把 sub2api response ID 当作 LangGraph `thread_id` 或本地事实源；
- 不向 sub2api 发送 raw 图像、音频、设备凭据、nonce、主机路径、环境变量、工具输出
  或设备 wire envelope；
- 不让远端模型直接执行 shell、文件、网络或设备工具；模型只返回结构化候选意图；
- 不接入 sub2api 的支付、用户管理、Admin API 或账户分发管理面。

## 2. 关键架构决定

### 2.1 Provider 接口

新增 `Sub2APIProvider`，通过 HTTPS 调用 OpenAI Responses 兼容接口：

- 模型列表/健康探测：`GET <base_url>/v1/models`；
- 推理：`POST <base_url>/v1/responses`；
- 鉴权：`Authorization: Bearer <api_key>`；
- 流式：仅在 contract test 证明该部署兼容后启用；首个可用版本允许非流式；
- structured output：发送有限 JSON Schema，并对返回结果再次执行 Pydantic/schema 校验。

配置全部由部署注入，不在仓库、checkpoint、日志或 UI 回显完整值：

```text
SUB2API_BASE_URL
SUB2API_API_KEY
SUB2API_MODEL
SUB2API_CONNECT_TIMEOUT_SECONDS
SUB2API_REQUEST_TIMEOUT_SECONDS
SUB2API_TLS_VERIFY
```

`SUB2API_BASE_URL` 标准化为不重复拼接 `/v1`；非 loopback 部署必须使用 HTTPS 并验证
证书。model/group 由配置固定，不相信用户 prompt 自报模型。

### 2.2 本地状态与上游状态

- LifeOS `thread_id`、graph version、checkpoint 和 conversation 摘要只保存在本机；
- 每次请求构造有界 context，不能依赖 sub2api/upstream 会话持久化；
- upstream `response_id` 仅作为诊断关联字段，不能用于恢复副作用；
- reasoning 请求本身无设备副作用，因此在明确的网络/5xx 条件下最多重试 1 次；
  dispatch/outbox 不随推理重试重复执行。

### 2.3 数据与工具边界

发送前建立严格 allowlist：用户文本、脱敏 observation 摘要、有限对话摘要、用户已允许的
偏好。默认拒绝 raw media、硬件唯一标识、MAC、串口、路径、token、nonce、环境变量、
完整日志和工具原始输出。

阶段 2 首版不向 Responses API 声明可直接执行的远程 tools。需要工具时，模型仅输出
`ToolIntent`；本地 LangGraph 节点执行 schema/policy/approval，再由本地 executor 执行。

### 2.4 合规决定

sub2api 官方 README 明确提示，上游订阅转 API 的使用方式可能违反上游服务条款。
真实 smoke/生产启用前必须记录：部署所有者、上游账户授权、可接受用途、数据区域、
密钥轮换和停用方式。未完成确认时只能运行 mock contract tests。

## 3. 领域契约

新增/冻结以下主机领域对象：

| 对象 | 核心字段 | 不变量 |
| --- | --- | --- |
| `ProviderConfig` | base URL、model、timeouts、TLS flags | 不包含可序列化 API key |
| `ProviderRequest` | run/thread/event ID、bounded input、schema version | 不含 wire envelope/raw media |
| `ProviderResult` | response ID、IntentPlan、usage、latency | 未校验结果不能 dispatch |
| `ProviderError` | category、retryable、status、request ID | 不含响应正文中的秘密 |
| `IntentPlan` | speech/emotion/registered behaviors/TTL/approval | 无 PWM/GPIO/角度/路径/URL/shell |

错误类别固定为：`auth_error`、`rate_limited`、`timeout`、`network_error`、
`upstream_unavailable`、`model_unavailable`、`invalid_response`、`policy_blocked`。

## 4. 里程碑与任务

### P2.0：契约与部署门禁

| ID | 任务 | 交付/验收 |
| --- | --- | --- |
| P2-001 | 新增 sub2api provider ADR 与本计划 | 文档不再把本机 Codex 当阶段 2 runtime |
| P2-002 | 冻结 config/error/request/result 类型 | 类型有 Pydantic 单元测试；API key 不可 dump |
| P2-003 | 定义 data redaction allowlist | raw media、路径、nonce、MAC、env 负例全部拒绝 |
| P2-004 | 合规与 TLS 启动检查 | 非 loopback HTTP、空 key、空 model、TLS verify=false 默认拒绝 |

退出：设计/安全/测试文档一致；真实 provider gate 默认关闭。

### P2.1：Sub2API Adapter

| ID | 任务 | 交付/验收 |
| --- | --- | --- |
| P2-101 | 将 `CodexProvider` 抽象重命名为中性 `InferenceProvider` | graph 不导入 Codex 专有类型 |
| P2-102 | 实现 `Sub2APITransport` | 仅 `/v1/models`、`/v1/responses`；Bearer key 不进日志 |
| P2-103 | 实现 `Sub2APIProvider.decide` | 输出最多 4 个 `BehaviorIntent`，未知字段/NaN 拒绝 |
| P2-104 | timeout/retry/error mapping | 401/403 不重试；429/5xx/网络错误最多 1 次；总时限有界 |
| P2-105 | mock contract server/fixtures | success、stream、中断、429、5xx、malformed JSON/schema 可重放 |
| P2-106 | 移除 Stage 2 对本机 app-server 生命周期的依赖 | 测试证明不调用 subprocess/thread/start/turn/start |

退出：无凭据离线测试全通过；没有连接真实 sub2api 也能验证契约。

### P2.2：LangGraph 可恢复主链

| ID | 任务 | 交付/验收 |
| --- | --- | --- |
| P2-201 | 扩展 graph 状态与节点 | `receive → context → recall → sub2api → validate → dispatch` 可回放 |
| P2-202 | 持久化 checkpointer/store | 主机重启恢复同一 thread；旧 IntentPlan 不自动重放 |
| P2-203 | graph/provider 版本治理 | checkpoint 记录 graph/provider contract 版本并支持拒绝不兼容恢复 |
| P2-204 | provider 不可用降级 | 进入 `DEGRADED_LOCAL`，设备保持本地反射/安全，不升级权限 |

退出：确定性 fixture 下 graph 回放一致；checkpoint 恢复不重复副作用。

### P2.3：IntentPlan、审批与本地工具

| ID | 任务 | 交付/验收 |
| --- | --- | --- |
| P2-301 | IntentPlan validator/registry | 未注册行为、超时、超强度、超数量、raw hardware 字段拒绝 |
| P2-302 | 本地 Policy Validator | provider 输出不能自报 priority/authorization/maintainer |
| P2-303 | LangGraph interrupt/resume | approve/reject/timeout/restart 均可恢复且有 audit ID |
| P2-304 | 本地 ToolIntent executor | 工具 allowlist、参数 schema、工作目录/网络范围和 outbox 幂等 |
| P2-305 | Device Gateway dispatch | 只投影高层 `lifeos.v1` command；SafetyGate 可再次拒绝 |

退出：远端 provider 无法绕过本地审批或设备安全；拒绝路径不会产生工具/运动副作用。

### P2.4：可观测性、真实 smoke 与发布

| ID | 任务 | 交付/验收 |
| --- | --- | --- |
| P2-401 | tracing/metrics | 关联 `run_id/thread_id/event_id/provider_request_id/device_id` |
| P2-402 | usage/latency 统计 | 记录 p50/p95/p99、429/5xx/timeout、usage；不记录 prompt/token |
| P2-403 | opt-in real sub2api smoke | `/v1/models`、structured `/v1/responses`、流式（若启用）均有脱敏报告 |
| P2-404 | 故障注入/soak | key rotation、TLS failure、429、5xx、流中断、provider restart、host restart |
| P2-405 | 发布/回滚 | provider gate 可立即关闭并回到 deterministic/local-reflex，不依赖本机 Codex |

退出：达到 PRD 的 provider/Agent SLO，或明确记录 BLOCKED；发布报告区分 mock 与真实调用。

## 5. 文件级实施顺序

1. `docs/adr/0003-sub2api-provider.md`、`docs/security.md`、`docs/testing.md`：先冻结边界。
2. `brain/provider.py`：使用中性 provider protocol + `Sub2APIProvider`；deterministic fake
   保留在同一边界，Phase 0 的 `AppServerTransport/CodexAppServerProvider` 仅隔离在
   `brain/legacy_codex.py`，不由阶段 2 graph/import 路径加载。
3. `brain/models.py`：增加 provider request/result/error、IntentPlan 和版本字段。
4. `brain/flow.py`：把 `codex_reason` 替换为 `sub2api_reason`，加入 validate、degrade、
   approval 和 checkpoint 节点。
5. `brain/tests/`：mock HTTP/stream、错误映射、redaction、恢复、interrupt/outbox 测试。
6. `contracts/sub2api/`：保存本项目请求/响应 fixture 和内部 schema；不复制 sub2api Admin API。
7. `tools/sub2api_contract.py`：离线 contract 默认；`--live` 明确 opt-in，输出脱敏报告。
8. `Makefile`：新增 `sub2api-contract`；原 `codex-contract` 降为 Phase 0 历史兼容证据，
   不再作为阶段 2 发布门禁。

## 6. 验收矩阵

| 场景 | 期望结果 |
| --- | --- |
| 缺少 key/model/base URL | 启动拒绝真实 provider；deterministic 测试仍可运行 |
| 非 loopback HTTP base URL | 拒绝；不发送请求 |
| 401/403 | `auth_error`，不重试，UI 提示配置错误 |
| 429 | bounded backoff，最多 1 次；设备继续本地反射 |
| 5xx/网络断开 | `upstream_unavailable/network_error`，不 dispatch |
| 首包/总超时 | 取消 provider run，记录 latency/error，不执行旧计划 |
| malformed/unknown JSON | `invalid_response`；IntentPlan 为空，不产生设备命令 |
| stream 中断 | 丢弃未完成结构化结果，不使用 partial JSON |
| prompt injection 要求 raw motion/shell | 本地 schema/policy 拒绝并审计 |
| host restart after provider response | checkpoint/outbox 不重复工具或设备副作用 |
| API key rotation | 新请求使用新 key；日志/checkpoint 中无旧 key |
| sub2api 不可用 | `DEGRADED_LOCAL`；不自动回退到本机 Codex |

## 7. 风险与缓解

| 风险 | 缓解 |
| --- | --- |
| 上游条款/账户风险 | 真实启用前书面确认；可一键关闭 provider；不纳入商业默认 |
| 本地优先隐私被削弱 | 显式 opt-in、字段 allowlist、raw media 禁止、脱敏审计 |
| 网关/上游模型漂移 | 固定 model/group、contract fixtures、schema 校验、canary smoke |
| API key 泄露 | 环境/OS secret store、Bearer header 脱敏、checkpoint/log 禁止 |
| 远端工具越权 | provider 不直接执行工具；本地 ToolIntent + policy + approval |
| provider 延迟/限流 | bounded timeout/retry、可观测性、`DEGRADED_LOCAL` |
| upstream state/sticky routing 不稳定 | thread/checkpoint 本地持有；请求不依赖 upstream thread |

## 8. 完成定义

阶段 2 只有在以下项目全部有证据时才可标为 PASS：

- 本机 LangGraph checkpoint/store、interrupt/outbox 恢复测试通过；
- sub2api mock contract 与 opt-in real smoke 均通过，且凭据/数据脱敏有验证；
- IntentPlan/ToolIntent 不能产生 raw hardware、未审批工具或过期副作用；
- provider 失败时设备保持本地安全反射，旧计划不重放；
- p95/p99、错误率和版本矩阵有发布报告；
- 不启动或依赖本机 Codex app-server；
- 阶段 1 的真实运动门禁仍独立满足，不能用阶段 2 软件测试替代。

## 9. 参考

- [sub2api 官方 README](https://github.com/Wei-Shaw/sub2api/blob/main/README.md)
- [sub2api gateway routes：Responses/Models](https://github.com/Wei-Shaw/sub2api/blob/main/backend/internal/server/routes/gateway.go)
- [LangGraph persistence](https://docs.langchain.com/oss/python/langgraph/persistence)
- [LangGraph interrupts](https://docs.langchain.com/oss/python/langgraph/interrupts)
