# M3.2 临时门观察建议接口

M3.1 真实连续片段验证显示，0.5 秒采样能延续部分图像 track，但不会产生可信实体门身份。现有 M2 编号推理需要明确关联的 landmark，`ReplaySession` 又仅在 M2 `VERIFY_TARGET` 时自动选可见目标，形成冷启动限制。`VisualGuidanceController` 本身已经能处理临时 track；它不要求永久 landmark。

本接口提供独立的观察意图，供 M3.2 策略设计与离线评估使用。它不改变 M2 的导航语义，也不解除 M3 执行门禁。

## 证据层次

| 对象 | 含义 | 不能推出 |
| --- | --- | --- |
| detector candidate | 一帧中模型提出的 door/sign 框与分数 | 实体门、可通行空间、正确房号 |
| temporary track_id | 图像重叠与时间约束下的短期关联 | 永久门身份或跨遮挡重识别 |
| OCR reading | 从该 ROI 新识别的原文和置信度 | 文本一定正确、来源一定是门牌 |
| INSPECT_DOOR | 建议继续观察一个当前可见的临时 door 候选 | 房号证据、实体 landmark、移动许可 |
| physical_door_id / landmark_id | 有独立关联来源的实体身份 | 房号已经被人工确认 |
| operator_confirmation | 独立操作员明确确认已有 landmark 的标签 | 自动 OCR 可以生成该事件 |

## Python 接口

```python
from roomnav.inspection import propose_inspection

proposal = propose_inspection(
    frame_id, timestamp_s, visible_candidates,
    now_s=now_s,
    track_id=requested_track_id,  # 可选；要求这个 ID，丢失时不会改选别的 ID
)
```

返回 `InspectionProposal`：`intent` 为 `INSPECT_DOOR` 或 `STOP_REOBSERVE`，带该帧 `frame_id`、`timestamp_s`、临时 `track_id`（弃权时为空）、稳定 reason code，以及固定 `motion_authorized=false`。它不包含房号或永久身份字段。

规则：

- 所有候选必须来自指定同一帧和时间，ID 唯一；非法输入拒绝。
- 未来帧或超过 2 秒的陈旧帧弃权；离线虚拟时钟不测量真实处理延迟。
- 仅 door 类且有已知分数、score≥0.3 的候选可被建议。此阈值沿用控制器已有门槛，不代表校准后的正确率。
- 未指定 ID 时，恰有一个符合条件的候选才给出建议；多个候选不按面积、OCR 或房号猜测目标。
- 显式指定 ID 可在多个候选中选择一个；该 ID 不可见、类别不符或置信度不足时弃权，不自动转移到其他 track。
- proposal 是逐帧建议，不是已锁定目标；需要保持某一目标的调用者须在后续帧继续显式传入原 ID。

**分数和 track 连续性不足以验证门类别。** 实测连续片段中，墙面可被误报为 door 并稳定跟踪。因此本接口仅建议继续观察，不能直接授权控制器执行，也不能把模型候选升级为实体门。

## 离线回放接入

```bash
PYTHONPATH=src python -m roomnav.offline_cli process \
  --video /private/recording.mp4 --mode detector --target LSS119 \
  --weights /private/yoloe-11s-seg.pt --device cpu \
  --confidence 0.1 --image-size 640 --stride 15 \
  --inspection-proposals --output outputs/example-fresh-events.jsonl
```

`stride` 是原帧索引采样；VFR 视频如需固定真实时间间隔，应像本轮实验一样从 `probe_video` 的真实 PTS 选帧，再复用 `iter_video` / `perceive_frame` / `ReplaySession`。不能从 FPS 推导替代时间戳。

`--inspection-proposals` 在 header 记录 `inspection_proposals=true`，每个有效 frame output 增加独立 `inspection`，不调用 `controller.select_target`，不清除 halt，不产生反馈、不插入 M2 Observation。显式 STOP、detector error、已确认 FOUND 或仍有执行反馈待处理时，proposal 弃权。现有 `guidance` 安全规则继续适用；INSPECT_DOOR 不属于 M2 的编号推理动作。

直接构造 ReplaySession 时，省略该 header 字段可保留旧输出。相同 header、输入和代码应通过确定性 JSONL replay；伪造 `motion_authorized` 会导致 replay mismatch。

## OCR 非门牌上下文

新 detector CLI 日志默认记录 `ocr_context_policy="exclude_non_room_context_v2"`。回放在整块 ROI 的全部原始读数上检查负面上下文：

- 同一读数含 `/` 分隔的多个完整房号：directional_sign。
- v2 还处理 OCR 拆行：整块 ROI 存在多个不同的房号格式读数，并含 `Classrooms` 或 `LEVEL <数字>` 方向牌提示时，为 directional_sign。只有多个读数而无来源提示仍是 unverified。
- 包含独立词 `workshop`、`academic literacies` 或 `scientific writing`：poster。

这些 ROI 原文与 confidence 继续保留，但 `M1Session.ingest(..., source_context=...)` 返回 `rejected/non_room_sign_context`，不创建 room Observation，也不向 M2 投票。没有上述标记时仍为 `unverified`，并非已验证门牌。框中混入多个标志可能导致保守拒绝；这些有限规则可能漏掉无关键字的海报、单房号方向牌或截断文字，需要后续语义来源验证。

通用房号 parser 保持原样。`WORKSHOP 4` 能匹配通用字母数字格式，不等于门牌来源正确；`SS0`、视野边缘截断后的 `LSS11` 也可能语法合法。本次不通过硬编码 LSS 前缀、长度或视频 GT 伪造成功。

同一 capture/region 的 source_context 在一个 M1Session 内不能改变；先按 poster 拒绝后改为 unverified，或反向更改，都会拒绝且不改写原证据。重新审核来源需在独立新会话明确重处理。

旧日志省略 `ocr_context_policy` 时按旧语义回放；显式 v1 保留原有 slash/海报规则，v2 才加入拆行方向牌提示，保留历史可复现性。human_verified_boxes 仍单独依赖已审核框和可信帧绑定，不因 detector 上下文推测被转换或升级；synthetic 日志也必须继续明确标记。

`test_wall_false_positive_tracked_ten_frames_never_becomes_verified_door` 是以本地墙面误报为动机的十帧合成回归：高分、稳定 door 类候选仍可能收到建议，但每帧 motion_authorized=false，控制器目标为空、M3 停止，且没有 Observation、永久身份或 FOUND。它验证证据隔离，未声称模型已能区分墙和门；对应真实十帧的检查结果只保存在 ignored outputs。

## 执行状态

这一步实现了可验证、可弃权的临时观察建议与已知非门牌文本拦截。它没有实现自动实体门绑定、语义目标搜索、执行端安全授权、真实反馈或重识别。下一步可以进入 M3.2 的候选核验和选择策略开发；在这些条件完成之前，实际 detector 回放保持 STOP_REOBSERVE，不宣称自主导航或 M3.3 完成。
