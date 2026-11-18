# Isaac Sim 单门视觉引导实验

把当前 M3.2 的 `DoorApproachPreview` 接入 Isaac Sim，观察图像驱动的选门、对准、短距离接近和停读。相机每次移动后重新渲染，下一次动作由新画面的 YOLOE／RapidOCR 结果决定。

[三组对照结果与审核证据](../../docs/experiments/isaac-single-door/README.zh-CN.md)。

## 实验范围

- 使用 `roomnav.rgb_perception.YOLOWorldDetector`、`perceive_frame` 和 schema3 `DoorApproachPreview`。检测阈值、跟踪、歧义检查、连续居中条件和每会话一次前进预算采用主线实现。
- 采用与前期教学楼相同的 USD 建模方式，生成可独立重建的小段教学走廊。默认门牌在门扇内，`wall_plate` 将它放在门旁墙面，检验当前包含关系假设。
- 策略只接收渲染 RGB 的检测框和 OCR。每帧的门位置真值由 USD 几何投影得到，另存于轨迹，供运行结束后评分。
- 执行端是有界运动学相机。每次转动 4°，每次前进 0.45 m，镜头高 1.48 m。实验测视觉反馈闭环，底盘动力学、碰撞避障和 ROS 执行仍需独立验证。
- 仿真时间戳是采样序号除以 2。模型推理的实际耗时另行记录。该结果不能衡量手机实时帧新鲜度和人机反馈延迟。
- `motion_authorized=false` 保留在原策略输出中。实验脚本单独记录 `simulated_camera_actuator` 的动作与前后位姿，未调用人工反馈接口。

## 环境与运行

渲染使用已配置的 Isaac Sim 6.2 Python。感知使用另一套 Python 环境，以兼容项目的 PyTorch、Ultralytics 和 RapidOCR。需要 FFmpeg、DejaVu Sans 字体及 YOLOE 权重。

在独立感知环境安装项目的 `vision`、`detection` 依赖，下载 `yoloe-11s-seg.pt`。实际验证的版本与权重哈希见每次运行的 `manifest.json`。

随代码提供的 `prompts.mobileclip.json` 缓存了原始三个检测提示词的 MobileCLIP 文本特征。感知进程验证提示词完全一致，再通过 YOLOE 自身的 `get_tpe` 和 `set_classes` 应用特征。这样远程推理无需再传输约572 MiB的文本编码器。原提示词和模型权重均保留，不使用房号或场景信息生成特征。缓存文件记录编码器 SHA256 及生成时的 PyTorch 版本。

从仓库根目录运行，按机器情况替换两个 Python 和权重路径。输出目录必须尚不存在。

```bash
export PYTHONPATH="$PWD/src"
export OMNI_KIT_ACCEPT_EULA=YES
/path/to/isaacsim/python examples/isaac_single_door/run.py \
  --perception-python /path/to/perception-env/bin/python \
  --weights /path/to/yoloe-11s-seg.pt \
  --text-features examples/isaac_single_door/prompts.mobileclip.json \
  --font /usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf \
  --device cuda:0 \
  --output outputs/isaac-single-door/run-01
```

可用 `--case center` 或多个 `--case` 限定用例。默认运行左偏、右偏、正对、近处、中距离、远处、空白门牌、仅墙面、双门竞争、遮挡和墙面门牌共11个用例。默认768×432，可用 `--width 1536 --height 864` 比较分辨率。默认最多30帧，连续7帧未移动或策略锁定停止时结束。停读机会后保留原位，最多尝试三次 OCR。一个关联 ROI 中的唯一房号读数 confidence≥0.8 时结束尝试，该条件不使用目标房号。

`--ocr-border 12` 在检测器给出的每个 sign 裁剪图四周添加12像素白边，再调用原 `read_roi`。默认0直接使用主线裁剪。该选项用于比较紧裁剪导致的 OCR 空结果，不改变检测框、门牌像素内容或原图中的 ROI 尺寸。原策略的32×16像素停读条件继续使用原始检测框计算。带白边的结果在 manifest 中明确记录，主线 `src/roomnav` 无代码改动。

省略 `--text-features` 会使用原始文本编码器路径并可能下载 `mobileclip_blt.ts`。如需重新生成缓存，在安装了 PyTorch 和项目指定 CLIP 的环境中运行，输出应选新文件。

```bash
PYTHONPATH=src python examples/isaac_single_door/prepare_text_features.py \
  --encoder /path/to/mobileclip_blt.ts --output /tmp/new-prompts.json
```

## 产物与检查

每个用例保存原始 `rgb_*.png`、检测叠加图、2 FPS 的 `demo.mp4`、带相对纹理引用的 `scene.usda`、`events.jsonl`、`trace.jsonl` 和 `summary.json`。根目录汇总结果、依赖版本、权重／源码 SHA256 和感知进程日志。渲染视频用于检查动作过程，其播放时长与实际推理耗时分别记录。

```bash
PYTHONPATH=src python examples/isaac_single_door/verify.py \
  outputs/isaac-single-door/run-01
```

该命令重新计算主线策略输出、核对 RGB 文件哈希、检查相机位姿是否按建议变化，并从原始轨迹重建评分。视频可用 `ffmpeg -v error -xerror -i demo.mp4 -f null -` 完整解码核验。

提交的紧凑审核包位于 `docs/experiments/isaac-single-door/`。其中的 gzip 日志包含逐帧感知、原策略输出、相机位姿、门投影真值和 RGB SHA256。使用 `--logs-only` 可以在无需 Isaac Sim、模型或原始 PNG 的机器上回放策略并核对评分。这个模式只验证已保存日志，完整 RGB 校验仍需本地运行目录。

成功要求最终 `STOP_AND_READ`、关联 ROI 的唯一高置信度 OCR 房号解析为目标 `LSS119`、镜头朝向误差不超过12°，且整个选中轨迹与目标门的投影框 IoU 均不低于0.5。`approach_then_read` 进一步要求实际执行过前进动作。近处直接读到门牌会单独显示为读牌成功。空白门牌和墙面误报即使触发停读，也不计为成功。

11个用例用于开发与回归，场景已经被开发者观察过。保留失败输出，结果不能外推为实楼检测准确率或实地寻房成功率。M1／M2 的实体门关联、独立人工房号确认与 `FOUND` 仍需其自身验证。
