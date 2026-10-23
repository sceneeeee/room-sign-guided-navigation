# 基于门牌与指示牌的室内导航（Room & Sign-Guided Indoor Navigation）

[English](README.md) · **项目进度（2026-10-21）：M0 现场采集与复核流程已通过 PR #1 合并至 `main`；M2.1–M2.4 在独立开发分支完成；M1/M3/M4 尚未完成**

## 研究目标与边界

当目标房间（如 `EB104`）的位置未知时，系统利用**已经观察到的门牌、方向指示牌和少量走廊拓扑标注**，判断下一处应该去哪里观察，并逐步寻找目标。核心研究问题是：能否比逐门检查更高效地搜索未知房间？

第一阶段采用**RGB 摄像头 + 人在回路（human-in-the-loop）**：人手持手机或 USB 摄像头移动，MacBook 提供交互并通过 SSH 连接远程 OCR；计划由感知模块记录实体门牌，再由规则或文本 LLM 提出下一步观察建议，由人执行。**完整的感知—推理—行动闭环尚未集成。** 当前手持实现不使用 Frogbot、深度、LiDAR、SLAM 或 Nav2；未来机器人运动执行阶段计划接入 LiDAR SLAM/Nav2，但不以它们驱动房号搜索决策。

## 研究路线更新（2026-10-23）：RGB 主导的门牌地标闭环寻房

**以下为经组会讨论后的计划，尚不代表已实现。** 给定目标房号（如 FB545），系统只通过一台 **RGB 摄像头**获取寻房所需视觉信息，主动发现门、门牌、指示牌和路口；依据 OCR、历史地标证据以及可选 VLM 推理，选择下一处值得观察的目标，并持续发出接近、对准、停稳读取和重新搜索的指导。**高层寻房策略不预先读取目标房号的地图坐标，不以 RGB-D 深度或 SLAM 位姿/语义地图作为搜索输入。**

- **当前阶段——人在回路：** 人手持 RGB 摄像头，遵守系统给出的有限、可修正的运动/观察提示，独立负责现场避障和安全；人不能暗中替系统选择正确门或方向。记录所有人为介入及拒绝指令。
- **未来阶段——机器人执行：** 尽量复用同一套 RGB 感知、LandmarkMemory 和高层策略；机器人通过里程计、LiDAR SLAM、Nav2 与避障控制完成运动。**SLAM 提供定位/几何地图，Nav2 负责规划和控制，不替 RGB 策略判定房号或选择搜索目标。** 但将“观察门 D07”的视觉目标转换为可执行、安全的机器人动作，仍需专门设计执行适配层，并以实机实验验证。
- **重要边界：** 目前目标为图像中的相对方向与短步动作，不是从单帧 RGB 精确测出门的米制坐标、门前全局 Approach Pose，或在未知障碍环境中保证可通行。人体安全能力不能算作算法自动避障能力。

**M1 线上进展（PR #4，尚未合并 main）：** M0/M2.4 集成、门/标牌候选检测与短期跟踪、M1 OCR 事件适配、M3 视觉短动作提示均有离线可测试实现。已有 YOLO-World 在同一扇门的 3 张相关图像中未检出门，唯一 sign 候选为开关/插座误检，因此新增[RGB 检测模型与提示词离线对照工具](docs/m1-detector-benchmark.zh-CN.md)；支持 YOLO-World / YOLOE、阈值扫描和候选框可视化。**YOLOE 的这组新参数尚无真实推理结果，完整寻房仍未实现或验证。**

### 目标系统架构（计划，不是当前代码）

~~~text
RGB 连续观测（现在手机，未来机器人摄像头）
  -> 门 / 指示牌候选检测 + 短期跟踪 + 图像清晰度检查
  -> RapidOCR 读取房号、标牌文字；关联到具体物理门
  -> 独立帧观测证据 -> LandmarkMemory
  -> 高层寻房策略：Rule / 文本 Qwen / 可选事件触发 VLM
     （选可见门、读牌、探索路口、重观察、核验或弃权）
  -> 视觉引导技能：SEARCH -> SELECT -> ALIGN -> APPROACH -> READ -> UPDATE
  -> 运动与安全执行：人（当前）/ 机器人导航与控制（未来）
  -> 动作反馈 + 新 RGB 帧，重复直至目标经独立验证
~~~

