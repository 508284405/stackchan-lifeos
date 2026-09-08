# 阶段 4（Web Bridge 可选扩展）验收报告

日期：2026-09-02  
范围：`DESIGN.md`、`docs/rfc/0001-web-bridge-fleet-control.md` 和
`docs/web-bridge-implementation-plan.md` 定义的 Web Bridge W1–W7。  
本报告覆盖 host/fake/contract，以及本轮已完成的真实 USB camera preview/browser 证据；
真实网络和物理动作仍按独立门禁处理。

## 总结

阶段 4 当前为 **PARTIAL**，不能标记为整体 PASS。

已通过的主机侧基线：W1/W2 fake Bridge、W3 host-only lease/dead-man、W4 batch 与运维
框架、W5 Edge contract/fake、W6 control-plane design contract、W7 可重复容量方法。

仍未通过或未测试的门禁：Web Bridge 非计划长连接 USB 断线/重连、真实 maintenance/OTA wire、
真实 Wi-Fi/mTLS/Edge Agent、4G/多站点生产实现、真实 wall-clock/load/soak/200 台容量，以及受阶段 1
整体 HIL 门禁约束的远程运动、急停和机械故障路径。

## W1–W7 状态

| 工作线 | 当前状态 | 证据边界 |
| --- | --- | --- |
| W1 Bridge 核心 | **PASS：host/fake** | registry/session/command/ACK/completed/audit、SQLite recovery、幂等和跨设备隔离通过。 |
| W2 Web 监控 | **PASS：host/fake + controlled USB + real browser/media** | fake REST/WebSocket/UI、直接 USB、Bridge service/adapter status/health、真实设备 HTTP MJPEG、真实浏览器实时帧和 0 console error/warning 通过；production 连续 35 秒、10.57 fps、371 个完整 JPEG 通过；非计划长连接断线/重连仍未测试。 |
| W3 远程控制 | **PARTIAL** | typed status/pause/resume/home/远程急停 fake/API/UI 通过；真实 Bridge 已用 `--enable-manual-control`，浏览器方向键可用，真实 lease acquire/release 通过；实体运动、急停和 HIL 仍未放行。 |
| W4 批量与运维 | **PASS：host-only** | batch、challenge、诊断 allowlist、rollout task 和重启不重放通过；真实维护/OTA BLOCKED/NOT TESTED。 |
| W5 Wi-Fi/Edge | **PASS：contract/fake** | `lifeos.edge.v1`、双向 seq、身份绑定、断线/重连/背压故障通过；真实 Wi-Fi/TLS/mTLS NOT TESTED。 |
| W6 多站点/4G | **PASS：design baseline** | `lifeos.control-plane.v1` schema/fixtures/安全 gates 通过；生产 control plane、4G、迁移和安全评审未完成。 |
| W7 容量验证 | **PASS：method baseline** | 有界 deterministic runner、分位数、coalesce/drop、partial/recovery 通过；真实容量和 200 台 NOT TESTED。 |

## 本轮验证

以下结果来自本工作树的最终整合后命令：

```text
tests/bridge: 110 passed (including camera media, write fencing, reader EOF, and host heartbeat)
W4 专项 tests: 10 passed
W5 Edge + W7 capacity 定向 tests: 16 passed
Browser fake E2E: control command and camera MJPEG request PASS
node/Vite web build: PASS
python3 tools/validate_schemas.py: PASS
direct USB hello/status read-only (`/dev/cu.usbmodem1101`): PASS
Web Bridge service + USB adapter status/health/disconnect read-only: PASS
real production continuous camera preview: 371 complete JPEG parts / 35 s, 10.57 fps: PASS
real browser camera preview + enabled manual UI + lease acquire/release: PASS; console errors/warnings: 0/0
```

容量方法示例输出为 `mode=host_fake_simulation`、`claim=capacity_method_baseline_only`，
并明确将 RSS/CPU/event-loop/真实 DB latency 标为 `unknown`；这不是 wall-clock 性能或
200 台容量证明。

