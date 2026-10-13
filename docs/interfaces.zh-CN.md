# 感知与推理模块接口 v0.1

## Perception -> Reasoning

```json
{
  "kind": "room",
  "text": "EB102",
  "segment_id": "corridor_A",
  "side": "left",
  "timestamp_s": 4.2,
  "confidence": 0.91,
  "frame_id": "frame_00001"
}
```

字段说明：

- `text`：OCR 识别出的原始门牌文字，**不是**经推理补全的房间编号。
- `segment_id`、`side`：手持原型允许人工录入的拓扑信息，**不是** SLAM 地图坐标。
- `confidence`：模型原始分数，可缺省；除非已校准，否则不作为正确率的概率解释。
- `timestamp_s`：相对于实验记录起点的时间；可缺省。
- `frame_id`：对应照片/视频帧的追溯标识，不保存个人信息。
- `kind`: `room` 或 `sign`；方向牌可以保存原始文本如 `EB100-120 ->`，解析待实现。

建议后续新增 `detector_bbox`、`ocr_bbox`、`observation_id`、`image_quality` 和 `camera_source`，但不要把新字段当成已实现功能。

## Reasoning -> Human

```json
{
  "action": "CONTINUE_ALONG_SEGMENT",
  "reason": "Observed EB102 -> EB103; EB104 may lie ahead if numbering remains locally monotonic",
  "assumptions": ["OCR is correct", "Room numbering trend continues"],
  "target": "EB104"
}
```

控制输出属于**人的行动建议**，不是 ROS 2 `NavigateToPose` 指令。

动作集合：`FOUND`、`CONTINUE_ALONG_SEGMENT`、`CHECK_MISSING_OR_OTHER_SIDE`、`INSUFFICIENT_EVIDENCE`。

## M2.1 补充：原始观测与地标关联

新增两个**可选字段**，老格式仍可读取：

- `observation_id`：单场实验内唯一的观测事件 ID，例如 `run01:frame012:roi0`。网络重传相同 ID 和相同内容时跳过；相同 ID 携带不同内容时拒绝。
- `landmark_id`：由**人工标注或未来的视频跟踪模块明确提供**的同一物理门牌关联 ID，例如 `run01:corridor_A:door02`。仅仅 OCR 读到了同样的 `EB103`，**不能据此自动认定两帧是同一扇门**。

未提供 `landmark_id` 的观测会原样保留，但不会自动创建或归并真实地标。若同一个关联 ID 对应不同走廊段或不同地标类别，Memory 会拒绝合并。

`LandmarkMemory.to_reasoning_context()` 额外提供可 JSON 序列化的地标摘要：

```json
{
  "raw_observation_count": 4,
  "unassociated_observation_count": 0,
  "landmarks": [
    {
      "landmark_id": "run01:corridor_A:door02",
      "kind": "room",
      "segment_id": "corridor_A",
      "side": "left",
      "candidate_text": "EB103",
      "label_votes": {"EB103": 3, "EB108": 1},
      "status": "conflicted",
      "observation_count": 4,
      "first_timestamp_s": 12.5,
      "last_timestamp_s": 15.5
    }
  ]
}
```

这里的 `candidate_text` 只是 OCR 票数最多的候选文本，不等于已经确认的真实房间编号。票数相同时返回 `null`；`consistent` 也仅指多次识别输出一致，不是人工核实。左右侧的定义必须使用一致的走廊参考方向；返回时观察方向反转应标注 `unknown` 或统一坐标规则。

**M2.2 重要待办：** 当前独立规则基线仍通过 `rooms()` 读取原始观测，尚未按上述冲突状态筛选证据。接入实时 OCR 搜索之前，必须让 Planner 区分原始观测、候选地标与确认事实，不能将噪声 OCR 直接当作目标已找到。

参考：[M2.1 设计与预训练模型复用](m2-memory.zh-CN.md)。
