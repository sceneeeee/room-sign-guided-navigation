# 1F 低机位门牌识别：从 LSS101 到 GitHub 的完整 SOP

## 1. 本轮固定条件

手机摄像头 → Camo → MacBook 浏览器 → `ssh -N -L 7860:127.0.0.1:7860 linux` → Linux Gradio/RapidOCR。系统自动保存**进入 OCR 的采样帧**及原始文字；你在网页里人工判断。数据先留在 Linux 的 `data/live_sessions/<Session ID>/`，该目录被 Git 忽略。

模拟小车时，**镜头中心离地约 1.0 m**，保持未来车载相机的安装俯仰，不把手机举到眼高。距离指镜头到门牌所在墙面的水平距离；正面 0° 指镜头大致垂直门牌，斜视 20° 指向走廊可通行一侧偏约 20°，不是上仰 20°。距离或角度无法实测时写“估计”。如果 2 m 时门牌不入画，先退到约 3 m；如果入画但字太小，再前进到约 1.5 m。不要为了得到正确结果临时改变主条件的相机高度。

疏散图上的起点附近是 **LSS101**，左侧另有不同的 **LSN101**。用实物门牌确认完整前缀。依次测试 LSS101 → LSS124 → LSS119 → LSS118；具体通行以现场为准，不把图上的房号当作 OCR 已看到的证据。

## 2. 开机与记录版本

Linux 服务未运行时，在 Linux 终端执行：

```bash
cd /home/sousuke/Desktop/room-sign-guided-navigation
.venv/bin/python apps/live_demo.py
```

MacBook 保持 Camo 连接，并在另一个终端保持隧道运行；如果 `7860` 已被现有隧道监听，不要重复启动：

```bash
ssh -N -L 7860:127.0.0.1:7860 linux
```

MacBook 浏览器打开 `http://127.0.0.1:7860`，选择 Camo。复制页面顶部的 **32 位 Session ID** 到现场笔记；本轮四块门牌一直用这个 ID。记录日期、1F、手机/Camo 分辨率、光照和镜头中心实际离地高度。在 Linux 终端记录实际运行文件的哈希：

```bash
cd /home/sousuke/Desktop/room-sign-guided-navigation
sha256sum apps/live_demo.py src/roomnav/field_logging.py src/roomnav/capture_timing.py
```

当前代码可能尚未提交，单写 `git rev-parse HEAD` 不足以确定实际运行版本；报告中保留这些 SHA-256。

## 3. LSS101：四轮标准采集

每轮先在页面左侧**“本轮采集条件”**填写对应文字，再按一次 Record。镜头高度和俯仰四轮都保持不变。

| 轮次 | 条件栏填写示例 | 次数 |
| --- | --- | --- |
| A | `LSS101 | 距门2.0m | 正面0° | 高1.0m | 第1轮` | 1 |
| B | `LSS101 | 距门2.0m | 正面0° | 高1.0m | 第2轮` | 1；检查重复性 |
| C | `LSS101 | 距门1.5m | 正面0° | 高1.0m | 第1轮` | 1 |
| D | `LSS101 | 距门2.0m | 斜视约20° | 高1.0m | 第1轮` | 1 |

**共 4 次 Record。** 若某条件失败或不稳定，原条件最多补 1 次；如果门牌根本不入画，可把该轮改成可通行的约 3 m，并如实写在条件栏。LSS124、LSS119、LSS118 分别做 A、C、D 三个条件（各 3 次），失败条件最多补 1 次。这样四块门牌正常共 **13 轮**，约 17 秒/轮，另加移动和人工复核时间。

## 4. 每次 Record 及人工复核

1. 停在条件栏写明的位置，让完整门牌入画；确认 Session ID 没变。点 **Record** 一次。
2. 页面先倒计时约 **5 秒**，这段时间不做 OCR、不保存；之后保持镜头稳定约 **12 秒**。页面自动停止，按钮回到 `Record`，右侧“保存状态”显示“本轮已结束”。名义 1.5 秒采样间隔大约有 8 次机会，实际帧数可能不同。
3. 若本轮结束后没有“已保存样本 NNNNNN”，先检查 Camo 画面、Linux 服务和 SSH 隧道；用**相同条件**只重试一次，并在报告中记录故障。不要把无结果的一轮写成成功。
4. 点右侧**刷新**。下拉框显示样本 ID、条件和 OCR 摘要；逐条查看本轮新增的每帧。依据**实物门牌**填写完整真值，如 `LSS101`，选择判断并点**保存判断**：
   - **准确**：前缀和数字全部一致；只读到 `101` 不算准确。
   - **错误**：任一字符误读、漏读、多出，或识别成其他文字。
   - **无文字**：这帧没有门牌文字输出。
   - **不确定**：画面或实物真值无法可靠核对，不进入正确率分母。
