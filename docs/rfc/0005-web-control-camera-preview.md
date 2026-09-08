# RFC 0005: Web 控制与 StackChan 摄像头预览

> 连续视频与真实手动控制的当前实现边界见 [`RFC 0006`](0006-continuous-mjpeg-and-manual-control.md)。

- 状态：Implementation baseline / single-device USB bootstrap
- 日期：2026-08-30
- 设计输入：`DESIGN.md`、`docs/rfc/0001-web-bridge-fleet-control.md`、`docs/rfc/0002-manual-control-v1.md`
- 范围：可信内网中的 Web 控制、单设备摄像头预览、Bridge 最新帧转发
- 不包含：公网访问、账号/RBAC、录像、音频、多人控制、200 台容量验收和真实运动 HIL 放行

## 1. 目标

让一个操作者可以在 Web Console 中完成以下闭环：

```text
选择设备 → 查看事实状态 → 发起高层控制 → 查看 accepted/completed/blocked
                    └──────→ 显式开启摄像头预览 → HTTP MJPEG 最新帧
```

首版控制分为两层：

1. 离散安全控制：`status`、`pause`、`resume`、`home` 和独立的
   `emergency_stop`。它们继续经过 Bridge capability、生命周期、SafetyGate 和设备
   固件安全回路；Web ACK 不代表执行器已经完成。
2. 连续手动控制：复用 `manual_control_v1` 的 lease/dead-man/10 Hz/300–500 ms TTL
   语义。浏览器只能发送归一化 `yaw/pitch` 方向和 `release`，不能发送角度、PWM、GPIO、
   I²C、速度、电流或原始 `lifeos.v1` envelope。该能力在没有完整固件执行与 HIL 证据前
   保持关闭；UI 必须显示 unavailable，而不是伪装成可用。

摄像头预览是独立的媒体出口：浏览器只访问 Bridge 的 MJPEG URL，绝不接触设备 wire
envelope。首版 USB 设备没有第二个物理媒体接口，因此使用一个有界、低帧率的
`lifeos.v1` 预览帧 bootstrap 隧道；它只用于诊断预览，不是高码率视频协议。未来 Wi-Fi/
4G 或更高帧率需求必须替换为独立 media transport，不扩大 JSONL 控制线的预算。

## 2. 控制 API

### 2.1 离散控制

Web API 继续使用：

```text
POST /api/v1/devices/{device_id}/commands
POST /api/v1/devices/{device_id}/emergency-stop
GET  /api/v1/commands/{command_id}
```

浏览器请求是 allowlisted DTO。Bridge 负责生成 command ID、session、seq、TTL、priority
和 wire payload。`control.home` 等设备命令不携带主机单调时钟字段；设备时钟与主机时钟
不共享，设备只依赖自己的安全回路和本地命令处理。Bridge 仍保留主机侧生命周期记录。

### 2.2 连续控制

浏览器使用已有 `/api/v1/control` WebSocket：

```json
{"type":"lease.acquire","device_id":"stackchan-01"}
{"type":"input","lease_id":"lease-…","input_seq":1,
 "action":"input","direction":{"yaw":0.25,"pitch":-0.1},"ttl_ms":400}
{"type":"input","lease_id":"lease-…","input_seq":2,
 "action":"release","ttl_ms":400}
```

服务器拒绝未知字段、越界值、乱序、超过 10 Hz、失效 lease、非当前 session 和非 fake/
未验收的真实 transport。`pointerup`、`keyup`、失焦、`visibilitychange`、WebSocket
close 和 TTL 到期都必须停止连续输入；设备端仍需再做限位、反馈、卡滞和故障处理。

## 3. 摄像头预览协议

### 3.1 Web API

```text
POST /api/v1/devices/{device_id}/camera-preview
GET  /api/v1/devices/{device_id}/camera/stream
```

请求只允许以下字段：

```json
{"action":"start"}
{"action":"stop"}
```

