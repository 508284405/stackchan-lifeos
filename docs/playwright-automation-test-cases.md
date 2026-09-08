# Web Bridge Playwright 黑盒用例

日期：2026-08-30  
目标服务：本机 fake-device Web Bridge；所有断线/错误注入均通过测试服务边界完成。

## 用例矩阵

| ID | 类别 | 用户动作 | 预期 |
| --- | --- | --- | --- |
| PW-SMOKE-01 | Smoke | 打开 `/` | 标题、Overview、Devices、Recent activity 和 Bridge online 可见 |
| PW-SMOKE-02 | Smoke | 使用键盘 Tab/Enter 到设备列表 | 设备行可聚焦、可选择，选中设备详情可见 |
| PW-MAIN-01 | Main flow | 查看 fake device 并过滤名称/ID | 设备计数、选中详情、online/fresh/session/health/能力信息一致 |
| PW-MAIN-02 | Main flow | 刷新 snapshot，观察事件流 | 不出现 raw wire；事件 timeline 保留 command/session/telemetry 语义 |
| PW-ERROR-01 | Error injection | 让测试服务断开设备 session | 页面显示 offline/attention，不把 stale 当 healthy |
| PW-ERROR-02 | Recovery | 恢复 fake session | 页面通过 WebSocket/REST snapshot 恢复 online，设备身份不改变 |
| PW-ACCESS-01 | Accessibility | 以 390px viewport 检查布局和可见焦点 | 无横向溢出，关键状态有文字而非只用颜色 |

## 黑盒约束

- 只使用页面可见文字、ARIA role/name、label、placeholder 和稳定 DOM 语义。
- 不读取 `web/app.js` 内部 state，不导入前端模块，不调用私有 helper。
- 网络/断线状态只通过真实 HTTP、WebSocket 和测试服务的公开注入 endpoint 观察。
- screenshot/trace 产物放在 `output/playwright/`；结果必须附命令和分类。

## 通过标准

Smoke、主流程和可访问性用例通过；错误注入至少证明 offline/stale 与恢复状态不乐观；
任何失败都要区分测试问题、服务注入问题、产品缺陷和环境阻塞。
