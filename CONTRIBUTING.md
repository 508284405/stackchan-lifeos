# 贡献指南 / Contributing Guide

感谢你对 StackChan LifeOS 的贡献。这个项目是面向 M5Stack StackChan K151 / ESP32-S3
的本地优先桌面机器人系统，包含固件、主机端认知层、Web Bridge、Web Console、协议契约、
模拟器和验收工具。项目仍在演进，开源贡献必须以可复现证据和设备安全为先。

请先阅读：

- [项目说明](README.md)
- [架构边界](docs/architecture.md)
- [设备协议](docs/protocol.md) 与 [契约说明](contracts/README.md)
- [安全与隐私设计](docs/security.md)
- [测试策略](docs/testing.md)
- [当前阶段验收](docs/phase1-acceptance.md)

## 先记住的边界 / Non-negotiable boundaries

- `contracts/phase1/envelope.schema.json` 是 `lifeos.v1` 设备 USB JSONL wire envelope 的唯一
  事实源。`contracts/web-bridge/` 只面向浏览器；根目录的
  `device-event`、`device-command`、`cognitive-decision` schema 仍是 Phase 2 draft，不能
  当作 Phase 1 固件协议。
- LLM、sub2api、LangGraph 和浏览器只能产生经过 schema/policy/TTL 校验的高层语义结果，
  不能发送 PWM、GPIO、I²C、原始舵机角、shell、URL 或任意设备 envelope。固件和执行器仍
  必须再次限幅；pitch 硬限制为 5°–85%，安全故障优先于用户、Agent、反射和动画。
- 维护与 HIL-only 命令必须保持 `LIFEOS_HIL_TEST_MODE` 门禁，不能为了测试方便在 production
  中放宽。`accepted` 只表示接收，不表示动作 `completed`。
- 当前验收必须分开写 `PASS`、`PARTIAL`、`BLOCKED`、`NOT TESTED`：host/fake 软件、真实
  provider、真实 media、hardware/HIL 各自独立。主机测试、fake device、schema 通过或目标
  build 不能证明真实运动、真实 provider、物理 OTA/回滚、网络或 200-device scale。
- 截至最新验收记录，真实连续手控/媒体状态仍有阻塞，真实签名升级与断电回滚为
  `NOT TESTED`；提交信息不得把历史报告或构建结果升级成当前实机验收。

## 开发环境 / Development setup

推荐使用 Python 3.12 虚拟环境，并从仓库根目录执行命令：

```sh
python3.12 -m venv .venv
.venv/bin/pip install -e 'brain[dev]'
```

固件目标构建需要将 `IDF_PATH` 指向固定的 ESP-IDF 5.5.4 checkout：

```sh
IDF_PATH=/path/to/esp-idf-5.5.4 tools/build_target.sh production build
IDF_PATH=/path/to/esp-idf-5.5.4 tools/build_target.sh hil build
```

`ota` profile 只用于显式的目标构建和验证准备：

```sh
IDF_PATH=/path/to/esp-idf-5.5.4 tools/build_target.sh ota build
```

这些命令不会自动证明设备可刷写、可回滚或可恢复；任何 `flash`、分区迁移、bootloader/eFuse
或实体断电操作都必须走单独的授权和硬件验收流程。不要把 signing private key 放入仓库。

## 本地验证 / Tests and checks

常用入口如下。根据改动范围选择相关检查，并在 PR 中粘贴实际输出摘要：

```sh
make test                 # brain、Bridge、Web、firmware host、Phase 1、schema、离线 contracts
make brain-test           # PYTHONPATH=. python3 -m pytest brain/tests -q
make bridge-test          # pytest tests/bridge
make web-check            # Vite build；无 Node 或 npm 失败时可能安全跳过
make firmware-test        # firmware host CMake/CTest
make phase1-acceptance    # Phase 1 unittest + simulator nominal replay
make phase2-3-acceptance  # brain regression + offline Sub2API contract
make phase4-acceptance    # Bridge/Web/schema + host/fake capacity method
make schemas              # JSON Schema validation
make sub2api-contract     # offline provider contract
```

当前 GitHub Actions `CI` job 运行 schema validation、Brain tests 和 firmware host tests；它
不会替代 Bridge/Web 全量回归、真实 provider、真实 media、HIL、物理 OTA 或 scale 验收。
`make web-check` 使用已提交的 `web/dist/` 作为无 Node 时的 fallback；请把 `SKIP` 与真正
的 build/test 结果区分记录。