**模型职责：** 门检测器提出候选（包括暂未识别房号的门）；跟踪模块维持当前目标；RapidOCR 提供可审计的原始文字；地标关联与 Memory 处理实体身份、重复观测和矛盾；Rule/LLM/VLM 选择下一观察目标；视觉引导器按最新图像发出短时、可取消动作。不能假设未训练导航的通用 VLM 能可靠包办上述所有模块。标准 COCO 的 YOLO 预训练类别不含 door：应先实测开放词汇检测器或专用门检测权重，需要时再微调。VLM 优先在出现新门、路口、方向牌或策略不确定等事件时调用，**不是每帧都运行大模型**。

**接口调整：** 当前 M2.4 的严格 Observation 只支持带非空 OCR 文字的 room/sign 事件，不接受未知房号的门、bbox 或 track 信息。建议**另建视觉候选事件**，记录真实 frame_id、归一化框、短期 track_id 与可空的 landmark_id；只有 OCR 真实产生证据、且物理关联可信时，才转成现有的严格 Observation。不能把 track_id 直接冒充永久物理门 ID，也不能把推断的房号冒充 OCR 读数。现有 FOUND 仍需独立人工确认；以后如要自动宣告成功，必须引入可独立评价的新核验协议。

### 调整后的 M0–M4 与后续 M5–M7 计划

| 阶段 | 计划新增工作 | 主要验收条件 |
| --- | --- | --- |
| **M0** | 保留现有定时 OCR/人工复核；增加连续 RGB 会话、真实采集时间、端到端延迟、显示提示与执行动作记录 | 可按时间重放帧—建议—执行—新帧闭环；杜绝使用未来帧 |
| **M1** | 先完成原完整房号/可信 ID/跨帧关联交付；再扩展门与标牌检测、短期跟踪、门牌—实体门关联 | 至少 3 扇不同实体门，包含漏读/误读；未读房号的门也可被检测；关联不确定时不强行认定 |
| **M2** | 保留旧 LandmarkMemory、Rule、文本 Qwen、194 项历史测试与实验数据；新增可见候选选择、结构化视觉规划、可选 VLM 和安全回退 | 目标 ID 和事实必须来自已有观测；旧测试与历史分数不覆盖；独立比较文本与图文输入 |
| **M3** | 接通 M0/M1/M2，开发短动作引导状态机、过期提示拒绝、目标丢失恢复、人工安全中止 | 人按系统生成的提示选择、对准、接近并读取多扇门，不能自行替系统决定搜索目标 |
| **M4** | 开展真实寻房闭环与分层评估，对照逐门、Rule、文本 LLM、VLM 纯文本、同款 VLM 图文 | 目标实体门正确找到率、错误 FOUND、检查/重复门数、时间、干预次数、跟踪/关联错误、端到端延迟；未实测距离则不报告 SPL |
| **M5（后续）** | 未见过的走廊与多路口、指示牌、编号跳跃、重复访问、模糊场景及失败恢复 | 未预置房号语义地图、无需人工选方向的跨场景实验 |
| **M6–M7（后续）** | 将视觉目标转接机器人运动控制，先单门后多门/路口；集成 LiDAR SLAM + Nav2 负责运动安全 | RGB 决定目标门与房号，执行端安全到达观察位置；单独评价人→机器人迁移误差 |

**执行顺序：** 先完成原定 **M1 → M2.4 的真实观测 JSONL 回放**，再完成**单门：检测 → 跟踪 → 对准 → 分段接近 → OCR → 更新记忆**，随后接多门搜索和 VLM 对照。M0/M2 已有成果不因为新路线而改称“完成视觉导航”；M3/M4 不得只用模拟或单步离线成绩宣称实地成功。

**评测须分三层：** ① 门、门牌检测/OCR/物理关联；② 冻结历史观测下的策略选择（尤其比较同一 VLM 文本 vs 图文的增益）；③ 相同起点/目标/预算下的真实闭环成功率与搜索成本。人的安全避障可保留，但额外的搜索方向纠正必须记录。完整闭环过程中不同策略的后续观察会不同，这是合理的，不能拿未来观察提前喂给模型。

### 检索到的直接相关研究及局限

