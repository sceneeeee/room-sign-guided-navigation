# M3.2 Recorded-video Door Approaching

当前版本3将非执行性观察建议与来源/身份确认分离，并提供实时会话接口；详见 [视觉建议与手持接口](m32-live-interface.md)。下文的每帧人工来源硬门槛属于可回放的版本2策略，不能当作版本3条件。

`DoorApproachPreview` 已实现从已保存 detector 帧到短期 Track、选择、左右对准、观察建议和停止读取的独立路径。它不依赖房号或永久门 ID，也不调用执行控制器、M2 `StreamSession` 或 `acknowledge()`。所有建议均为 `motion_authorized=false`；后续录像帧只更新画面建议，不证明人执行过动作。

## 使用

先通过现有 M3.1 detector 路径保存真实输入；然后转换原有完整日志，无需重新加载 YOLOE 或 OCR：

```bash
python -m roomnav.offline_cli approach \
  --input outputs/m32-continuous-validation/lss119-revised-events.jsonl \
  --video data/field_sessions/1f_raw/session_01/video/route_LSS101_LSS124_LSS119_raw.mp4 \
  --output outputs/m32-single-door-preview/lss119-auto \
  --demo-video

python -m roomnav.offline_cli approach-replay \
  outputs/m32-single-door-preview/lss119-auto/events.jsonl
```

输出目录必须不存在，以免覆盖实验。`--video` 核验视频 SHA256、源帧索引和整数 PTS，并从原录像解码 RGB 作叠加；不修改原视频。不传视频仍可生成 JSONL 和报告。可选 `--frames-dir` 复用 PNG 可视化，但明确标为 `cached_rgb_unverified`，不能代替原视频来源核验。`--demo-video` 生成 2 FPS 演示，每帧叠加实际 PTS；演示编码时间不是源视频时间。

输入限于完整、可确定性回放的 M3.1 detector 帧日志，拒绝 human GT 模式、人工 bbox/关联、execution feedback、operator confirmation 和非 frame 的源事件。派生日志保留原始 detector bbox/分数、ROI OCR、源 header、视频和源日志哈希；schema 为 `roomnav-m32-door-approach-2`。预览日志也可含显式 `select_target` 和 `stop`；禁止 execution feedback 和确认房号事件。损坏/截断日志和派生输出漂移会被拒绝。

人工调试选择使用 `--manual-selections /private/selections.jsonl`，每行如：

```json
{"type":"select_target","frame_id":"video_frame_00000000","track_id":"track00002"}
```

该事件在指定帧处理后提交，只能选择当时真实可见的 detector door；下一帧开始给出建议。事件与后续建议均记录 `selection_source=manual`，事件另标 `manual_target_selection=true`。不能算作系统自动选门。不能按未来帧回填 bbox 或重新标记帧。

## 本轮单门修复与决策边界

新日志 schema 为 `roomnav-m32-door-approach-2`。上一轮 v1 代码和私有产物保存在 `outputs/m32-single-door-preview/starting-files/` 与原输出目录，作为失败对照，不覆盖。当前 CLI 明确拒绝 v1 preview；请用同一份原始 M3.1 detector 输入重新生成，避免把旧的不安全停读语义静默解释成新策略。M3.1 回放不变。