默认测试必须使用 deterministic fake/mock provider，不调用真实 provider，不消耗账户配额。
`tools/sub2api_contract.py --live BASE_URL` 是显式 opt-in 的 live smoke，不属于普通 CI；
除非维护者明确授权，不要运行它，也不要在 issue/PR/日志中暴露 `SUB2API_API_KEY`。

## 开发与提交流程 / Change workflow

1. 先搜索现有 issue、RFC、ADR 和验收报告。新能力若会改变协议、权限、安全默认值、设备
   状态或 UI 语义，应先提交设计讨论或更新对应 RFC；不要在代码和文档中创建第二套事实源。
2. 保持改动聚焦。协议、schema、parser、simulator、tests、docs 需要同步考虑；Web Console
   设计与 `DESIGN.md` 冲突时，应调整界面，不要削弱 `lifeos.v1` 或 `SafetyGate`。
3. 对每个行为说明它是 `implemented`、`host-only`、`fake`、`Draft` 还是已通过独立硬件
   验收。不要因为 HTTP 200、ACK、`accepted`、构建成功或单元测试通过就声称真实设备能力。
4. 提交 PR 前运行相关测试，检查 diff 中没有凭据、原始图片/音频、串口 dump、绝对本机路径、
   设备唯一密钥、生成缓存或与本次任务无关的用户改动。

## 真实设备、HIL 与媒体 / Hardware safety

仓库中的硬件工具有明确的 fail-closed 门禁。贡献者不能把真实设备当作普通 CI 资源，也不能
在没有设备所有者明确授权、备份与身份复核、现场监督和停止条件的情况下发送非零运动、刷写
或断电测试。

- `PYTHONPATH=. python3 tools/scan_usb_devices.py` 默认只列出候选串口。加 `--probe` 会打开
  USB CDC 并触发 ESP32-S3 Serial/JTAG 外设复位；先关闭占用端口的 Bridge/HIL 会话。不要
  对 Bridge 当前拥有的端口执行 probe；`no-response` 在端口被其他进程占用时是 inconclusive。
- `tools/phase1_hil.py` 的真实运行同时需要 `--allow-hardware` 和经过核验的 `hello.device`；
  `--require-touch`、`--require-mechanical-stall` 会引入受监督的实体检查。不要用手堵舵机
  替代真实机械卡滞验收，也不要绕过 `LIFEOS_HIL_TEST_MODE`。
- `tools/web_bridge_real_server.py --usb-port /dev/cu.usbmodemXXXX --hardware-id DEVICE_MAC_FROM_SCAN`
  是真实 USB Bridge 入口。
  连续手控还需要当前设备/firmware 匹配的 supervised `manual_control` evidence，以及
  session/lease/dead-man/TTL、视频新鲜度和设备 SafetyGate；fake/API 测试不构成实体运动证据。
- 视频控制必须满足实际采集到显示不超过 500 ms；旧帧、重复帧、未知年龄或视频恢复不能
  自动恢复手控。最后一个观看者离开后采集在有界宽限期后停止，重新观看必须显式 start。
- 固件升级必须先验证镜像兼容性、签名、摘要、分区与回滚条件。旧单应用分区不能承诺
  power-loss rollback；`ota` target build、主机 sanitizer 或 rollout task 通过都不是物理 OTA。
- 真实设备日志、raw JPEG/audio、API key、nonce、配对凭据和备份中的秘密不能提交。公开
  硬件报告时请脱敏 serial path、MAC、唯一硬件密钥和用户数据。

## PR 要求 / Pull requests

PR 描述应至少包含：

- 目标、范围和关联 issue/RFC；
- 影响的 layer（firmware / brain / bridge / web / contracts / simulator / docs）；
- 运行过的命令和结果，未运行的检查及原因；
- 四类独立证据状态：`host/fake software`、`real provider`、`real media`、`hardware/HIL`；
  如涉及 OTA、网络、fleet 或 capacity，另外列出 `OTA/recovery`、`network/identity`、
  `scale/soak`；
- 失败、降级、回滚、断线和安全门禁行为，以及是否需要更新验收文档。

提交前请使用 PR template 的 checklist。维护者可能要求补充 `git diff --check`、schema
fixture、协议回放、Web/Bridge 回归或受监督 HIL 证据；未完成的硬件/OTA能力应继续标为
`BLOCKED` 或 `NOT TESTED`，而不是由 PR 的 host-only 结果代替。

## 安全问题 / Security issues

请勿在公开 issue 或 PR 中发布可利用细节、凭据、token、API key、原始媒体、设备唯一密钥或
完整敏感日志。安全漏洞请按 [SECURITY.md](SECURITY.md) 的 private disclosure 说明报告；
行为规范问题请按 [CODE_OF_CONDUCT.md](CODE_OF_CONDUCT.md) 报告。
