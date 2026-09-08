# CGraph ESP-IDF spike record

日期：2026-08-29

## 固定输入

- 上游：CGraph `v3.2.5`，tag object `2caa1dfb379073981dfeae2936ed2ffb662e7c77`，实际 commit `f65ffdb952c9cce18b7903bf8c84f60f56d9160d`。
- 目标工具链：ESP-IDF `v5.5.4`，ESP32-S3 Xtensa GCC `esp-14.2.0_20260121`。
- 上游源码只放在临时目录 `/private/tmp/stackchan-cgraph-v3.2.5`，没有作为生产依赖复制进固件。

## 试验结果

| 试验 | 结果 | 证据 |
| --- | --- | --- |
| 上游主机 CMake/tutorial 编译 | PASS | `cmake -S /private/tmp/stackchan-cgraph-v3.2.5 -B /private/tmp/stackchan-cgraph-v3.2.5-build -DCGRAPH_BUILD_TUTORIALS=ON -DCGRAPH_BUILD_EXAMPLES=OFF`；`T00-HelloCGraph` 链接成功。 |
| 用 ESP32-S3 Xtensa 工具链编译上游最小教程 | FAIL（不可直接采用） | 编译 `tutorial/T00-HelloCGraph.cpp` 失败：`UThreadBase.h:184:25: error: 'pthread_setschedparam' was not declared in this scope`。 |
| LifeOS selective-port 目标构建 | PASS | `idf.py -C firmware/idf -B firmware/idf/build-hil-16m build`；ESP-IDF 5.5.4/ESP32-S3 镜像生成成功。 |

## 决策

阶段 0 spike 已明确失败边界：上游 CGraph 在桌面目标可编译，但其线程调度实现不能直接进入 ESP-IDF/ESP32-S3。阶段 1 保留本地 `CGraph-shaped` 固定容量 `StaticGraph`/`StaticExecutor`，将 20 ms safety task 和执行器驱动置于图外；不引入 pthread、线程池或未经验证的上游调度器。

后续若重新评估上游，只能在独立分支中固定新的 tag/commit，并重新完成目标编译、静态内存、单 executor、故障停止和许可证检查。
