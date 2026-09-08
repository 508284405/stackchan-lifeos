# Web Bridge W7 容量方法与基线

状态：方法基线（host/fake simulation），不是容量验收。

## 目的与边界

`tools/bridge_capacity.py` 是一个无外网、无真实设备、确定性且有界的 runner。它用
现有 `bridge.events.EventLog` 验证设备过滤、telemetry coalesce 和有界事件窗口，用
`bridge.persistence.SQLiteStore` 验证 in-memory batch recovery；batch fan-out 逐目标保留
online 成功与 offline 失败，聚合状态因此可以是 `partial`。单次运行的设备、telemetry、
fan-out、队列和事件窗口均有输入上限，避免无限内存或运行时间。

命令示例：

```bash
PYTHONDONTWRITEBYTECODE=1 PYTHONPATH=. python3 tools/bridge_capacity.py \
  --registered 8 --online 6 --active-control 2 --telemetry 5 \
  --batch-fanout 8 --queue-capacity 16 --event-log-capacity 32 --pretty
```

输出包括配置、人口模型、确定性的 logical workload 分位数（p50/p95/p99）、订阅过滤、
coalesce/drop、队列 peak/drop、batch partial 和恢复计数。`rss_bytes`、`cpu_percent`、
`event_loop_lag_ms` 以及真实 DB wall-time latency 在该 runner 中没有可靠测量，必须输出
`unknown`，不能用模拟常数伪造。`latency_ms` 明确标注为 logical workload units，不是
主机 wall-clock 性能。

## 参数与语义

| 参数 | 含义 |
| --- | --- |
| `registered` / `online` | 注册数 / 当前在线数；在线数不能超过注册数 |
| `active-control` | 正在控制的在线设备数 |
| `telemetry` | 每在线设备的 telemetry 样本数；同设备样本可 coalesce |
| `batch-fanout` | batch 目标数；在线目标 completed，离线目标 offline |
| `queue-capacity` / `event-log-capacity` | 有界队列和事件保留窗口 |
| `seed` | 记录在输出中；当前场景不依赖随机数，保证重复运行稳定 |

所有计数上限为 256，事件窗口至少为 8，并有总 logical-work 上限。拒绝关系错误和
超限输入；这只是防护上限，不是单实例容量结论。

## 运行与判读

定向测试：

```bash
PYTHONDONTWRITEBYTECODE=1 PYTHONPATH=. python3 -m pytest tests/bridge/test_capacity.py -q
```

本工具是容量方法/基线，不是“支持 200 台”的证明。真实 capacity/load/soak、真实设备、
网络、长时间 event-loop/RSS/CPU 行为仍为 **NOT TESTED**。未来报告必须附上当次 registered、
online、active-control、telemetry、fan-out、主机/版本、持续时间和原始 JSON，并将测得的
wall-clock 指标与本 logical baseline 分开；不得把模拟结果写成 W7 退出验收或 200 台 PASS。
