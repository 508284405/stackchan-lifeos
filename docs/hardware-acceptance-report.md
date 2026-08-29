# Phase 1 硬件验收报告

日期：2026-08-29（第二轮：烧录与真实 HIL 会话）
结论：**PARTIAL — target build、LifeOS HIL 镜像烧录、真实 USB 协议验证、有界真实 soak 均 PASS；设备已恢复原始固件。执行器/传感器安全指标 NOT TESTED。**

## 设备安全门禁（烧录前清单，全部核对通过）

| 项目 | 证据 |
| --- | --- |
| 端口 | `/dev/cu.usbmodem101`（USB Serial/JTAG，VID/PID 0x303A:0x1001） |
| MAC | `1c:db:d4:ba:43:40`（esptool chip_id 复核） |
| 芯片 | ESP32-S3 (QFN56) rev v0.2 |
| Flash 容量 | 16 MiB（esptool flash_id：Manufacturer 46 / Device 4018） |
| 备份 | 16 MiB 全片 `stackchan-1cdbd4ba4340-20260829-fullflash.bin`，SHA-256 `669507af37296a09677b8b6ae831090a6cef357a634815011daf0f8622d11b35` 复核通过 |
| 原固件基线 | 只读探测返回 `OK READY StackChan USB controller v1` |

烧录内容为运动关闭的 HIL 镜像（`motion_enabled=false`，无 M5Stack BSP、不驱动舵机）。全程未发送任何运动/急停命令，未手堵舵机，未触碰硬限位。

## 已执行证据（真实硬件）

```text
idf.py -C firmware/idf set-target esp32s3 && build && size-components
→ PASS（最终镜像 0x36390 字节，SHA-256 314f09fd6cd429e2a12c19bf543746a0a9781db511587994600accb5e1f4232e，app 分区剩余 79%；0x36350/a7214582 为会话重同步前的版本，0x36240/b48c482d 为 seq/detail 修复前的版本）
idf.py -C firmware/idf flash
→ PASS（bootloader 0x0 / 分区表 0x8000 / app 0x10000，三段 Hash of data verified）
启动 banner
→ `LIFEOS_HIL_READY lifeos-phase1-hil-0.1.0 motion=disabled`
python3 tools/hil_usb_runner.py --port /dev/cu.usbmodem101 --timeout 3
→ EXIT=0，hello.device（marker lifeos-phase1-hil-0.1.0、motion_enabled:false、MAC 匹配、heap_free 286668）
   + ack.command（status: completed, idempotent: true）
python3 -m unittest discover -s tests/hil/safety -v
→ PASS（host 侧安全场景单测）
PYTHONDONTWRITEBYTECODE=1 PYTEST_ADDOPTS='-p no:cacheprovider' make test
→ PASS（pytest 5、firmware/protocol host 测试、phase1 replay 7 项、schema 校验）
python3 tools/soak.py --duration-seconds 28800
→ PASS（mode=dry-run，确定性指标管线，576000 样本；非真实硬件长稳）
```

## 真实设备协议行为（受控只读探测，紧凑 JSONL）

| 场景 | 设备行为 | 判定 |
| --- | --- | --- |
| `hello.host`（含 device_id） | `hello.device`：marker `lifeos-phase1-hil-0.1.0`、`motion_enabled:false`、MAC 匹配、`heap_free` | PASS |
| `command.control(status)` | `ack.command`：`status:completed, idempotent:true`，含 `heap_free`/`uptime_ms` | PASS |
| 重复同一 command（相同 event_id） | `ack.command`：`status:duplicate`，不重复执行 | PASS |
| seq 跳号错误的原因透传 | `error.protocol`：`code:unauthorized, detail:sequence_rejected` | PASS |
| 未知 action 的原因透传 | `error.protocol`：`code:unsupported, detail:unsupported_action` | PASS |
| 重复 hello（相同 event_id） | 静默（hello 重复不产生输出，不重复处理） | PASS（按设计） |
| seq 跳号（9 after 2） | `error.protocol` 拒绝；随后合法 seq(3) 正常接受 | PASS |
| TTL 过期（expires_at_ms < now） | `error.protocol` 拒绝 | PASS |
| 未知 action（"reboot"） | `error.protocol` 拒绝，设备不复位、不动作 | PASS |
| 非法 JSON（截断行） | 静默拒绝（parse 错误按设计不回包），不崩溃 | PASS |
| 超长行（17 KB） | 有界丢弃，不崩溃，缓冲不越界 | PASS |
| 断开/重连（端口重新打开） | 设备重新可枚举、重新握手成功 | PASS |

注意：`error.protocol` 的 payload 统一为 `code:unauthorized`，不区分具体 ParseError 名（可诊断性改进项，不影响本轮判定）。

## 本轮发现并修复的目标机缺陷（host 测试无法覆盖）

1. **stdin 立即 EOF + newlib 锁断言**：IDF 启动控制台 VFS 对 USB Serial/JTAG 的 stdin 读取为非阻塞，`fgets` 立即得到 EOF 导致主循环从未运行、`app_main` 返回后触发 `check_lock_nonzero locks.c:319` 断言 panic 循环。修复：输入改用 `usb_serial_jtag_read_bytes()` 驱动直读 + 有界行缓冲，不再走 stdio stdin。
2. **主任务栈溢出（实测）**：gateway ingest 路径携带 8 KiB payload 边界缓冲的 parse/ack 临时对象，实测峰值约 44 KiB（8/16/40 KiB 栈均溢出，HWM 采样确认）。修复：`Gateway` 单例移至静态存储，`CONFIG_ESP_MAIN_TASK_STACK_SIZE=65536`（余量约 20 KiB）。
3. **HIL runner 打开端口即复位**：macOS CDC-ACM 驱动 open 时断言 DTR，经 USB-JTAG 外设触发 `rst:0x15 (USB_UART_CHIP_RESET)`，首条 hello 在重启期间丢失。修复：runner 增加启动 banner 宽限窗口与幂等 hello 重试（不改任何命令语义，仍为只读探针）。