- [Follow the Signs: Using Textual Cues and LLMs to Guide Efficient Robot Navigation（2026，arXiv）](https://arxiv.org/abs/2601.06652)：**任务上最贴近**，利用房号规律、指示牌和 LLM 预测目标方向，融合探索与路径规划；包含网格环境实验和 Spot 实机示例。**但实机在提前标注房号、标牌的 occupancy/semantic grid 上运行，没有完成 RGB 实时识别→语义推理的完整链路。** 可作为高层策略参照，不能直接当作纯 RGB 端到端基线。
- [Mobile Robot Navigation in Indoor Environments Using Object and Character Recognition（ICRA 2000）](https://doi.org/10.1109/ROBOT.2000.844076)：已经实现通过物体/房号字符识别寻找指定房间；“根据房号寻房”本身绝非首次提出。
- [Indoor Mobile Robot Navigation Using Doorplate Landmarks（2006）](https://doi.org/10.1504/IJVAS.2006.012204)、[Room Searching Robot Based on Door Detection and Room Number Recognition（2021）](https://doi.org/10.1007/978-981-33-6926-9_4)：较早的视觉门牌/门检测与真实机器人寻房工作，但不是面向未知楼层的现代 RGB/VLM 地标推理方案。
- [Visual Semantic Navigation Based on Deep Learning for Indoor Mobile Robots（2018）](https://doi.org/10.1155/2018/1627185)：以单 RGB 输入完成机器人房间/走廊导航与实时纠偏，但三个感知网络需在指定实验环境的房间/区域上训练，并非直接未知房号搜索。
- [Efficient Exploration for Room Finding Using Wall-Following Based Path Planning（2009）](https://doi.org/10.5302/J.ICROS.2009.15.12.1232)：指出离墙太远会漏读小门牌，因此要考虑靠近扫描和重访；属于传统探索/SLAM 路线。
- [ReasonNav（CoRL 2025）](https://reasonnav.github.io/)：现代门、门牌、指示牌地标推理的主参考，但完整系统使用地图/空间地标与底层导航。
- [NaVid（RSS 2024）](https://arxiv.org/abs/2402.15852)、[NaVILA（RSS 2025）](https://arxiv.org/abs/2412.04453)、[Uni-NaVid（RSS 2025）](https://arxiv.org/abs/2412.06224)：借鉴 RGB 视频历史、分层行为和运动接口；并非给目标房号直接寻找未知房间的即用算法，其训练与计算条件也需另外评估。
- [SignScene（2026，arXiv）](https://arxiv.org/abs/2602.12686)：借鉴方向牌含义与真实通路的视觉关联，但方法包含空间表示，并非单靠无附加信息的 RGB 图片。
- [Automatic Sign Reading and Localization for Semantic Mapping with an Office Robot（2022）](https://arxiv.org/abs/2209.11432)：可参考门牌检测/OCR，技术路线使用 RGB-D 和 SLAM 赋予地图语义，**不是本项目当前的搜索主线**。

**文献边界：** 房号导航、视觉门检测和单目纠偏都有明确先例。此次检索尚未确认存在一个可直接复现、同时满足**实时 RGB 发现门牌、未知房号推理、闭环靠近、无需预标注语义地图、可切换人/机器人执行端**的完整系统；这不等于保证该方向具有论文创新性。最终贡献应由公平基线、真实数据和可复现结果证明。

### 线上原型最新进度（开发分支，非现场验证）

截至 2026-10-23，草稿 [PR #4](https://github.com/sceneeeee/room-sign-guided-navigation/pull/4) 的 `feat/online-rgb-guidance-integration` 分支已整合 M0/M2.4、增加可测试的 M1 OCR→M2 适配、保守图像轨迹关联和视觉方向提示，并新增 **独立 RGB 候选检测/引导演示入口** `apps/rgb_guidance_demo.py`（详见 [运行说明](docs/rgb-guidance-demo.md)）。这些均为待实机核验的开发能力；**没有宣称门检测准确率、现场闭环成功率，也没有替换原有 M0 页面**。线下标注与测试依赖 [Issue #2](https://github.com/sceneeeee/room-sign-guided-navigation/issues/2)。

## 开发进度快照（2026-10-21）

**请区分项目进度与 `main` 实际可运行的代码：**

- **`main`（当前分支）：** 已通过 PR #1 合并 M0 的浏览器/Gradio/RapidOCR 定时采样、采样帧与 OCR 日志、人工复核及 CSV/关键帧导出；**新增录制保存无损 PNG**，2026-10-18 已采集的历史样本仍为 JPEG。`main` **尚不包含** M2.4 流式推理与完整 M1/M3 寻房闭环。
- **[PR #1 — M0 现场采集与实验结果](https://github.com/sceneeeee/room-sign-guided-navigation/pull/1)：** 已于 **2026-10-20 合并到 `main`**，合并提交 [`c247107`](https://github.com/sceneeeee/room-sign-guided-navigation/commit/c2471077cba6be5b393ffd4fb905c9e46d26f672)。此前 Review 要求的真值补录与统计口径、后续无损保存、Gradio 版本固定及录制复测均已处理；现场旧 JPG 未修改。
- **[`feat/m2-reasoning`](https://github.com/sceneeeee/room-sign-guided-navigation/tree/feat/m2-reasoning)：** M2.1–M2.4 已实现并推送（M2.4 功能提交 `65c7e26`），**尚未合并进 `main`**；下述 M2 命令和实验结果属于此分支。该分支也**尚未同步 PR #1 合并后的 M0 采集/复核代码**。

**实际完成情况与限制：**

- **M0 · 有限现场验证：** 2026-10-18，使用**手机 → Camo → MacBook 浏览器 → SSH → Linux RapidOCR**，在约 **2 m、正面、镜头高 1.0 m** 的单一条件下，对实物门牌 `LSS101` 完成 **1 次定点 Record、共 6 张采样帧**。测试者确认六帧均对应实物 `LSS101`；逐帧完整编号识别为 **5/6（83.3%）**，1 帧无文字。远端 OCR 耗时 **0.54–1.09 秒/帧**，不含端到端传输与显示。这只覆盖一扇门、一个条件，不能外推为其他门牌或现场条件的准确率。 **这六帧来自一次 Record，不是六次独立试验；旧记录为有损 JPG，后续代码改为 PNG。**
- **M1 · 待开发：** 从原始 OCR 提取完整房号、区分不同物理门牌、跨帧关联和去重，为每条观测提供可信的 `frame_id`、`observation_id`、`landmark_id`。首版可以人工标注关联，**不能仅凭 OCR 字符串相同就认定是同一扇门**。
- **M2 · 开发分支已独立实现：** M2.1 `LandmarkMemory`；M2.2 编号规则基线；M2.3a 受约束文本 LLM Planner 与安全回退；M2.3b 本地 Ollama/Qwen3-4B；M2.3c 冻结 20 场景评估；M2.3d 可选 Prompt v2/v3（**v2 默认，v3 策略效果仍不稳定**）；M2.4 JSONL/标准输入流式观测、增量规则决策与同帧重复计数修复。**本地 WSL 全量 194 项单元测试通过**。模拟 `FB541 → FB543 → FB545` 回放可依次返回 `REOBSERVE` / `VERIFY_TARGET` / 人工确认后 `FOUND`，但这**不是现场寻房成功案例**。
- **历史离线对照（Prompt v2）：** 20 个**人工设计、冻结的单步场景**中，旧 Rule **19/20** 动作合法且可接受；Qwen **10/20** 可接受、**20/20** 合法、**0** 次 fallback、**17/20** 弃权。M2.4 修复已知同帧 Bug 后，Rule 在**同一套已暴露**场景上回归为 **20/20**；这是修复回归，**不是独立泛化验证**。Prompt v3 只有 5 次开发检查，不能据此推断真实策略更优。
- **M3/M4 · 尚未开展真实闭环与正式对照：** M2.4 尚未收到实时 M1 观测；没有多门牌实地寻房的成功率、查看门数、行走时间/距离指标。当前使用文本 OCR + Rule / 文本 Qwen，未部署 VLM，现阶段也不必以 VLM 为前置条件。

## 最初的文本推理基线（历史集成路径；最终目标见上文新架构）

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

浏览器以约 **1.5 秒/帧**采样，而非 30 FPS 连续视频。每次 Record 先稳定镜头约 5 秒，再采样、OCR、保存约 12 秒并自动停止。**当前 `main` 已支持保存后续 PNG 无损采样帧、原始 OCR、人工复核及导出**；Gradio UI 依赖固定为 **6.30.0**。原始数据位于 Linux 本地 `data/live_sessions/`，2026-10-18 的既有 JPG 不会自动转换。OCR 不是人工确认真值，`segment_id` / `side` 可人工标注，不代表定位或 SLAM。

## 快速开始

**本分支的 M0 程序**（远程推理电脑，Python 3.10+）：

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

在 MacBook 浏览器打开 `http://127.0.0.1:7860`，授权访问摄像头；摄像头由 **MacBook 浏览器**采集，SSH 只转发网页连接。每轮先填写采集条件；点击 `Record` 后约 5 秒用于稳定相机，不做 OCR 或保存；随后约 12 秒采样、识别、保存并自动停止。首次加载 OCR 模型可能较慢；界面耗时仅为**远端 OCR 执行时间**，不包含网络、排队与显示延迟。详见[部署说明](docs/setup.zh-CN.md)。

**M2.4 离线流式回放**需先切换到开发分支（以下命令**不能直接在 `main` 使用**）：

~~~bash
git switch feat/m2-reasoning
pip install -e .
python -m roomnav.stream_cli --target FB545 --input examples/m24_stream_demo.jsonl
python -m unittest discover -s tests -q
~~~

此流式 CLI 默认**仅使用 Rule-based**，不会自动调用 Ollama/Qwen；`FOUND` 必须由独立的人工确认事件授权。单张照片调试仍可使用 `scripts/ocr_image.py` 或 `apps/photo_demo.py`。

## 最初 M0–M4 进度与分工（历史状态，新增要求见上文）

| 阶段 | 关键工作 | 当前状态 |
| --- | --- | --- |
| **M0 · 取帧/OCR** | 远程 OCR、定时取帧、PNG 保存、人工复核和现场导出 | **PR #1 已合并 `main`**；LSS101 单条件 1 次 Record / 6 帧，5/6 逐帧完整识别，其他条件仍待验证 |
| **M1 · 视觉感知** | 完整房号提取、同门跨帧关联、不同门牌去重与观测 ID | **队友下一阶段重点**，未完成 |
| **M2 · 记忆与策略** | Rule / 文本 Qwen、离线评估、流式 JSONL 决策 | **M2.1–M2.4 已在开发分支完成**，尚未接真实 M1 输入 |
| **M3 · 现场寻房闭环** | 将 M1 输出接入 M2.4，由人执行观察建议 | 尚未集成 |
| **M4 · 受控对照** | 逐门搜索 vs Rule vs 文本 LLM；记录成功率与成本 | 尚未开始 |

- **成员 A（推理/评估）：** 维护 M2、Rule 和文本 Qwen 对照，负责 M1/M2 适配与 M3/M4 评价方案。
- **成员 B（感知/现场）：** 维护 M0 采集与可复现实验记录，继续开发 M1 完整门牌提取、物理门关联、真实观测 ID 和现场测试。
- **共同：** 固定 `Observation` 数据契约，区分 OCR 原始证据与人工确认，分析失败案例并完成多门牌实地演示。

**近期工作：** 队友从最新 `main` 创建独立 M1 分支，完成完整房号提取、可靠 `frame_id` / `observation_id`、同一实体门的稳定 `landmark_id` 关联与可回放 JSONL，按 [M2.4 Observation 接口](https://github.com/sceneeeee/room-sign-guided-navigation/blob/feat/m2-reasoning/docs/m24-streaming.zh-CN.md) 输出；先用**至少 3 扇不同实体门**验证，并保留误读/漏检样本。随后由成员 A 接入 M2.4，开始 M3 的人机闭环联调。**M1/M3 尚未完成，M2 也尚未合并。**

## 参考资料与文档

- [ReasonNav 项目主页（CoRL 2025）](https://reasonnav.github.io/)
- [ReasonNav 官方代码](https://github.com/ReasonNav/ReasonNav)
- [实验设计](docs/experiment.zh-CN.md)
- [1F 手机 + Camo 现场测试与上传完整 SOP](docs/field-test-sop-1f.zh-CN.md)
- [路线与分工](docs/roadmap.zh-CN.md)
- [`main` 的早期模块接口](docs/interfaces.zh-CN.md)
- [M0 采集与人工复核实现：已合并 PR #1](https://github.com/sceneeeee/room-sign-guided-navigation/pull/1)
- [LSS101 现场实验记录](docs/field-tests/2026-10-18-1f.md)
- [1F 手机 + Camo 现场测试 SOP](docs/field-test-sop-1f.zh-CN.md)
- [M2.3c 冻结场景与离线策略评估（开发分支）](https://github.com/sceneeeee/room-sign-guided-navigation/blob/feat/m2-reasoning/docs/m2-offline-evaluation.zh-CN.md)
- [M2.3d Prompt v3 开发报告（开发分支）](https://github.com/sceneeeee/room-sign-guided-navigation/blob/feat/m2-reasoning/docs/m23d-prompt-v3.zh-CN.md)
- [M2.4 流式接口与 Rule 帧门槛修复（开发分支）](https://github.com/sceneeeee/room-sign-guided-navigation/blob/feat/m2-reasoning/docs/m24-streaming.zh-CN.md)
- [环境部署与 SSH](docs/setup.zh-CN.md)

**研究边界：** 人工设计的单步测试成绩和单条件 OCR 现场样本，不代表真实寻房成功率或模型性能优势；工程整合本身也不等于已证明的算法创新。OCR 误读、跳号、编号规则不一致以及主动重复观察属于后续需进一步查新验证的研究方向。

## 数据与许可证

不要将包含可识别个人信息的原始视频、图片、账户凭据上传到仓库。目前尚未选择开源许可证，公开复用与发布前需明确许可。
