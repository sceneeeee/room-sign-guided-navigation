# M1 关键帧人工审核指引

图片目录：

`data/field_sessions/1f_raw/session_01/review/keyframes_issue2/`

填写模板：

`docs/field-tests/2026-11-07-m1-human-review-template.csv`

## 每帧审核顺序

1. 先看 `review_focus` 指定的区域。
2. 画出这一帧中**所有可见实体门**的 `door_bbox_xyxy`；一帧有多扇门时，复制该帧行并递增 `visible_instance_index`。不确定的门也要记录，并在备注写明原因。
3. 画出每个可见门牌的 `sign_bbox_xyxy`。墙面方向牌、公告牌和消防标识不要当作门牌。
4. 只有能确认门牌属于哪一扇实体门时，才填写 `physical_door_id` 和 `association_status=manual_confirmed`。模板中的 `annotation_source` 留空，完成后再填 `human_reviewed` 或 `manually_corrected`。
5. 能读出的房号填 `room_label_gt`；读不清写 `unknown`。OCR 只作参考，不作为真值。
6. 只有确认这张图里的所有可见门和门牌都已检查，才把 `annotation_complete` 改为 `true`。

## bbox 格式

`x_min,y_min,x_max,y_max`，原点在图片左上角，图片大小为 1920×1080。例如：`820,120,1710,1080`。

## 关联状态

- `manual_confirmed`：人工确认门牌与实体门的对应关系。
- `unknown`：看到了门或牌，但无法确认对应关系。
- `not_visible`：没有看到可用的门牌或实体门。

## 特别注意的帧

- `frame_000005`、`frame_000010`：OCR 为空，判断是否漏读或画面不可读。
- `frame_000050`、`frame_000068`：近距离公告/门牌区域，确认是否有实体门同框；不要把公告文字直接当房号真值。
- `frame_000084`、`frame_000090`、`frame_000096`：方向牌、消防标识和门同时出现，注意不要错配。
- `frame_000110`、`frame_000126`：多扇门同框，要逐门记录。
- `frame_000145`、`frame_000160`、`frame_000161`：LSS119 近景，重点补门框和门牌框。
