# Web 控制与摄像头预览验收报告

日期：2026-09-02  
范围：`bridge/`、`web/`、`contracts/web-bridge/`、StackChan GC0308 固件预览协议

## 结论

- **PASS**：fake/API/Web 控制闭环。`status`、`pause`、`resume`、`home` 和独立远程急停均
  经过 Bridge command lifecycle；UI 显示设备回执状态。
- **PASS**：fake 摄像头闭环。显式 start → `camera.frame.begin/chunk/end` → 最新完整 JPEG
  → `multipart/x-mixed-replace`，坏帧和缺块不发布。
- **PASS**：Web 页面真实浏览器验收。真实设备页面显示“设备已完成”和“实时帧”，真实 JPEG
  在 `<img>` 中可见；浏览器控制台 0 错误、0 警告。截图见
  [`output/playwright/real-web-control-camera-final.png`](../output/playwright/real-web-control-camera-final.png)。
- **PASS**：production/HIL ESP-IDF 5.5.4 target build、主机 C++ protocol tests、Bridge
  tests 和 Web Vite build 通过。
- **PASS**：真实 StackChan 已在用户授权后刷入生产固件；只读启动和安全空闲检查通过。
- **PASS**：真实设备 USB → Bridge → HTTP MJPEG → 浏览器连续帧通过。
- **PASS**：真实设备持续视频会话通过：35 秒 HTTP 流保持连接，收到 371 个完整 JPEG，实测
  10.57 fps，启动命令 `duration_ms=0` 未在 30 秒处停止。
- **PASS**：真实 Bridge 以 `--enable-camera-preview --enable-manual-control` 启动；页面方向键
  已解除禁用，真实 WebSocket lease acquire/release 通过，未发送运动输入。
- **PASS**：真实浏览器执行无运动的 `暂停 → 恢复`；设备 status 分别确认
  `paused=true` / `paused=false`，两次均 `torque_enabled=false`。
- **PARTIAL / NOT TESTED**：实体运动动作（home、连续手动、远程急停的物理执行）未在本轮触发；
  真实执行器与 Phase 1 总体 HIL 门禁仍按独立报告保持 BLOCKED/PARTIAL。

## 历史真实设备部署与验收（2026-08-31）

- 设备身份：`/dev/cu.usbmodemXXXX`，`stackchan-01`，MAC/硬件 ID
  `<device-mac>`，ESP32-S3 rev0.2，16 MiB flash。
- 全量恢复备份：`<secure-backup-dir>/stackchan-device-20260829-fullflash.bin`，SHA-256
  `669507af37296a09677b8b6ae831090a6cef357a634815011daf0f8622d11b35`。
- 生产镜像：`lifeos-phase1-0.4.6`，423648 bytes，SHA-256
  `823c8a28c5fc605a60cde936b36a5aec812cbb07e484a946e54511c3cebab173`；写入 bootloader、
  partition table、application 三段均显示 `Hash of data verified`。
- 只读 `hello/status`：**PASS**；`camera_ready=true`，能力含 `camera`，
  `fault=false`，`safety_faults=0`，`torque_enabled=false`，`safe_idle=true`。
- 真实 Bridge：以 `--enable-camera-preview` 启动，health `media=true`，设备 session online；
  `camera-preview start` 返回 accepted，随后 HTTP 返回
  `Content-Type: multipart/x-mixed-replace; boundary=lifeos-frame`。
- HTTP media：一次 6 秒连接收到 13 个连续 JPEG part，大小 2973–2995 bytes，全部 JPEG
  `ff d8` / `ff d9` 完整；停止后流端点返回 `409 camera preview has not been started`，旧帧失效。
- 浏览器：真实页面点击“开启预览”后显示“实时帧”和真实图像；点击“停止预览”后显示“未开启”；
  点击“读取状态”显示“设备已完成”；随后 `暂停 → 恢复` 显示实际 ACK lifecycle；控制台
  0 errors / 0 warnings。

## 已实现边界

- Bridge API：
  - `POST /api/v1/devices/{device_id}/commands`
  - `POST /api/v1/devices/{device_id}/emergency-stop`
  - `POST /api/v1/devices/{device_id}/camera-preview`
  - `GET /api/v1/devices/{device_id}/camera/stream`
