# Firmware architecture spike

[中文版 (Chinese)](README.zh-CN.md)

This directory is a host-buildable C++17 reference for the StackChan body runtime.
It is deliberately **CGraph-shaped**, not an unmodified copy of upstream CGraph.

Upstream CGraph documents desktop/Android targets and contains a general-purpose
thread pool with pthread scheduling assumptions. Direct ESP-IDF integration is a
separate experiment; it must pass target compilation, memory, timing and soak-test
gates before production adoption. The initial stable reference is CGraph `v3.2.5`
(tag object `2caa1dfb379073981dfeae2936ed2ffb662e7c77`, commit
`f65ffdb952c9cce18b7903bf8c84f60f56d9160d`). The upstream ESP-IDF spike is
recorded in `docs/cgraph-spike-report.md` and is intentionally not adopted.

The production mapping is:

- one fixed FreeRTOS task dispatches the bounded business DAG;
- camera/audio/network retain dedicated I/O tasks;
- ISR, 20–100 Hz servo control and the safety watchdog remain outside the graph;
- graph nodes produce semantic behavior commands, never PWM/GPIO/I²C writes;
- the actuator driver clamps again before touching hardware.

The host test proves graph ordering, cycle rejection, command priority, TTL and
pitch/yaw limits. The ESP-IDF target build proves the selected StackChan HAL
compiles for CoreS3; only the supervised HIL runner can prove real-time timing,
feedback and physical safety behavior.
