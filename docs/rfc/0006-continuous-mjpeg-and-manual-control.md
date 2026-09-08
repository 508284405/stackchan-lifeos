# RFC 0006: 连续 MJPEG 视频与真实手动控制启用

- 状态：Implementation baseline
- 日期：2026-09-02
- 范围：单台真实 StackChan/CoreS3、USB Serial/JTAG、Web Bridge、浏览器控制台

## 1. 需求与边界

本次需求有两个独立目标：

1. 摄像头不再是 2 fps、30 秒的诊断预览，而是由真实 GC0308 持续产生的 QVGA JPEG
   帧，并由 Bridge 输出连续 MJPEG 流。
2. 真实设备的 `manual_control_v1` 不再因为 Bridge 以只读模式启动而显示不可用；手动
   控制仍必须经过显式服务端 gate、设备 capability、control lease、dead-man TTL 和
   SafetyGate。启用 gate 不等于实体运动 HIL 已通过。

本次“高帧率”定义为当前 USB/ESP32-S3 架构可稳定承载的 10 fps 上限，不承诺 WebRTC、
HLS、RTSP、音频、1080p 或无限码率。浏览器继续使用 `<img>` 播放 MJPEG，因为每个
   part 是真实 JPEG 帧；若未来需要 H.264/WebRTC，必须另建媒体 transport。

## 2. 连续视频方案

### 2.1 设备到 Bridge

- `command.camera_preview` 的 `fps` 允许 1–10；Bridge 默认发送 `fps=10`。
- `duration_ms=0` 表示持续到显式 stop、USB session 断开或设备 host watchdog 触发。
- 设备 camera task 按帧间隔采集 GC0308 原生 RGB565 原彩、双缓冲转换 JPEG，并沿现有有界
  `camera.frame.begin/chunk/end` JSONL 事件发送。
- 相机事件使用就地静态 envelope 和 20 ms 有界 USB 写入，避免 8 KiB envelope 拷贝和
  背压阻塞控制路径。

### 2.2 Bridge 到浏览器

- `POST /api/v1/devices/{id}/camera-preview` 仍只接受 `start/stop`，不让浏览器提交
  fps、分辨率、quality、chunk 或串口参数。
- start command 只使用 10 秒首帧确认 TTL；视频 session 本身不因 command TTL 到期而
  停止。
- Bridge 保留每个 device/session 的最新完整 JPEG，HTTP 输出
  `multipart/x-mixed-replace; boundary=lifeos-frame`。
- 设备断线、session 变化、显式 stop 或 host heartbeat 失败时立即清理最新帧并停止
  camera heartbeat。连续 session 不写 SQLite、audit 或认知链路。

## 3. 手动控制方案

- 真实 Bridge 以 `--enable-manual-control` 显式启动；该 flag 同时打开 host gate 和
  `manual_control_verified` transport 标记。
- startup 仍必须完成真实 hello/status：设备声明 `manual_control_v1`、
  `motion_enabled=true`、`fault=false`、`torque_enabled=false`。
- 浏览器只能通过 WebSocket 获取 lease，发送归一化 yaw/pitch、连续 `input_seq` 和
  300–500 ms TTL；松手、失焦、页面隐藏、WebSocket 断开或 TTL 到期必须 release。
- 固件再次执行 pitch 5°–85°、反馈、暂停、故障、卡滞和失联安全检查；host/UI 显示
  可用不表示已经证明实体运动方向、限位、急停时延或机械卡滞。
- 只有在设备周围清空、操作者在实体急停旁并完成受监督 HIL 后，才允许发送第一次
  `command.manual_control`；本实现不自动发送运动命令。

## 4. 验收矩阵

| 门禁 | 证据 |
| --- | --- |
| 协议/Schema | fps=10、duration=0 通过；fps>10 或非法 duration 拒绝 |
| 连续设备媒体 | 真实 GC0308 帧连续产生；至少 10 fps 目标窗口内无协议崩溃 |
| HTTP 媒体 | 200、MJPEG content type、连续完整 JPEG、stop 后无旧帧 |
| Bridge 生命周期 | command 首帧 completed；持续超过 30 秒；stop/disconnect 清理 |
| 手控门禁 | 只读启动保持 unavailable；带 `--enable-manual-control` 且健康状态满足时控件可用 |
| 手控安全 | lease、TTL、release、session fencing 通过；不发送 raw PWM/GPIO/角度 |
| 实体动作 | 另行现场 HIL；方向、反馈、急停、触摸、卡滞和长稳单独报告 PASS/PARTIAL/BLOCKED |
