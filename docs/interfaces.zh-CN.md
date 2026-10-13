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