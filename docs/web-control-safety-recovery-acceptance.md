# Web 视频安全与维护恢复验收

日期：2026-09-17
需求基线：[RFC 0008](rfc/0008-web-control-safety-and-recovery.md)。

## 范围

本轮实现用户确认的视频新鲜度、失联停止、观看者生命周期、可恢复升级、批次暂停和设备
重置边界。真实刷写、分区迁移、签名信任配置、实体运动和断电 HIL 不由本轮本地开发授权。

## 验证状态

本轮实现已完成 host/fake 回归；目标构建、真实媒体/运动和断电恢复仍须独立验收，不能由
本地软件结果替代。

| 证据层 | 状态 | 范围 |
| --- | --- | --- |
| 既有 Brain 确定性回归 | PASS | Python 3.12：`brain/tests` 64 passed；不调用真实 provider。 |
| 新增 Bridge/API/恢复测试 | PASS | `make test`：Bridge 132 passed；覆盖显示确认、500 ms 帧龄拒绝、重复帧不续期、维护确认过期、签名镜像流式复核和 rollout 重启待确认。 |
| 固件 host 安全与恢复测试 | PASS | host C++ 测试通过，包含固件更新 begin/chunk/commit、校验失败和故障路径；不是物理 OTA。 |
| ESP-IDF production/HIL 构建 | NOT TESTED | 本轮 `IDF_PATH` 未配置，未取得目标构建证据。 |
| Web 构建/API fake 联调 | PASS | Vite production build 通过；FastAPI/TestClient 覆盖 viewer/display/lease 路径。 |
| 浏览器真实交互 E2E | NOT TESTED | 未运行 Playwright 或人工浏览器验收。 |
| 真实 provider | NOT TESTED | 本轮不调用。 |
| 真实媒体与连续手控 | BLOCKED | USB 枚举身份为 `1C:DB:D4:BA:43:40`，但 `/dev/cu.usbmodem2101` 只见 HIL 启动标记，零运动 hello 探测未收到 `hello.device`。 |
| 真实签名升级/断电回滚 | NOT TESTED | 需要可恢复布局、信任配置及独立硬件验收。 |

## 本轮验证命令

```sh
PYTHONDONTWRITEBYTECODE=1 PYTEST_ADDOPTS='-p no:cacheprovider' make test
PYTHONPATH=. python3 tools/scan_usb_devices.py --probe --timeout 2 --grace 3 --json
```

`make test` 还通过 Brain 64、Phase 1 unittest 10、模拟器 nominal replay、全部 JSON Schema、
离线 Sub2API contract 和离线 Codex protocol contract。USB 探测会复位 CDC 外设，但不发送
运动、维护或刷写命令；本轮探测结果为 `no-response`，因此没有执行实体运动、固件写入或
分区迁移。

## 必须保留的回归场景

| ID | 场景 | 断言 |
| --- | --- | --- |
| WC-01 | 写入/验证/首次启动各阶段失败 | 不破坏最后可用镜像；无不可信镜像启动；恢复保持停止。 |
| WC-02 | 本地自检成功、Bridge 离线/重启 | 设备保留新版本；主机任务待确认，不将本地接受误作远程完成。 |
| WC-03 | 视频过期、客户端不显示、旧帧迟到、释放请求丢失 | 撤销控制；设备本地过期机制仍能停止；恢复视频不自动运动。 |
| WC-04 | 控制连接失效后重新连接并提交旧/新动作 | 不自动 home，不重放；明确恢复前新的 Agent/自主动作也不能重新启动运动。 |
| WC-05 | 批次首个目标失败/结果不明、主机重启、明确恢复 | 未开始目标暂停；已成功目标不回滚；已开始目标独立收尾。 |
| WC-06 | 确认重放/过期/跨操作、设备恢复出厂 | 非法确认拒绝；重置只影响设备用户设置与配对，主机数据和审计保留。 |
| WC-07 | 多观看者中一个离开、最后一个离开、宽限期边界 | 有观看者时不误停；无人观看时有界停止；自动停止后须显式 start。 |
| WC-08 | 画面年龄 500 ms 边界、过期时钟映射、未来时间、旧 session | 未知或超过门限拒绝；接收时间/输入 TTL 不冒充实际采集年龄。 |

设备物理停止时限、时钟误差与视频/手控并发负载必须以真实时序证据单独验证；主机循环
周期、单元测试虚拟时钟或设备 ACK 均不能替代这些测量。
