# 基于门牌与指示牌的室内导航（Room & Sign-Guided Indoor Navigation）

[English](README.md) · **项目进度（2026-10-21）：PR #1 的 M0 现场采集/复核已合并 `main`；本分支 M2.1–M2.4 已实现；M1/M3/M4 尚未完成**

## 核心目标

当目标房间（例如 `EB104`）的具体位置未知时，系统利用**已经观察到的门牌、方向指示牌及少量拓扑关系**，推断下一处应该去哪里观察，再逐步找到目标。

**第一阶段不使用 Frogbot。** 由人手持手机 / USB RGB 摄像头在真实教学楼中移动；MacBook 负责现场交互，可通过 SSH 与远程 GPU 电脑通信；远端运行文字识别与高层推理。模型返回下一步建议，由人执行并采集新观察。

研究问题分为两层：

1. **视觉感知：** RGB 图像能否稳定找到门牌并读取完整编号？
2. **空间推理：** 当目标尚未出现时，基于已有编号和走廊关系，能否提高搜索效率？

## 开发进度快照（2026-10-21）

**分支边界：** [`main`](https://github.com/sceneeeee/room-sign-guided-navigation/tree/main) 已于 2026-10-20 合并 [PR #1](https://github.com/sceneeeee/room-sign-guided-navigation/pull/1)（`c247107`），现包含 **M0 定时采集、无损 PNG 保存、逐帧复核和实验导出**。**本 `feat/m2-reasoning` 分支**包含独立的 M2.1–M2.4，**尚未合并到 `main`**；反过来，**本分支也尚未纳入 PR #1 新增的 M0 采集/复核代码**，目前仍使用较早的摄像头原型。下述 M2 命令和文档可直接在本分支运行。

- **本分支已实现：** M2.1 地标与证据记忆、M2.2 Rule 基线、M2.3a 受约束文本 LLM Planner/回退、M2.3b 本地 Ollama/Qwen3-4B、M2.3c 20 个冻结单步场景评估、M2.3d Prompt v2/v3 版本切换、M2.4 JSONL/标准输入流式观测与 Rule 决策。**M2.4 完成后，本地 WSL 全量 194 项测试通过**。
- **历史离线对照（20 个人工设计的单步场景，Qwen3-4B-Instruct-2507 Q4_K_M，Prompt v2）：** **旧版** Rule-based **19/20（95%）**动作合法且可接受，存在 1 个同帧重复计数问题；Qwen **10/20（50%）**可接受、**20/20** 合法、**0 次** fallback、**17/20（85%）**弃权，平均延迟约 **1.89 秒**。M2.4 修复后的 Rule 在**同一套已暴露的测试集上回归达到 20/20**，这是缺陷修复验证，**不等于独立泛化成绩**。
- **M0 单条件现场实验（已通过 PR #1 合并到 `main`）：** 队友使用**手机/Camo → MacBook → SSH → Linux RapidOCR**，在约 **2 m、正面、相机高 1.0 m** 的条件下对实物 `LSS101` 进行**1 次 Record，采样 6 帧**。测试者已补齐并确认 6 帧的实物真值均为 `LSS101`；OCR 完整识别 **5/6（83.3%）**，1 帧无文字；远端处理耗时 **0.54–1.09 秒/帧**，未测端到端延迟。这不是六次独立实验，更不能外推到其他门或条件。历史帧仍为 JPEG，`main` 上新采集的帧改存 PNG；**相关新版 M0 代码尚未合入本 M2 分支**。
- **M2.3d 开发检查：** 两个 LLM CLI 支持 `--prompt-version v2|v3`，默认仍为 v2，证据校验未改。5 次新开发案例的 Qwen 请求中，v3 改善了一次冲突重观测，趋势案例仍失败，另一次跨走廊引用被拒绝并安全回退。这些不是独立盲测，见 [Prompt v3 报告](docs/m23d-prompt-v3.zh-CN.md)。
- **M2.4 流式接入：** 从 JSONL/标准输入逐事件更新现有 Landmark Memory，默认只运行 Rule 并输出 JSON；人工 confirmation 事件才允许 FOUND。新 Rule `m2.4-distinct-captures-v1` 复用 LLM 的帧证据门槛，修复同帧重复 OCR 授权问题；旧冻结结果、oracle 和 v2/v3 提示原文保留。见 [接口与走廊回放](docs/m24-streaming.zh-CN.md)。
- **尚未完成：** M0 只有一扇门、一个固定条件的现场记录；M2 结果主要来自人工设计的**单步模拟场景**，不能代表真实寻房成功率。**M1 完整房号提取和同一物理门的跨帧关联、M1→M2 实时对接、M3/M4 的实地寻房与受控对照仍未完成**。Prompt v3 策略效果仍不稳定；当前 Rule/文本 LLM 路线无需先部署 VLM。

旧实验方法与历史成绩详见[开发分支 M2.3c 评估文档](https://github.com/sceneeeee/room-sign-guided-navigation/blob/feat/m2-reasoning/docs/m2-offline-evaluation.zh-CN.md)。下一步由 M1 显式提供地标关联与真实 frame_id，接入流式接口并记录现场 JSONL 回放。

```bash
python -m roomnav.stream_cli --target FB545 --input examples/m24_stream_demo.jsonl
```

## 系统结构（视频流优先）

~~~text
手机/Camo 或 USB 摄像头 → Gradio 采样       [M0 原型；PR #1 已单条件实测]
      ↓ Gradio 按约 1.5 秒取一帧 + SSH 端口转发
远程 GPU 电脑：RapidOCR 原始文本
      ↓ 门/门牌检测、跨帧确认和去重     [M1 待开发]
RoomObservation → Landmark Memory       [已有数据结构，待接入视频]
      ↓
编号规则 / LLM 高层搜索建议            [本地 Qwen 20 场景对照已完成]
      ↓
人按建议行走并继续观察                [现场闭环待集成]
~~~

这里的“视频流”是**浏览器定时采样**，不是连续 30 FPS。**PR #1 的定时 Record、PNG 保存、人工复核和导出功能已经合并到 `main`，但尚未进入本 M2 开发分支**；此处分支上的 webcam demo 仍是早期 M0 原型。OCR 结果是未经人工确认的原始文字，不构成可信门牌关联或导航决策；`segment_id`、`side` 可人工标注，不代表 SLAM 定位。

## 快速开始：实时视频流（M0）

在远程推理电脑上（Python 3.10+）：

~~~bash
python3 -m venv .venv
source .venv/bin/activate
pip install -e '.[vision,ui]'
python apps/live_demo.py
~~~

MacBook 现场建立 SSH 隧道并保持连接：

~~~bash
ssh -N -L 7860:127.0.0.1:7860 USER@GPU_HOST
~~~

在 MacBook 浏览器打开 `http://127.0.0.1:7860`，授权访问实验室 USB RGB 摄像头。默认约每 1.5 秒上传一帧到远程 OCR，结果在同一个网页显示。首次加载 OCR 模型可能较慢；界面耗时仅为**远端 OCR 执行时间**，不包含网络、排队与显示延迟。若 SSH 先进入 Windows 但模型在 WSL2，请核实端口可达。

照片仍作为**调试与回归输入**，可单独运行：

~~~bash
python scripts/ocr_image.py /path/to/door.jpg
python apps/photo_demo.py
~~~

已实现的 **M2.2 证据分级规则基线** 可独立测试：

~~~bash
roomnav-baseline --target EB104 --observations examples/observations_eb.jsonl
python -m unittest discover -s tests -v
~~~

M2.1–M2.4 均已在本分支实现：本地 Ollama/Qwen3-4B 在 20 个冻结单步场景中完成过真实调用对照，评估 CLI 默认只运行 Rule；**M2.4 流式 CLI 已能处理 JSONL 并增量更新 Landmark Memory，但尚未接入队友真实摄像头/M1 输出**。可运行下方回放，详见 [M2.4 文档](docs/m24-streaming.zh-CN.md)、[离线评估协议](docs/m2-offline-evaluation.zh-CN.md)及[部署文档](docs/setup.zh-CN.md)：

~~~bash
python -m roomnav.stream_cli --target FB545 --input examples/m24_stream_demo.jsonl
~~~

## 视频流优先的里程碑

| 阶段 | 核心工作 | 验收结果 |
| --- | --- | --- |
| **M0 · 实时视频接入** | 手机/Camo → Gradio → SSH → RapidOCR，保存采样帧与人工复核 | **PR #1 已合并 `main`**；一扇 LSS101、一个条件、1 次 Record / 6 帧，逐帧 OCR 5/6；本分支仍是旧版 M0 原型 |
| **M1 · 门牌实时识别** | 提取完整房号；提供真实 `frame_id` / `observation_id`；对同一实体门明确关联 `landmark_id`，不同门不能凭相同 OCR 合并 | 队友下一阶段任务；首版允许人工标注地标关联，至少测试 3 扇实体门 |
| **M2 · 地标记忆和推理** | M2.1–M2.4 独立实现；JSONL/标准输入接入 Memory 和默认 Rule | 同帧重复问题修复、FB541→FB545 回放；v3 策略目标部分未达成，真实楼层验证待完成 |
| **M3 · 线下人机闭环** | M1 观测接入 M2.4；人按下一步观察建议移动并明确确认目标 | 真实多门牌闭环尚未验证 |
| **M4 · 对照评估** | 对比逐门搜索、Rule、文本 LLM | 搜索成功率、查看门数、行走距离和耗时；尚未开展 |

照片和录制视频用于**回归测试与失败复现**，不作为独立的前置开发阶段。

**暂不涉及：** Frogbot、深度相机、LiDAR、SLAM、Nav2、云台硬件和完整 ReasonNav 部署。

## 两个人的分工

- **成员 A（推理与评估）：** 维护 Landmark Memory、编号规则和**文本 LLM** 基线，设计下一步观察动作与 M3/M4 评估，负责后续 M1/M2 接口集成；当前不把 VLM 部署作为前置任务。
- **成员 B（队友）｜视觉感知与线下测试：** 以 **USB RGB 摄像头实时视频流接入（M0）**及线下测试为首要任务，随后负责门牌检测、OCR、跨帧去重，照片与录像用于回归，负责感知指标统计；主导 **MacBook + SSH + 远程 GPU** 的线下联调。
- **成员 B 的 Codex 辅助现场调试：** 在实时视频流的线下测试过程中使用 **Codex** 辅助阅读日志、定位识别或取流问题、修改采集与推理代码、调整参数、重新运行并即时验证。每次改动应保留可复现的版本与测试记录，重要改动经人工确认后再应用。
- **共同：** 统一感知输出与推理输入的 JSON 接口、集成端到端人机交互流程，并复盘失败案例。

**现场迭代流程：** 摄像头采样 → 远端 OCR → 分析识别、耗时与失败案例 → Codex 辅助修改 → 回归测试和记录版本。**M0 测试与增强的 UI 已通过 PR #1 合并到 `main`**；多条件、多实体门牌与 M1→M2 真实闭环尚未验证。Codex 是开发调试工具，**不是**导航决策模型。

## 参考资料

- [ReasonNav 项目主页（CoRL 2025）](https://reasonnav.github.io/)
- [ReasonNav 官方代码](https://github.com/ReasonNav/ReasonNav)
- [实验设计](docs/experiment.zh-CN.md)
- [路线与分工](docs/roadmap.zh-CN.md)
- [模块接口](docs/interfaces.zh-CN.md)
- [M0 LSS101 现场采集与数据：已合并 PR #1](https://github.com/sceneeeee/room-sign-guided-navigation/pull/1)
- [M0 现场实验报告（`main`）](https://github.com/sceneeeee/room-sign-guided-navigation/blob/main/docs/field-tests/2026-10-18-1f.md)
- [M2.2 基于证据的规则推理](docs/m2-rule-reasoning.zh-CN.md)
- [M2.3b 真实 LLM 接入与运行](docs/m2-llm-serving.zh-CN.md)
- [M2.3c 冻结场景与离线策略评估](docs/m2-offline-evaluation.zh-CN.md)
- [M2.3d Prompt v3 开发报告](docs/m23d-prompt-v3.zh-CN.md)
- [M2.4 流式接口与 Rule 修复](docs/m24-streaming.zh-CN.md)
- [环境部署与 SSH](docs/setup.zh-CN.md)

目前的算法价值首先是**可验证的工程复现与实验对照**，不是声称原创了 OCR+VLM 找房间；OCR 误读、跳号、编号规则不一致以及主动重复观察属于后续需进一步查新验证的研究方向。
