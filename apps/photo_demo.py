"""Minimal photo/webcam-frame OCR user interface (not high-level reasoning)."""

import gradio as gr
from roomnav.ocr import read_text


def recognize(image_path):
    if not image_path:
        return "No image provided."
    try:
        lines = read_text(image_path)
    except Exception as exc:
        return f"OCR error: {exc}"
    return "\n".join(lines) if lines else "No text detected."


with gr.Blocks(title="RoomNav photo OCR baseline") as demo:
    gr.Markdown("# RoomNav: OCR feasibility check\nPhoto and webcam-frame baseline only; no navigation is running.")
    with gr.Tab("Upload a photo"):
        photo = gr.Image(type="filepath", sources=["upload"], label="Room sign / doorplate")
        result = gr.Textbox(lines=5, label="Raw OCR text")
        gr.Button("Recognize").click(recognize, inputs=photo, outputs=result)
    with gr.Tab("Webcam sample"):
        webcam = gr.Image(type="filepath", sources=["webcam"], label="Take a webcam photo")
        webcam_out = gr.Textbox(lines=5, label="Raw OCR text")
        gr.Button("Recognize frame").click(recognize, inputs=webcam, outputs=webcam_out)


if __name__ == "__main__":
    demo.launch(server_name="127.0.0.1", server_port=7860, share=False)