## 有界真实 soak（10 分钟，只读命令）

`hello` + 每 2 秒一次 `command.control(status)`（唯一 event_id、严格递增 seq），全量原始串口日志留存（296 行，逐字节可复核）：

- 294 条 status 命令 → 294 个 `status:completed` ACK，0 丢失、0 错误、每命令恰好一个响应（296 行 = 1 hello + 294 ack + 1 会话初始 error）；
- `uptime_ms` 全程严格单调（4568 → 604329，覆盖 600 秒），日志中 0 次 `ESP-ROM` 复位、0 次 panic/assert —— 设备全程未复位；
- `heap_free` 全程恒定 286668，无增长趋势；
- **seq 字段异常已根因修复并复验**：soak 中观察到的 `seq` 重复/回绕（8→1、98→19→203）根因是 `serialize()` 中 `number_text(seq)` 与 `number_text(ts_ms)` 的视图共享同一 `numbers[32]` 暂存缓冲，后者 `to_chars` 覆盖前者数据——输出 seq 实为 ts_ms 的前几位数字（host 可复现，现有测试只做 roundtrip 不断言字段值故漏网）。修复为每数字独立缓冲后，真机复验：hello seq=1，40/40 status ACK 的设备 seq 严格递增 2..41，拒绝路径响应 seq 连续（42/43/44），正式 runner 报告 hello seq=1 / ack seq=2。
- **error 原因透传已实现并复验**：`make_error` 新增 `detail` 参数；`error.protocol` payload 现为 `{"code":"...","detail":"..."}`。真机验证：seq 跳号 → `{"code":"unauthorized","detail":"sequence_rejected"}`；未知 action → `{"code":"unsupported","detail":"unsupported_action"}` 且重复该 action 仅回 duplicate、不重复执行。`unsupported_kind→unsupported`、`queue_full→busy` 映射，其余保留 `unauthorized` 缺省。
- **会话重同步缺口已修复并复验**：按 `docs/protocol.md` 既定语义"seq 重连后从 hello 协商"，结构合法的 hello（类型 `hello.host`/`hello.device` 且 device_id 匹配）现在总是重建会话簿记（seq 窗口、去重窗口、hello 标记），安全状态（暂停/故障/扭矩锁存）不受影响；device_id 不匹配或类型非法的 hello 被拒绝且不复位。固件 `Gateway::ingest` 与主机回放模拟器（`simulator/phase1/replay.py`）两端对齐，各补 3 项回归测试。真机验证：活会话（hello+3 status）上 runner 第二次重连未复位设备直接 PASS（此前该场景必被 `sequence_rejected` 卡死）；单会话内错配 hello 被拒后会话保留、合法 hello 重同步后旧 seq 正确拒绝、新会话 seq2 正常执行。仍待后续：执行器命令上线时的显式时钟偏移/TTL 会话策略。

## PASS / BLOCKED / NOT TESTED

**PASS（真实证据）**

- ESP-IDF v5.5.4 ESP32-S3 target build + size-components（本目录产物）；
- HIL 镜像烧录（三段哈希校验）与启动；
- 真实 USB Serial/JTAG 上的 lifeos.v1 hello/status/ACK/seq/TTL/重复/非法输入/超长行/断线重连行为；
- host 全量测试（pytest、firmware/protocol、phase1 replay、schema、safety unittest）；
- 10 分钟真实设备只读协议 soak；
- 设备恢复原始固件并复核（见下）。

**BLOCKED（本机/环境）**

- 无。上一轮的串口 open 权限阻塞（`Operation not permitted`）与 flash 写审批拒绝在本环境不复现。

**NOT TESTED（无证据，不得宣称）**

- 真实舵机/触摸/相机/IMU（HIL 镜像不含 M5Stack BSP，`motion_enabled=false`）；
- 执行器急停时延（≤50 ms）、20 Hz 控制 tick、`FAULT_STALL`、反馈卡滞冻结、硬限位/越界拒绝；
- 连续 8 小时真实运行的热稳定性、heap/PSRAM 单调增长、执行器耐久；
- 扭矩释放时延与断电安全姿态。

## 恢复状态

- 原始 16 MiB flash 已用备份整片写回（`write_flash 0x0 <backup>`，Hash of data verified）；
- 读回复核：16 MiB 读回内容 SHA-256 与备份一致（`669507af…11b35`）；
- 原固件复核：只读探测返回 `OK READY StackChan USB controller v1`；
- 设备已恢复到验收前状态，LifeOS 镜像仅存在于 `firmware/idf/build/`（git 忽略，不入库）。

## 第二轮修复复验与恢复（2026-08-29）

修复提交后的镜像（0x36350 / a7214582…）经烧录清单复核后写入并完成上述 seq/detail 复验，随后再次整片恢复：16 MiB 读回与备份逐字节一致（SHA-256 `669507af…11b35`），原固件 STATUS 复核通过。

## 项目 Git 状态

- 目标仓库在 `2e4ed19` 基础上新增提交：同步开发副本 Phase 1 状态（内容镜像 `ebbadc3`）+ 本轮硬件会话提交（固件修复、runner 启动宽限、两份报告更新）；
- 未提交 `firmware/idf/build/`、`sdkconfig` 或任何本地工具链路径。
