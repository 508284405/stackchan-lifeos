# ADR 0003：阶段 2 推理 Provider 采用 sub2api

- 状态：Accepted for Phase 2 planning
- 日期：2026-08-30
- 决策者：StackChan LifeOS maintainers

## 决策

阶段 2 不再以本机 `codex app-server` 作为 Agent runtime。LangGraph 仍在本机负责状态、
审批、工具和副作用；LLM 推理通过可替换 `Sub2APIProvider` 调用 sub2api 的 OpenAI
Responses 兼容 API。设备协议、SafetyGate 和图外 safety loop 不受此 provider 选择影响。

## 驱动因素

- 通过标准 HTTP/Responses 边界替代 Codex CLI 版本绑定的 thread/turn schema；
- provider 可独立部署、轮换 model/group，并统一 timeout/rate-limit/usage 观测；
- 保留本地 LangGraph、审计、审批和安全投影，不把 sub2api 变成设备控制面。

## 被拒绝方案

- 继续使用本机 Codex app-server：与新的部署方向不一致，仍需管理进程和版本专有 schema。
- 让 sub2api/upstream 持有 graph/thread/工具：会削弱本地恢复、审批与数据安全边界。
- 浏览器或设备直连 sub2api：绕过主机 policy、审计和 Device Gateway。
- 同时在生产自动回退本机 Codex：形成两套行为不一致的 provider，难以验证与审计。

## 后果

正面：provider 接口更标准、部署解耦、错误/usage 可统一观测。负面：推理数据离开本机，
引入网络、TLS、API key、上游条款、账户调度和 provider 可用性风险；必须执行数据最小化、
显式 opt-in 和合规检查。

## 门禁

- 非 loopback 必须 HTTPS + TLS verify；key 只来自 secret 注入；
- provider 只接收脱敏 bounded context，不接收 raw media/路径/nonce/wire envelope；
- 工具和副作用只在本地 policy/approval 后执行；
- 真实 smoke 需要显式 opt-in，默认测试使用 mock/deterministic provider；
- 详细任务与完成定义见[阶段 2 任务计划](../phase2-sub2api-plan.md)。

## 参考

- [sub2api 官方 README](https://github.com/Wei-Shaw/sub2api/blob/main/README.md)
- [sub2api Responses 路由](https://github.com/Wei-Shaw/sub2api/blob/main/backend/internal/server/routes/gateway.go)
