"""Video-first Gradio feasibility demo, not a full navigation pipeline.

MacBook browser captures a webcam frame and submits it over SSH forwarding
to this remote Python server; returns raw OCR, not trusted room observations.
"""
from time import perf_counter

import gradio as gr

from roomnav.ocr import read_text

STREAM_EVERY_SECONDS = 1.5


def recognize_rgb(frame):
    if frame is None:
        return "Waiting for a frame", "Not measured"
    start = perf_counter()
    try:
        # Gradio frame is RGB, OpenCV/RapidOCR ndarray convention is BGR.
        texts = read_text(frame[:, :, ::-1].copy())
    except Exception as exc:
        return (
            f"OCR error: {type(exc).__name__}: {exc}",
            f"Remote OCR: {perf_counter() - start:.2f}s (error)",
        )
    return (
        "\n".join(texts) if texts else "No text detected in this sampled frame.",
        f"Remote OCR: {perf_counter() - start:.2f}s (excludes network/queue)",
    )


with gr.Blocks(title="RoomNav video-first OCR prototype") as demo:
    gr.Markdown(
        "# RoomNav — live camera OCR\n"
        "M0: remote webcam frame sampling only. "
        "No door detection, cross-frame memory or navigation yet."
    )
    with gr.Tab("Live webcam (M0)"):
        webcam = gr.Image(sources=["webcam"], type="numpy", streaming=True, label="Local browser USB camera")
        live_text = gr.Textbox(lines=5, label="Raw OCR on most recent sampled frame")
        live_time = gr.Textbox(label="Remote processing time")
        webcam.stream(
            recognize_rgb, webcam, [live_text, live_time],
            stream_every=STREAM_EVERY_SECONDS,
            time_limit=60,
            concurrency_limit=1,
            show_progress="minimal",
        )

    with gr.Tab("Photo regression"):
        photo = gr.Image(sources=["upload"], type="numpy", label="Saved OCR failure frame")
        photo_text = gr.Textbox(lines=5, label="Raw OCR text")
        photo_time = gr.Textbox(label="Remote processing time")
        gr.Button("Test photo").click(
            recognize_rgb, photo, [photo_text, photo_time],
            concurrency_limit=1,
        )


if __name__ == "__main__":
    demo.launch(server_name="127.0.0.1", server_port=7860, share=False)
