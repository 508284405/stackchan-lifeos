# StackChan/CoreS3 硬件 BSP 状态

更新时间：2026-08-30

## 当前结论

目标板身份、引脚和官方初始化路径已确认；LifeOS 的 ESP-IDF HAL 已实现并
通过 ESP32-S3 target build。生产代码不依赖 Arduino BSP，使用 ESP-IDF
driver-ng 与锁定版本的 `espressif/esp32-camera` 2.1.5。

真实 SCS 执行器闭环子门禁已通过：最新 HIL 镜像完成反馈运动、pause/home、故障
注入、设备侧急停和断线恢复验证。阶段 1 整体出口仍未闭合：端到端急停 `<100 ms`
未满足，触摸、8 小时 soak、真实机械卡滞和部分独立时序/传感器读取证据仍为
BLOCKED/NOT TESTED。完整原厂 flash 备份已保存，可按历史验收报告恢复。

## 已确认的硬件事实

| 项目 | 事实 | 来源/证据 |
| --- | --- | --- |
| 主控 | M5Stack StackChan / CoreS3 / ESP32-S3 rev 0.2 | esptool chip_id、官方 StackChan 文档 |
| USB | `/dev/cu.usbmodem101`，VID/PID `0x303A:0x1001` | macOS 枚举、esptool |
| Flash | 16 MiB | esptool flash_id |
| I²C | SDA GPIO12，SCL GPIO11，400 kHz bus | 官方 StackChan 源码/配置 |
| 电机电源 | AW9523 `BUS_EN=1/BOOST_EN=1`，PY32L020 `0x6F`，VM_EN 为 pin 0 | 官方 CoreS3 电源路径；实机读回 |
| 触摸 | Si12T `0x68`，3 个电容触摸区；LCD FT6336U `0x38` | 官方 Si12T/FT6336 驱动 |
| IMU | BMI270 `0x69` | 官方 StackChan 配置 |
| 接近 | LTR-553 `0x23` | 官方 StackChan 硬件清单 |
| 相机 | GC0308 `0x21`，DVP GPIO39/40/41/42/15/16/48/47，VSYNC46/HREF38/PCLK45，外部 20 MHz XCLK | 官方 `config.h` |
| LCD | ILI9342C 320×240，SPI3 MOSI37/SCLK36/CS3/DC35；动画笑脸渲染（`face.hpp`/`face.cpp`，RGB565 MSB-first） | 官方 StackChan display driver |
| 舵机 | SCS UART1 1 Mbps，TX6/RX7，ID1 yaw / ID2 pitch；zero raw 460/620 | 官方 M5StackChan servo adapter |

## LifeOS adapter 映射

- `firmware/include/lifeos/hal/stackchan/stackchan.hpp`：固定容量设备边界，
  不把 Arduino `String`、线程池或动态媒体 buffer 带入安全路径。
- `firmware/src/hal/stackchan/stackchan.cpp`：I²C、SCS、Si12T、FT6336U、
  BMI270、LTR-553、GC0308、AW9523 和 ILI9342C 适配；普通感知路径只验证采集元数据并
  立即归还，显式 Web 预览路径以固定 QVGA JPEG 分块发送后再归还；Phase 1 不伪装成人脸
  推理完成。
- `firmware/include/lifeos/hal/stackchan/face.hpp` +
  `firmware/src/hal/stackchan/face.cpp`：纯整数、主机可测的动画笑脸光栅模型
  （眨眼、瞳孔漂移、微笑呼吸/说话张合、整体浮动，及 SLEEPING/PAUSED/FAULT
  表情变体），`SmileyFace` 只在有变化的 100 ms 图任务 tick 上重绘脸部区域，
  `StackChanDisplay` 通过 `FaceWriter` 用局部窗口流式送显。rgb 字节序以
  ILI9342 面板 RGB565 MSB-first 为准，若实机色相相反，仅需交换 palette 的 R/B。
- 舵机启动先自检反馈并关闭 torque；任何越界、反馈失败、命令失败或停止
  路径都在返回前关闭 torque/VM_EN。LifeOS 限制 yaw ±90°、pitch 5–85°，
  safety loop 进一步使用软限位 yaw ±75°。
- `firmware/idf/sdkconfig.hil.defaults` 只为受监督 HIL 打开维护命令；生产
  `sdkconfig.defaults` 明确不打开该开关。

## 构建证据

```text
ESP-IDF v5.5.4 / ESP32-S3 Xtensa esp-14.2.0_20260121
production: tools/build_target.sh production build
HIL:        tools/build_target.sh hil build
HIL image:  firmware/idf/build-hil-16m/stackchan_lifeos_phase1.bin
flash args: --flash_size 16MB
```

生产与 HIL 配置分别生成在各自的 ignored build directory；不要直接复用一
个含旧 `sdkconfig` 的目录进行刷写。

## 历史实机启动快照（2026-08-29）

以下日志记录的是舵机回包修复前的旧 HIL 镜像，不代表当前 `0.3.0` 执行器闭环状态；
当前证据见 `docs/scs-runtime-io-acceptance.md`。

```text
PY32L020 address=0x6f version=0x41 ready=1
AW9523 external bus outputs p0=0x07 p1=0x83 bus_en=1 boost_en=1
PY32 VM_EN requested=1 readback=1 output=0x01
servo self-test vm=1 yaw_ping=0 yaw_feedback=0 pitch_ping=0 pitch_feedback=0 torque_off=0
LIFEOS_HIL_READY lifeos-phase1-0.2.0 motion=disabled board=degraded
```

外部电源控制寄存器已读回正确；舵机仍无响应，当前需要检查 StackChan
CoreS3/底座插合、舵机连接器和舵机本体，不能把运动 HIL 标为 PASS。

补充对照：临时刷入之前的官方 USB 控制镜像后，`stackchanctl.py` 的
`STATUS`、`X 10`、`Y 50`、`HOME` 均返回 `OK`，随后已恢复 LifeOS HIL。
这只证明官方命令路径接受请求；若现场观察到实际转动，则应优先继续检查
LifeOS 的 SCS 回包读取，而不是判定设备本体损坏。

## 验收边界

已通过：主机 fake HAL、协议回放、固件 C++ 测试、生产/HIL target build、
Codex schema contract、CGraph upstream ESP-IDF spike 记录。

待真实样机完成：舵机运动与反馈、触摸暂停/长按清故障、急停时延、反馈冻结
与卡滞、硬限位、断线后扭矩释放、传感器连续采集、以及连续 8 小时长稳。当前
物理阻塞和证据详见 [`docs/hardware-acceptance-report.md`](hardware-acceptance-report.md)。
