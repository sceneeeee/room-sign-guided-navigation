# 基于门牌与指示牌的室内导航（Room & Sign-Guided Indoor Navigation）

[English](README.md) · **阶段：研究原型初始化；目前尚无实验结果**

## 核心目标

当目标房间（例如 `EB104`）的具体位置未知时，系统利用**已经观察到的门牌、方向指示牌及少量拓扑关系**，推断下一处应该去哪里观察，再逐步找到目标。

**第一阶段不使用 Frogbot。** 由人手持手机 / USB RGB 摄像头在真实教学楼中移动；MacBook 负责现场交互，可通过 SSH 与远程 GPU 电脑通信；远端运行文字识别与高层推理。模型返回下一步建议，由人执行并采集新观察。

研究问题分为两层：

1. **视觉感知：** RGB 图像能否稳定找到门牌并读取完整编号？
2. **空间推理：** 当目标尚未出现时，基于已有编号和走廊关系，能否提高搜索效率？

## 系统结构

```text
手机照片 / USB RGB 相机 / 录像
          ↓
门 / 门牌检测 + OCR / VLM         （待开发）
          ↓
观测结果：编号 + 左右侧 + 走廊段 + 时间戳
          ↓
Landmark Memory                 （已有最小结构）
          ↓
规则基线 / VLM 高层推理          （规则基线已实现；VLM 待开发）
          ↓
下一步：前进 / 查看另一侧 / 返回 / 重新观察
          ↓
人执行动作并继续采集
```

注意：手持阶段的 `segment_id` 和 `side` **允许人工标注**，它们不是通过 SLAM 自动估计的全局坐标。未实现的功能已明确标出。

## 运行最小规则基线

要求 Python 3.10+。

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -e .
roomnav-baseline --target EB104 --observations examples/observations_eb.jsonl
python -m unittest discover -s tests -v
```

示例数据为 `EB102 → EB103`；规则基线可能输出“沿当前走廊继续”，但会明确指出这依赖编号沿走廊局部单调的假设，并非对真实 EB104 位置的确认。

可选单张图片 OCR / 浏览器照片采样界面：

```bash
pip install -e '.[vision,ui]'
python scripts/ocr_image.py /path/to/door.jpg
python apps/photo_demo.py
```

远端运行 Gradio 后，MacBook 通过 `ssh -N -L 7860:127.0.0.1:7860 USER@GPU_HOST` 转发端口，浏览器访问 `http://localhost:7860`。目前此界面只输出 OCR 原始文字，**尚不包含门检测、地图关联或 VLM 决策**。

## 确定的项目范围

| 阶段 | 核心工作 | 验收输出 |
| --- | --- | --- |
| M0 · 照片 | 实拍门牌，RapidOCR / PaddleOCR / VLM 对照 | 完整编号识别率和失败分类 |
| M1 · 记忆与推理 | 记录已见门牌，完成编号规则基线，再加入 VLM | 不同方法的下一步搜索建议 |
| M2 · 视频 | 从录制视频提取门牌观测，多帧融合与去重 | 可复现的视频测试报告 |
| M3 · 线下闭环 | 人 + 摄像头 + MacBook SSH + 远程推理 | 人按系统建议找到目标房间 |
| M4 · 评估 | 同起点、同预算比较逐门搜索、规则与 VLM | 成功率、行程、查看门数、时间 |

**暂不涉及：** Frogbot、D455f 深度、LiDAR、SLAM、Nav2、云台硬件，及 ReasonNav 整机部署。这些可作为后续扩展，不影响当前实验。

## 两个人的分工

- **成员 A（你）：** Landmark Memory、规则与 VLM 推理策略、下一步动作选择、实验对照与评估。
- **成员 B：** 摄像头/照片/视频输入、门和门牌检测、OCR/VLM 读取、多帧去重与感知指标。
- **共同：** 约定观测 JSON 接口、真实走廊数据采集、线下人机闭环与实验复盘。

## 参考资料

- [ReasonNav 项目主页（CoRL 2025）](https://reasonnav.github.io/)
- [ReasonNav 官方代码](https://github.com/ReasonNav/ReasonNav)
- [实验设计](docs/experiment.zh-CN.md)
- [路线与分工](docs/roadmap.zh-CN.md)
- [模块接口](docs/interfaces.zh-CN.md)
- [环境部署与 SSH](docs/setup.zh-CN.md)

目前的算法价值首先是**可验证的工程复现与实验对照**，不是声称原创了 OCR+VLM 找房间；OCR 误读、跳号、编号规则不一致以及主动重复观察属于后续需进一步查新验证的研究方向。