# Phase 1 ESP32-S3 硬件构建报告

日期：2026-08-29

## 环境探测

本机初始 shell 未发现可复用的 ESP-IDF 5.5.4 安装或其配套工具；本回合随后在 `work/esp-idf-5.5.4` 与 `work/idf-tools-5.5.4` 完成隔离安装：

| 工具 | 结果 |
| --- | --- |
| `idf.py` | 隔离 ESP-IDF v5.5.4，可用 |
| `cmake` | 隔离 CMake 3.30.5，可用 |
| `ninja` | 隔离 Ninja 1.11.1，可用 |
| `python3` | `/usr/bin/python3`，Python 3.9.6 |
| ESP-IDF 目录 | `work/esp-idf-5.5.4`（detached pinned checkout） |

未执行全局安装；所有工具均位于项目工作区的可回收隔离目录。

## 项目检查

目标工程位于 `firmware/idf`，目标为 `esp32s3`，并注册了以下固定核心源文件：

- `firmware/src/runtime/runtime.cpp`
- `firmware/src/behavior/behavior.cpp`
- `firmware/src/protocol/protocol.cpp`
- `firmware/src/phase1/controller.cpp`

上述源文件和 `lifeos/runtime/runtime.hpp` 均存在。`sdkconfig.defaults` 禁用 C++ exceptions/RTTI，并启用 panic reboot；`app_main` 明确保持运动关闭，等待目标 HAL、自检和反馈校准。

## 执行记录

已执行：

```sh
command -v idf.py; idf.py --version
command -v cmake; cmake --version
command -v ninja; ninja --version
g++ -std=c++17 -fno-exceptions -fno-rtti -Wall -Wextra -Werror -Wpedantic \
  -I firmware/include -c firmware/src/runtime/runtime.cpp -o /tmp/stackchan-runtime-test/report-runtime.o
```

结果：初始探测前三项工具均不可用；安装后 target 工具链和 host 固件核心均可用。

以下 target build 命令已执行并通过：

```sh
idf.py -C firmware/idf set-target esp32s3
idf.py -C firmware/idf build
idf.py -C firmware/idf size-components
```

证据：ESP32-S3 编译器完成 Ninja 构建；HIL image `stackchan_lifeos_phase1.bin` 为 `0x33bb0` 字节，1 MiB app 分区剩余 80%。构建过程中使用 `IDF_COMPONENT_MANAGER=0`，因为该项目没有第三方 IDF component manifest。
工具链记录：ESP-IDF `v5.5.4`，checkout `735507283d5b2f9fb363a1901172dbd9e847945d`；CMake `3.30.5`；Ninja `1.11.1.git.kitware.jobserver-1`。

## 硬件与发布门禁

- **PASS：** ESP-IDF 5.5.4 target build/size-components。
- **BLOCKED：** 烧录与启动日志；系统审批因账户额度上限拒绝写 flash，本回合未执行 flash。
- **BLOCKED：** StackChan/CoreS3 BSP、舵机反馈接线、限位和扭矩控制 HIL。
- **BLOCKED：** 急停 ≤50 ms、20 Hz 控制 tick、卡滞反馈冻结和 8 小时长稳证据。

在上述证据补齐前，不宣称 ESP32-S3 构建或阶段 1 硬件出口已通过。