- 原 tracker 和几何方向函数继续复用，未降 score≥0.3、IoU≥0.35 等门槛；无新模型。执行 controller、M2 与 Gradio 未改动。
- 自动选择要求连续两帧只有一个合格 door，且没有其他几何合理的可见 door 框。弱分竞争框仍算未解决的歧义，不能因其降分就认为消失；歧义期间不累积唯一候选计数。人工选择仍可显式消歧，但不等于实体门核验。
- 合格几何仍为宽≥0.06、高≥0.2、中心 x∈[0.08,0.92]；相机相对方向 x<0.38 向左、x>0.62 向右，其余 ALIGN。
- 宽度、高度的逐帧比例不在 (0.55,1.8)，或中心位移>0.18，均停止并锁定。即使该帧分数低也先检查几何突变，不能用低分帧隐去不稳定历史。该代理不能识别所有模糊或相机转动。
- track 丢失、时序异常、感知失败、采样间隔>1.5s 时安全停止，不自动换门，不延长 tracker 的丢失容忍，不按后续房号 OCR 重连。日志保留原停止原因和当前目标是否可见；旧 track_id 仅表示保留的观察锁。
- 独占包含、标牌分数/像素大小、OCR 高分和房号格式只能建立图像假设，不能证明实体门或标牌来源。**仅凭这些条件，或普通手选 track，均不再触发 FORWARD_SHORT / STOP_AND_READ。**
- 前进/停读额外需要对**当前帧、当前门 track、当前 sign track**的独立人工来源复核；复核只确认画面中是门及该标牌的来源，不确认房号、不创建实体门 ID。实际录像没有这类输入，本轮不补造。
- 来源复核不能绕过置信度、几何、歧义、OCR 失败或标牌质量条件；过期、错门/错标牌会拒绝或弃权。`not_door` 锁定停止，`uncertain` 当前帧停止。
- 居中、来源复核、标牌质量合格但尚无可读房号、左右未截断时，至多一次供人判断的 FORWARD_SHORT。预算不由新帧或重复选门恢复，不估算安全距离、路径或步长。
- 居中、来源复核且当前关联 ROI 中一个单房号格式读数 confidence≥0.8、ROI至少32×16像素时才可 STOP_AND_READ。这表示停止进一步读牌，仍非房号确认或 FOUND。

### 可选的当前帧人工来源复核

只在人工真正检查该帧后，单独提供 `--frame-reviews /private/reviews.jsonl`。示例是接口格式，**不是本轮真实实验输入**：

```json
{"frame_id":"video_frame_00000015","track_id":"track00001","sign_track_id":"track00002","status":"door_and_plate","reviewed_by":"operator_name","source":"human_current_frame"}
```

允许 status=`door_and_plate`、`not_door`、`uncertain`；否定/不确定复核可将 sign_track_id 设 null。文件哈希写入 header，复核原文写入该 frame 的 `visual_review`；原 detector bbox/OCR 不变。不支持 GT bbox 输入，复核不能作为执行反馈或永久身份。

当前帧复核不传到下一帧。建议的 `evidence_mode` 分为 `real_detector_automatic`、`real_detector_manual_track`、`real_detector_human_reviewed`，合成输入则使用 `synthetic_*`。汇总另有 `actions_by_evidence_mode` / `non_stop_frames_by_evidence_mode`，人工复核的动作不得计入自动指导效果。真实自动/手选实验不提供该文件；正例前进和停读只有显式合成复核测试。

每个 frame output 增加 `diagnostics`：全部 door 候选的原框/分数/中心/宽高/变化/IoU/质量阻止条件、当前可见目标、选择计数、全部 ROI OCR、每个候选与当前目标的标牌假设，以及 TURN_LEFT/RIGHT、FORWARD_SHORT、STOP_AND_READ 的分别阻止条件。`frame-reasons.jsonl` 和逐帧 Markdown 同步输出这些证据。图像空间变化不解释为执行完成。

## 真实失败原因与修复后结果

复用 0–6s、12–20s、76–83s 三段45个原PTS、约2FPS的YOLOE/RapidOCR输入；额外 detector/OCR 推理0次。未使用 GT 框，未提供真实人工来源复核。完整结果：ignored `outputs/m32-single-door-preview/`，原实验目录保留不变。

