"""Optional live RGB *diagnostic* UI, separate from the proven M0 recorder.

Start: python apps/rgb_guidance_demo.py
Requires: pip install -e '.[ui,detection]'
Streamed video is only sampled by Gradio. In this initial UI, timestamps mark
SERVER frame receipt, not phone capture; network/display latency is unmeasured.
This is NOT navigation, collision avoidance, door-identity confirmation or
robot control. Human operator is solely responsible for safety.
"""
from dataclasses import dataclass, field
from functools import lru_cache
from time import monotonic
from uuid import uuid4

import gradio as gr

from roomnav.rgb_perception import YOLOWorldDetector
from roomnav.visual_tracking import ConservativeTracker
from roomnav.visual_guidance import VisualCandidate, VisualGuidanceController


@dataclass
class GuidanceSession:
    run_id: str = field(default_factory=lambda: uuid4().hex[:12])
    frames_seen: int = 0
    tracker: ConservativeTracker = field(default_factory=ConservativeTracker)
    controller: VisualGuidanceController = field(default_factory=lambda: VisualGuidanceController(max_frame_age_s=3.0))
    candidates: list[VisualCandidate] = field(default_factory=list)
    last_frame_id: str | None = None
    last_motion_id: int | None = None


@lru_cache(maxsize=1)
def detector():
    # No model download until the app actually receives a frame.
    return YOLOWorldDetector(weights="yolov8s-world.pt", confidence=0.30)


def annotate_frame(frame, candidates, selected):
    """Render transient track IDs; these are NOT physical landmark IDs."""
    import numpy as np
    from PIL import Image, ImageDraw

    annotated = Image.fromarray(np.asarray(frame).astype("uint8")).convert("RGB")
    drawer = ImageDraw.Draw(annotated)
    width, height = annotated.size
    for candidate in candidates:
        box = candidate.bbox_xyxy_norm
        rect = (round(box[0]*width), round(box[1]*height),
                round(box[2]*width), round(box[3]*height))
        active = candidate.track_id == selected
        color = "#f24a4a" if active else "#3386d0"
        drawer.rectangle(rect, outline=color, width=4 if active else 2)
        tag = f"{candidate.track_id} / {candidate.category}" + (" [selected]" if active else "")
        drawer.text((rect[0] + 4, max(0, rect[1] - 15)), tag, fill=color)
    return np.asarray(annotated)


ACTION_ZH = {
    "SEARCH": "观察画面，手动选择要查看的门",
    "TURN_LEFT": "建议小幅向左转动（请先确认安全）",
    "TURN_RIGHT": "建议小幅向右转动（请先确认安全）",
    "FORWARD_SHORT": "建议短步向前（必须由人判断前方安全）",
    "STOP_AND_READ": "暂停移动；可另行运行 OCR 读取真实门牌",
    "STOP_REOBSERVE": "暂停；目标丢失、帧时序异常或人工停止，重新观察",
    "WAIT_EXECUTION": "等待用户确认上一条动作完成，不发出新的移动指令",
}


def process_frame(frame, state, *, predict=None, now=None):
    """Pure callback logic accepting an injectable detector for offline tests."""
    if state is None:
        state = GuidanceSession()
    if frame is None:
        return None, gr.update(), "等待 RGB 视频帧", state, "尚无视频"
    now = monotonic() if now is None else now
    if predict is None:
        predict = detector()
    state.frames_seen += 1
    fid = f"{state.run_id}-f{state.frames_seen:07d}"
    try:
        detected = predict.detect(frame)
        tracks = state.tracker.update(fid, now, detected)
        state.candidates = tracks
        state.last_frame_id = fid
        decision = state.controller.step(fid, now, tracks, now_s=now)
        state.last_motion_id = decision.action_id or state.last_motion_id
        overlay = annotate_frame(frame, tracks, state.controller.target_track_id)
        choices = [(f"{x.track_id} ({x.category})", x.track_id) for x in tracks]
        selection = state.controller.target_track_id
        if selection is not None and selection not in {x.track_id for x in tracks}:
            selection = None
        message = ACTION_ZH.get(decision.action, decision.action)
        details = (f"Action: {decision.action} | frame: {fid} | candidates: {len(tracks)}\n"
                   f"Reason: {decision.reason}\n"
                   f"Server frame receipt: {now:.3f}; no camera-side timestamp or measured network latency\n"
                   f"Action ID: {decision.action_id if decision.action_id is not None else '-'}")
        return overlay, gr.update(choices=choices, value=selection), message, state, details
    except Exception as e:
        state.controller.halt()
        return None, gr.update(choices=[], value=None), "暂停：检测或处理失败", state, f"{type(e).__name__}: {e}"


