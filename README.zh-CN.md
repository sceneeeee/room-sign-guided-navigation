# 基于门牌与指示牌的室内导航（Room & Sign-Guided Indoor Navigation）

[English](README.md) · **阶段：M2.1–M2.3c 已完成开发并取得 20 场景文本 LLM 对照结果；现场验证待完成**

## 核心目标

当目标房间（例如 `EB104`）的具体位置未知时，系统利用**已经观察到的门牌、方向指示牌及少量拓扑关系**，推断下一处应该去哪里观察，再逐步找到目标。

**第一阶段不使用 Frogbot。** 由人手持手机 / USB RGB 摄像头在真实教学楼中移动；MacBook 负责现场交互，可通过 SSH 与远程 GPU 电脑通信；远端运行文字识别与高层推理。模型返回下一步建议，由人执行并采集新观察。

研究问题分为两层：

1. **视觉感知：** RGB 图像能否稳定找到门牌并读取完整编号？
2. **空间推理：** 当目标尚未出现时，基于已有编号和走廊关系，能否提高搜索效率？

## 开发进度快照（2026-10-14）

**分支边界：** 默认 `main` 分支目前提供 M0 摄像头/RapidOCR 原型与早期推理脚手架。**M2.1–M2.3c 的完整实现位于 [`feat/m2-reasoning`](https://github.com/sceneeeee/room-sign-guided-navigation/tree/feat/m2-reasoning)，尚未合并至 `main`。** 下述 M2 命令与实验结果属于开发分支，不能假定可在 `main` 直接运行。

- **开发分支已实现：** M2.1 地标与证据记忆、M2.2 规则基线、M2.3a 受约束文本 LLM Planner/回退、M2.3b 本地 Ollama/Qwen3-4B 接入、M2.3c 20 个冻结单步场景与策略评估；**154 项单元测试在本地 WSL 连续五轮通过**。
- **已完成的离线对照（20 个模拟场景，Qwen3-4B-Instruct-2507 Q4_K_M，Prompt v2）：** Rule-based **19/20（95%）**动作合法且可接受（同帧重复计数有 1 个已知问题）；Qwen **10/20（50%）**可接受、**20/20（100%）**合法、**0 次** fallback、**17/20（85%）**选择弃权，平均推理延迟约 **1.89 秒**。
- **尚未完成：** 结果仅针对人工设计的**单步模拟场景**，不代表真实寻房成功率；M0 现场链路验收、M1 门牌检测/跨帧关联、M2 与视频流集成、M3/M4 现场闭环及 VLM 部署均未完成。**Prompt v3 尚未开发。**

详见[开发分支 M2.3c 评估文档](https://github.com/sceneeeee/room-sign-guided-navigation/blob/feat/m2-reasoning/docs/m2-offline-evaluation.zh-CN.md)。后续重点是分析 Qwen 过度弃权、在不降低证据校验标准的前提下开展 Prompt 对照，再与真实视觉感知链路集成。

## 系统结构（视频流优先）

~~~text
MacBook + USB RGB 摄像头（实时视频采样）  [M0 最小代码已实现，线下未验证]
      ↓ Gradio 按约 1.5 秒取一帧 + SSH 端口转发
远程 GPU 电脑：RapidOCR 原始文本
      ↓ 门/门牌检测、跨帧确认和去重     [M1 待开发]
RoomObservation → Landmark Memory       [已有数据结构，待接入视频]
      ↓
编号规则 / LLM 高层搜索建议            [本地 Qwen 20 场景对照已完成]
      ↓
人按建议行走并继续观察                [现场闭环待集成]
~~~

这里的“视频流”是**浏览器定时提交图像帧**，不是连续 30 FPS 原始视频。当前输出未经核验的 OCR 文本，不包含自动门检测或导航推理。照片与录制视频保留作回归测试；`segment_id` 和 `side` 可人工标注，不代表 SLAM 自动定位。

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

M2.1–M2.3c 已实现，使用本地 Ollama/Qwen3-4B 在 20 个冻结走廊场景中完成与 Rule-based 的真实调用对照；评估 CLI 默认只运行 Rule，[评估文档](docs/m2-offline-evaluation.zh-CN.md)包含运行方式与指标定义。现场摄像头、门牌检测、跨帧去重、实时记忆入库和 VLM 仍未集成。详细步骤见 [部署文档](docs/setup.zh-CN.md)。

## 视频流优先的里程碑

| 阶段 | 核心工作 | 验收结果 |
| --- | --- | --- |
| **M0 · 实时视频接入** | MacBook USB 摄像头 → Gradio 采样 → SSH → GPU OCR → 返回文本 | 流式上传和远程识别跑通，测量实际采样与延迟（原型代码已有，实测待做） |
| **M1 · 门牌实时识别** | 自动发现门/门牌，读取 EB104 等完整编号，多帧确认、同门去重 | 输出可核验的门牌编号并保存失败样本 |
| **M2 · 地标记忆和推理** | M2.1–M2.3c 完成独立实现，20 个冻结场景与真实本地 Qwen 对照 | Rule 19/20、Qwen 10/20 可接受；Prompt v3 和真实楼层验证未完成 |
| **M3 · 线下人机闭环** | 人在走廊移动，按系统建议检查门牌；Codex 辅助现场调试 | 完成未知房间搜索并留存日志 |
| **M4 · 对照评估** | 对比逐门搜索、局部编号规则、VLM | 搜索成功率、查看门数、行走距离和耗时 |

照片和录制视频用于**回归测试与失败复现**，不作为独立的前置开发阶段。

**暂不涉及：** Frogbot、深度相机、LiDAR、SLAM、Nav2、云台硬件和完整 ReasonNav 部署。

## 两个人的分工

- **成员 A（你）｜推理与评估：** 负责 Landmark Memory、门牌编号规则基线、VLM 高层推理、下一步观察动作选择，以及逐门搜索 / 规则 / VLM 的对照实验设计与结果分析。
- **成员 B（队友）｜视觉感知与线下测试：** 以 **USB RGB 摄像头实时视频流接入（M0）**及线下测试为首要任务，随后负责门牌检测、OCR、跨帧去重，照片与录像用于回归，负责感知指标统计；主导 **MacBook + SSH + 远程 GPU** 的线下联调。
- **成员 B 的 Codex 辅助现场调试：** 在实时视频流的线下测试过程中使用 **Codex** 辅助阅读日志、定位识别或取流问题、修改采集与推理代码、调整参数、重新运行并即时验证。每次改动应保留可复现的版本与测试记录，重要改动经人工确认后再应用。
- **共同：** 统一感知输出与推理输入的 JSON 接口、集成端到端人机交互流程，并复盘失败案例。

**现场迭代流程（实时采样示例已实现，线下流程待验证）：** 摄像头取流 → 远程 GPU 识别 / 推理 → 观察失败案例与延迟 → 队友使用 Codex 辅助修改代码 → 重新测试并记录效果。Codex 是现场开发与调试工具，**不是**自动识别门牌或决定导航方向的模型；上述实时联调尚未完成验证。

## 参考资料

- [ReasonNav 项目主页（CoRL 2025）](https://reasonnav.github.io/)
- [ReasonNav 官方代码](https://github.com/ReasonNav/ReasonNav)
- [实验设计](docs/experiment.zh-CN.md)
- [路线与分工](docs/roadmap.zh-CN.md)
- [模块接口](docs/interfaces.zh-CN.md)
- [M2.2 基于证据的规则推理](docs/m2-rule-reasoning.zh-CN.md)
- [M2.3b 真实 LLM 接入与运行](docs/m2-llm-serving.zh-CN.md)
- [M2.3c 冻结场景与离线策略评估](docs/m2-offline-evaluation.zh-CN.md)
- [环境部署与 SSH](docs/setup.zh-CN.md)

目前的算法价值首先是**可验证的工程复现与实验对照**，不是声称原创了 OCR+VLM 找房间；OCR 误读、跳号、编号规则不一致以及主动重复观察属于后续需进一步查新验证的研究方向。
