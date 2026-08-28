# StackChan LifeOS

让 M5Stack StackChan K151 在没有持续 LLM 调用时也保持“活着”的本地优先桌面机器人系统。

项目采用双脑架构：

- `firmware/`：ESP32-S3 身体与反射层。以 CGraph 的 DAG / pipeline 思想编排低频业务节点，实时控制、安全看门狗和 ISR 保持独立。
- `brain/`：主机端 Python 认知层。LangGraph 管理事件、状态、语义门、上下文、Codex 推理、审批、记忆和行为仲裁。
- `contracts/`：设备与主机之间唯一可信的版本化 JSON Schema。
- `simulator/`：无硬件事件回放和端到端验证入口。

## 当前完成度

这是第一份可执行架构基线，不宣称已经在实机上烧录验证：

- 设计文档、ADR、威胁模型、阶段路线和测试计划已落库。
- 主机端有可测试的生命状态、语义门、行为仲裁和 LangGraph 图。
- Codex 通过 provider/bridge 隔离；默认测试使用确定性 fake，不消耗账户配额。
- 固件侧有可在桌面 CMake 编译的 C++17 DAG、安全与仲裁骨架。
- 真机 BSP、CGraph 上游依赖和 Codex app-server 均设置显式验证门，未被伪装成已完成。

## 快速开始

```bash
make test
make firmware-test
make brain-demo
```

Python 依赖安装：

```bash
python3.12 -m venv .venv
.venv/bin/pip install -e 'brain[dev]'
```

运行脑端演示：

```bash
.venv/bin/python -m brain.cli simulate --text "今天下午有什么安排？"
```

## 安全边界

Codex 和 LangGraph 永远不发送 PWM、GPIO、I²C 或原始舵机角。模型只能产生带 TTL 的高层 `CognitiveDecision`；Schema 校验、主机 Policy Validator、Behavior Arbiter 与设备 Safety Controller 都可拒绝它。

Pitch 硬限制为 5°–85°。任何安全故障优先于反射、用户、Agent、主动行为与空闲动画。

## 文档入口

- [产品需求](docs/product-requirements.md)
- [系统架构](docs/architecture.md)
- [设备协议](docs/protocol.md)
- [安全设计](docs/security.md)
- [测试策略](docs/testing.md)
- [实施路线](docs/roadmap.md)
- [LangGraph ADR](docs/adr/0001-langgraph.md)
- [CGraph ADR](docs/adr/0002-cgraph.md)

## 与现有人员追踪项目的关系

`/Users/wangyu/product/stackchan-person-tracker` 保留为 Phase 1 视觉追踪基线。本仓库不覆盖它；待实机验证后，以组件或固定提交迁入 `firmware/components/perception`。
