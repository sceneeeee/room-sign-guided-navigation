# M2.2：基于地标证据的保守规则推理

## 为什么单靠 OCR 不够

目标是 EB104，摄像头可能把同一个门牌连续识别为 EB103、EB103、EB108、EB103。系统必须区分**读到了什么**和**真实门牌是什么**。本阶段不训练基础模型，而是建立可解释的 Rule-based Baseline，作为后续 LLM/VLM 的对照组。

人手持 RGB 摄像头，`segment_id`、左右位置及走廊前进方向可人工标注。系统不运行 SLAM，也不生成地图坐标。

## 决策规则

| 证据 | 返回动作 |
| --- | --- |
| 未关联目标相似 OCR、或单帧疑似目标 | `REOBSERVE` |
| 同一物理地标多帧一致地识别为 EB104，但没有人工确认 | `VERIFY_TARGET` |
| 人工明确核对该地标确实为 EB104 | `FOUND` |
| 同走廊至少两个不同地标的可靠候选呈局部单调趋势 | `CONTINUE_ALONG_SEGMENT` |
| 同侧 EB101、EB103 这样的单侧奇偶趋势，目标为 EB104 | 可**暂时建议** `CHECK_MISSING_OR_OTHER_SIDE`，必须标记奇偶规律为假设 |
| OCR 冲突 | `REOBSERVE` |
| 跨走廊、编号不单调、同编号重复、相关证据不足 | `INSUFFICIENT_EVIDENCE` |

可靠候选包括：明确人工确认过的地标，或者同一地标至少两次一致的 OCR 文本。**一致 OCR 不是人工真值**。由于同一视频中的相邻帧高度相关，多帧一致也不能代表独立验证。一个地标 ID 只能代表同一物理门牌，不能只凭编号相同就自动归并。

每次返回独立的 `facts`（发生了哪些观察/确认）、`assumptions`（局部编号继续单调、可能奇偶分边）、`uncertainties`（OCR 误差、跳号、走廊分叉等）和 `evidence_landmark_ids`（用了哪些地标）。

## WSL 上运行 M2.2

切换到自己的 `feat/m2-reasoning` 分支，在项目根目录：

```bash
git pull --ff-only
python -m unittest discover -s tests -v
```

模拟走廊 EB102 → EB103，目标 EB104：

```bash
roomnav-baseline --target EB104 --observations examples/observations_eb.jsonl
```

预期动作：`CONTINUE_ALONG_SEGMENT`，表示继续观察，**不是已定位目标房间**。

视频多帧疑似识别 EB104，尚未人工确认：

```bash
roomnav-baseline --target EB104 --observations examples/observations_target_eb.jsonl
```

预期动作：`VERIFY_TARGET`。

操作员实际核对并明确确认：

```bash
roomnav-baseline --target EB104 --observations examples/observations_target_eb.jsonl --confirmations examples/confirmations_eb.jsonl
```

预期动作：`FOUND`。这里只表示人工确认过一个目标门牌，仍没有机器人定位。

可选参数 `--segment corridor_A` 显式提供当前走廊。如果没有 `landmark_id`，旧格式 JSONL 仍能读取，但原始帧不再被当作可靠的独立地标用于趋势推算。

## 边界与下一步

当前规则使用地标**首次关联的先后顺序**；如果反向行走、帧乱序、左右两侧混杂编号，可能导致弃权或错误建议。`confirmed_by` 是事件来源，不保证人的确认永远正确。每个地标目前只保存最新一次确认，尚未实现完整操作审计。

M2.3 将在相同结构化证据上比较可替换的开源文本 LLM（如 Qwen）与 ReasonNav 风格的 VLM 策略，重点检验其是否真的比 Rule-based 更有效。**当前没有真实楼层搜索成功率的实验结论，也没有把 M2 代码接入 M0 的视频流。**