Bridge 使用固定的安全配置：QVGA `320x240`、JPEG、最多 10 fps、持续到显式停止、每设备
只保留一帧。开始/停止必须产生可审计的高层 command record；GET stream 不会偷偷开始设备采集。
预览默认不录制、不写 SQLite、不写普通事件 payload、不进入 LangGraph 或 provider 上下文。

### 3.2 USB bootstrap frame

设备在收到 `command.camera_preview` 且 host hello 已声明 `media_enabled=true` 后，发送
以下 `lifeos.v1` event。每个 envelope 仍受 16 KiB line / 8 KiB payload 限制：

```text
event.camera.frame.begin  {frame_id, format="jpeg", width, height, size, chunk_count}
event.camera.frame.chunk  {frame_id, index, data=<base64>, chunk_count}
event.camera.frame.end    {frame_id, size}
```

在线 USB session 由 Bridge 统一以最多 500 ms 的间隔发送 `host.heartbeat` event；预览期间
payload 为 `{"media_enabled":true}`，否则与当前 media gate 一致。它只刷新设备的 1.5 s
主机失联看门狗，不产生 ACK、不携带图像；session 断开时停止。没有这个心跳，设备必须按
安全策略停止预览和普通主机动作。

`data` 每块解码后最多 5 KiB；Bridge 严格校验 frame/session/size/index/总块数，完成后才
发布一帧。缺块、重复块、超限、跨 session 或超过超时时间的 frame 全部丢弃并只记录脱敏
原因。Bridge 的 `CameraFrameStore` 仅保存每个 device 的最新完整 JPEG；断线、停止或新
session 会使旧帧失效。

### 3.3 Browser stream

Bridge 将最新帧输出为：

```text
Content-Type: multipart/x-mixed-replace; boundary=lifeos-frame
```

每个 part 只有 `image/jpeg`、长度和 frame ID。没有可用帧时连接保持 stale/waiting；设备
离线、能力缺失、media gate 关闭或 session 变化时返回明确的 4xx/关闭流，而不是展示旧图。
前端必须显示采集状态、最后一帧时间和 stale/offline，不把 `<img>` 成功建立连接当作设备
健康。

## 4. 安全与部署门禁

- `media` 是独立 feature gate；不因 UI 隐藏而绕过服务端 gate。
- 默认部署仍是 loopback。可信 LAN 无认证模式必须是具体私网地址、显式 Origin allowlist，
  页面显示风险 banner；不得端口映射或公网暴露。
- 不保存 JPEG、不把 JPEG 放进审计/诊断包、不上传 provider、不把浏览器字段透传到固件。
- 摄像头帧不是安全状态。安全状态、急停、暂停、故障和 lease 仍按独立 control/event
  路径判定。
- 浏览器不能请求自定义 fps、分辨率、JPEG quality、chunk、路径或 transport 参数。

## 5. 验收门禁

| 门禁 | 结果要求 |
| --- | --- |
| Bridge fake 控制 | 离散命令显示真实 lifecycle；emergency stop 仍独立优先 |
| Bridge fake media | start → 多帧 begin/chunk/end → MJPEG part；坏帧/跨 session 丢弃 |
| Web Console | 控制按钮有 loading、accepted、completed、rejected/offline；预览有 start/stop/stale |
| 固件 host build | parser、固定大小边界、JPEG frame 发送和 preview stop 编译通过 |
| 真实设备 build | production/HIL target build PASS；不自动刷写 |
| 真实设备摄像头 | 只有在用户授权刷写后执行；需记录型号、固件、分辨率、帧率和断线结果 |
| 真实远程运动 | 继续受 `docs/phase1-acceptance.md` 总出口约束；没有 HIL 证据则 BLOCKED |

## 6. 后续替换点

USB bootstrap 只解决“一台设备在本地 Bridge 上能看见低帧率诊断画面”。在 Wi-Fi/4G、多站点、
高帧率或音视频双向场景中，保留 `CameraFrameStore` 和浏览器媒体 API，替换设备到 Bridge
之间的 `CameraTransport`；不得把当前 base64 JSONL 隧道扩展成无界视频通道。
