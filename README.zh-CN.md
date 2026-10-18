# 基于门牌与指示牌的室内导航（Room & Sign-Guided Indoor Navigation）

[English](README.md) · **项目状态（2026-10-18）：M0 已有单条件现场测试（PR #1 待合并）；M2.1–M2.4 已在开发分支完成；M1/M3/M4 待完成**

## 研究目标与边界

当目标房间（如 `EB104`）的位置未知时，系统利用**已经观察到的门牌、方向指示牌和少量走廊拓扑标注**，判断下一处应该去哪里观察，并逐步寻找目标。核心研究问题是：能否比逐门检查更高效地搜索未知房间？

第一阶段采用**RGB 摄像头 + 人在回路（human-in-the-loop）**：人手持手机或 USB 摄像头移动，MacBook 提供交互并通过 SSH 连接远程 OCR；计划由感知模块记录实体门牌，再由规则或文本 LLM 提出下一步观察建议，由人执行。**完整的感知—推理—行动闭环尚未集成。** 当前不使用 Frogbot、深度、LiDAR、SLAM 或 Nav2。

## 开发进度快照（2026-10-18）

**请区分项目进度与 `main` 实际可运行的代码：**

- **`main`（当前 README 所在分支）：** 只有早期浏览器摄像头 / Gradio / RapidOCR 原型和推理脚手架，尚不包含完整 M2.4 流式模块。
- **[PR #1 — M0 现场采集与实验结果](https://github.com/sceneeeee/room-sign-guided-navigation/pull/1)：** 目前仍是针对 `main` 的开放 PR。队友开发了定时录制、保存实际采样帧和 OCR、人工复核与导出，并提交现场实验记录；**相关改动尚未合并**。
- **[`feat/m2-reasoning`](https://github.com/sceneeeee/room-sign-guided-navigation/tree/feat/m2-reasoning)：** M2.1–M2.4 已开发、提交并推送，M2.4 对应 commit `65c7e26`；**尚未合并到 `main`**。下面提到的 M2 命令、文档与测试结果属于该开发分支。

**实际完成情况与限制：**

- **M0 · 有限现场验证：** 2026-10-18，队友使用**手机 → Camo → MacBook 浏览器 → SSH → Linux RapidOCR**，在约 **2 m、正面、镜头高 1.0 m** 的单一条件下，对实物门牌 `LSS101` 完成 **1 次定点 Record、共 6 张采样帧**。其中 **5/6 帧**的 OCR 原文包含完整 `LSS101`，1 帧无文字；远端 OCR 耗时 **0.54–1.09 秒/帧**，不含端到端传输与显示。CSV 中仅 **2/6 帧单独填写了 `room_truth`**，这 2 帧均正确（已填写真值子集为 2/2），**不能据此宣称整体准确率 100%**。样本量也不足以证明不同房间、角度与运动状态下的稳健性。
- **M1 · 待开发：** 从原始 OCR 提取完整房号、区分不同物理门牌、跨帧关联和去重，为每条观测提供可信的 `frame_id`、`observation_id`、`landmark_id`。首版可以人工标注关联，**不能仅凭 OCR 字符串相同就认定是同一扇门**。
- **M2 · 开发分支已独立实现：** M2.1 `LandmarkMemory`；M2.2 编号规则基线；M2.3a 受约束文本 LLM Planner 与安全回退；M2.3b 本地 Ollama/Qwen3-4B；M2.3c 冻结 20 场景评估；M2.3d 可选 Prompt v2/v3（**v2 默认，v3 策略效果仍不稳定**）；M2.4 JSONL/标准输入流式观测、增量规则决策与同帧重复计数修复。**本地 WSL 全量 194 项单元测试通过**。模拟 `FB541 → FB543 → FB545` 回放可依次返回 `REOBSERVE` / `VERIFY_TARGET` / 人工确认后 `FOUND`，但这**不是现场寻房成功案例**。
- **历史离线对照（Prompt v2）：** 20 个**人工设计、冻结的单步场景**中，旧 Rule **19/20** 动作合法且可接受；Qwen **10/20** 可接受、**20/20** 合法、**0** 次 fallback、**17/20** 弃权。M2.4 修复已知同帧 Bug 后，Rule 在**同一套已暴露**场景上回归为 **20/20**；这是修复回归，**不是独立泛化验证**。Prompt v3 只有 5 次开发检查，不能据此推断真实策略更优。
- **M3/M4 · 尚未开展真实闭环与正式对照：** M2.4 尚未收到实时 M1 观测；没有多门牌实地寻房的成功率、查看门数、行走时间/距离指标。当前使用文本 OCR + Rule / 文本 Qwen，未部署 VLM，现阶段也不必以 VLM 为前置条件。

## 系统结构（预期闭环）

~~~text
手机/Camo 或 USB 摄像头 → MacBook 浏览器 → SSH → 远程 RapidOCR
                                          [M0：PR #1 已有单条件现场实验]
             ↓ 原始 OCR 与真实采样帧
完整房号提取 + 同一实体门牌跨帧关联       [M1：尚未实现]
             ↓ 显式 Observation JSONL
               (segment_id / frame_id / observation_id / landmark_id)
LandmarkMemory → Rule / 可选文本 Qwen     [M2：开发分支独立可运行]
             ↓ 高层下一步观察建议（不是路径或机器人控制）
人到下一扇门继续观察并更新记忆            [M3：待联调]
             ↓
逐门搜索 / Rule / 文本 LLM 受控对照        [M4：待实验]
~~~

浏览器按约 **1.5 秒/帧**采样，不是连续 30 FPS 视频；OCR 输出是未经人工确认的原始证据。走廊段与左右侧可以人工标注，不代表机器人定位、自动建图或目标位置真值。

## 快速开始

**`main` 上可以运行的 M0 早期原型**（远程推理电脑，Python 3.10+）：

~~~bash
python3 -m venv .venv
source .venv/bin/activate
pip install -e '.[vision,ui]'
python apps/live_demo.py
~~~

MacBook 建立 SSH 隧道并保持连接：

~~~bash
ssh -N -L 7860:127.0.0.1:7860 USER@GPU_HOST
~~~

在 MacBook 浏览器访问 `http://127.0.0.1:7860` 并允许摄像头权限。摄像头由 **MacBook 浏览器**访问，SSH 只是转发网页请求；显示的 OCR 耗时仅代表远端处理，不包含网络、排队与浏览器渲染。**PR #1 新增的定时录制、保存与人工复核尚不在 `main`**。详见[部署说明](docs/setup.zh-CN.md)。

**M2.4 离线流式回放**需先切换到开发分支（以下命令**不能直接在 `main` 使用**）：

~~~bash
git switch feat/m2-reasoning
pip install -e .
python -m roomnav.stream_cli --target FB545 --input examples/m24_stream_demo.jsonl
python -m unittest discover -s tests -q
~~~

此流式 CLI 默认**仅使用 Rule-based**，不会自动调用 Ollama/Qwen；`FOUND` 必须由独立的人工确认事件授权。单张照片调试仍可使用 `scripts/ocr_image.py` 或 `apps/photo_demo.py`。

## 开发里程碑与分工

| 阶段 | 关键工作 | 当前状态 |
| --- | --- | --- |
| **M0 · 取帧/OCR** | 远程文字识别、现场采样、逐帧保存和人工复核 | `main` 有初版；PR #1 含一次 LSS101 现场记录和升级版 UI，待合并 |
| **M1 · 视觉感知** | 完整房号提取、同门跨帧关联、不同门牌去重与观测 ID | **队友下一阶段重点**，未完成 |
| **M2 · 记忆与策略** | Rule / 文本 Qwen、离线评估、流式 JSONL 决策 | **M2.1–M2.4 已在开发分支完成**，尚未接真实 M1 输入 |
| **M3 · 现场寻房闭环** | 将 M1 输出接入 M2.4，由人执行观察建议 | 尚未集成 |
| **M4 · 受控对照** | 逐门搜索 vs Rule vs 文本 LLM；记录成功率与成本 | 尚未开始 |

- **成员 A（推理/评估）：** 维护 M2、Rule 和文本 Qwen 对照，负责 M1/M2 适配与 M3/M4 评价方案。
- **成员 B（感知/现场）：** 维护 M0 采集与可复现实验记录，继续开发 M1 完整门牌提取、物理门关联、真实观测 ID 和现场测试。
- **共同：** 固定 `Observation` 数据契约，区分 OCR 原始证据与人工确认，分析失败案例并完成多门牌实地演示。

**近期工作：** 先修正 PR #1 中逐帧 `room_truth` 与准确率口径、复查取帧复现性；随后按 [M2.4 Observation 接口](https://github.com/sceneeeee/room-sign-guided-navigation/blob/feat/m2-reasoning/docs/m24-streaming.zh-CN.md) 开发 M1。M1 至少测试三扇不同实体门，输出真实 `frame_id` / 显式 `landmark_id` 与可回放 JSONL，再进行 M3 集成。README 更新**不意味着已合并 M2 或 PR #1**。

## 参考资料与文档

- [ReasonNav 项目主页（CoRL 2025）](https://reasonnav.github.io/)
- [ReasonNav 官方代码](https://github.com/ReasonNav/ReasonNav)
- [实验设计](docs/experiment.zh-CN.md)
- [路线与分工](docs/roadmap.zh-CN.md)
- [`main` 的早期模块接口](docs/interfaces.zh-CN.md)
- [M0 现场结果 PR #1](https://github.com/sceneeeee/room-sign-guided-navigation/pull/1)
- [M2.3c 冻结场景与离线策略评估（开发分支）](https://github.com/sceneeeee/room-sign-guided-navigation/blob/feat/m2-reasoning/docs/m2-offline-evaluation.zh-CN.md)
- [M2.3d Prompt v3 开发报告（开发分支）](https://github.com/sceneeeee/room-sign-guided-navigation/blob/feat/m2-reasoning/docs/m23d-prompt-v3.zh-CN.md)
- [M2.4 流式接口与 Rule 帧门槛修复（开发分支）](https://github.com/sceneeeee/room-sign-guided-navigation/blob/feat/m2-reasoning/docs/m24-streaming.zh-CN.md)
- [环境部署与 SSH](docs/setup.zh-CN.md)

**研究边界：** 人工设计的单步测试成绩和单条件 OCR 现场样本，不代表真实寻房成功率或模型性能优势；工程整合本身也不等于已证明的算法创新。

## 数据与许可证

不要将包含可识别个人信息的原始视频、图片、账户凭据上传到仓库。目前尚未选择开源许可证，公开复用与发布前需明确许可。
