# RFC 0007: 手动方向控制的零运动预检

- 状态：Proposed
- 日期：2026-09-05
- 范围：为 `manual_control_v1` 增加可审计的“准备方向控制”路径；不放宽实际运动门禁。

## 问题

当前设备在 `PowerOff` 时不采样舵机反馈，而网页必须看到 400 ms 内的新鲜反馈才启用方向键。
因此设备处于安全断扭矩状态时，首个方向输入永远无法成为唤醒反馈的入口。

## 决策

新增高层 `control.preflight`，映射为 `lifeos.v1` 的
`command.control` / `{"action":"preflight"}`。设备通过 `manual_preflight_v1`
能力明确声明支持后，它只能请求以下固定流程：

```text
PowerOff
  -> VM_EN on
  -> 两次间隔位置反馈读取
  -> ReadyTorqueOff
  -> 持续空闲反馈采样
```

该流程不得写入目标角度、不得开启 torque、不得产生运动命令。任何反馈、供电、暂停、
故障、急停或链路异常都会保持或回到安全停止状态。

方向键只在预检成功、设备在线、反馈不冻结、反馈年龄不大于 400 ms、无暂停/故障/失联时启用。
首个真实方向输入仍由固件从刚读取的测得位置计算 2° 小步目标；浏览器不发送角度或底层参数。

## 实现边界

- Firmware：`ServoIoCore` 增加由 SafetyTask 消费的一次性预检请求，唯一的 Servo I/O
  任务执行 VM/反馈事务；`PoweringOn` 成功后停在 `ReadyTorqueOff`。
- Bridge：`control.preflight` 只映射到 allowlisted 的 `command.control`，使用
  `manual_preflight_v1` capability 与既有 `manual_control_v1` feature gate，保留命令审计和 TTL。
- Web：在方向键上方显示“准备方向控制”；它只在安全状态允许点击，成功后等待健康快照
  确认再解锁箭头。
- Contract/docs/tests：同步协议动作白名单、host/firmware/bridge/UI 测试和用户可见状态。

## 验收与停止条件

1. 预检成功时，两个反馈读数有效、`io_state=ready_torque_off`、`torque_enabled=false`，且
   `feedback_age_ms<=400`。
2. 任一预检失败不得输出目标或打开 torque；UI 继续禁用方向键。
3. 方向输入的 lease、10 Hz、300–500 ms TTL、失焦/断链 release、设备端硬软限位和
   SafetyLoop 不变。
4. 当前真实设备需在独立受监督 HIL 下验证预检和第一条方向输入。刷写实际设备前重新核验
   设备身份与恢复备份，并取得明确刷写授权。
