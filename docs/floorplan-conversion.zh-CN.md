# 1F 平面图转机器人地图：独立地图制作参考

本文说明如何把一楼疏散图转换为 RGB-D + VLM + ROS 2/Nav2 可使用的地图。平面图是**静态先验**；OCR、RGB-D 和 SLAM 是现场观测。不能用房间编号推断物理门已经被看见，也不能把像素坐标直接当成机器人位姿。

**适用边界：** 这是独立地图工具及未来运动执行实验的参考，不是当前 RGB 寻房的接入流程。当前感知与 M2 高层搜索只使用 RGB 观测证据，不能查询预标注房号位置、候选门坐标或地图路线来选择语义目标或安排观察顺序。本次 M1 → M2.4 现场回放不调用这些地图工具；未来 SLAM/Nav2 仅承担运动执行，不能向高层搜索泄露目标房间位置。RGB-D/地图语义检索实验如另行开展，需单独记录协议与结果。

## 目标文件

建议最终形成以下文件：

```text
data/maps/1f/
  floor_1f_rectified.png       # 校正后的平面图
  floor_1f.gpkg                # QGIS 权威矢量图层
  floor_1f_semantic.json       # VLM/检索使用
  floor_1f_topology.json       # 门级拓扑，含验证状态
  floor_1f_nav.png             # Nav2 栅格图
  floor_1f_nav.yaml            # Nav2 地图参数
  README.md                    # 数据来源、测量和版本
```

## 第一步：准备可校正的底图

优先级如下：

1. 原始 CAD/DWG/PDF 或正射扫描；
2. 手机正对图纸拍摄的高分辨率照片；
3. 当前斜拍照片，只作为临时底图。

如果只有照片，先裁剪出完整平面图，再选取图纸外框的四个角点做透视校正。OpenCV 的 `getPerspectiveTransform` 计算四点透视矩阵，`warpPerspective` 生成校正图。校正后要保存原图、角点和变换矩阵，便于追溯。

不要在透视未校正的照片上直接测房间宽度或画精确墙线。

## 第二步：在 QGIS 中建立矢量图层

在 QGIS 中加载 `floor_1f_rectified.png`，创建一个 GeoPackage，并建立这些图层：

| 图层 | 几何类型 | 必要字段 |
|---|---|---|
| `walls` | LineString/Polygon | `id`, `wall_type`, `status` |
| `rooms` | Polygon | `id`, `room_label`, `wing`, `confidence`, `status` |
| `portals` | Point/LineString | `id`, `a_space`, `b_space`, `door_type`, `status` |
| `stairs` | Polygon | `id`, `label`, `status` |
| `elevators` | Polygon | `id`, `label`, `status` |
| `voids` | Polygon | `id`, `walkable=false` |
| `signs` | Point | `id`, `text`, `sign_type`, `status` |
| `corridors` | Polygon | `id`, `status` |

打开 snapping 和 advanced digitizing。绘制顺序必须是：

```text
外墙/内墙 → Void → 房间 → 走廊 → 门洞 → 楼梯/电梯 → 文字和标牌
```

房间应画成实际多边形。门必须单独成为 portal，记录它连接的两个空间；不要直接把两个房间连边。

## 第三步：建立两个坐标系

地图至少保留两个坐标系：

- `image_px`：原图像素坐标，用于回看 OCR 帧和 VLM 图像提示；
- `plan_local_m`：楼层本地米制坐标，用于机器人和 Nav2。

没有实测长度时，只能使用 `image_px` 或任意单位 `plan_local_u`。获得一条可靠长度后，可以设置比例；获得三处以上非共线测量点后，再做平移、旋转和尺度校准。

坐标变换必须写入 JSON：

```json
{
  "source_frame": "image_px",
  "target_frame": "plan_local_m",
  "method": "affine_or_homography",
  "scale_known": false,
  "matrix": null,
  "control_points": []
}
```

## 第四步：建立房间和门级拓扑

`floor_1f_semantic.json` 保存房间和设施，`floor_1f_topology.json` 保存空间节点和门级连接。

