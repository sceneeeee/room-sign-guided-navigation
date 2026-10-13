# M2.1：基于观测证据的地标记忆

## 实验边界

这部分代码服务于**人 + RGB 摄像头**的真实楼道实验，而不是机器人定位、SLAM 或自主导航。它不运行任何 VLM，也不自动解决视频中的物体跟踪；核心是为后续推理提供可复核的观测证据。

- **Observation（原始观测）**：一帧图像对应的一次 OCR 输出，原文不被推理模块悄悄改写。
- **Landmark（地标）**：已通过 `landmark_id` 明确关联为同一物理门牌的多次观测。
- **LandmarkMemory（记忆）**：保留完整原始观测时间序列，同时按已知地标关联形成候选汇总。可阻止重复上传事件和跨走廊 ID 碰撞。

同样的编号不代表同样的物理门牌。没有物体跟踪信息时，可由实验人员人工标注 `landmark_id`；不确定则暂不关联。

## 原始证据、候选、冲突

代码保留每次 OCR 的原文，在同一地标内用大写化、去除首尾空白后的文本统计票数（不做上下文纠错）：

- `single`：目前只有一次关联观测。
- `consistent`：至少两次观测的文本和已知左右侧一致；**不能视为人工验证**。
- `conflicted`：文本存在不同版本或已知左右侧发生冲突。
- `candidate_text`：票数唯一最多的候选，票数平局时为 `None`。即使候选是 `EB103`，出现过 `EB108` 时仍保留 `conflicted` 状态。

例如四帧识别结果是 `EB103, EB103, EB108, EB103`，同属实验人员标注的一个地标，则票数为 `3:1`、候选为 `EB103`，但状态仍然是 `conflicted`。

`side` 表示在约定的**同一走廊前进方向**下的左右。若摄像头掉头，左右相反并非门牌移动，不能擅自合并为空间事实。

## 本地测试

```bash
python -m unittest discover -s tests -v
```

可通过 `memory.to_reasoning_context()` 查看 JSON 化的地标候选和冲突状态。该摘要现已由 M2.2 独立规则基线使用，并包含可选人工确认信息；尚未与实时视频界面或 M2.3 VLM 连接。见 [M2.2 设计](m2-rule-reasoning.zh-CN.md)。

## 复用预训练模型，不从零训练

| 已有项目/模型 | 适合的复用方式 | 当前限制 |
| --- | --- | --- |
| [ReasonNav](https://github.com/ReasonNav/ReasonNav) | 参考 `vlm_prompts.py` 的下一地标决策和 `landmark_database.py` 的数据抽象 | 原项目依赖 ROS 2、地图和机器人，无法在手持相机任务中直接一键部署 |
| [Qwen3-4B-Instruct-2507](https://huggingface.co/Qwen/Qwen3-4B-Instruct-2507) | 将已知 JSON 地标转换为下一步观察建议；成本较低的文本 LLM 对照 | 不是专门训练的房间搜索模型 |
| [Qwen3-4B-Thinking-2507](https://huggingface.co/Qwen/Qwen3-4B-Thinking-2507) | 比较更强文本推理能力是否值得额外延迟 | 需要实测效果及显存、时间开销 |
| [Qwen3-VL-4B-Instruct](https://huggingface.co/Qwen/Qwen3-VL-4B-Instruct) | 将图像和结构化地标同时输入进行多模态对照 | 视觉输入与显存开销较大，需要实测 |

因此后续路线是：**M2.2 在同一观测证据上做可解释 Rule-based Baseline → M2.3 引入可替换的 LLM/VLM Planner**，不训练新的基础模型。策略输出需区分事实、编号趋势假设、不确定性，并验证“继续走 / 检查另一侧 / 重新观察”等决策是否真能提高搜索效率。模型的论文新颖性与真实楼层表现均尚未确立。
