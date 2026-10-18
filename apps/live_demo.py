"""Video-first Gradio feasibility demo, not a full navigation pipeline.

MacBook browser captures a webcam frame and submits it over SSH forwarding
to this remote Python server; returns raw OCR, not trusted room observations.
"""
from time import monotonic, perf_counter

import gradio as gr

from roomnav.field_logging import (
    DATA_ROOT,
    get_sample,
    latest_judgment,
    list_samples,
    new_session_id,
    save_judgment,
    save_sample,
)
from roomnav.capture_timing import TOTAL_SECONDS, capture_phase, start_capture, stop_capture
from roomnav.ocr import read_text

STREAM_EVERY_SECONDS = 1.5
PAGE_CSS = """
.gradio-container {max-width: 100% !important; padding: 8px 14px !important;}
#topbar {align-items: center; gap: 12px;}
#topbar h2 {margin: 0; font-size: 1.35rem;}
#topbar p {margin: 0; font-size: .82rem;}
#workspace {gap: 14px; align-items: start;}
#results-panel {gap: 3px;}
#results-panel h3 {margin: 0; font-size: 1rem;}
#results-panel .block {margin-bottom: 0 !important;}
@media (max-width: 1050px) {
  #workspace {flex-direction: column;}
  #capture-panel, #results-panel {width: 100% !important;}
}
"""
PAGE_JS = f"""() => {{
  const selector = '#live-camera button.capture-button[aria-label="capture photo"]';
  const startSignal = () => document.getElementById('capture-start-signal')?.click();
  const stopSignal = () => document.getElementById('capture-stop-signal')?.click();
  let timer = null;
  window.__roomnavAutoStopReady = true;
  document.addEventListener('click', (event) => {{
    const button = event.target.closest(selector);
    if (!button) return;
    const stopping = button.textContent.trim().startsWith('Stop');
    if (stopping) {{
      if (timer) clearTimeout(timer);
      timer = null;
      stopSignal();
      return;
    }}
    startSignal();
    if (timer) clearTimeout(timer);
    timer = setTimeout(() => {{
      const current = document.querySelector(selector);
      if (current?.textContent.trim().startsWith('Stop')) current.click();
      else stopSignal();
      timer = null;
    }}, {int(TOTAL_SECONDS * 1000)});
  }}, true);
}}"""


def recognize_rgb(frame, session_id, source, condition=""):
    if frame is None:
        return "Waiting for a frame", "Not measured", ""
    start = perf_counter()
    try:
        # Gradio frame is RGB, OpenCV/RapidOCR ndarray convention is BGR.
        texts = read_text(frame[:, :, ::-1].copy())
        raw_ocr = "\n".join(texts) if texts else ""
        status = ""
    except Exception as exc:
        raw_ocr = f"OCR error: {type(exc).__name__}: {exc}"
        status = " (error)"
    elapsed = perf_counter() - start
    try:
        sample = save_sample(session_id, frame, raw_ocr, elapsed, source, condition)
        saved = f"已保存样本 {sample['sample_id']}（Linux 本地）"
    except Exception as exc:
        saved = f"SAVE FAILED: {type(exc).__name__}: {exc}"
    return raw_ocr or "No text detected in this sampled frame.", f"Remote OCR: {elapsed:.2f}s{status} (excludes network/queue)", saved


def recognize_live(frame, session_id, condition, capture_state):
    phase, remaining, capture_state = capture_phase(monotonic(), capture_state)
    if phase == "idle":
        return gr.update(), gr.update(), gr.update(), capture_state
    if phase == "settling":
        return f"准备中：约 {remaining} 秒后开始识别", "尚未开始 OCR", "准备中，不保存画面", capture_state
    if phase == "finished":
        return "本轮采样已结束", "", "本轮已结束，可刷新样本列表", capture_state
    raw_ocr, processing_time, saved = recognize_rgb(frame, session_id, "live", condition)
    return raw_ocr, processing_time, f"{saved}；约 {remaining} 秒后自动结束", capture_state


def recognize_photo(frame, session_id, condition):
    return recognize_rgb(frame, session_id, "photo", condition)


def begin_live_capture():
    return start_capture(monotonic()), "准备中：约 5 秒后开始识别", "准备中，不保存画面"


def end_live_capture(capture_state):
    return stop_capture(capture_state), "本轮已结束，可刷新样本列表"


def refresh_samples(session_id):
    samples = list_samples(session_id)
    choices = [
        (
            f"{item['sample_id']} · {(item.get('condition') or item['source'])[:28]} · {item['raw_ocr'][:18]}",
            item["sample_id"],
        )
        for item in samples
    ]
    return gr.update(
        choices=choices,
        value=samples[-1]["sample_id"] if samples else None,
        label=f"已保存样本（{len(samples)}）",
    )


