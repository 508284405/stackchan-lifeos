# Simulator

首版模拟器复用 `brain` 的 CLI，将版本化 `DeviceEvent` 输入认知图并输出 `CognitiveDecision` 与高层设备命令。后续加入从 USB/WebSocket JSONL 日志回放、虚拟时钟、故障注入与 320×240 虚拟脸。

模拟器禁止绕过与真机相同的 Schema Validator、TTL、去重和 Behavior Arbiter。

