# Firmware architecture spike

This directory is a host-buildable C++17 reference for the StackChan body runtime.
It is deliberately **CGraph-shaped**, not an unmodified copy of upstream CGraph.

Upstream CGraph documents desktop/Android targets and contains a general-purpose
thread pool with pthread scheduling assumptions. Direct ESP-IDF integration is a
separate experiment; it must pass target compilation, memory, timing and soak-test
gates before production adoption. The initial stable reference is CGraph `v3.2.5`
(`2caa1dfb379073981dfeae2936ed2ffb662e7c77`).

The production mapping is:

- one fixed FreeRTOS task dispatches the bounded business DAG;
- camera/audio/network retain dedicated I/O tasks;
- ISR, 20–100 Hz servo control and the safety watchdog remain outside the graph;
- graph nodes produce semantic behavior commands, never PWM/GPIO/I²C writes;
- the actuator driver clamps again before touching hardware.

The host test proves graph ordering, cycle rejection, command priority, TTL and
pitch/yaw limits. It does not prove M5Stack BSP compatibility or real-time timing.

