# M4 开源项目参考与对照实验协议

核查日期为 2026-11-20。优先在同一批真实图像上对比 NavOCR 和 RapidOCR，再用相同真实视频对比 BoT-SORT 和现有 IoU 跟踪。ReasonNav 用于参考决策接口，TextMap_Examples 用于评估未来 SLAM/Nav2 迁移。本轮核查公开论文、项目文档和接口，不安装依赖、不下载权重、不复制外部代码。本页只有实验协议，尚无基于真实标注数据的对照结果。

## NavOCR

- **目标与许可。** 面向导航场景的 OCR，检测门牌、导向标识等相关文字和标识。仓库声明 Apache-2.0。推理与数据生成器是两项独立工作，实际使用前记录所选权重与数据的来源、版本和许可。上游 README 尚未给出新版模型的 FPS 基准。 [NavOCR 仓库与 README](https://github.com/kc-ml2/NavOCR#readme) · [许可证声明](https://github.com/kc-ml2/NavOCR#license)
- **输入、输出与依赖。** 独立脚本接收单张图像或图像目录，用微调的 PP-YOLOE 检测区域，然后用 PP-OCRv5 读取裁剪图。独立实现返回像素框、检测分数、类别和原始文字或错误值。该分数字段来自检测器，不能当成 OCR 置信度。默认 ONNX 路径依赖 ONNX Runtime、OpenCV、NumPy、PyYAML，可在 CPU 运行。另有 OpenVINO、Paddle 和 PyTorch 路径。ROS 2 是可选接口，README 说明其导向标识类别只做检测。 [运行与后端文档](https://github.com/kc-ml2/NavOCR#backend-composition) · [独立入口](https://github.com/kc-ml2/NavOCR/blob/main/navocr_standalone.py) · [ROS 2 文档](https://github.com/kc-ml2/NavOCR#ros-2-node)
- **RoomNav 适配。** 对应 M1，可比较同一标牌 ROI 上的识别器，或比较哪套全图流程更容易找到并读出墙面标牌。RGB-only 足够做这两类对照。用适配层保留检测与 OCR 分数的来源、失败原因及原始文字。M1 的门牌归属、M2 实体记忆和 M4 独立确认继续使用 RoomNav 的证据规则。
- **实验成本与优先级。** 值得优先做隔离 A/B。需要选定版本的 NavOCR 推理权重和相同真实图像的独立标注。可复用上游预训练权重，无需重新训练或 GPU。先用 CPU ONNX 路径及下述协议。

## Ultralytics 与 BoT-SORT

- **目标与许可。** Ultralytics 提供基于 Python/PyTorch 的视觉检测和跟踪工具，RoomNav 的 RGB 检测已依赖该库。上游仓库采用 AGPL-3.0，发行方另提供 Enterprise 许可。评估所用包和现有 YOLO 权重的版本与授权应分别记录。BoT-SORT 原论文针对多行人跟踪，结合运动模型、相机运动补偿和可选外观重识别。作者另有 MIT 许可的研究实现，本文的 A/B 使用 Ultralytics 的实现。论文中的行人基准结果不能直接推导门体跟踪效果。 [Ultralytics 仓库许可证](https://github.com/ultralytics/ultralytics/blob/main/LICENSE) · [发行方许可说明](https://www.ultralytics.com/license) · [BoT-SORT 原论文](https://arxiv.org/abs/2206.14651) · [作者的研究实现](https://github.com/NirAharon/BoT-SORT)
- **输入、输出与依赖。** `model.track()` 接收视频或帧序列，输出带跟踪 ID 的视觉检测结果。底层 `BOTSORT.update()` 接收每帧检测框、分数与类别，可使用相应图像做相机运动补偿。固定 `botsort.yaml`、缓存检测和对应视频帧，首轮使用 `with_reid: false`，无需新跟踪权重或 GPU。后续独立评估 ReID 时需要明确记录所用特征或编码器，部分版本的 `auto` 配置可能自动获取备用权重。 [Ultralytics 跟踪文档](https://docs.ultralytics.com/modes/track/) · [BOTSORT Python API](https://docs.ultralytics.com/reference/trackers/bot_sort/)
- **RoomNav 适配。** 对应 M1 跟踪和 M3 目标保持。手持 RGB 足够测试相机转向时的短期跟踪。跟踪 ID 仍是临时视觉身份，M2/M4 的实体约束和确认门槛不随跟踪器改变。跟踪缓冲中的预测框不能当成当帧的新检测或新 OCR。
- **实验成本与优先级。** 值得做隔离 A/B。用相同视频和相同检测提议对比 `ConservativeTracker` 与 BoT-SORT。需要物理门和门牌的逐帧关联标注。初次对照复用缓存检测，记录 CPU 处理时间和跨门误接续。

## ReasonNav

- **目标与许可。** CoRL 2025 论文 *Human-like Navigation in a World Built for Humans* 探索让机器人利用门牌、方向标识、人和地图前沿等地标进行室内导航。核查时，公开仓库根目录未见项目级许可证或 GitHub 许可证标记；代码复用需要维护方明确授权，各依赖另行核对。这里仅引用论文与公开架构。 [作者项目页](https://reasonnav.github.io/) · [作者论文](https://arxiv.org/abs/2509.21189) · [公开仓库](https://github.com/ReasonNav/ReasonNav)
- **输入、输出与依赖。** VLM 读取 JSON 地标记忆和按类型标记地标的俯视地图，选择一个地标，再由机器人系统执行对应的读标识、查门号、问路或探索行为。公开部署依赖 Linux/WSL2、ROS 2、NanoOWL TensorRT 编码器和模型/API 配置，仿真另需 Isaac Sim 4.5 容器。作者报告在 NVIDIA RTX 4070、4090 上测试，配置说明列出 GPT-4.1 与搜索 API。复现整栈需要相应权重、场景或真实传感器数据和算力。 [架构与行为](https://reasonnav.github.io/) · [运行依赖与 API 配置](https://github.com/ReasonNav/ReasonNav#setup)
- **RoomNav 适配。** 对应 M2 的结构化证据和 M4 的行为选择，也可借鉴 M2 观察意图与 M3 接近处理之间的边界。保留可引用的候选门、近期观察和证据不足时的重观察。当前手持 RGB 方案没有其俯视地图与定位输入，采用接口思路无需新权重、数据或 GPU。整套机器人栈保持为论文参考。
- **实验成本与优先级。** 暂不做整栈 A/B。后续若需评估地标摘要或下一观察意图的设计，可固定两个版本可见的 RGB 证据、模型与预算，在同一批回放上比较有效候选选择、弃权和错误地标引用。该对照评估的是 RoomNav 自己的接口设计。

## TextMap_Examples

- **目标与许可。** 演示以场景文字为查询目标的导航。NavOCR 提供 2D 检测，TextMap 将文字投影成 3D SLAM 地标，`text_nav_bridge` 从已保存的地图中匹配文字并向 Nav2 发送 `NavigateToPose`。示例仓库声明 Apache-2.0，各依赖、数据和示例资产的许可需要分别核对。 [TextMap_Examples README](https://github.com/kc-ml2/TextMap_Examples#readme) · [示例仓库的许可证声明](https://github.com/kc-ml2/TextMap_Examples#license)
- **输入、输出与依赖。** 除 RGB OCR 检测外，TextMap 需要深度图、相机内参、里程计及 SLAM/定位信息。它发布 RViz 地标并可保存 YAML。例程以 ROS 2 Humble 为主要测试环境，Gazebo 示例使用 `slam_toolbox` 和 Nav2。真实示例用 RealSense D455 或 rosbag 与 `rtabmap_ros`，上游将其覆盖范围列为文字建图。CPU 可运行 ONNX/OpenVINO 路径。复现时需要 NavOCR 权重及匹配的传感器记录或仿真环境。 [TextMap I/O](https://github.com/kc-ml2/TextMap#interface) · [示例覆盖范围](https://github.com/kc-ml2/TextMap_Examples#examples) · [真实 rosbag 指南](https://github.com/kc-ml2/TextMap_Examples/blob/main/examples/realworld.rtabmap.rosbag.guide.md)
- **RoomNav 适配。** 对应未来 M2 空间地标记忆、M3 目标执行接口及 M4 与 SLAM/Nav2 的衔接。可参考文字观察与有坐标的地标分离、位姿图更新后的地标重定位和地图导出接口。当前 RGB-only 手持运行缺少建立该地图所需的输入，保持现有非执行式建议及人工确认接口。
- **实验成本与优先级。** 近期不做 M4 实时 A/B。未来拿到对齐的 RGB-D、相机标定、位姿和基准地标后，可先对同一 rosbag 做离线建图对照，记录同名实体混淆、回环后的重定位和文字地标位置误差。该实验无需发送真实 Nav2 动作。

## 真实数据 A/B 协议

### A. NavOCR 与 RapidOCR

1. 从获准使用的真实手持视频提取同一批原始 RGB 帧，标注可读文字、可见标牌框、物理门归属和歧义。纳入墙面标牌、空白、导向标识、反光、运动模糊、远距离和类似房号。按录制序列及物理门划分调参与评估集合，避免近重复帧跨集合。
2. **同裁剪对比。** 两个识别器接收完全相同的人工标注 ROI，固定像素和方向，明确 RGB/BGR 转换。NavOCR 使用独立识别入口，RapidOCR 使用现有 ROI 路径。逐项保存原文、各自提供的 OCR 置信度和失败原因。缺失置信度保留为空。
3. **同全图对比。** 对相同原始帧分别跑现有 YOLOE 标牌检测 + RapidOCR 与 NavOCR 独立检测识别。固定门体检测和 RoomNav 下游归属逻辑。按预先固定的一对一框匹配分别计算标牌检测召回率、误检率和端到端标准房号命中率，并单记错误归门和几何歧义下的弃权。若检测器漏掉标牌，端到端统计应计入漏检。
4. 汇报字符错误率、可读标牌的标准房号精确匹配（沿用 RoomNav 的房号解析）、漏读与空白/无关标识上的假读数。逐组给出分母，按门或视频汇总配对差异。分别测冷启动及预热后的检测、识别和总耗时，记录 CPU、线程、分辨率、配置与模型指纹。

### B. BoT-SORT 与 ConservativeTracker

1. 对同一组真实视频逐帧标注门和标牌、帧间物理身份及遮挡。包含突然转向、遮挡再出现、相邻相似门、相同文字和离开画面后重访。
2. 两个跟踪器复用相同 RGB 帧、帧序、PTS、检测框、分数、类别与预过滤设置。记录各自的关联阈值、缓冲与版本，在调参集合固定后再跑评估集合。首轮关闭 ReID，并单记相机运动补偿配置。以相同类别约束适配检测，保留缺失帧和失败原因。
3. 区分每次可见序列的 ID 切换、轨迹中断与恢复、丢失已锁定门和错误跨门接续。报告人工标注分母、每段视频结果、门牌错归门和 M3/M4 因误锁定产生的错误候选，并记录跟踪耗时的 p50/p95。重访期间换 Track ID 可与下游仍能正确复用物理实体同时成立，两项分别统计。
4. 只有在真实回放显示跨门错误接续和误关联可接受、并且不破坏新帧检查、人工反馈和 STOP 后，再讨论默认跟踪器变更。后续若评估 ReID 或单独测相机运动补偿，每次只调整一项配置。
