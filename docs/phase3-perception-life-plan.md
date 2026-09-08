# 阶段 3 任务计划：感知与生命感

- 状态：Host-only implementation complete / physical media gate not tested
- 日期：2026-08-30
- 范围：稳定目标选择、有限主动行为、情绪/表情语义、语音请求边界、长期记忆策略、安静时段和用户偏好。
- 上游门禁：阶段 1 整体出口仍为 `BLOCKED/PARTIAL`；阶段 3 只验收主机策略和确定性回放，不启用真实相机、麦克风、音频传输或持续运动。

## 1. 设计边界

阶段 3 产生的是有界的语义结果，不产生 PWM、GPIO、I²C、原始舵机角度、原始媒体或设备 wire envelope。目标跟踪只保存有限状态；短时目标丢失可以保持，达到期限后必须停止搜索。主动行为必须同时受频率、冷却、持续时长、安静时段和用户开关约束。

原始图像、音频、帧、二进制和凭据默认不得进入长期记忆。记忆写入必须有可解释原因和来源 run ID，读取只返回摘要，删除保留删除审计而不复制被删内容。设备断链时只保留本地无语音反射，不排队等待重连。

语音适配在本阶段止于 `VoiceRequest` 校验；真正音频 driver/media transport 需要独立硬件、带宽和隐私验收后才可启用。

## 2. 任务与进度

| ID | 任务 | 当前状态 | 证据 |
| --- | --- | --- | --- |
| P3-001 | 有限 presence target 状态机：tracked/holding/lost | PASS | `brain/target.py`；`brain/tests/test_phase3.py` |
| P3-002 | 目标保持、丢失截止和停止搜索 | PASS | `TargetSelector.search_state()` 与阶段 3 回归 |
| P3-003 | 主动行为频率、冷却、持续时长 | PASS | `ProactivePolicy.can_emit()` 与阶段 3 回归 |
| P3-004 | quiet hours 与 proactive 用户开关 | PASS | `brain/preferences.py` 与阶段 3 回归 |
| P3-005 | mood 到语义 `BehaviorIntent` 映射 | PASS | `brain/emotion.py` 与阶段 3 回归 |
| P3-006 | 有界语音请求（文本、语言、voice、TTL），断链不排队 | PASS | `brain/voice.py` 与阶段 3 回归 |
| P3-007 | 可解释、可删除、可审计的长期摘要记忆 | PASS | `brain/store.py` 与阶段 3 回归 |
| P3-008 | 真实感知/音频/媒体/HIL | NOT TESTED | 独立硬件与媒体门禁尚未满足 |

## 3. 验收矩阵

| 场景 | 期望 | 结果 |
| --- | --- | --- |
| presence 短暂丢失 | 在保持窗口内可保持目标 | PASS |
| presence 超过 loss deadline | 状态变为 `lost`，禁止继续搜索/追踪 | PASS |
| 达到主动频率或冷却上限 | 不产生新的主动提案 | PASS |
| quiet hours 或用户关闭 proactive | 不产生主动行为 | PASS |
| 情绪表达 | 只有注册语义行为，无 hardware fields | PASS |
| 语音请求越界/非法 voice/language/TTL | 拒绝；断链不入队 | PASS |
| 记忆写入 raw media/二进制/凭据 | 拒绝；合法摘要可删除且留审计 | PASS |
| 设备断链 | 降级为本地无语音反射，不搜索、不重复播报 | PASS（host-only） |
| 真实摄像头、麦克风、音频播放、机械追踪 | 需真实设备证据 | NOT TESTED |

## 4. 运行入口

```bash
PYTHONDONTWRITEBYTECODE=1 PYTEST_ADDOPTS='-p no:cacheprovider' \
  PYTHONPATH=. python3 -m pytest brain/tests/test_phase3.py brain/tests/test_phase2_3_hardening.py -q
```

阶段 3 的主机 PASS 不替代阶段 1 的真实执行器、触摸、卡滞、传感器、长稳或端到端 HIL 证据。

