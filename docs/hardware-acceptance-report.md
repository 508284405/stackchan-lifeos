# Phase 1 硬件验收报告

历史快照日期：2026-08-29。当前执行器子门禁请以
[`docs/scs-runtime-io-acceptance.md`](scs-runtime-io-acceptance.md) 为准。

## 历史结论（2026-08-29）

**阶段 0 PASS；阶段 1 软件、目标构建和板级初始化 PASS；真实执行器 HIL
BLOCKED。** HIL 镜像已经成功刷入并稳定启动，但两个 SCS 舵机在官方电源、
UART 和协议路径均已确认后仍无任何回包。为安全起见，设备保持
`motion=disabled`、`torque=off`，因此不能把阶段 1 的运动、急停、故障注入、
触摸验收和 8 小时长稳写成整体 PASS。

## 已通过的证据

- 设备身份：`/dev/cu.usbmodemXXXX`，VID/PID `0x303A:0x1001`，MAC
  `<device-mac>`，ESP32-S3 rev 0.2，16 MiB flash。
- 原厂恢复资料已核验：
  `<secure-backup-dir>/stackchan-device-20260829-fullflash.bin`，SHA-256
  `669507af37296a09677b8b6ae831090a6cef357a634815011daf0f8622d11b35`。
- 主机 `make test`：brain pytest、firmware C++、protocol、Phase 1 replay、
  schema、Codex contract 全部通过。
- CGraph v3.2.5 upstream ESP-IDF spike 的失败边界已记录；LifeOS
  CGraph-shaped selective port 的 ESP32-S3 target build 通过。
- 生产/HIL 镜像均在 ESP-IDF v5.5.4 / ESP32-S3 上编译通过；HIL 镜像启用
  `CONFIG_LIFEOS_HIL_TEST_MODE=y`，生产镜像未启用；两个 profile 都显式使用
  `--flash_size 16MB`。
- 当前 HIL 镜像写入成功：bootloader、app、partition table 三段均输出
  `Hash of data verified`；当前 app SHA-256 为
  `faa17db192a0dd8d70e4901b69b3493ddbb407b2cb92a9c2f73a403972778e74`，大小
  `401936` bytes。
- 启动稳定：PSRAM 8 MiB memory test、GC0308 320×240 YUV422、Si12T/FT6336U、
  BMI270、LTR-553、AW9523/LCD 初始化均完成，没有 panic 或重启循环。

## 舵机阻塞的最新启动证据

```text
I (...) lifeos-stackchan: PY32L020 address=0x6f version=0x41 ready=1
I (...) lifeos-stackchan: AW9523 external bus outputs p0=0x07 p1=0x83 bus_en=1 boost_en=1
I (...) lifeos-stackchan: PY32 VM_EN requested=1 readback=1 output=0x01
I (...) lifeos-stackchan: servo self-test vm=1 yaw_ping=0 yaw_feedback=0 pitch_ping=0 pitch_feedback=0 torque_off=0 raw=460/620
I (...) lifeos-stackchan: PY32 VM_EN requested=0 readback=1 output=0x00
LIFEOS_HIL_READY lifeos-phase1-0.2.0 motion=disabled board=degraded
```

舵机路径已经与官方 StackChan 实现逐项对齐：UART1、1 Mbps、APB clock、TX
GPIO6/RX GPIO7、SCS ping/read packet、ID1 yaw/ID2 pitch、zero raw 460/620。
当前证据说明主控能正常启动、AW9523/PY32 控制寄存器读回正确，但舵机总线在
实际设备上无响应；尚不能区分底座未插合、舵机线缆/连接器问题和舵机本体故障。

## 继续验收前的实体检查

在保持设备断电时确认：

1. CoreS3 已完全插入 StackChan 底座，头部/底座没有半插状态。
2. 底座到两个舵机的连接器均已插牢，没有被外壳挤压或插反。
3. 设备放在稳定平面，不要手动扭动头部；重新上电后观察两个舵机是否能被
   自检 ping 到。

检查完成后再次运行：

```sh
python3 tools/phase1_hil.py \
  --port /dev/cu.usbmodemXXXX \
  --allow-hardware \
  --require-touch \
  --soak-seconds 28800
```

只有当启动 `motion_enabled=true` 且脚本完整输出 `status=PASS`，才可验收
触摸暂停/长按清故障、回正、急停时延、反馈冻结、越界、断线释放和 8 小时
长稳。真实机械卡滞不能通过手堵舵机替代；反馈冻结注入只用于 HIL 镜像。

## 官方控制镜像对照

为区分设备故障与 LifeOS 适配故障，已将之前的官方 USB 控制镜像临时写入，
并使用原测试脚本执行：

```text
STATUS -> OK READY StackChan USB controller v1
X 10   -> OK X 10 degrees
Y 50   -> OK Y 50 degrees
HOME   -> OK HOME
```

复测时使用更明显但仍在限制内的动作，结果为：

```text
X 25   -> OK X 25 degrees
Y 60   -> OK Y 60 degrees
HOME   -> OK HOME / OK HOME 0 degrees
```

随后已恢复本项目 HIL 镜像并通过三段 flash 哈希校验。上述结果证明官方
控制固件的 USB 命令路径可用；官方脚本只返回命令接受结果，不读取位置反馈，
是否确实左右/上下转动仍以现场观察为准。若现场确实转动，则设备本体和
舵机总线基本正常，剩余问题集中在 LifeOS SCS 自检/反馈适配路径。

## 恢复原厂固件

如需恢复，先确认上方备份 SHA-256，再进入 ROM download mode 后执行：

```sh
python -m esptool --chip esp32s3 --port /dev/cu.usbmodemXXXX \
  --before no_reset --after hard_reset write_flash \
  --flash_mode dio --flash_size 16MB --flash_freq 80m \
  0x0 <secure-backup-dir>/stackchan-device-20260829-fullflash.bin
```
