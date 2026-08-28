# Phase 1 验收入口与硬件阻塞项

## 可执行回放

回放器只使用 Python 标准库，不连接真实设备，也不执行 payload 中的任何动作：

```sh
python3 -m unittest discover -s tests/phase1 -v
python3 -m simulator.phase1.replay simulator/phase1/scenarios/nominal.jsonl
```

JSONL 每行一个 `lifeos.v1` envelope。回放器在解析前拒绝超过 16 KiB 的行，检查 `contracts/phase1/envelope.schema.json` 对应的字段、版本、枚举、TTL、重复 `event_id`、严格连续 seq、hello/断线行为、非法数字和 yaw/pitch 硬限位。断线后普通命令拒绝，本地/协议急停仍可执行；重复 ID 只返回 `duplicate`，不重复执行。

## 场景矩阵

| 场景 | 自动回放断言 | 硬件/HIL 仍需证明 |
| --- | --- | --- |
| 重复命令 | duplicate，无二次副作用 | 实际舵机只产生一次动作 |
| 乱序/回退 | seq 回退拒绝 | USB CDC 丢包/重连时序 |
| 过期 TTL | expired，不输出动作 | 设备 monotonic clock 在重启后行为 |
| 断线 | 普通命令拒绝，急停可用 | 拔线 2 s 后真实扭矩关闭 |
| 越界/NaN/超长 | schema 或 safety 拒绝 | 真实 PWM 永不越过硬限位 |
| 卡滞/反馈冻结 | 记录 safety stop | 舵机反馈冻结触发 `FAULT_STALL` |
| executor 卡死 | 回放不依赖 graph | safety loop ≤50 ms 仍急停 |

## BLOCKED 硬件清单

以下项目在没有明确的 StackChan/CoreS3 样机、舵机反馈接线和可控故障注入前保持 **BLOCKED**，不能写入“阶段 1 已通过”：

1. 真实急停、触摸暂停、回正和本地清故障操作。
2. 舵机卡滞/反馈冻结、硬限位和扭矩释放的 HIL 证据。
3. 主机断开 1500 ms、设备重启、重新 hello/nonce 后旧命令不重放。
4. 20 Hz 控制 tick、≤50 ms safety loop、串口队列和 heap/PSRAM 预算。
5. 连续 8 小时运行的崩溃、硬限位越界和动态内存趋势报告。

自动回放通过只证明契约和可重复的软件拒绝路径；不能替代 ESP-IDF 编译、真实硬件或 HIL 证据。
