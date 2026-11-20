"""Host the M4 policy outside Isaac. The renderer accepts only simulation camera poses."""
import argparse
from dataclasses import asdict, replace
import json
import hashlib
from importlib.metadata import version
from pathlib import Path
from queue import Empty, Queue
import subprocess
import sys
from threading import Thread

import numpy as np
from PIL import Image
from roomnav.multidoor import MultiDoorSearchSession, TERMINAL
from roomnav.offline_video import FrameStamp, VideoFrame
from roomnav.rgb_perception import YOLOWorldDetector
from roomnav.search_episode import score_isaac
from roomnav.search_io import EpisodeLog, rgb_command
from roomnav.search_policy import POLICIES, configured_backend

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "isaac_single_door"))
from control import Pose, move


class Renderer:
    def __init__(self, command, output):
        self.output = output
        self.messages = Queue()
        self.process = subprocess.Popen(command, stdin=subprocess.PIPE, stdout=subprocess.PIPE,
                                        stderr=subprocess.STDOUT, text=True, errors="replace", bufsize=1)
        self.thread = Thread(target=self._read, daemon=True)
        self.thread.start()

    def _read(self):
        with (self.output / "isaac.log").open("x") as log:
            for line in self.process.stdout:
                log.write(line)
                log.flush()
                if line.startswith("ROOMNAV_"):
                    self.messages.put(line.strip())
        self.messages.put("renderer_exited")

    def expect(self, message, timeout=180):
        try:
            actual = self.messages.get(timeout=timeout)
        except Empty:
            raise TimeoutError(f"Timed out waiting for {message}, see isaac.log") from None
        if actual != message:
            raise RuntimeError(f"Expected {message}, got {actual}, see isaac.log")

    def render(self, index, pose):
        self.process.stdin.write(json.dumps({"index": index, "pose": asdict(pose)})+"\n")
        self.process.stdin.flush()
        self.expect(f"ROOMNAV_FRAME {index}")
        with Image.open(self.output / f"rgb_{index:05}.png") as im:
            return np.asarray(im.convert("RGB"))

    def close(self):
        if self.process.poll() is None:
            try:
                self.process.stdin.write('{"end":true}\n')
                self.process.stdin.close()
                self.expect("ROOMNAV_CLOSED", timeout=30)
                self.process.wait(timeout=20)
            except (OSError, RuntimeError, TimeoutError, subprocess.TimeoutExpired):
                if self.process.poll() is None:
                    self.process.terminate()
                    self.process.wait(timeout=20)
                raise
        self.thread.join(timeout=2)


def run(args):
    directory = args.output.resolve()
    directory.mkdir(parents=True, exist_ok=False)
    session = MultiDoorSearchSession("isaac", args.target, origin="isaac_rgb", policy=args.policy, budget=args.budget,
                                     backend=configured_backend(args.policy, args.budget), model_timeout_s=args.model_timeout_s)
    if not args.weights.is_file():
        raise ValueError("Local weights required")
    detector = YOLOWorldDetector(weights=str(args.weights), device=args.device)
    manifest = {"initial_pose": asdict(Pose(x=-2.6, y=-3)), "frame_budget": args.frames,
                "weights_sha256": hashlib.sha256(args.weights.read_bytes()).hexdigest(),
                "policy": args.policy, "model_budget": args.budget, "target": args.target,
                "device": args.device, "supervised": args.supervised,
                "versions": {k: version(k) for k in ("torch","ultralytics","rapidocr","onnxruntime")}}
    (directory / "manifest.json").write_text(json.dumps(manifest, indent=2)+"\n")
    log = EpisodeLog(directory/"episode.jsonl")
    log.flush(session)
    pose, direction, idle = Pose(x=-2.6, y=-3), 1, 0
    renderer = Renderer(args.renderer, directory)
    try:
        renderer.expect("ROOMNAV_READY")
        with (directory/"execution.jsonl").open("x") as execution:
            for index in range(args.frames):
                rgb = renderer.render(index,pose)
                stamp = FrameStamp(index,index,"1/2")
                cmd, timing = rgb_command(VideoFrame(stamp,rgb),detector,origin=session.origin)
                session.apply(cmd, rgb=rgb, timing=timing)
                def interact(kind, *, operator=False, **fields):
                    return session.apply(dict(fields,type=kind,at_s=session.last_at+.001,
                                              frame_id=stamp.frame_id, actor_source="operator" if operator else "simulator"), rgb=rgb)
                action, next_pose = session.advice["action"], pose
                if session.pending_id:
                    proposed = move(pose,action)
                    safe = abs(proposed.x)<3.4 and -7<proposed.y<-.9
                    next_pose = proposed if safe else pose
                    interact("feedback",action_id=session.pending_id,completed=safe,safe=safe)
                    idle = 0
                elif session.state not in TERMINAL:
                    idle += 1
                    if args.supervised:
                        print(directory / f"rgb_{index:05}.png", session.snapshot(), flush=True)
                        # Inspection & independent reader use the displayed rendered image.
                        value = input("Operator command JSON or Enter to continue: ").strip()
                        if value:
                            command = json.loads(value)
                            interact(command.pop("type"), operator=True, **command)
                    if session.state not in TERMINAL:
                        if session.recovery or idle>=6:
                            interact("next")
                            # A bounded lateral observation sweep reverses and revisits views.
                            # Its bounds are fixed actuator limits, independent of door positions.
                            if not -3.1 <= pose.x+direction*.8 <= 3.1:
                                direction *= -1
                            next_pose = replace(pose,x=pose.x+direction*.8)
                            action, idle = "SCAN_NEXT_VIEW", 0
                        elif not session.active and session.intent.get("suggested_track"):
                            interact("accept_intent",track_id=session.intent["suggested_track"])
                execution.write(json.dumps({"frame":index,"simulation_time_s":stamp.timestamp_s,
                                "action":action,"pose_before":asdict(pose),"pose_after":asdict(next_pose),
                                "execution_source":"simulated_camera"})+"\n")
                execution.flush()
                pose = next_pose
                log.flush(session)
                if session.state in TERMINAL:
                    break
        if session.state not in TERMINAL:
            session.apply({"type":"fail","at_s":session.last_at,"reason":"simulation_frame_budget"})
    finally:
        try:
            renderer.close()
        finally:
            log.flush(session)
            log.close()
    result = score_isaac(directory)
    (directory / 'score.json').write_text(json.dumps(result, indent=2) + '\n')
    return result


if __name__ == "__main__":
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--output", type=Path, required=True)
    p.add_argument("--weights", type=Path, required=True)
    p.add_argument("--device", default="cpu")
    p.add_argument("--frames", type=int, default=96)
    p.add_argument("--target", default="LSS119")
    p.add_argument("--policy", choices=POLICIES, default="rule")
    p.add_argument("--budget", type=int, default=8)
    p.add_argument("--model-timeout-s", type=float, default=3.0)
    p.add_argument("--supervised", action="store_true")
    p.add_argument("--renderer", nargs=argparse.REMAINDER, required=True,
                   help="Isaac Python, render.py, --output (same shared directory), --font")
    print(json.dumps(run(p.parse_args()), indent=2))
