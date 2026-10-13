# 环境与线下运行

## 开发机

本仓库使用 Python 3.10+；只跑规则基线时不需要 GPU。

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -e .
roomnav-baseline --target EB104 --observations examples/observations_eb.jsonl
python -m unittest discover -s tests -v
```

## 从照片开始

```bash
pip install -e '.[vision,ui]'
python scripts/ocr_image.py path/to/door.jpg
python apps/photo_demo.py
```

Gradio 在远程电脑监听 `127.0.0.1:7860`。MacBook 本地通过 SSH 访问：

```bash
ssh -N -L 7860:127.0.0.1:7860 USER@GPU_HOST
```

然后在 MacBook 浏览器打开 `http://127.0.0.1:7860`；浏览器负责选取 USB 摄像头或上传照片。浏览器权限与 HTTPS/localhost 安全上下文需要满足要求。若 SSH 登录 Windows 而服务运行在 WSL2，请检查两端的端口转发和网络模式。

## 未来视频流

先使用 **OpenCV 读取保存的 MP4**，再考虑 Gradio 摄像头采样。只有在确实需要低延迟连续视频时，再评估 FastRTC/WebRTC 或 MediaMTX/RTSP。

本项目先保持单张照片与视频共享同一 `process_image` API，避免绑定某种摄像头硬件。