def load_for_review(session_id, sample_id):
    if not sample_id:
        return None, "", "", None, "", "Select a saved sample first."
    sample = get_sample(session_id, sample_id)
    judgment = latest_judgment(session_id, sample_id) or {}
    return (
        sample["image_path"],
        sample["raw_ocr"] or "No text detected",
        judgment.get("room_truth", ""),
        judgment.get("verdict"),
        judgment.get("notes", ""),
        f"已加载样本 **{sample_id}** · {sample.get('condition') or '未填写条件'}",
    )


def record_judgment(session_id, sample_id, verdict, room_truth, notes):
    if not sample_id:
        return "Select a saved sample first."
    item = save_judgment(session_id, sample_id, verdict, room_truth, notes)
    return f"已保存样本 **{item['sample_id']}** 的判断：{item['verdict']}"


with gr.Blocks(title="RoomNav 现场门牌识别", fill_width=True) as demo:
    with gr.Row(elem_id="topbar"):
        with gr.Column(scale=3, min_width=300):
            gr.Markdown("## RoomNav · 现场门牌识别\n每个采样帧与 OCR 结果保存在 Linux 本地；人工判断单独保存。")
        with gr.Column(scale=2, min_width=350):
            session_id = gr.Textbox(
                value=new_session_id,
                label="Session ID（复制后可重新打开本次记录）",
                interactive=True,
            )
    with gr.Row(elem_id="workspace"):
        with gr.Column(scale=7, min_width=650, elem_id="capture-panel"):
            with gr.Tabs():
                with gr.Tab("实时摄像头"):
                    webcam = gr.Image(
                        sources=["webcam"], type="numpy", streaming=True,
                        label="Camo / MacBook 摄像头", height="58vh", elem_id="live-camera",
                    )
                    capture_state = gr.State(value=None)
                    capture_start_signal = gr.Button(visible="hidden", elem_id="capture-start-signal")
                    capture_stop_signal = gr.Button(visible="hidden", elem_id="capture-stop-signal")
                with gr.Tab("照片复测"):
                    photo = gr.Image(
                        sources=["upload"], type="numpy", label="上传已保存的样本",
                        height="58vh",
                    )
                    test_photo = gr.Button("识别这张照片")
            condition = gr.Textbox(
                label="本轮采集条件（每次 Record 前填写）",
                placeholder="LSS101 | 距门2.0m | 正面0° | 相机高1.0m | 第1轮",
            )

        with gr.Column(scale=5, min_width=420, elem_id="results-panel"):
            gr.Markdown("### 本次采样结果")
            latest_ocr = gr.Textbox(label="OCR 原文", lines=2, interactive=False)
            with gr.Row():
                processing_time = gr.Textbox(label="远端 OCR 耗时", interactive=False)
                saved_record = gr.Textbox(label="保存状态", interactive=False)
            gr.Markdown("### 人工复核")
            with gr.Row():
                sample_list = gr.Dropdown(label="已保存样本", choices=[], scale=4)
                refresh_button = gr.Button("刷新", scale=1, min_width=70)
            with gr.Row():
                with gr.Column(scale=2, min_width=185):
                    review_image = gr.Image(
                        label="选中样本的实际采样帧", type="filepath",
                        height=175, interactive=False,
                    )
                with gr.Column(scale=3, min_width=230):
                    review_ocr = gr.Textbox(
                        placeholder="选中样本的 OCR 原文", show_label=False,
                        container=False, lines=1, interactive=False,
                    )
                    room_truth = gr.Textbox(
                        placeholder="实物门牌真值（可选）", show_label=False,
                        container=False,
                    )
                    notes = gr.Textbox(
                        placeholder="备注（可选）", show_label=False,
                        container=False,
                    )
            verdict = gr.Radio(
                choices=[("准确", "exact"), ("错误", "wrong"), ("无文字", "no_text"), ("不确定", "uncertain")],
                label="你的判断",
            )
            with gr.Row():
                save_button = gr.Button("保存判断", variant="primary", scale=1)
                review_status = gr.Markdown("尚未保存判断", scale=3)

    webcam.stream(
        recognize_live, [webcam, session_id, condition, capture_state],
        [latest_ocr, processing_time, saved_record, capture_state],
        stream_every=STREAM_EVERY_SECONDS,
        time_limit=60,
        concurrency_limit=1,
        show_progress="minimal",
    )
    capture_start_signal.click(
        begin_live_capture, outputs=[capture_state, latest_ocr, saved_record],
        queue=False,
    )
    capture_stop_signal.click(
        end_live_capture, inputs=capture_state, outputs=[capture_state, saved_record],
        queue=False,
    )
    test_photo.click(
        recognize_photo, [photo, session_id, condition], [latest_ocr, processing_time, saved_record],
        concurrency_limit=1,
    )
    refresh_button.click(refresh_samples, session_id, sample_list)
    sample_list.change(
        load_for_review,
        [session_id, sample_list],
        [review_image, review_ocr, room_truth, verdict, notes, review_status],
    )
    save_button.click(
        record_judgment,
        [session_id, sample_list, verdict, room_truth, notes],
        review_status,
    )


if __name__ == "__main__":
    demo.launch(server_name="127.0.0.1", server_port=7860, share=False, css=PAGE_CSS, js=PAGE_JS)
