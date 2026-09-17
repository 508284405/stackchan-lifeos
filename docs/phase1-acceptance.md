# Phase 1 验收入口

## 软件与目标构建

```sh
PYTHONDONTWRITEBYTECODE=1 PYTEST_ADDOPTS='-p no:cacheprovider' make test
IDF_PATH=/path/to/esp-idf-5.5.4 tools/build_target.sh production build size-components
IDF_PATH=/path/to/esp-idf-5.5.4 tools/build_target.sh hil build
```

主机回放器只使用 Python 标准库，不连接真实设备，也不执行 payload 中的
动作。它检查 `contracts/phase1/envelope.schema.json`、版本、TTL、重复
`event_id`、严格连续 seq、hello/断线行为、非法数字、yaw/pitch 硬限位和
故障清除门禁。

## 真实设备验收矩阵

真实运行必须使用 HIL 镜像、已核验的 `/dev/cu.usbmodemXXXX` 和显式
`--allow-hardware`；维护运动/故障命令只有在 `LIFEOS_HIL_TEST_MODE` 下编
译，并要求 `authorization=maintainer` 与 `test_mode=phase1`。

```sh
python3 tools/phase1_hil.py \
  --port /dev/cu.usbmodemXXXX \
  --allow-hardware \
  --require-touch \
  --require-mechanical-stall \
  --soak-seconds 28800
```

如果由自动化台架执行而没有人工触摸，省略 `--require-touch`；该模式仍会
验证命令暂停/回正和维护镜像的反馈冻结故障注入。

| 场景 | 通过条件 |
| --- | --- |
| 启动与自检 | `hello.device` 为 StackChan/CoreS3，motion enabled，启动 torque off |
| 触摸暂停 | 触摸持续时停止目标运动并释放 torque |
| 回正 | 合法 home 只在非暂停/非故障状态执行，pitch 保持 5–85° |
| 本地/协议清故障 | 长按本地触摸约 1.5 s，或带 `local_confirmation=true` 的控制命令，清除后仍须保持停止 |
| 急停 | torque off，状态锁存，端到端响应耗时 <100 ms；后续 home 被拒绝 |
| 反馈冻结/卡滞 | 先用 HIL 注入触发 `FAULT_STALL`；启用 `--require-mechanical-stall` 时再由操作者轻轻阻挡一次真实小幅运动，停止并锁存故障；清除注入后才能恢复 |
| 越界 | yaw ±90°、pitch 5–85° 之外不写舵机并锁存 hard-limit |
| 主机断开 | 1500 ms 内停止、释放 torque；重新 hello 只重协商会话，不清除安全状态 |
| 传感器 | Si12T、BMI270、LTR-553、GC0308 capture、ILI9342C 初始化/读取不崩溃 |
| 长稳 | 8 h 只读状态采样无复位，uptime 单调，heap/PSRAM 无增长趋势 |

## 当前验收状态

更新时间：2026-09-02。

阶段 0、软件/协议、安全逻辑、目标构建和板级初始化已通过；SCS 执行器闭环
子门禁也已通过，详见 [`docs/scs-runtime-io-acceptance.md`](scs-runtime-io-acceptance.md)。
但阶段 1 整体出口仍为 **BLOCKED/PARTIAL**，不能把“执行器闭环 PASS”写成完整阶段
通过：

- **PASS（历史 HIL/设备子集）**：真实位置反馈、运动、torque/VM 释放、pause、home、
  反馈冻结故障、硬限位、设备侧 1 ms 急停、clear_fault、断线后不自动恢复，以及
  host/unit/replay/build 验证；这些证据不等于当前 production 的整体出口。
- **PASS（本轮真实 production 只读）**：已核验 MAC `<device-mac>` 后刷入
  `lifeos-phase1-0.5.0`；hello/status、`motion_enabled=true`、
  `manual_control_v1` capability、`health.report` 和 `torque_enabled=false` 安全空闲状态通过。
- **PASS（本轮真实媒体）**：GC0308 QVGA JPEG 经 USB Bridge/MJPEG 到真实浏览器的连续帧
  与启停生命周期通过；35 秒收到 371 个完整 JPEG，实测 10.57 fps，`duration_ms=0`
  持续运行超过 30 秒；这不是实体运动证据。
- **PASS（本轮真实手控通道）**：Bridge 使用 `--enable-manual-control` 启动，设备声明
  `manual_control_v1`，浏览器方向键解除禁用，真实 WebSocket lease acquire/release 通过；
  为遵守实体安全门，本轮没有发送运动输入。
- **BLOCKED**：端到端急停当前主机观测约 209.6 ms，而本入口要求 `<100 ms`；设备侧
  VM_EN cut 为 1 ms，不能替代端到端指标。
- **NOT TESTED / BLOCKED**：production 实体 home、连续手动、实体运动轨迹和远程急停物理
  效果；还缺 `--require-touch` 触摸暂停/长按清故障、8 小时 soak、真实机械卡滞、断线
  瞬间到 power cut 的独立时序、传感器读取/GC0308 capture 的独立成功样本。

当前实机 production app SHA-256 为
`2b3a498ad44b9ec3d467bf92558a3a33fa11185f9ed289af384e1811d407774f`；对应 HIL app
`c16ec79d8eca4d7d32a9cab10ec9c6ff27be12ebf9da854441471ff9b5d4f7d6` 仅完成构建、未刷写。
上述主机回放、模拟器、媒体和执行器子门禁不能代替尚未完成的触摸、长稳、实体运动和
端到端时延证据。
