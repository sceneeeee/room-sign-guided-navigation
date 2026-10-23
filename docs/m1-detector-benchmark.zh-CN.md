# M1.2 — RGB 门 / 门牌检测基线对比（线上可执行）

**2026-10-23 新增：实验工具和测试已加入 PR #4，模型对比尚未在这批新配置上实测。** 这里的检测器只提出 RGB 图像中的候选门框/标牌框，绝不提供门牌编号、距离、物理门身份或通行安全保证；它也不调用 SLAM、深度、LiDAR 或 VLM。

## 为什么现在要做

Codex 在同一扇 LSS101 门的 3 张已公开 RGB 帧上用 YOLO-World 默认提示词进行实测：**3 张均未检出 door**，唯一分数约 0.446 的 sign 框住了开关/插座，属误检；CUDA 推理可运行，热推理约 23–26 ms。这是 **一次与样本相关的实测诊断**，既不是独立多门统计，也不代表 YOLOE 的结果。

因此应优先测试**模型 / 提示词 / 置信度组合对实际门检测与标牌误检的影响**，而非根据模型宣传直接转去训练 VLM。

## WSL 使用方法（不影响原实时页面）

~~~bash
cd ~/projects/room-sign-guided-navigation
git status --short
git pull --ff-only
.venv/bin/python -m unittest discover -s tests -q
~~~

初步验证：**未标注的现有图片只展示候选框，不会生成 precision/recall**。

~~~bash
.venv/bin/python scripts/benchmark_rgb_detection.py \
  --image-root . \
  --images \
    docs/field-tests/assets/2026-10-18-1f/frame_000001.jpg \
    docs/field-tests/assets/2026-10-18-1f/frame_000002.jpg \
    docs/field-tests/assets/2026-10-18-1f/frame_000006.jpg \
  --weights yolov8s-world.pt yoloe-11s-seg.pt \
  --profiles baseline office doorway \
  --thresholds 0.10 0.20 0.30 0.50 \
  --device cuda:0 \
  --output outputs/door-smoke-20261023.json
~~~

这将逐一加载 2 个模型 × 3 套提示词：每次模型与提示词组合独立运行，**不会在 GPU 同时保留全部模型**。第一次运行可能需要下载 YOLOE 权重和文本编码器，存在 CLIP、TLS、VRAM、模型 API 等实际兼容性风险；个别组合失败会在结果中记录 \`status=error\`，不能当作该组合没有检测到门。先用这 3 张小图判断候选框是否看起来符合实体门/门牌，再使用更多独立房间做有标注评估。

提示词配置位于 \`scripts/benchmark_rgb_detection.py\`：
- \`baseline\`：原版 door / room number sign / directional sign；
- \`office\`：office door、closed door、wooden door、room number plate 等，增加 electrical outlet 作为竞争负类（该类不会作为导航候选）；
- \`doorway\`：interior door、doorway、door frame、wall mounted room sign 等，并增加 light switch 竞争负类。

注意：加入负类只是实验设计，**尚未证明能减少开关/插座误检**。当前 \`YOLOWorldDetector\` 兼容 \`yoloe-*.pt\` 以使用 Ultralytics 的 \`YOLOE\` 类和相同的 \`set_classes\` 文本接口。YOLOE \`*-seg.pt\` 虽有分割输出，本工具只取检测框用于同标准比较。

## 有人工标注后再计算 TP / FP / FN

将已获拍摄许可的图片放在例如 \`data/detection_eval/\`（默认 gitignored），为所有可见**物理门**和**门牌/指示牌**标注**完整的正例框及负例**，JSONL 每行一张图片：

~~~jsonl
{"image":"hall-a/frame01.png","scene_id":"hall-a","annotation_complete":true,"annotations":[{"category":"door","bbox_xyxy_norm":[0.10,0.08,0.80,0.96]},{"category":"sign","bbox_xyxy_norm":[0.63,0.18,0.76,0.26]}]}
{"image":"hall-a/frame02.png","scene_id":"hall-a","annotation_complete":true,"annotations":[]}
~~~

**上述示例是假设性格式示例，不是任何真实图像的人工真值，不能直接用作评测集。**

- \`image\` 相对于 \`--image-root\`，不得用绝对路径或 \`..\` 离开数据根目录。
- \`scene_id\` 应用于同一个实体门/走廊相关帧的分组。**3 张相关帧不是 3 个独立环境**。
- \`annotation_complete=true\` 代表你已经人工核对**所有在本评测定义中应被框选**的门与标牌。漏标会把真门错误算成模型 FP；仅当人工确认不存在目标时，才把 \`annotations: []\` 视为负例。
- \`bbox_xyxy_norm=[x1,y1,x2,y2]\`，以原图宽高归一化到 0–1，左上至右下；不能把门牌框混为整扇门框。
- \`sign\` 是门牌或导航指示牌的合并候选类，分类准确率还需要额外的细分类实验。
- 该评测只使用框真值，不使用房号文本、房间编号规律或地图坐标。

有人工真值时：

~~~bash
.venv/bin/python scripts/benchmark_rgb_detection.py \
  --image-root data/detection_eval \
  --manifest data/detection_eval/labels.jsonl \
  --validate-only \
  --output outputs/door-label-validation.json

.venv/bin/python scripts/benchmark_rgb_detection.py \
  --image-root data/detection_eval \
  --manifest data/detection_eval/labels.jsonl \
  --weights yolov8s-world.pt yoloe-11s-seg.pt \
  --profiles baseline office doorway \
  --thresholds 0.10 0.20 0.30 0.50 \
  --device cuda:0 \
  --output outputs/door-detection-benchmark.json
~~~

结果中每个组合在 IoU ≥ 0.5 条件下按分数排序进行**一对一贪心匹配**，记录两类 TP/FP/FN、precision、recall、F1 和每个 \`scene_id\` 的分组统计。它**不是 COCO mAP/AP**，也不能利用这几个相关帧宣称泛化成功。输出还包含模型类别提示、单帧推理时间、首帧时间、显式输入图片哈希与 manifest SHA-256，方便复现实验。

### 如何决定下一步

1. **只做 3 张图的烟雾测试**：检查 YOLOE / 改提示词是否能提出较真实的门候选，是否仍把开关当标牌；逐框人工检查，不报总体召回率。
2. **Issue #2 的标注到位之后**：至少覆盖不同**实体门、走廊、闭/开门、远/近与强反光**；统一标注规则与数据划分，才能比较每门检出率和真实 FP。
3. 若所有开放词汇模型在关门场景持续漏检，**优先收集标注后微调轻量专用门检测器**，而不是马上训练视频 VLA 或换更大的通用 VLM。
4. 若门框尚可、标牌漏检严重，则单独评估更近距离的 ROI/文字检测（RapidOCR 仍作为原始证据），将门检测、门牌 OCR、门牌—门关联分别量化。
5. 最终可选稳定模型作为 M3 引导候选来源，但**在实测前不要替换原有默认实时模型或自动开启无人监管运动**。

### 设备与安全说明

开发机 4060 Ti 8GB 可优先试 \`yoloe-11s-seg.pt\`，不要默认以“首帧热启动时间”冒充端到端摄像头延迟。YOLOE 首次调用 \`set_classes\` 可能下载编码器；运行前保证有效网络及已安装的检测依赖。原始视频、含个人信息的素材、模型下载及 JSON 结果只在获许可的本地私有目录；\`outputs/\` 与 \`data/\` 均被 Git 忽略。真人移动与障碍安全仍归操作者负责。