5. 不要只标正确帧；本轮新增帧应逐条判断。判断可重存，导出时取同一 ID 的**最后一次判断**。备注只写条件栏没有的信息，如反光、遮挡、运动模糊。

在现场笔记记下 **3–6 个拟公开的关键帧 ID**，覆盖清晰成功、典型误读/漏检、低机位边界。先只记 ID，不上传整段视频或 `data/`。如果画面有人脸、姓名、工号等，不选为公开帧。走到下一门牌时先核对实物编号，再改条件栏中的房号。

## 5. Linux：导出待审阅的核心数据与关键帧

全部复核后，在 Linux 终端执行；把 `FIELD_SESSION_ID` 的示例值换成页面上的真实 ID，把关键帧 ID 换成你记下的值：

```bash
cd /home/sousuke/Desktop/room-sign-guided-navigation
FIELD_SESSION_ID=0123456789abcdef0123456789abcdef
.venv/bin/python scripts/export_field_session.py \
  --session "$FIELD_SESSION_ID" \
  --keyframes 000003 000014 000027
```

导出写入 Git 忽略的 `outputs/field_exports/<Session ID>/`：`summary.md` 是数量和统计口径，`review_full.csv` 是留本地的完整表，`review_public_candidate.csv` 是仅含已判断行的公开候选核心字段，`keyframes/` 只复制你指定的图片。脚本**不提交、不上传**。同名输出目录已存在时，指定新的 `--output-dir`，不要覆盖旧结果。

只有**填写了实物真值**且判断为准确/错误/无文字的样本计入 Exact Match 分母；不确定和未判断样本单列。报告中同时写明总采样数、已复核数、各条件结果、失败原因和代码哈希。

## 6. MacBook：逐项检查后提交 GitHub

在 MacBook 仓库目录取回**小型待审阅导出**，不是整个原始会话；同样替换 Session ID：

```bash
cd /Users/sousuke/Desktop/room-sign-guided-navigation
FIELD_SESSION_ID=0123456789abcdef0123456789abcdef
mkdir -p outputs/field_exports
scp -r "linux:/home/sousuke/Desktop/room-sign-guided-navigation/outputs/field_exports/$FIELD_SESSION_ID" outputs/field_exports/
```

逐张打开 `outputs/field_exports/$FIELD_SESSION_ID/keyframes/`；检查画面和 `review_public_candidate.csv` 的 OCR 原文是否含不应公开的个人信息。若有，重新挑选 ID 导出或从**待审阅副本**排除，不改动 Linux 原始数据。结合 `summary.md` 和现场笔记填写报告模板，别把地图房号当作 OCR 实测值。

确认公开内容后，在 MacBook **单独的现场结果分支**中，只复制这些文件到可跟踪目录：

```bash
git switch -c "field/1f-camo-$FIELD_SESSION_ID"
mkdir -p docs/field-tests/assets/2026-10-18-1f
cp docs/field-tests/TEMPLATE.zh-CN.md docs/field-tests/2026-10-18-1f.md
# 编辑报告，填入实测条件、summary.md 的统计、失败案例和代码哈希
cp "outputs/field_exports/$FIELD_SESSION_ID/review_public_candidate.csv" docs/field-tests/2026-10-18-1f-review.csv
cp "outputs/field_exports/$FIELD_SESSION_ID/keyframes/"*.jpg docs/field-tests/assets/2026-10-18-1f/
git status --short
git add docs/field-tests/2026-10-18-1f.md docs/field-tests/2026-10-18-1f-review.csv docs/field-tests/assets/2026-10-18-1f/
git diff --cached --check
git diff --cached --stat
git diff --cached -- docs/field-tests/2026-10-18-1f.md docs/field-tests/2026-10-18-1f-review.csv
git commit -m "docs: add reviewed 1F room-sign field results"
git push -u origin HEAD
```

在 GitHub 为该分支创建 PR，逐张核对报告、CSV 和关键帧再合并。**不要**使用 `git add -A`、`git add -f data/...`，也不要把原始会话目录上传。照片和视频默认留本地；未经实际采样和人工核对，不填写结果或宣称准确率。