## 安全门

- 默认 Web Bridge 仍 loopback-only；可信 LAN 必须显式 opt-in。
- 配置 Origin allowlist 后，WebSocket 和 REST CORS 只允许显式来源；通配来源拒绝。
- trusted LAN 启用 manual/maintenance/rollout 等高影响 gate 时，没有非空 Origin allowlist
  会在启动时拒绝。
- maintenance confirmation 必须匹配 device/session、operation、`result=confirmed` 和
  `valid_for_ms`，断线、重连或 Bridge 重启后旧 challenge 失效。
- `manual_control_v1` 默认关闭；真实 Bridge 必须显式通过 `--enable-manual-control` 才打开
  verified transport，普通 `manual_control` API 不能绕过 control lease。当前仅验证了目标固件
  capability、实机身份、UI 和 lease acquire/release；未发送实体输入，正式物理使用仍需 HIL。
- Bridge 由单一 per-device writer lock 串行化所有 USB 出站报文；session/lease 失效、reader EOF
  或 `USB serial write failed` 会闭锁并释放真实 transport，旧命令不会在新 session 重放。
- 浏览器不能提交 raw `lifeos.v1`、PWM、GPIO、I²C、舵机参数、优先级或设备序列；所有真实
  physical safety 仍由固件 SafetyGate/SafetyLoop 负责。

## 未测试 / 阻塞

- Web Bridge 非计划长连接 USB discovery、断线/重连：**NOT TESTED**；直接
  `/dev/cu.usbmodem1101` hello/status、一次性 service/adapter status/health、真实 camera
  MJPEG 和真实浏览器预览已 **PASS**。
- 触摸暂停/清故障、8 小时 soak、真实机械卡滞、普通感知路径的传感器/camera capture：**NOT TESTED**；
  本轮只证明显式 Web camera preview 的真实采集和传输。
- 真实 home、manual input 实体轨迹、behavior/speech、远程急停和 batch motion：**BLOCKED/NOT TESTED**，
  阶段 1 整体出口尚未闭合。
- maintenance wire、OTA 分片/签名/boot confirmation/A-B rollback、真实断电恢复：**NOT TESTED**。
- Wi-Fi、mTLS、Edge Agent、4G/NAT/弱网、证书轮换、站点迁移和公网 Web 身份：**NOT TESTED**。
- 真实 wall-clock CPU/RSS/DB/event-loop、load/soak 和 200 台规模：**NOT TESTED**。
- 失焦/断链 UI 流程：**NOT TESTED**；真实设备浏览器连续 camera MJPEG 与手控 lease
  acquire/release 已 **PASS**，最新截图见 `output/playwright/real-web-control-camera-20260902.png`。

## 可复现验收入口

```bash
PYTHONDONTWRITEBYTECODE=1 PYTEST_ADDOPTS='-p no:cacheprovider' \
  PYTHONPATH=. python3 -m pytest tests/bridge -q
node --check web/app.js
python3 tools/validate_schemas.py
PYTHONPATH=. python3 tools/bridge_capacity.py --pretty
# Read-only real-device checks; re-discover the current port before running.
PYTHONDONTWRITEBYTECODE=1 PYTHONPATH=. python3 tools/hil_usb_runner.py \
  --port /dev/cu.usbmodem1101 --timeout 3
PYTHONDONTWRITEBYTECODE=1 PYTHONPATH=. python3 tools/web_bridge_usb_acceptance.py \
  --port /dev/cu.usbmodem1101 --device-id stackchan-01 \
  --hardware-id 1c:db:d4:ba:43:40
```

完整整仓回归：

```bash
PYTHONDONTWRITEBYTECODE=1 PYTEST_ADDOPTS='-p no:cacheprovider' make test
```

任何需要刷写、运动、真实网络或长时间 soak 的命令，都不能用上述 host/fake 结果替代，
必须在对应设备身份、备份、授权和安全门禁具备后单独记录 PASS/BLOCKED/NOT TESTED。
