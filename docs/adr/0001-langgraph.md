# ADR 0001：主机 Agent 采用 LangGraph

- 状态：Accepted for V1
- 日期：2026-08-29
- 决策者：StackChan LifeOS maintainers

## 背景

LifeOS 需要把连续 observation、对话上下文、长期记忆、工具调用、人工审批和故障恢复编排在一起。单次 prompt 或自写 while-loop 难以表达可恢复状态，也难以审计主动行为。

## 决策

主机 Agent 采用 Python LangGraph StateGraph/Graph API。图状态、节点、边和策略属于 LifeOS 代码；LLM provider 通过 Adapter 注入，默认 Adapter 连接本机 Codex CLI app-server。设备不依赖 LangGraph，也不接受图节点名称作为命令。

### 运行约定

- 使用持久化 checkpointer 保存 thread-scoped 短期状态；long-term store 只保存经过策略允许的记忆。
- 使用稳定、不可猜测的 `thread_id` 恢复会话；同一 thread 只能有一个活动 run。
- 需要人类确认的工具调用使用 LangGraph `interrupt`，恢复时复用原 `thread_id`，并使节点副作用幂等。
- 使用事件流向 UI/Gateway 提供状态；设备只收到经过 `IntentPlan` 校验的最终投影。
- 任何 graph 输出都先过类型/schema validator、策略检查和 TTL 检查，再进入 Gateway。

## 原因

LangGraph 官方文档明确提供持久化、thread/checkpoint、跨会话 store、中断恢复和流式事件等能力，正好覆盖 LifeOS 的持续状态和人工确认需求。它也允许使用非 LangChain 原生的 LLM API 作为流式来源，因此可把 Codex app-server 隔离在 Adapter 后面。

## 被拒绝的选项

- 纯 prompt + while-loop：难以可靠恢复 checkpoint、审批和副作用，不利于回放测试。
- 让设备直接调用 LLM：资源、网络和安全边界不符合 ESP32 身体层职责。
- 把 Codex 原生 thread/turn 直接作为设备协议：Codex app-server 是实验性且版本绑定，原生 schema 变化会把主机升级风险传到固件。
- 只使用长期数据库而不使用 graph checkpoint：无法恢复中断中的节点执行状态。

## 后果

正面：节点边界清晰，可用 replay 验证决策；审批可暂停；主机重启后可恢复。负面：需要持久化后端、幂等副作用和 graph 版本治理；LangGraph 升级可能改变运行语义，必须运行兼容与回放测试。

## 兼容/验收门禁

1. InMemorySaver 仅用于单元测试，生产使用持久化实现。
2. Adapter 必须屏蔽 Codex 的原生事件和 schema 变化。
3. 图状态不能存原始音视频、密钥、shell 命令或不受限文件路径。
4. 任何恢复测试都要覆盖“中断前副作用已执行”和“重复恢复”两种情况。

## 参考

- [LangGraph 持久化](https://docs.langchain.com/oss/python/langgraph/persistence)
- [LangGraph 中断](https://docs.langchain.com/oss/python/langgraph/interrupts)
- [LangGraph 流式输出](https://docs.langchain.com/oss/python/langgraph/streaming)
- [OpenAI Codex app-server README](https://github.com/openai/codex/blob/main/codex-rs/app-server/README.md)
