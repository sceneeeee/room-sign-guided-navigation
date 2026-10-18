# 1F 门牌识别现场任务与上传流程（手机 + Camo）

> 本页是早期计划；当前现场操作和上传命令以[完整 SOP](field-test-sop-1f.zh-CN.md)为准。

## 任务边界

现场链路由测试者报告为：手机摄像头 → Camo 虚拟摄像头 → MacBook 浏览器 → `ssh -N -L 7860:127.0.0.1:7860 linux` → Linux 上的 Gradio/RapidOCR → 浏览器显示原始 OCR。SSH 转发的是网页连接，摄像头画面由 MacBook 浏览器采集并提交。`apps/live_demo.py` 将每个实际提交的采样帧、原始 OCR 和远端处理时间写到 Linux 本地 `data/live_sessions/`；人工真值和判断在页面右侧的“人工复核”区另存。它不执行门牌去重或导航。

本次目标是验收 M0 的现场链路，并为 M1 找到真实环境下能够完整识别的条件和失败条件。不要把本次测试写成 M2/M3 寻房闭环。

**低机位约束：** 本次模拟小车时，摄像头中心尽量固定在离地约 1.0 m；门牌中心约 1.75 m，以实测为准。主测试保持与未来小车相同的相机俯仰设置，通过改变水平距离与机身朝向取景，不把相机举到人眼高度。若小车将来有俯仰机构，可另做“允许上仰”条件；若没有，人工上仰不能计入小车主条件。高机位对照也须单独标记。

## 地图与起点

用户提供的 1F 疏散图中，“您所在位置”标记位于右侧区域的 `LSS101` 门口附近。左侧还有不同的 `LSN101`。开始前拍摄实际门牌，确认完整前缀；若实物与图不符，以实物为准并记录差异。疏散图用于规划采集区域和人工核对，不将未看到的房号作为导航算法已知观测。

按实际开放的走廊分区采集，以下是观察候选点，不是精确行走指令：

| 区域 | 图上可选门牌 | 采集目的 |
| --- | --- | --- |
| A：右侧上方，起点附近 | LSS101、LSS124、LSS122、LSS121、LSS119 | 验证起点与不同数字长度、光照和角度；先确认实际可达及实物编号 |
| B：右侧外沿及下方 | LSS118、LSS117、LSS116、LSS115、LSS111、LSS110 | 移动拍摄、较远距离、不同门牌背景 |
| C：左侧区域，允许通行时再测 | LSN101、LSN102、LSN112、LSN118、LSN124 | 检查 `LSN` 与 `LSS` 前缀混淆和重复的 `101` |

先在 A、B 各选至少 2 块实际可见的门牌；时间允许再到 C 选至少 2 块。不要根据图推断没有看到的门牌、走廊通行情况或编号连续性。

## 现场执行顺序

1. 在 Linux 端保持 `python apps/live_demo.py` 运行；MacBook 确认 Camo 画面正常，在浏览器 `http://127.0.0.1:7860` 的摄像头选择器中选 Camo。若 SSH 隧道尚未运行，在 MacBook 终端执行 `ssh -N -L 7860:127.0.0.1:7860 linux` 并保持终端开启。若端口已被占用，先确认现有隧道，不重复启动。
2. 记下页面顶部的 Session ID，以及日期、地点、起点实物门牌、手机与 Camo 的分辨率设置、光照、代码提交号（`git rev-parse --short HEAD`）。先固定观察 LSS101 附近的门牌，确认画面、OCR 输出、`Saved record` 和断流情况。页面的 `Remote OCR` 时间不包含上传、排队与浏览器显示。
3. 按 A → B → 可选 C 的顺序扫查。每块选定门牌先保持相机约 1.0 m 高和未来车载安装俯仰，距门牌水平约 2 m 观察。若门牌没有进入画面，先后退到约 3 m；若已入画但字太小，再前进到约 1.5 m，必要时到约 1 m。若门牌中心实际高 1.75 m、相机高 1.0 m，门牌中心相对水平线的仰角约为 14°（距 3 m）、21°（距 2 m）、27°（距 1.5 m）、37°（距 1 m）；这解释了低机位越靠近越容易把门牌移出画面。若未来小车允许相机上仰，可另做允许上仰的对照。距离与机身朝向测试完成后，再做一次斜视对照。距离、角度和高度能测量就记数值，否则明确写“估计”或“未测”。每个条件点击一次 `Record`，先保持稳定约 5 秒，再等待约 12 秒自动采样和停止；记录是否出现稳定的完整编号、误读、漏读及断流。
4. 停止当前录制后在页面右侧点击“刷新”，逐条查看已保存的**实际采样帧与对应 OCR**，填写实物门牌真值，标注“准确”“错误”“无文字”或“不确定”，点击“保存判断”。可重复保存以修正判断；统计时取同一样本的最后一次判断。也可从成功和失败条件各保存代表性静态照片，在左侧“照片复测”标签复测；照片复测会作为新样本保存。
5. 结束前检查是否包含 `LSN101`/`LSS101` 等前缀混淆样本，以及模糊、反光、小字、多人经过等失败样本。若画面包含可识别个人，留在本地并剔除公开材料。

## 本地记录

页面自动将采样图像、`samples.jsonl` 和 `judgments.jsonl` 放在 Linux 仓库的 `data/live_sessions/<Session ID>/`；仓库的 `.gitignore` 默认忽略 `data/`，不要用 `git add -f` 绕过。其他原图与备注可放在本地 `data/2026-10-18-1f/`。如需分析表，可从 JSONL 整理为 CSV，一行对应一张保存的样本：

```csv
sample_id,zone,room_truth,ocr_raw,distance_m,angle_deg,camera_height_m,condition,result,notes
001,A,LSS101,LSS101,2.0,0,1.0,front,exact,
002,A,LSS101,LS5101,2.0,30,1.0,oblique,wrong,字母与数字混淆
```

示例数值只展示格式，不能当作现场测量结果。`room_truth` 必须由实物门牌人工核对；`ocr_raw` 原样复制，不根据疏散图纠正。没有文字时留空并标 `no_text`；无法确认真值时标 `uncertain`，不纳入正确率分母。记录代码版本和每次改动，改动前后的结果分开统计。

## 汇总与上传到 GitHub

从 `docs/field-tests/TEMPLATE.zh-CN.md` 复制一份到 `docs/field-tests/2026-10-18-1f.md`，填入可公开的摘要：链路、版本、实际走过的区域、门牌/样本数量、Exact Match 分子和分母、典型成功与失败条件、未验证项。照片和原始视频默认只留本地；如要分享样本，先逐张检查个人信息并单独决定发布范围。不要把疏散图的房号当作 OCR 实测结果。

在 MacBook 仓库根目录执行；先写报告，再提交：

```bash
cd /Users/sousuke/Desktop/room-sign-guided-navigation
git status --short --branch
git switch -c field/1f-camo-2026-10-18
cp docs/field-tests/TEMPLATE.zh-CN.md docs/field-tests/2026-10-18-1f.md
# 编辑 docs/field-tests/2026-10-18-1f.md，填入实际结果
git add README.zh-CN.md docs/field-test-1f-camo.zh-CN.md docs/field-tests/TEMPLATE.zh-CN.md docs/field-tests/2026-10-18-1f.md
git diff --cached --check
git diff --cached
git commit -m "docs: record 1F Camo room-sign field test"
git push -u origin field/1f-camo-2026-10-18
```

推送后在 GitHub 为该分支创建 PR，核对公开内容后再合并。若分支名已存在，换一个新的日期或序号。未经现场采样和人工核对时，只能上传本计划，不能生成虚构的测试结果。
