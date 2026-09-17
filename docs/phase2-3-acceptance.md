# 阶段 2/3 主机验收报告

- 日期：2026-08-30
- 范围：`docs/phase2-sub2api-plan.md` 与 `docs/phase3-perception-life-plan.md` 的 host-only 实现。
- 总结：阶段 2/3 的确定性主机软件出口 **PASS**；真实 Sub2API smoke、真实媒体和物理设备出口 **NOT TESTED/BLOCKED**，不把它们混入软件 PASS。按完整 release 门禁，整体仍是 **PARTIAL**。

## 阶段 2 进度

| 里程碑 | 结果 | 证据 |
| --- | --- | --- |
| P2.0 契约、配置、脱敏与启动门禁 | PASS | `brain/models.py`、`brain/config.py`、`brain/redaction.py`、provider 回归 |
| P2.1 Sub2API adapter、错误映射、mock fixture | PASS | `brain/provider.py`、`contracts/sub2api/fixtures/`、`tools/sub2api_contract.py` |
| P2.2 graph、checkpoint/store、降级 | PASS | `brain/flow.py`、`brain/checkpoint.py`、阶段 2 graph 回归 |
| P2.3 validator、审批恢复、ToolIntent/outbox/semantic dispatch | PASS（host-only，未注册工具 fail-closed） | `brain/validator.py`、`brain/interrupt.py`、`brain/dispatch.py` |
| P2.4 本地指标与发布边界 | PASS（host-only metrics）；真实 fault injection/release gate 未测试 | `brain/metrics.py`、本报告 |
| `/v1/models` / `/v1/responses` 真实 smoke、真实 TLS/网关兼容性 | NOT TESTED | 未注入凭据；默认不发起真实请求 |

Brain API 已补齐生产生命周期代码：默认生成 host thread，支持
`LIFEOS_THREAD_ID` / `LIFEOS_CHECKPOINT_PATH`，启动时调用 provider `/v1/models`
健康检查，并将真实 graph thread 绑定到 `ProviderRequest`。这些路径已有确定性 mock 测试；
真实 provider 连接仍保持 NOT TESTED。

阶段 2 的关键安全结论：provider 失败进入 `DEGRADED_LOCAL`，不启动或回退本机 Codex；未知行为、越权工具、原始硬件字段、过期/重复副作用均在主机边界拒绝；checkpoint 只保存受限摘要，不保存 API key、原始事件 payload 或上游完整响应体；设备投影使用 `contracts/phase1/envelope.schema.json` 的 canonical `lifeos.v1` 字段。

## 阶段 3 进度

详见 [`phase3-perception-life-plan.md`](phase3-perception-life-plan.md)。目标状态机、主动策略、情绪语义、语音请求边界、记忆治理和断链降级均有确定性测试；真实感知、音频和机械追踪仍未测试。

## 可重复验收命令

```bash
PYTHONDONTWRITEBYTECODE=1 PYTEST_ADDOPTS='-p no:cacheprovider' \
  PYTHONPATH=. python3 -m pytest brain/tests -q
python3 tools/sub2api_contract.py
make bridge-test
make phase1-acceptance
make schemas
make web-check
make firmware-test
```

最近一次整仓运行结果：brain **67 passed**；Bridge **132 passed**；固件宿主测试
（lifeos/protocol/manual_control_v1/servo_io/firmware update）全部通过；Phase 1 replay **10 passed**
且 nominal replay `accepted=2,rejected=0`；全部 schema、Web 语法、Sub2API offline
contract 和 Codex offline contract 通过。运行环境为系统 Python 3.9；仅有既存的
LibreSSL/LangGraph warning，没有测试失败。

报告中的软件 PASS 只表示上述 host/fake/回放/宿主固件证据通过。真实 Sub2API `--live`、上游条款/TLS/网关兼容性、真实媒体和设备 HIL 没有运行；阶段 1 总体状态仍按 [`phase1-acceptance.md`](phase1-acceptance.md) 为 `BLOCKED/PARTIAL`，不能被阶段 2/3 的主机测试替代。
