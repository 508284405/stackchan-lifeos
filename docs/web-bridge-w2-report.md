# Web Bridge W2 监控验收报告

日期：2026-08-31  
范围：静态监控 UI + REST snapshot + cursor WebSocket + feature-gated real camera preview；不包含真实运动控制

## 结论

fake device/API/WebSocket 监控链路 **PASS**；真实 USB 直接 JSONL 无运动 smoke **PASS**，
Web Bridge 串口适配器/API 的一次性 status/health 只读闭环 **PASS**；真实设备 camera
MJPEG 和浏览器实时帧 **PASS**。
W2 使用 FastAPI 托管的零依赖静态 HTML/CSS/ES module，不引入前端框架或 CDN。

## 交付

- `bridge/events.py`：有界 event log、cursor、设备过滤、telemetry coalesce/drop、
  `resync_required` cursor 过期语义；命令/session/safety 事件先写审计再入流。
- `bridge/service.py`：为设备详情维护最新 health 摘要，保留最新 session 状态和 freshness。
- `bridge/api.py`：`GET /api/v1/devices/{device_id}`、REST snapshot health/session 字段、
  `WS /api/v1/events` 订阅与重同步、根路径静态页面托管。
- `web/`：Overview、Devices、设备 detail、health/session/capability、recent activity；
  明确 loading/empty/offline/degraded/fault 语义，键盘焦点和 reduced-motion 支持。

## 验证

| 项目 | 结果 |
| --- | --- |
| EventLog unit：cursor、过期、设备过滤、telemetry 合并/容量、payload 上限 | PASS |
| W1/W2 相关 Bridge 回归 | PASS（当前完整 `tests/bridge` 回归 100 tests） |
| Fake device REST snapshot/detail | PASS |
| WebSocket 订阅、设备过滤、命令/session 事件 | PASS |
| Static HTML/CSS/ES module served from `/` | PASS |
| `node --check web/app.js` | PASS |
| Desktop 1440px screenshot | NOT REPRODUCIBLE（当前仓库未保留 PNG/截图产物） |
| Mobile 390px screenshot + DOM width check | NOT REPRODUCIBLE（当前仓库未保留截图产物；需重新执行浏览器检查） |
| Direct real USB hello/status read-only smoke | PASS（`/dev/cu.usbmodemXXXX`；见 `artifacts/web-bridge/usb-readonly-20260830.json`） |
| Web Bridge service/USB adapter one-shot status/health/disconnect | PASS（见 `artifacts/web-bridge/usb-bridge-readonly-20260830.json`） |
| Controlled 2-second USB disconnect/reconnect through Bridge | PASS（见 `artifacts/web-bridge/usb-bridge-reconnect-20260830.json`） |
| Unplanned long-lived USB disconnect/reconnect | NOT TESTED |
| Real browser camera MJPEG + typed status command | PASS（0 console errors / 0 warnings） |
| Configured WebSocket Origin allowlist enforcement | PASS（未配置时仍只建议 loopback） |
| Command controls/dead-man/manual control/maintenance | NOT EXPOSED（按计划留在 W3/W4） |
| 200-device capacity | NOT TESTED |

完整回归入口：

```sh
PYTHONDONTWRITEBYTECODE=1 PYTEST_ADDOPTS='-p no:cacheprovider' make test
```

## 明确边界

- `202` 只表示 Bridge 创建记录；UI 通过 command event/status 区分 accepted 与 completed。
- WebSocket 只推 Bridge domain event，不接收 raw serial、raw `lifeos.v1` envelope、PWM、
  GPIO、I²C 或舵机参数。
- `visible_devices` 或显式 `device_ids` 是订阅必需过滤条件；cursor 失效后 UI 清空本地
  增量并重新拉 REST snapshot。
- 页面可展示离线/故障，但不能把 absence of telemetry 解释为 healthy。

本次真实验收：生产固件 `lifeos-phase1-0.4.6` 在授权后刷入；
`python3 tools/hil_usb_runner.py --port /dev/cu.usbmodemXXXX --timeout 5` 返回
`blocked=false`、`safe_idle=true`、`camera_ready=true`；随后真实 Web Bridge 在 6 秒
MJPEG 连接收到 13 个完整 JPEG part，浏览器页面显示实时帧，`control.status` 显示设备已完成。
下一门禁是长时间运行中的 USB 断线/重连；真实远程运动仍受 Phase 1 整体出口约束。