```json
{
  "spaces": [
    {"id": "room_LSS119", "type": "room", "label": "LSS119"},
    {"id": "corridor_lss", "type": "corridor", "label": "LSS circulation"}
  ],
  "portals": [
    {
      "id": "door_LSS119_01",
      "a": "room_LSS119",
      "b": "corridor_lss",
      "status": "observed",
      "geometry_status": "approximate"
    }
  ],
  "edges": [
    {
      "from": "room_LSS119",
      "to": "door_LSS119_01",
      "relation": "contains_portal",
      "status": "verified"
    }
  ]
}
```

拓扑状态建议使用：

- `candidate`：从图面推测，尚未现场确认；
- `observed`：图中能看到门或通道，但还没有现场通行确认；
- `verified`：现场、CAD 或多次 RGB-D 观测确认；
- `blocked`：墙、锁门或障碍导致当前不可通行；
- `unknown`：信息不足。

当前仓库中的 `segment_id` 和 `side` 可以继续作为人工输入，但它们不是 SLAM 坐标。只有当 OCR 文字与具体物理门关联后，才把 `landmark_id` 写入 M1 Observation。

## 第五步：独立检查地图候选（不接入 RGB 高层搜索）

地图工具可以独立检查场景标注和候选连接，输出与原始 OCR、严格 M2 Observation 分开保存：

```text
独立地图检查请求（人工指定标签，仅用于地图核对）
  → 查询 floor_1f_semantic.json
  → 查询 floor_1f_topology.json
  → 输出候选关系供地图维护者核对
```

地图查询可以返回：

```json
{
  "room_id": "LSS119",
  "candidate_position": [12.4, 8.1],
  "position_frame": "plan_local_m",
  "confidence": "medium",
  "route_ready": false,
  "reason": "door connection not verified"
}
```

无论 `route_ready` 的值如何，当前 M2 高层搜索都不能使用这些预标注目标位置做候选检索或观察顺序建议，也不能据此发出 `NavigateToPose`。`FloorScene` / `M1SceneBridge` 保留为独立工具，`scene_match` 不属于严格 M2.4 事件格式。真正的 RGB 回放入口及地图缺失依赖说明见 [M1 → M2.4 适配文档](m1-m2-offline-adapter.md)。

## 第六步：生成 Nav2 栅格图

QGIS 矢量图确认后，再把墙体和不可通行区域栅格化为黑色占据区域，把走廊和可通行区域栅格化为白色区域，保存为 PNG/PGM，并生成 YAML：

```yaml
image: floor_1f_nav.png
resolution: 0.05
origin: [0.0, 0.0, 0.0]
negate: 0
occupied_thresh: 0.65
free_thresh: 0.196
```

`resolution` 必须来自实测比例；没有尺度时不要伪造这个值。Nav2 的 `map_server` 使用 YAML 加载地图并发布 `map` frame，静态图不能代替现场 SLAM 的障碍物更新。

## 第七步：验收标准

在地图进入机器人实验前，至少完成：

- 所有房间编号与现场门牌逐一核对；
- 所有门洞有唯一 ID，并能找到两侧空间或明确标记为 unknown；
- 房间多边形没有意外重叠，Void 不被标记为可通行；
- 墙线、房间边界和门端点启用 snapping；
- 至少三个非共线地标完成平面坐标校准；
- RGB-D 现场观测能在地图中找到同一楼梯、电梯或门牌；
- 只有 `verified` 的门边才允许进入自主规划图；
- 失败、漏检和冲突观测被保留，而不是覆盖原始证据。

## 最小实验顺序

独立地图制作实验可按下面顺序检查数据；该顺序不用于当前 RGB 寻房策略：

1. 只标注 `LSS101 → LSS124 → LSS119 → LSS118` 所在的一段区域；
2. 现场测一条墙长或走廊宽度，建立 `plan_local_m`；
3. 只验证 3–5 个门和 2 个交叉口；
4. 用 RGB-D/SLAM 检查地图与真实墙体是否重合；
5. 在独立地图检查工具中复核已验证的门，不向当前 M1/M2 回放注入地图标签或位置；
6. 再逐步扩展到整个 1F。

这些检查用于减少地图制作中的几何错误，不代表当前纯 RGB 寻房闭环通过验收。真实地图文件与连接验证由数据负责人后续处理。
