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
## 1F 静态场景候选路线（M1 接入）

M1 可以通过 `roomnav.floor_scene.FloorScene` 查询透视校正后的 1F 场景：

```python
from roomnav.floor_scene import FloorScene
scene = FloorScene()
ctx = scene.observation_context("LSS119")
```

返回值包含房间节点、候选门、候选路线和校正底图路径。候选路线只用于观察顺序和策略实验；门连接未验证前，执行策略必须拒绝直接导航。

地图来源固定为：

```text
data/maps/1f/floor_1f_base_modified.tif
```

不要把 `IMG_3167.HEIC` 或未校正的 `floor_1f_base.png` 用作路线几何来源。

M1 场景桥接示例：

```python
from roomnav.m1_scene_bridge import M1SceneBridge
bridge = M1SceneBridge()
enriched = bridge.enrich(m1_observation)
```

`enriched["scene_match"]` 只添加静态场景候选，不修改 `text`、`frame_id` 或 `landmark_id`，也不会把候选门标记为已验证。
