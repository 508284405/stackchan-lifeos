# StackChan LifeOS

让 M5Stack StackChan K151 在没有持续 LLM 调用时也保持“活着”的本地优先桌面机器人系统。

项目采用双脑架构：

- `firmware/`：ESP32-S3 身体与反射层。以 CGraph 的 DAG / pipeline 思想编排低频业务节点，实时控制、安全看门狗和 ISR 保持独立。
- `brain/`：主机端 Python 认知层。LangGraph 管理事件、状态、语义门、上下文、sub2api 推理、审批、记忆和行为仲裁。
- `contracts/`：设备与主机之间唯一可信的版本化 JSON Schema。
- `simulator/`：无硬件事件回放和端到端验证入口。

## 当前完成度

阶段 0 的证据、契约和安全边界已通过；阶段 1 的软件实现、目标镜像、板级初始化
和 SCS 执行器闭环子门禁已通过。阶段 1 整体出口仍为 BLOCKED/PARTIAL：端到端
急停观测约 209.6 ms（设备侧 VM_EN cut 为 1 ms），触摸、8 小时 soak、真实机械
卡滞和部分独立时序/传感器证据尚未完成，详见 [Phase 1 验收入口](docs/phase1-acceptance.md)
和 [执行器闭环报告](docs/scs-runtime-io-acceptance.md)：

- 设计文档、ADR、威胁模型、阶段路线和测试计划已落库。
- 主机端有可测试的生命状态、语义门、行为仲裁和 LangGraph 图。
- 阶段 2 的 host-only sub2api adapter、脱敏、错误降级、LangGraph checkpoint/审批/幂等
  dispatch 和离线 contract 已通过；默认测试继续使用确定性 fake，不调用真实 provider、
  不消耗账户配额。真实 endpoint smoke 尚未测试。
- 阶段 3 的 host-only 目标选择、主动行为上限、情绪/语音语义、偏好和审计记忆已通过；
  真实相机、麦克风、音频 transport 和机械追踪尚未测试。
- 固件侧有可在桌面编译的 C++17 静态 DAG、图外 FastSafetyLoop、安全仲裁、固定容量协议网关、HAL fake 和 Phase 1 controller。
- `firmware/idf/` 提供 ESP-IDF 5.5.4 的目标工程、真实 StackChan/CoreS3 HAL、生产/HIL 双配置和可复现构建入口 `tools/build_target.sh`。
- `simulator/phase1/` 与 `tests/phase1/` 提供 JSONL 回放、会话/seq/TTL/断线/越界/故障验收。
- 生产镜像和维护 HIL 镜像均已在 ESP-IDF v5.5.4 / ESP32-S3 上编译通过；HIL-only 维护命令由 `LIFEOS_HIL_TEST_MODE` 严格隔离。
- 设备身份和 16 MiB 全片备份已核验；最新 HIL 镜像已刷入并完成真实运动/故障子门禁；
  触摸、长稳和整体阶段出口仍按上述 PASS/BLOCKED/NOT TESTED 划分。
- Web Bridge W1 host-only/fake-device 核心已实现；真实一台设备的 status 控制和 camera preview
  已单独通过，仍不代表真实远程运动或 200 台容量已支持。
- W2 已提供 React（Vite 构建，中文默认、可切换英文）的 Overview、Devices、Tasks、
  Audit & diagnostics、System 界面、REST snapshot 和 cursor WebSocket；支持设备多选、安全
  离散批量命令、逐设备结果、诊断导出和 feature gate 可见性。
- W3/W4 已提供默认关闭的 host-only semantic intent、lease/dead-man、独立急停和
  BatchTask API；maintenance/签名 OTA 的 Bridge/firmware 路径已实现但默认门禁关闭，A/B
  布局迁移、信任配置和真实恢复仍是独立门禁；firmware semantic intent execution 尚未启用。
- Web 控制与摄像头预览已接入 Bridge/UI：离散控制和远程急停走设备 ACK 生命周期；摄像头
  使用显式 start/stop、固定 QVGA JPEG、最新帧 MJPEG 出口，真实 `stackchan-01` 预览已通过。
  连续手动控制仍需 `manual_control_v1` 固件/HIL 门禁。
- CGraph 上游依赖设置显式验证门；sub2api/OpenAI API 不直接暴露为设备协议。

## 快速开始

