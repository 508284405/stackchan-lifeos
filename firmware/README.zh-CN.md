# 固件架构验证(Spike)

本目录是一个可在宿主机(Host)上构建的 C++17 参考实现,用于 StackChan 身体运行时(body runtime)。
它刻意采用了 **CGraph 的结构形态**,并非上游 CGraph 的原样拷贝。

上游 CGraph 官方文档覆盖的是桌面端/Android 目标平台,并包含一个依赖 pthread 调度假设的
通用线程池。将其直接集成到 ESP-IDF 属于另一个独立实验:必须先通过目标平台编译、内存、
时序和长时浸泡测试(soak test)等门槛,才能在生产环境中采用。初始的稳定参考版本为
CGraph `v3.2.5`(tag 对象 `2caa1dfb379073981dfeae2936ed2ffb662e7c77`,commit
`f65ffdb952c9cce18b7903bf8c84f60f56d9160d`)。上游 ESP-IDF 验证实验记录在
`docs/cgraph-spike-report.md` 中,当前刻意未被采纳。

生产环境的映射关系如下:

- 由一个固定的 FreeRTOS 任务来调度有界的业务 DAG;
- 摄像头/音频/网络保留各自独立的 I/O 任务;
- ISR、20–100 Hz 舵机控制和安全看门狗(watchdog)保持在图之外;
- 图节点只产出语义化的行为指令,绝不直接产生 PWM/GPIO/I²C 写操作;
- 执行器驱动在接触硬件之前会再做一次钳位(clamp)保护。

宿主机测试用于验证图的执行顺序、环的拒绝(cycle rejection)、指令优先级、TTL 以及
俯仰/偏航(pitch/yaw)限位。ESP-IDF 目标平台构建用于验证所选的 StackChan HAL 能在
CoreS3 上通过编译;而实时时序、反馈和物理安全行为,只有受监督的 HIL 运行器才能证明。
