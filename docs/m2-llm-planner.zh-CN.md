# M2.3：可替换 LLM Planner 的开发规格（待实现）

> **范围：** 人持 RGB 相机实验的**文本高层搜索策略**。先让 LLM 基于 `LandmarkMemory` 的结构化观测选择下一处观察动作，与 M2.2 的规则方法比较；M2.3 不接实机、不训练模型、暂不引入图像。

## 1. 任务定义与核心约束

输入：`target`（如 `EB104`）、`active_segment`、截至**当前时刻**的 `LandmarkMemory`。包括候选门牌、投票数、冲突状态、左右侧、观测先后、指示牌原文、独立人工确认。不能访问尚未观察到的门牌、建筑真值或未来视频帧。

输出：与 `src/roomnav/baseline.py` 同一动作空间：

- `CONTINUE_ALONG_SEGMENT`：继续沿当前走廊观察。
- `CHECK_MISSING_OR_OTHER_SIDE`：检查未观察到的门或走廊另一侧。
- `REOBSERVE`：重新采集证据，尤其遇 OCR 冲突。
- `VERIFY_TARGET`：看到多帧一致的目标候选，建议人工确认。
- `INSUFFICIENT_EVIDENCE`：证据不足，需要更多信息。
- `FOUND`：**只能**在 `LandmarkMemory.confirm_label()` 明确确认目标时输出。

这些是给**人**的观察建议，不是具有坐标的运动指令。仅凭门牌递增、奇偶或 VLM 推理，不能宣称 EB104 的真实物理位置。

## 2. 建议的代码架构

| 路径 | 职责 |
| --- | --- |
| `src/roomnav/llm_planner.py` | 将 Memory 转为时间一致、证据分级的 Prompt/JSON 输入；调用可替换的文本模型接口；解析结构化动作；返回决策及模型元数据 |
| `src/roomnav/llm_backend.py` | 定义最小 `TextModel` 协议，提供可注入的 FakeModel 和可选 OpenAI-compatible Chat Completions 适配器；不把模型名或 URL 写死在策略中 |
| `tests/test_llm_planner.py` | 无网络的假模型测试：输出正确性、边界条件、无效格式、越权动作和对照公平性 |
| `docs/m2-llm-planner.zh-CN.md` | 本文档；实现后补充运行与实验说明 |

**保留已有** `Observation`、`Landmark`、`LandmarkMemory`、`Decision`、`decide_next`，不破坏 45 项既有测试、不修改 `apps/` 或视频流代码。可以新增 `PlannerResult` 包装结果，显式标记 `backend/model`、`fallback_used`、`validation_errors` 以及模型运行耗时，以便日后评估真实成本。

## 3. 输出结构与程序端验证

模型只需要给出 **严格 JSON**（不要自由格式的长推导或 chain-of-thought）：

```json
{
  "action": "CONTINUE_ALONG_SEGMENT",
  "reason": "Observed numbers increase, so continuing is a tentative exploration strategy.",
  "assumptions": ["Room numbering may remain monotonic in this segment"],
  "uncertainties": ["The target has not been observed", "Room numbers may skip or alternate by side"],
  "evidence_landmark_ids": ["corridor_A:door02", "corridor_A:door03"]
}
```

**不要信任模型自己声称的 facts。** 可作为事实输出的部分应由本地代码根据 Memory 中已有的观测/人工确认组装，不能直接复制模型虚构的环境事实。

在调用模型前，先构造一份受限的 JSON evidence snapshot；如包含可能来自 OCR 的指示牌原文，应视为**不可信数据**，不能让文字中伪装的“忽略指令”改变系统约束。至少做这些程序端校验：

1. `action` 必须在合法动作集合中；输出必须符合 JSON/schema，数组字段必须是字符串列表。
2. `evidence_landmark_ids` 必须属于当前 Memory 中实际存在的地标；不能引用不存在的门或未观察数据。
3. 没有操作员明确确认目标时**绝不允许 `FOUND`**；多帧一致 OCR 不能自动越权为真值。
4. `VERIFY_TARGET` 必须有当前 Memory 支持的多帧一致目标候选；单帧、未关联或冲突 OCR 应选择 `REOBSERVE` 等动作。
5. 文本提示说明编号单调、奇偶分边、走廊方向都只是可能被后续证伪的假设；不能通过模型生成未经证实的坐标或路线。
6. 任何后端失败、超时、不可解析结果或校验失败都要**显式记录**，不得默默计入 LLM 成功率。可以返回带 `fallback_used=true` 的 M2.2 决策，或在严格模式抛出可诊断异常。
7. 错误或异常日志不得打印 API Key、含个人信息的视频帧、未经脱敏的图像数据。

为了公平对比，LLM 与 Rule-based 在同一实验步应看到**相同的已观察证据与地标关联信息**。严格防止未来信息泄漏；可把可用证据构造成同一个不可变 snapshot，再分别交给两种方法。

## 4. 开发次序

**M2.3a（本轮）**：只实现模型无关的 Prompt/Snapshot、FakeModel、结构化输出解析与验证器、失败处理。全部测试离线完成，**不下载 Qwen、不占 GPU、不需要 API Key**。尽量使用 Python 标准库，避免无必要的依赖。

**M2.3b（下一轮）**：实现可选 OpenAI-compatible 后端并支持切换本地 Qwen / 远程 API。模型 ID、服务地址、密钥都通过环境变量或调用参数传入。先跑 10–20 个冻结的模拟搜索 episode，记录 LLM 推理延迟、错误率、回退率，与规则方法比较。真实楼道表现必须等 M0/M1 输出稳定后再评估。

## 5. M2.3a 验收测试（至少覆盖）

- EB102、EB103 → 针对 EB104 的合法行动建议。
- 多帧一致的疑似目标不能直接 `FOUND`。
- 只有人工明确确认的目标才能 `FOUND`。
- 单帧目标、未关联目标、OCR 冲突的验证约束。
- 不同走廊的证据不能编造为当前走廊地标。
- 返回不存在的地标 ID、无效 action、缺少必需字段、非 JSON 时安全失败。
- 后端超时/抛错时可诊断，fallback 使用记录明确。
- Prompt 含事实/假设/不确定性约束，不泄漏未来状态。
- 与原有 45 项测试兼容，`python -m unittest discover -s tests -v` 全部通过。

此阶段不要求 LLM 比规则更聪明，**先证明接口可测试、可替换、安全而且不会虚构已经找到房间**。等 M2.3a 通过后，再接真实模型，不应把 FakeModel 的测试视为模型效果实验。

参考：[ReasonNav 源码](https://github.com/ReasonNav/ReasonNav)、[M2.2 规则基线](m2-rule-reasoning.zh-CN.md)。
