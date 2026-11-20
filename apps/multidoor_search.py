"""Supervised webcam/Camo M4 application. Run locally with preloaded model weights."""
import argparse
from base64 import b64encode
from functools import lru_cache
from io import BytesIO
from math import inf
from pathlib import Path
from threading import Lock
from time import monotonic
from uuid import uuid4

import gradio as gr
from PIL import Image

from roomnav.live_search import LiveSearchRun
from roomnav.multidoor import MultiDoorSearchSession
from roomnav.offline_video import VideoFrame
from roomnav.rgb_perception import YOLOWorldDetector, annotate_frame
from roomnav.search_io import EpisodeLog, rgb_command
from roomnav.search_policy import POLICIES, configured_backend


def build(weights, directory, device="cpu", *, warm=False, perceive_frame=None):
    runs = {}
    registry_lock = Lock()

    @lru_cache(maxsize=1)
    def detector():
        if not weights.is_file():
            raise ValueError("Supply existing local weights")
        return YOLOWorldDetector(weights=str(weights), device=device)

    if warm:
        import numpy as np
        from roomnav.ocr import read_roi
        dummy = np.zeros((96, 160, 3), dtype=np.uint8)
        detector().detect(dummy)
        read_roi(dummy)

    def perceive(stamp, rgb, received):
        return rgb_command(VideoFrame(stamp, rgb), detector(), origin="live_rgb", received_s=received)

    def annotate(rgb, search):
        image = annotate_frame(rgb, search.evidence.current, search.active,
                               category_colors={"door": "lime", "sign": "cyan"})
        with BytesIO() as buf:
            Image.fromarray(image).save(buf, format="JPEG")
            return "data:image/jpeg;base64," + b64encode(buf.getvalue()).decode("ascii")

    def lookup(request, client):
        if not client:
            return None
        with registry_lock:
            entry = runs.get(request.session_hash)
            return entry[1] if entry and entry[0] == client["epoch"] else None

    def packet(run, client, *, poll=False):
        if run is None:
            return gr.skip()
        update = run.packet(client["epoch"], client["request_ms"],
                            after=client["generation"] if poll else -1)
        # Only status polls publish leases. Camera uploads reuse a prior UI token.
        if update is None or (not poll and update["lease"] is not None):
            return gr.skip()
        return update

    def reset(target, policy, budget, client, request: gr.Request):
        epoch = client["epoch"]
        key = uuid4().hex
        search = MultiDoorSearchSession(key, target, origin="live_rgb", policy=policy, budget=int(budget),
                                        backend=configured_backend(policy, int(budget)))
        run = LiveSearchRun(search, EpisodeLog(directory / (key + ".jsonl")))
        with registry_lock:
            current_epoch, previous = runs.get(request.session_hash, (-1, None))
            if epoch <= current_epoch:
                run.close("superseded_reset")
                return gr.skip()
            if previous:
                previous.close("session_reset")
            runs[request.session_hash] = (epoch, run)
        return packet(run, client)

    def frame(rgb, client, request: gr.Request):
        run = lookup(request, client)
        if run is not None and rgb is not None and client.get("token"):
            run.frame(rgb, client["token"], perceive_frame or perceive, annotate)
        return gr.skip()

    def poll(client, request: gr.Request):
        return packet(lookup(request, client), client, poll=True)

    def rendered(client, request: gr.Request):
        run = lookup(request, client)
        if run and client.get("rendered_token"):
            run.record_render(client["rendered_token"], client.get("browser_update_ms"))

    def stop(client, request: gr.Request, *, reason="emergency_stop"):
        epoch = client["epoch"]
        with registry_lock:
            current_epoch, run = runs.get(request.session_hash, (-1, None))
            if epoch < current_epoch:
                return gr.skip()
            if epoch > current_epoch:
                # STOP may arrive before the reset request. Reserve that epoch
                # so the late reset cannot open a session after the click.
                if run:
                    run.close(reason)
                runs[request.session_hash] = (epoch, None)
                return gr.skip()
            if run:
                run.stop(reason)
        return packet(run, client)

    def clear(client, request: gr.Request):
        return stop(client, request, reason="camera_cleared")

    def disconnect(request: gr.Request):
        with registry_lock:
            _, run = runs.get(request.session_hash, (-1, None))
            # A request from this closed Gradio page may still be in flight.
            runs[request.session_hash] = (inf, None)
            if run:
                run.close()

    def interaction(kind):
        def apply(door, sign, physical, actor, label, client, request: gr.Request):
            run = lookup(request, client)
            token = client.get("token") if client else None
            if run is None or not token:
                raise gr.Error("Start a session")
            cmd = {"type": kind, "frame_id": token["frame"]}
            if kind == "exhausted":
                cmd.update(type="fail", reason="operator_search_exhausted")
            if kind == "select":
                cmd.update(track_id=door.strip())
            if kind == "visual_confirm":
                cmd.update(track_id=token["track"], actor=actor)
            if kind == "bind":
                cmd.update(track_id=token["track"], sign_track_id=sign.strip(),
                           physical_door_id=physical.strip(), actor=actor)
            if kind == "confirm_room":
                cmd.update(landmark_id=token["landmark"], text=label, actor=actor)
            if kind in ("done", "refuse"):
                cmd.update(type="feedback", action_id=token["action"], safe=kind == "done", completed=kind == "done")
            try:
                accepted = run.interact(cmd, token)
            except ValueError as exc:
                raise gr.Error(str(exc)) from exc
            if kind == "refuse" and not accepted:
                run.stop("operator_refused")
            return packet(run, client)
        return apply

    def export(client, request: gr.Request):
        run = lookup(request, client)
        if not run:
            raise gr.Error("Start a session")
        records = run.records()
        path = directory / f"{records[0]['session_id']}-export-{uuid4().hex}.jsonl"
        log = EpisodeLog(path)
        try:
            log.flush_records(records)
        finally:
            log.close()
        return str(path)

    script = Path(__file__).with_name("multidoor_safety.js").read_text(encoding="utf-8")
    with gr.Blocks(title="RoomNav · Multi-door search") as demo:
        demo.load(None, js=script, queue=False)
        gr.Markdown("# Multi-door room search\nOnly act when you can see a safe path. All movement is operator advice.")
        client = gr.JSON(visible=False)
        response = gr.JSON(visible=False)
        with gr.Row():
            target = gr.Textbox(label="Target room", value="LSS119")
            policy = gr.Dropdown(POLICIES, value="rule", label="Search policy")
            budget = gr.Number(value=8, precision=0, label="Maximum model calls")
            start = gr.Button("New session / reset")
        with gr.Row():
            camera = gr.Image(sources=["webcam"], type="numpy", streaming=True, label="Camera / Camo",
                              webcam_options=gr.WebcamOptions(mirror=False))
            gr.HTML('<img id="roomnav-marked" alt="Doors, signs and temporary tracks" hidden>')
        gr.HTML('<div id="roomnav-advice" role="status" aria-live="polite">Start a session</div>'
                '<details><summary>Target, OCR, visits and landmark memory</summary>'
                '<pre id="roomnav-details" style="max-height:30rem;overflow:auto"></pre></details>')
        with gr.Row():
            door = gr.Textbox(label="Door track ID")
            sign = gr.Textbox(label="Sign track ID")
            physical = gr.Textbox(label="Physical door ID (reuse on a verified revisit)")
            actor = gr.Textbox(label="Operator name")
        label = gr.Textbox(label="Independently read room number")
        download = gr.Button("Prepare log download")
        artifact = gr.File(label="Experiment JSONL", interactive=False)

        def request_js(kind):
            return f'(...args) => [...args.slice(0, -1), window.roomnavLive.request("{kind}")]'

        download.click(export, client, artifact, js=request_js("export"), queue=False)
        actions = [("select", "Manually select door"), ("visual_confirm", "Confirm visual target"),
                   ("bind", "Associate selected door and sign"), ("confirm_room", "Confirm independent room reading"),
                   ("done", "Action completed safely"), ("refuse", "Refuse / unsafe"),
                   ("next", "Finish or release lost target; inspect next door"),
                   ("exhausted", "End search / no more doors")]
        inputs = [door, sign, physical, actor, label, client]
        with gr.Row():
            for kind, title in actions:
                gr.Button(title).click(interaction(kind), inputs, response, queue=False,
                                       js=request_js(kind), api_name=kind)
            gr.Button("Emergency STOP", variant="stop").click(stop, client, response, queue=False,
                                                              js=request_js("stop"))
        start.click(reset, [target, policy, budget, client], response, queue=False, js=request_js("reset"))
        camera.stream(frame, [camera, client], response, stream_every=.4, concurrency_limit=1)
        camera.clear(clear, client, response, queue=False, js=request_js("stop"))
        gr.Timer(.25).tick(poll, client, response, queue=False, js=request_js("poll"), show_progress="hidden")
        response.change(None, response, client, js="(packet) => window.roomnavLive.receive(packet)",
                        queue=False, trigger_mode="multiple")
        client.change(rendered, client, [], queue=False, show_progress="hidden")
        demo.unload(disconnect)
    return demo


if __name__ == "__main__":
    p = argparse.ArgumentParser()
    p.add_argument("--weights", type=Path, required=True)
    p.add_argument("--logs", type=Path, default=Path("outputs/m4-live"))
    p.add_argument("--device", default="cpu")
    p.add_argument("--port", default=7862, type=int)
    args = p.parse_args()
    build(args.weights, args.logs, args.device, warm=True).launch(
        server_name="127.0.0.1", server_port=args.port, share=False)