| 片段 / 模式 | 有 door 候选帧 | 当前锁定 | 左转 | 右转 | 前进 | 停读 | 重观察 |
| --- | --- | --- | ---: | ---: | ---: | ---: | ---: |
| LSS101 / automatic | 11/13 | 无，竞争框未消歧 | 0 | 0 | 0 | 0 | 13 |
| LSS101 / manual track | 11/13 | track00002，可见木门 | 4 | 0 | 0 | 0 | 9 |
| LSS124 / automatic | 17/17 | 无，歧义或低分 | 0 | 0 | 0 | 0 | 17 |
| LSS119 / automatic | 14/15 | track00001，可见木门 | 0 | 2 | 0 | 0 | 13 |

**LSS101 人工木门**：0.5–2s 的 score=0.5070/0.4524/0.4149/0.3346，中心x≈0.359/0.356/0.323/0.287，四次左转。2.5s分数0.1578、3s分数0.2699均不足0.3；框中心移至0.219/0.145，宽度缩至0.437/0.290。3.5s与当前框最大IoU约0.110，低于0.35，所以跟踪消失。目标没有在合格分数期间居中，画面也不是跟随左转提示的执行记录；不得放宽跟踪去拼接右侧墙面。

**LSS119 自动木门**：76.495/76.995s分数0.3636/0.4054、中心0.884/0.846，给出两次右转。77.495/77.995s分数降至0.2574/0.2033，中心仍为0.780/0.752；78.495s最大匹配IoU约0.292，低于0.35。没有合格居中阶段；后续约80s的其他框和LSS119 OCR不能证明是同一实体门，更不能作为已执行反馈。后续单帧能看到木门及文字，但没有足够的同目标连续检测链路。

**LSS101 旧自动墙面**：0–2s木门与白墙均有候选；旧实现累计了歧义期间的稳定计数。2.5s木门降分，白墙score≈0.3009、中心≈0.591，被立即选中，框内OCR满足图像可读条件，错误STOP_AND_READ。新策略保留低分木门为竞争候选，并要求独立来源复核，已消除该误停读。

**LSS124**：正前白墙形成track00003；14/14.498s分数约0.319/0.301，虽然居中，但没有合格关联门牌。左侧真实木门候选多数低分、狭窄/截断且存在冲突；约15–16s读出LSS124的sign没有组成合格同门链路。旧策略会锁白墙再停止；新策略在选择阶段就弃权，不把弱分竞争框消失当作事实。

当前已知墙面没有前进、转向或停读建议，但这只是本录像的回归结果，不能据此宣称解决了墙面分类。新门框仍可能是未知误报，相机相对转向也仅为观察候选。

## 素材边界与最短补录

现有录像足以复现真实候选、人工/自动选门、左右观察、低分/丢失/误报回退；**不足以验证同一可信门从远处缓慢居中、接近、稳定读牌的完整过程。** 没有真实 FORWARD_SHORT，没有可靠真实 STOP_AND_READ。现有帧不能通过修改 bbox、降低门槛、回连新 ID 或补写人工复核来制造成功。

只需补一段10–20秒的单门专项录像，无需重录路线：从走廊安全位置同时拍到完整门及周边，停稳约2秒；缓慢转动使门居中；在人的安全判断下靠近；保持门和门牌在画面中，最后停稳读牌约3秒。避免快速扫过、变焦和中途换门；保留原视频PTS，最好全程有门边框/门把手等结构与门牌。人的实际移动由人决定，录像仍不算响应系统指令的现场闭环。

补录只能改善连续观察条件。当前策略还明确要求独立的逐帧门/标牌来源复核，且没有自动来源核验器；即使补录质量理想，也不能单靠 bbox/OCR 获得全自动前进或停读。带人工来源复核的结果必须单列评估。

## M3.3 前还需完成

复用现有Gradio接入手机真实帧、时间来源和当前track选择；来源复核必须由独立人工UI事件产生，不能由detector/OCR自动填入。记录拒绝、STOP、人的真实反馈及推理/UI/人响应时延，完成有人监督的单门现场试验；再验证多门和丢失恢复。仍需更可靠的门结构/门牌关联证据；本轮没有自动来源核验器。Issue #5保持开放，可信GT绑定仍由Issue #2并行跟进。
