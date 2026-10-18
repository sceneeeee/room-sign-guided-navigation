# 环境与线下运行（M0 视频流优先）

## 实验架构

~~~text
USB RGB camera → MacBook browser (Gradio webcam)
       → SSH local port forwarding
       → remote Gradio server / RapidOCR
       → recognized raw text shown in MacBook browser
~~~

Gradio 正式采样期间每隔约 1.5 秒上传**一张图像帧**；这不是 30 FPS 视频流和端到端导航系统。填写本轮条件后点击 `Record`，先等待约 5 秒用于稳定相机，此时不执行 OCR 或保存；随后正式采样约 12 秒，页面自动按下停止。每个实际进入 OCR 的帧、条件及原文保存在 Linux 仓库的 `data/live_sessions/<Session ID>/`，人工判断单独保存。

## 1. GPU 电脑启动 OCR 服务

Python 3.10+，在 GitHub 仓库根目录：

~~~bash
python3 -m venv .venv
source .venv/bin/activate
pip install -e '.[vision,ui]'
python apps/live_demo.py
~~~

默认服务地址是 GPU 电脑本地的 `127.0.0.1:7860`，不会公开分享。首次运行 RapidOCR 可能需要初始化模型。代码在同一 Python 进程内缓存 OCR 引擎，避免每帧重复初始化。

## 2. MacBook 现场连接

~~~bash
ssh -N -L 7860:127.0.0.1:7860 USER@GPU_HOST
~~~

在 MacBook 浏览器打开 `http://127.0.0.1:7860`。选择已连接的 USB RGB 摄像头，授予浏览器权限，启动摄像头流，观察 OCR 结果。

- 摄像头插在 **MacBook**；SSH 只转发访问 Gradio 的网络连接，并不直接转发 USB 设备。
- `stream_every=1.5` 是名义采样间隔，不保证每秒固定处理帧数；页面显示的是服务器 OCR 耗时，不包含网络/排队/浏览器显示时间。
- 如果 SSH 登录的是 Windows、服务在 WSL2，确认 SSH 终点能访问该服务端口；必要时直接 SSH 到 WSL2。
- 如果出现设备找不到、空白帧或结果延迟，先检查 macOS 摄像头权限、浏览器选用设备、SSH 连通性和 OCR 服务日志。
- 避免把可能含有师生人像的真实视频公开发布或开启公网分享。

页面顶部的 Session ID 用于找到本次数据。点击画面中的 `Record` 后，先看右侧准备倒计时，保持相机稳定；倒计时后检查“保存状态”是否出现编号。若出现 `SAVE FAILED`，本次结果没有可靠落盘，应先修复问题。等待本轮自动结束后在右侧“人工复核”区点击“刷新”，选择样本，核对图片与 OCR 原文，再保存人工判断。服务端重启可能产生新的默认 Session ID；把旧 ID 填回可复查旧数据。服务端重启不会删除已保存的本地记录。

## 3. 离线回归与规则推理

同一 `apps/live_demo.py` 有照片上传标签页，用于重复分析失败帧。此外可以用：

~~~bash
python scripts/ocr_image.py /path/to/door.jpg
python apps/photo_demo.py
roomnav-baseline --target EB104 --observations examples/observations_eb.jsonl
python -m unittest discover -s tests -v
~~~

这不是需要先单独完成的照片阶段。门牌自动检测、多帧确认与去重、观测自动写入 Landmark Memory 和 VLM 决策均属 M1/M2 待实现内容。

## 4. 成员 B 使用 Codex 现场调试

采集 → 查看 OCR 与延迟 → 保存非隐私失败样本和错误日志 → 用 Codex 辅助修改采集/推理脚本 → 人工检查 → 重启或重新运行 → 复测并记录代码版本。不应把 Codex 当成门牌识别或搜索决策模型。
