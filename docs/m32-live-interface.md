# M3.2 视觉建议与手持会话接口

版本3移除了版本2对每帧 `human_reviewed` 的硬依赖。观察未知门不需要房号、永久ID或先读出OCR。它仍不能验证门类别或可通行性；真人现场验收尚未完成。

| 层次 | 来源 | 可推出什么 |
| --- | --- | --- |
| Unverified visual candidate | 当前 detector 框 | 模型提出的候选，可能是墙面 |
| Temporary tracked observation target | 短期跟踪和自动/人工选择 | 可以继续观察这一图像目标 |
| Human-confirmed visual target | 独立 `confirm_visual_target` 人工事件 | 人确认当前画面是要观察的门；只在同一未丢失track内保留 |
| Verified physical landmark | 原有可信实体关联规则 | 不由前三层生成，不由本会话创建 |
| Independent room-number confirmation | 原有独立房号确认规则 | 继续交给M1/M2，视觉建议不产生FOUND |

人工视觉确认支持的动作单列为 `*_human_visual_confirmed`，实时输入使用 `live_detector_*`，不计入自动效果。人工选track仍只是第二层。停止、丢失或换目标会清除第三层确认，不延续到新ID。房号确认与动作完成是不同事件。

## 版本3条件

- 保留分数≥0.3、尺寸/边缘、快速几何变化、采样时序、丢失和弱分竞争框检查。左右对准复用原 `camera_relative_advice`。
- 前进/停读均需连续3帧合格居中；相邻中心变化≤0.04、宽/高比例0.85–1.15。存在其他合理可见门框时弃权，人工选择也不能绕过这一前进/停读检查。
- 未人工视觉确认的前进目标，还需同一sign track连续2帧形成合格、无歧义的图像关联；只凭高分稳定door框不足以前进。人工视觉确认可为尚未检测到门牌的门提供额外观察依据，但不验证实体身份。已识别的海报/方向牌、OCR冲突仍停止。
- 前进仅限居中、左右未截断、标牌尚不足以停读的目标；同一track/session最多一次，后续录像帧不补预算。OCR引擎报错不会成为前进依据。
- 单一合格关联sign ROI达到32×16原像素、稳定性满足时，可以STOP_AND_READ尝试OCR，即使尚无房号读数或需要重试OCR。不要求先证明门牌归属；输出始终保留 `association_verified=false`、`physical_landmark_id=null` 和 `room_number_confirmed=false`。
- 新风险：墙面加小标牌可能同时满足视觉代理，造成一次不合适的前进建议或停读。此策略没有自动门结构验证器，不宣称消除所有误报；前进建议必须由人自行判断、可拒绝，系统没有运动执行端。

## 兼容与离线命令

schema2及原有381项测试语义保留；schema3明确记录新策略。`convert_detector_log`的Python默认仍为schema2，新的调用应显式传 `preview_schema=ADVISORY_SCHEMA`。CLI默认使用schema3：

```bash
python -m roomnav.offline_cli approach --input /private/detector.jsonl \
  --output outputs/fresh-visual-advice --preview-policy visual_advisory
python -m roomnav.offline_cli approach-replay outputs/fresh-visual-advice/events.jsonl
```

`--preview-policy strict_source`保留schema2。为兼容旧用法，提供 `--frame-reviews` 且未指定policy时仍使用schema2；显式policy优先。v1仍明确拒绝，历史产物/冻结代码保留，须从M3.1输入重建，不能静默解释旧语义。

## 实时 seam

`LiveDoorApproachSession`复用preview与原执行反馈controller，供现有Gradio回调调用：

```python
session = LiveDoorApproachSession()
result = session.process_rgb(rgb, predict=detector, received_s=server_monotonic_time)
session.select_target(visible_track_id)
session.confirm_visual_target(confirmed_by="local_operator")  # 可选，非实体/房号确认
session.feedback(action_id, completed=True, safe=True)       # 只能来自人的实际反馈
session.stop()
```

RGB帧串行处理；人工操作可以打断推理。完成后必须等新的接收帧；等待期间仍跟踪目标，不消耗尚未展示的前进预算。丢失、低质量或冲突导致需要停止时取消pending并锁定会话。拒绝和紧急STOP均需新会话对象重新开始，不自动恢复。所有结果固定 `motion_authorized=false`；`requires_execution_feedback` 表示等待人反馈，不是运动许可。视频建议本身不接受execution_feedback。

`received_s`是服务端单调接收时刻，内部帧stamp明确标为receipt clock，绝不冒充相机PTS。可选 `capture={timestamp_s, clock_id}`单独保存；未知相机时钟只能检查自身顺序，不能推断帧年龄。只有调用者确有依据标为 `server_monotonic` 的共享时钟才能比较年龄/反馈时间。没有capture时明确记录未知；重复RGB保守停止，但这不是新曝光证明。

`events`保留实际感知输入、preview输出、接收/处理/可取得的采集时间、人工事件，不保存原RGB，只保存摘要。它是内存审计记录，**尚无完整live事件回放器**，不能当作已实现手机闭环或测得端到端延迟。schema2/3离线JSONL可确定性回放。

现有Gradio布局未重写，UI默认新会话；旧 `GuidanceSession` 与回调接口保留。视觉确认和重启可通过上述接口调用，尚未新增专用UI控件。M3.3仍需相机时间接入、明确视觉确认/重启控件、可审计的人反馈及延迟记录、有人监督的单门现场试验。

## 真实证据范围

复用原45个detector/OCR采样，不重跑模型。schema3四组日志见ignored `outputs/m32-live-readiness/`：101人工左转4次、119自动右转2次，101自动及124自动均弃权；没有新真实前进/停读。新前进/停读及反馈状态只由显式synthetic测试验证，不能计作真人或录像成功。
