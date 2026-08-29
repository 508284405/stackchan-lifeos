# Phase 1 ESP32-S3 硬件构建报告

日期：2026-08-29（第二轮：烧录与真实 HIL 会话）

## 环境探测

本机初始 shell 未发现可复用的 ESP-IDF 5.5.4 安装或其配套工具；本回合随后在 `work/esp-idf-5.5.4` 与 `work/idf-tools-5.5.4` 完成隔离安装：

| 工具 | 结果 |
| --- | --- |
| `idf.py` | 隔离 ESP-IDF v5.5.4，可用 |
| `cmake` | 隔离 CMake 3.30.5，可用 |
| `ninja` | 隔离 Ninja 1.11.1，可用 |
| `python3` | `/usr/bin/python3`，Python 3.9.6 |
| ESP-IDF 目录 | `work/esp-idf-5.5.4`（detached pinned checkout `735507283d5b2f9fb363a1901172dbd9e847945d`） |

未执行全局安装；所有工具均位于项目工作区的可回收隔离目录。构建使用 `IDF_COMPONENT_MANAGER=0`（项目无第三方 IDF component manifest）。

## 项目检查

目标工程位于 `firmware/idf`，目标为 `esp32s3`，并注册了以下固定核心源文件：

- `firmware/src/runtime/runtime.cpp`
- `firmware/src/behavior/behavior.cpp`
- `firmware/src/protocol/protocol.cpp`
- `firmware/src/phase1/controller.cpp`

上述源文件和 `lifeos/runtime/runtime.hpp` 均存在。`sdkconfig.defaults` 禁用 C++ exceptions/RTTI，启用 panic reboot；`app_main` 明确保持运动关闭，等待目标 HAL、自检和反馈校准。

## 执行记录

以下 target build 命令已执行并通过：

```sh
idf.py -C firmware/idf set-target esp32s3
idf.py -C firmware/idf build
idf.py -C firmware/idf size-components
```

证据：ESP32-S3 编译器完成 Ninja 构建；真机烧录并验证的 HIL image `stackchan_lifeos_phase1.bin` 为 `0x36240` 字节（SHA-256 `b48c482d1297d9c26265ebf5e5fd665253accf0e33fb3fabcad64d133fda642a`），1 MiB app 分区剩余 79%。提交后从同一源码重建的镜像尺寸一致（0x36240），SHA 变为 `1fa42c4f1b79996a516128a8e2fbf4d2f0b730cf5e45f3847bff1422e0499466`——差异仅来自镜像内嵌的 git describe 版本号与构建时间戳，代码相同。
工具链记录：ESP-IDF `v5.5.4`，checkout `735507283d5b2f9fb363a1901172dbd9e847945d`；CMake `3.30.5`；Ninja `1.11.1.git.kitware.jobserver-1`；Xtensa 工具链 `esp-14.2.0_20260121`。

## 烧录与目标机缺陷修复（真实硬件发现）

烧录前清单（端口/MAC/Flash 容量/备份 SHA-256）全部核对通过后执行 `idf.py -C firmware/idf flash`：bootloader(0x0)/分区表(0x8000)/app(0x10000) 三段 `Hash of data verified`。

首次真实启动即暴露三个 host 测试无法覆盖的缺陷，均已在本目录修复并复验：

1. **stdin 立即 EOF + newlib 锁断言 panic**：启动控制台 VFS 的 stdin 非阻塞读取使 `fgets` 立即返回 EOF，主循环未运行；`app_main` 返回后触发 `assert failed: check_lock_nonzero locks.c:319`。修复：`app_main.cpp` 输入循环改用 `usb_serial_jtag_read_bytes()` 驱动直读 + 有界行缓冲（`kMaxLineBytes` 上界语义保持，超长行由 `parse()` 以 `TooLarge` 拒绝）。
2. **主任务栈溢出（实测峰值 ≈44 KiB）**：`GatewayResult`/`ParseResult` 携带 8 KiB payload 边界缓冲，ingest 峰值超过 8/16/40 KiB 栈（backtrace + HWM 采样证实）。修复：`Gateway` 单例移至静态存储；`CONFIG_ESP_MAIN_TASK_STACK_SIZE=65536`。
3. **HIL runner 打开端口即复位**：macOS CDC-ACM open 断言 DTR → USB-JTAG 外设 `rst:0x15`，首条 hello 丢失。修复：`tools/hil_usb_runner.py` 增加启动 banner 宽限与幂等 hello 重试；命令语义不变（仍只发 hello + status，无 flash/reset/motion）。

## 硬件与发布门禁

- **PASS：** ESP-IDF 5.5.4 target build/size-components（含修复）。
- **PASS：** HIL 镜像烧录、启动、真实 USB 协议验证（详见 `hardware-acceptance-report.md`）。
- **PASS：** 设备恢复原始 16 MiB flash（读回 SHA 与备份一致，原固件 STATUS 复核）。
- **BLOCKED→已解除：** 上一轮的串口写审批拒绝与串口 open 权限阻塞在本环境未复现。
- **NOT TESTED：** StackChan/CoreS3 BSP、舵机反馈接线、限位和扭矩控制 HIL；急停 ≤50 ms、20 Hz 控制 tick、卡滞反馈冻结和 8 小时长稳。在这些证据补齐前，不宣称阶段 1 硬件出口已通过。

备份位置：`/Users/wangyu/Documents/Codex/2026-08-28/new-chat/work/hardware-backups/stackchan-1cdbd4ba4340-20260829-fullflash.bin`（SHA-256 `669507af37296a09677b8b6ae831090a6cef357a634815011daf0f8622d11b35`）。恢复前再次核对 `flash_id`、MAC 和端口；恢复命令必须显式指定该文件，不能使用未核验的 glob。
