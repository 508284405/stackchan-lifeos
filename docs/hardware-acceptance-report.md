# Phase 1 硬件验收报告

日期：2026-08-29
结论：**PARTIAL（target build + 现有固件无损 HIL PASS；LifeOS 镜像真实 HIL BLOCKED）**

## 设备安全门禁

本机枚举到 `/dev/cu.usbmodem101`，ROM 查询确认 ESP32-S3 rev 0.2、MAC `1c:db:d4:ba:43:40`、16 MiB flash；现有固件只读 `STATUS` 返回 `OK READY StackChan USB controller v1`。这闭合了当前设备的 CoreS3/StackChan 软件资产关联，但尚未把 LifeOS HIL 镜像写入设备。

## 已执行证据

```text
ESP-IDF v5.5.4 target build → PASS
idf.py size-components → PASS (0x33bb0 image, 80% app partition free)
esptool chip_id/flash_id/full read → PASS (read-only; backup SHA-256 669507af37296a09677b8b6ae831090a6cef357a634815011daf0f8622d11b35)
existing firmware STATUS → PASS
LifeOS HIL runner on `/dev/cu.usbmodem101` → BLOCKED (`Operation not permitted`; no bytes sent)
python3 -m unittest discover -s tests/hil/safety -v
→ PASS
python3 tools/soak.py --duration-seconds 28800
→ status=PASS, mode=dry-run, control_hz=20, samples=576000
```

dry-run 覆盖急停与扭矩关闭、暂停后反馈/运动冻结、反馈冻结故障、恢复、断线拒绝、越界拒绝和 8 小时等效 20 Hz 样本计数。采集器默认不等待八小时，因此该结果是确定性指标管线验证，不是热/电气/内存 HIL 证明。LifeOS HIL 镜像已构建但因写 flash 的系统审批在当前账户额度上限下被拒绝，未发送写入操作。

## 尚未通过（BLOCKED）

- LifeOS 镜像上的真实急停、暂停、反馈冻结、断线后 1500 ms 行为与扭矩释放；
- 真实硬限位/越界拒绝与 `FAULT_STALL`；
- 20 Hz tick、≤50 ms safety loop 的目标板时序；
- 连续 8 小时真实运行的崩溃、heap/PSRAM 单调增长和热稳定性。

恢复条件：在账户具备写 flash 审批后，使用已保存的 16 MiB 备份与 `firmware/idf/build/stackchan_lifeos_phase1.bin`，先执行无负载、可观测、随时可断电的协议场景；完成后恢复备份。禁止手堵舵机或触碰硬限位。

备份位置：`/Users/wangyu/Documents/Codex/2026-08-28/new-chat/work/hardware-backups/stackchan-1cdbd4ba4340-20260829-fullflash.bin`。恢复前再次核对 `flash_id`、MAC 和端口；恢复命令必须显式指定该文件，不能使用未核验的 glob。