def select_target(track_id, state):
    if state is None or not track_id:
        return "请先等待图像中的候选门出现并选择"
    if track_id not in {c.track_id for c in state.candidates}:
        return "该候选目标已经过期，请重新观察"
    if state.controller.target_track_id == track_id:
        return f"当前已在观察 {track_id}，无需重复选择"
    try:
        state.controller.select_target(track_id)
    except ValueError as e:
        return f"切换目标被拒绝：{e}"
    return f"已选中 {track_id}，等待下一帧后计算观察方向"


def confirm_action(state, safe):
    if state is None or state.controller.pending_action_id is None:
        return "当前没有待确认的移动动作", state
    action_id = state.controller.pending_action_id
    state.controller.acknowledge(action_id, completed=safe, safe=safe)
    state.last_motion_id = None
    if safe:
        return f"已记录动作 {action_id} 完成；等待新画面", state
    return "已记录动作拒绝/不安全并锁定移动；可重新选择目标开始新实验", state


def halt(state):
    if state is None:
        state = GuidanceSession()
    state.controller.halt()
    return "已请求停止；当前不允许继续给出运动指令", state


with gr.Blocks(title="RoomNav RGB Visual Guidance — experimental") as demo:
    gr.Markdown(
        "## RoomNav · RGB 视觉方向引导（实验入口）\n"
        "只负责候选门框和相机相对方向；**人负责避障与安全**。"
        "仅将帧发送到当前服务端，不自动录像，不声称准确门牌识别或完整导航。"
    )
    session = gr.State(value=GuidanceSession)
    with gr.Row():
        with gr.Column():
            incoming = gr.Image(sources=["webcam"], type="numpy", streaming=True, label="手机/Camo 摄像头")
            candidates = gr.Dropdown(label="选择需要查看的可见门/标牌", choices=[], interactive=True)
            select_status = gr.Textbox(label="选中目标状态", interactive=False)
        with gr.Column():
            marked = gr.Image(label="候选检测框与临时跟踪 ID", interactive=False)
            action = gr.Textbox(label="运动/观察建议", interactive=False)
            diagnostics = gr.Textbox(label="执行日志（最近一帧）", lines=4, interactive=False)
            with gr.Row():
                done = gr.Button("已安全完成本次动作")
                unsafe = gr.Button("不能安全执行 / 拒绝动作")
                stop = gr.Button("紧急停止", variant="stop")
            feedback = gr.Textbox(label="动作反馈", interactive=False)
    incoming.stream(
        process_frame, inputs=[incoming, session],
        outputs=[marked, candidates, action, session, diagnostics],
        stream_every=1.0, concurrency_limit=1, show_progress="minimal",
    )
    # .input only fires on user interaction; .change would reselect on every
    # stream-driven Dropdown update and reset the controller frame gate.
    candidates.input(select_target, inputs=[candidates, session], outputs=[select_status], queue=False)
    done.click(lambda state: confirm_action(state, True), inputs=[session], outputs=[feedback, session], queue=False)
    unsafe.click(lambda state: confirm_action(state, False), inputs=[session], outputs=[feedback, session], queue=False)
    stop.click(halt, inputs=[session], outputs=[feedback, session], queue=False)


if __name__ == "__main__":
    demo.launch(server_name="127.0.0.1", server_port=7861, share=False)