```bash
make test
make firmware-test
make phase1-acceptance
make phase2-3-acceptance
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

Brain 服务生产部署应固定 host thread 并启用持久 checkpoint；未配置路径时明确使用开发态
内存实现。真实 Sub2API 模式启动时会执行一次受限的模型健康检查：

```bash
export LIFEOS_THREAD_ID=stackchan-host-01
export LIFEOS_CHECKPOINT_PATH=/var/lib/stackchan-lifeos/brain-checkpoints.json
```

连接真实设备（USB 串口）：

```bash
# 列出候选串口（不开端口）；--probe 追加只读 hello 握手识别 device_id/MAC/固件
PYTHONPATH=. .venv/bin/python tools/scan_usb_devices.py [--probe] [--json]
# 用识别到的身份启动真实 Web Bridge + 控制台（默认 http://127.0.0.1:8766）。
# 此生产 USB 入口固定启用真实摄像头；manual_control_v1 还要求显式提供与当前
# device/hardware/firmware 匹配的 HIL evidence，否则保持关闭。
PYTHONPATH=. .venv/bin/python tools/web_bridge_real_server.py --usb-port /dev/cu.usbmodemXXXX
```

控制台设备详情提供 `status / pause / resume / home / 远程急停`。预览必须手动开启，
使用真实连续 MJPEG（QVGA、最高 10 fps），只保留内存中的最新帧，不录制、不写 SQLite；
该生产 USB 入口不会降级为 fake 摄像头或关闭 `manual_control_v1`。真实设备的远程运动
仍不能由 fake/API 测试替代：故障、断链、失焦、lease、TTL 与设备端 SafetyGate 继续生效。

也可以在 Web 控制台"设备"区点击"扫描 USB 设备"直接扫描并添加（两个随附启动脚本已
开启 `usb_add` 能力门控；自建 `create_app` 需显式传入 `feature_gates={"usb_add": True}`）。

注意：`--probe` 或控制台扫描会打开 USB CDC 并触发 ESP32-S3 复位，执行前先关闭占用该端口的其他会话（Bridge 对自己占用的端口自动跳过探测）。

## 安全边界

sub2api 上游模型和 LangGraph 永远不发送 PWM、GPIO、I²C 或原始舵机角。模型只能产生带 TTL 的高层 `CognitiveDecision`；Schema 校验、主机 Policy Validator、Behavior Arbiter 与设备 Safety Controller 都可拒绝它。

Pitch 硬限制为 5°–85°。任何安全故障优先于反射、用户、Agent、主动行为与空闲动画。

## 文档入口

- [Web Console 体验设计](DESIGN.md)
- [Web Bridge 多设备与远程控制 RFC](docs/rfc/0001-web-bridge-fleet-control.md)
- [Web Bridge 实施计划](docs/web-bridge-implementation-plan.md)
- [Web 控制与摄像头预览 RFC](docs/rfc/0005-web-control-camera-preview.md)
- [Web 控制与摄像头预览验收报告](docs/web-control-camera-report.md)
- [Web Bridge W1 Core 验收报告](docs/web-bridge-w1-report.md)
- [Web Bridge W2 监控验收报告](docs/web-bridge-w2-report.md)
- [manual_control_v1 RFC（设计中）](docs/rfc/0002-manual-control-v1.md)
- [Web Bridge W3 host-only lease 报告](docs/web-bridge-w3-report.md)
- [Web Bridge W4 host-only batch 报告](docs/web-bridge-w4-report.md)
- [产品需求](docs/product-requirements.md)
- [系统架构](docs/architecture.md)
- [设备协议](docs/protocol.md)
- [安全设计](docs/security.md)
- [测试策略](docs/testing.md)
- [实施路线](docs/roadmap.md)
- [阶段 2：LangGraph + sub2api 任务计划](docs/phase2-sub2api-plan.md)
- [阶段 2/3 主机验收报告](docs/phase2-3-acceptance.md)
- [阶段 3：感知与生命感任务计划](docs/phase3-perception-life-plan.md)
- [LangGraph ADR](docs/adr/0001-langgraph.md)
- [sub2api Provider ADR](docs/adr/0003-sub2api-provider.md)
- [CGraph ADR](docs/adr/0002-cgraph.md)

## 与现有人员追踪项目的关系

`/Users/wangyu/product/stackchan-person-tracker` 保留为 Phase 1 视觉追踪基线。本仓库不覆盖它；待实机验证后，以组件或固定提交迁入 `firmware/components/perception`。
