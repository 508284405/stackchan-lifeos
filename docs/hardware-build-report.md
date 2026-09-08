# Phase 1 ESP32-S3 硬件构建报告

更新时间：2026-08-31

## 固定环境

| 项目 | 版本/位置 |
| --- | --- |
| ESP-IDF | v5.5.4，隔离 checkout `735507283d5b2f9fb363a1901172dbd9e847945d` |
| Xtensa GCC | `esp-14.2.0_20260121` |
| CMake/Ninja | 3.30.5 / 1.11.1 |
| 目标 | ESP32-S3，16 MiB flash |
| 摄像头组件 | `espressif/esp32-camera` 2.1.5，锁定于 `firmware/idf/dependencies.lock` |

工具链位于工作区外的隔离目录，不写入仓库依赖。构建入口
`tools/build_target.sh` 显式传递 profile 对应的 `SDKCONFIG` 与 defaults，
避免 HIL 配置污染生产镜像。

## 构建结果

```text
IDF_PATH=/path/to/esp-idf-5.5.4 tools/build_target.sh production build size-components
→ PASS；firmware/idf/build/stackchan_lifeos_phase1.bin
→ CONFIG_LIFEOS_HIL_TEST_MODE 未设置，flash args 为 --flash_size 16MB
→ image size 0x676e0，1 MiB app 分区剩余 0x98920（约 60%）
→ SHA-256 `823c8a28c5fc605a60cde936b36a5aec812cbb07e484a946e54511c3cebab173`

IDF_PATH=/path/to/esp-idf-5.5.4 tools/build_target.sh hil build
→ PASS；firmware/idf/build-hil-16m/stackchan_lifeos_phase1.bin
→ CONFIG_LIFEOS_HIL_TEST_MODE=y，flash args 为 --flash_size 16MB
→ image size 0x69670，1 MiB app 分区剩余 0x96990（约 59%）
→ SHA-256 `8d05c5359999e9ebbf7dbb231a8e70e945605df2588ff6052d5408eff944dd2d`
```

两种镜像均包含真实 StackChan HAL；只有 HIL 镜像暴露维护运动和反馈冻结
注入命令。普通 `make test` 仍只执行主机单元/回放/schema/Codex contract，
不会在没有明确设备许可时刷写。

## 目标 HAL 与安全路径

- `firmware/src/hal/stackchan/stackchan.cpp` 使用 ESP-IDF driver-ng I²C，
  避免与 `esp32-camera` 的旧 I²C driver 冲突。
- 舵机走 UART1 1 Mbps SCS 协议，启动反馈自检后 torque off；失败后无论
  readiness 标记如何都清除 torque 和 PY32 `VM_EN`。
- 触摸、IMU、接近、相机和显示均在图任务中低频采样/渲染；20 Hz safety
  task 独立读取反馈、检查 graph heartbeat、主机超时、急停、卡滞和限位。
- 相机使用官方 StackChan GC0308 DVP pinout 与外部 20 MHz XCLK；普通 Phase 1 感知只证明
  capture 元数据，Web 预览另有固定 JPEG 分块路径；不宣称人脸识别已完成。

## 设备与备份基线

已识别设备为 `/dev/cu.usbmodem1101`，MAC `1c:db:d4:ba:43:40`，ESP32-S3
rev 0.2，16 MiB flash。完整原厂备份：

```text
/Users/wangyu/Documents/Codex/2026-08-28/new-chat/work/hardware-backups/
  stackchan-1cdbd4ba4340-20260829-fullflash.bin
SHA-256: 669507af37296a09677b8b6ae831090a6cef357a634815011daf0f8622d11b35
```

0.4.6 生产镜像已在用户授权后刷入；实机 `hello/status` 显示
`camera_ready=true`、`fault=false`、`safety_faults=0`、`torque_enabled=false`，设备
保持 safe-idle。两个 SCS 舵机的实体运动验收仍不在本轮范围内。

## 历史实机日志摘要（2026-08-29）

```text
AW9523 external bus outputs p0=0x07 p1=0x83 bus_en=1 boost_en=1
PY32 VM_EN requested=1 readback=1 output=0x01
servo self-test vm=1 yaw_ping=0 yaw_feedback=0 pitch_ping=0 pitch_feedback=0 torque_off=0
LIFEOS_HIL_READY lifeos-phase1-0.2.0 motion=disabled board=degraded
```

SCS 适配与官方实现一致：UART1 / 1 Mbps / APB / TX6 / RX7 / ID1、ID2；
当前无回包更符合底座、线缆或舵机物理链路问题，不能用启动成功替代运动验收。

## Web 控制/摄像头预览增量构建（2026-08-31）

新增的 `command.camera_preview`、GC0308 YUV422→JPEG 转换、固定 QVGA JPEG 分块发送和
USB 输出互斥已用同一 ESP-IDF 5.5.4 工具链完成 production/HIL target build。生产镜像
已按授权刷入；HIL 镜像只构建、未刷写：

| profile | image | size | SHA-256 |
| --- | --- | ---: | --- |
| production | `firmware/idf/build/stackchan_lifeos_phase1.bin` | `0x676e0` | `823c8a28c5fc605a60cde936b36a5aec812cbb07e484a946e54511c3cebab173` |
| HIL | `firmware/idf/build-hil-16m/stackchan_lifeos_phase1.bin` | `0x69670` | `8d05c5359999e9ebbf7dbb231a8e70e945605df2588ff6052d5408eff944dd2d` |

0.4.6 生产镜像的真实摄像头帧、USB 带宽、HTTP MJPEG 和浏览器预览已在独立报告中
通过；HIL 镜像仍未刷写。实体运动、触摸、急停端到端时延和 8 小时 soak 不由本次
摄像头预览验收覆盖。

## 真实预览部署证据（2026-08-31）

- 写入三段生产镜像（bootloader、partition table、application）均返回
  `Hash of data verified`，随后硬复位。
- `tools/hil_usb_runner.py --port /dev/cu.usbmodem1101 --timeout 5` 返回
  `lifeos-phase1-0.4.6`、`camera_ready=true`、`fault=false`、`safe_idle=true`。
- `tools/web_bridge_real_server.py --enable-camera-preview` 运行时，HTTP MJPEG 6 秒收
  到 13 个完整 JPEG part；浏览器真实页面显示实时帧。完整记录见
  [`docs/web-control-camera-report.md`](web-control-camera-report.md)。