- CameraFrameStore 只保留每个 device/session 的最新完整 JPEG；不写 SQLite、audit、
  LangGraph 或 provider。
- 预览参数由服务端固定为 320×240、JPEG、最多 10 fps、持续到显式停止；浏览器不能提交 raw
  envelope、分辨率、quality、chunk、串口或硬件参数。
- GC0308 不支持硬件 JPEG；固件以 QVGA 原生 RGB565 原彩采集后在 ESP32-S3 上转成 JPEG。预览期间
  Bridge 每 500 ms 发送 `host.heartbeat`，使设备的 1.5 s 主机失联安全门持续有效；停止、
  session 断开或设备 host watchdog 触发时心跳取消。
- 连续手动控制 UI 与 host lease 仍要求 `manual_control_v1`；生产/HIL Bridge 默认不打开
  该能力，真实 transport 仍需独立验收。

## 2026-09-02 连续视频与真实手控复验

- 已按用户授权重新核验身份并刷入 production `lifeos-phase1-0.5.0`；设备为
  `/dev/cu.usbmodemXXXX`、`stackchan-01`、MAC `<device-mac>`、ESP32-S3 rev0.2、
  16 MiB flash。写入 bootloader、partition table、application 三段均报告
  `Hash of data verified`。
- 当前 production 镜像 `firmware/idf/build/stackchan_lifeos_phase1.bin` 为 438160 bytes，
  SHA-256 `2b3a498ad44b9ec3d467bf92558a3a33fa11185f9ed289af384e1811d407774f`；HIL 镜像为
  439984 bytes，SHA-256 `c16ec79d8eca4d7d32a9cab10ec9c6ff27be12ebf9da854441471ff9b5d4f7d6`，
  未刷入。
- 真实 Bridge 以 `--enable-camera-preview --enable-manual-control` 启动；设备在线，固件
  声明 `motion`、`manual_control_v1`、`health`、`camera`，`motion_enabled=true`、
  `fault=false`、`torque_enabled=false`。
- `camera.preview.start` 实际下发 `fps=10,duration_ms=0`；HTTP 流保持 35.07 秒，返回
  `multipart/x-mixed-replace; boundary=lifeos-frame`，收到 371 个完整 JPEG，实测 10.57 fps，
  启动命令在超过 30 秒后仍为 `completed`。
- GC0308 使用 QVGA 原生 RGB565 原彩双缓冲转 JPEG；真实帧已检查 JPEG SOI/EOI 和画面内容。浏览器
  页面显示“实时帧”、`MJPEG · ≤10 fps · 持续`，四个手动方向键均已解除禁用。
- 真实 WebSocket lease acquire/release **PASS**，没有发送 `command.manual_control`、`home`
  或其他实体运动命令；实体运动方向、急停物理时延和机械卡滞仍保持 **BLOCKED/NOT TESTED**。
- 最终浏览器证据截图：
  [`output/playwright/real-web-control-camera-20260902.png`](../output/playwright/real-web-control-camera-20260902.png)。

## 验证命令

```text
PYTHONDONTWRITEBYTECODE=1 PYTEST_ADDOPTS='-p no:cacheprovider' PYTHONPATH=. python3 -m pytest tests/bridge -q
./tools/run_firmware_tests.sh
(cd web && npm run build --silent)
```

结果：`make test` **PASS**（brain 64、Bridge 110、Web build、firmware host tests、Phase 1
回放、schema 和离线 contract 均通过，仅有一个 LangGraph 弃用警告）；production/HIL target
build **PASS**。当前镜像 SHA-256 为
`2b3a498ad44b9ec3d467bf92558a3a33fa11185f9ed289af384e1811d407774f`（production）和
`c16ec79d8eca4d7d32a9cab10ec9c6ff27be12ebf9da854441471ff9b5d4f7d6`（HIL，未刷写）。
真实摄像头连续 MJPEG、浏览器实时帧、manual capability 和 lease handshake 已通过；实体
运动输入、连续手控实际轨迹、急停物理时延和阶段 1 总体出口仍未通过。
