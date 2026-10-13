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

`observation_id` 与 `landmark_id` 已实现为可选字段；`detector_bbox`、`ocr_bbox`、`image_quality` 和 `camera_source` 仍待实现。

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

动作集合：`FOUND`、`VERIFY_TARGET`、`REOBSERVE`、`CONTINUE_ALONG_SEGMENT`、`CHECK_MISSING_OR_OTHER_SIDE`、`INSUFFICIENT_EVIDENCE`。只有明确记录的人工确认才会得到 `FOUND`；重复 OCR 疑似目标返回 `VERIFY_TARGET`。

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

**M2.2 已实现：** 规则基线已改为只依赖显式关联地标的候选或人工确认结果形成编号趋势；遇到 OCR 冲突时要求复查。视频流与规则模块尚未直接相连。

参考：[M2.1 设计与预训练模型复用](m2-memory.zh-CN.md)。

## M2.2 补充：人工确认与可审查的决策

操作员确认作为额外的 JSONL 事件输入（不是 OCR 自动产生）：

```json
{"landmark_id":"corridor_A:door04","text":"EB104","confirmed_by":"operator"}
```

使用 `memory.confirm_label("corridor_A:door04","EB104")` 记录。此操作**不改写**历史 OCR 文字。当前仅保留地标的**最新确认**，尚未实现确认事件的全量审计。

`Decision.to_dict()` 包含 `action`、`reason`、`facts`、`assumptions`、`uncertainties` 和 `evidence_landmark_ids`；命令行额外输出目标及样本数量。`facts` 指真实发生的观测/人工确认事件，不能把未知目标位置写成既定事实。

[完整规则、局限和示例命令](m2-rule-reasoning.zh-CN.md)。
