# ADR 0002：设备端采用 CGraph-shaped selective-port

- 状态：Accepted for V1
- 日期：2026-08-29
- 决策者：StackChan LifeOS maintainers

## 背景

用户要求嵌入式固件采用 CGraph 架构。CGraph 上游提供 C++ DAG/流图抽象，但官方资料没有声明 ESP32/ESP-IDF 作为支持目标；上游 `main` 的 README/CMake 版本也可能随时间变化。将未验证的上游直接加入固件会把编译、内存和调度风险带入安全路径。

## 决策

V1 采用 CGraph-shaped / selective-port：在 ESP-IDF 内实现 LifeOS 所需的最小节点、边、静态 DAG 和单 executor 抽象，接口和命名参考 CGraph，但不宣称是未经修改上游的完整移植。固定周期 safety loop 在图外运行；普通行为图是静态、有界、固定内存的 DAG。

若后续采用上游代码，只能从锁定 tag/commit 的小子集开始，并先通过独立 ESP-IDF spike。建议初步评估 `v3.2.5`，不跟随 `main`。所有补丁、工具链、组件版本和许可证信息进入构建记录。

## 设计约束

- 一个 FreeRTOS executor 顺序执行普通图，V1 不引入未经验证的线程池。
- 图节点不得阻塞等待网络、Codex、串口或无限队列。
- 图消息为固定上限结构体；禁止在热路径动态分配图像大小的缓冲区。
- 急停、硬/软限位、看门狗和故障锁存不依赖 CGraph；safety loop 可直接切断运动输出。
- GPIO、PWM、framebuffer、I2S 等通过 LifeOS domain interface 注入，图节点不直接调用底层驱动。

## 被拒绝的选项

- 直接把上游 CGraph 当作 ESP32 已支持组件：官方未声明该兼容性，证据不足。
- 把 safety loop 放入 DAG：图调度、节点阻塞或 executor 异常时可能延迟急停。
- 在设备侧实现完整通用数据流/线程池：超出 V1 需求，增加资源和实时性不确定性。
- 为了“看起来像 CGraph”复制全部上游 API：复制面越大，越难验证、维护和同步。

## 后果

正面：保留用户要求的图架构，同时把硬件实时性和安全性置于可验证边界内；可以先用 host fake adapter 做图测试。负面：V1 不是完整 CGraph 上游，不能直接声称 ABI/API 兼容；未来若升级为上游子集，需维护移植层和 spike。

## 集成 spike 的通过标准

- 固定 tag/commit 的最小 DAG 可由当前 ESP-IDF/编译器编译。
- 单 executor 的最大周期、栈、heap/PSRAM 占用有实测预算。
- executor 卡死或节点超时不会阻塞 safety loop。
- 断线、重复 command、非法节点输出和急停测试通过。
- 生成可审计的补丁、依赖、许可证和回滚记录。

## 参考

- [CGraph 官方仓库与 README](https://github.com/ChunelFeng/CGraph)
- [CGraph 官方编译说明](https://github.com/ChunelFeng/CGraph/blob/main/COMPILE.md)
- [ESP-IDF 官方文档](https://docs.espressif.com/projects/esp-idf/en/latest/esp32s3/)
