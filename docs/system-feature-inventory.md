# StackChan LifeOS Web Bridge 功能清单

日期：2026-08-31  
范围：`bridge/`、`web/`、`contracts/web-bridge/` 以及真实 USB 只读适配器。

## 系统定位与边界

Web Bridge 是浏览器与 StackChan 设备之间的主机控制面。浏览器只提交高层 DTO；
`Bridge` 负责设备注册、session、命令生命周期、批量任务、审计和安全门；设备继续
使用 `lifeos.v1` JSONL、nonce/seq/TTL 和固件 SafetyLoop。Web 首版无用户认证，只允许
明确的 loopback/可信内网部署。

## UI 可见能力

- Overview：注册数、在线数、需要关注数和全局安全 banner。
- Devices：按名称/ID/硬件身份过滤，选择设备查看 session freshness、health、能力和活动事件。
- Event stream：通过 cursor WebSocket 接收 session、命令、安全和 telemetry 事件。
- 当前 UI 提供 typed status/pause/resume/home/远程急停，以及显式、feature-gated 的
  QVGA JPEG 摄像头预览；不显示 raw envelope、PWM、GPIO、I²C 或 maintenance 入口。
  四向手动控制入口始终可见，但在 `manual_control_v1` 未协商、服务端 gate 关闭或安全
  状态不允许时显示为禁用；不会因此发送控制帧。

## API/服务能力

- REST：health、device list/detail、claim、typed command、batch task、诊断、gated 运维 task
  和显式 camera-preview/MJPEG media surface。
- WebSocket：范围化事件订阅；另有 gated manual-control host-only socket。
- 真实 USB：标准库 POSIX 串口适配器执行 host hello、只读 `control.status` 和 feature-gated
  camera preview；把设备 ACK 中的有界 health 字段保存到设备快照，JPEG 只留在内存 media path。
- Fake：支持延迟、重复、乱序、断线、拒绝、ACK 丢失、故障和 completion 注入。

## 状态流与持久化

`discovery candidate → claim → negotiating → online/degraded/offline`；命令从
`created → validated → routed → sent → accepted/executing → completed` 或结构化终态；
批量任务保留逐设备结果。SQLite migration v3 持久化设备、session、command、batch、
maintenance、rollout 和 audit；重启不恢复旧 session，不重放物理动作。

## 已有覆盖

- `tests/bridge`：domain、SQLite、fake transport、REST/WebSocket、batch、lease、camera media、W4 运维、
  W5 Edge、W6 contract、W7 capacity，共 100 项 host/fake 测试。
- `tools/hil_usb_runner.py`：真实 USB 直接 hello/status 只读 smoke。
- `tools/web_bridge_usb_acceptance.py`：真实 USB 经 Bridge service/adapter 的一次性只读闭环。
- 本次新增的浏览器用例只使用用户可见 DOM/ARIA 和真实 HTTP/WebSocket 服务，不导入前端内部状态。

## 未知与风险

- 真实设备 camera frame 传输和浏览器预览已通过；真实设备的实体运动、触摸、机械卡滞和长稳
  仍需独立证据。
- `motion_enabled=true` 不表示本次 smoke 驱动过舵机；本次真实链路保持 torque off。
- 200 台、Wi-Fi/mTLS/4G、多站点、OTA 和公网身份不由本清单推导完成。
