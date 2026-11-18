"""Close the RGB -> existing M3.2 advice -> simulated camera motion loop."""

import argparse
from dataclasses import asdict
import hashlib
from importlib.metadata import version
import json
from pathlib import Path
import select
import subprocess
import time
import traceback

from control import Pose, evaluate, move, readable_room


CASES = {
    "left": {"pose": Pose(yaw_deg=-22)},
    "right": {"pose": Pose(yaw_deg=22)},
    "center": {"pose": Pose()},
    "near": {"pose": Pose(y=-2.5)},
    "medium": {"pose": Pose(y=-3.5)},
    "far": {"pose": Pose(y=-6.5)},
    "blank_plate": {"pose": Pose(), "variant": "blank_plate", "expected_failure": True},
    "wall_only": {"pose": Pose(), "variant": "wall_only", "expected_failure": True},
    "competing_doors": {"pose": Pose(), "variant": "competing_doors", "expected_failure": True},
    "occluded": {"pose": Pose(yaw_deg=-22), "occlude_at": 3, "expected_failure": True},
    "wall_plate": {"pose": Pose(), "variant": "wall_plate"},
}


def write_json(path, value):
    path.write_text(json.dumps(value, indent=2, allow_nan=False) + "\n", encoding="utf-8")


class PerceptionWorker:
    def __init__(self, python, weights, device, directory, text_features=None, ocr_border=0):
        self.log = (directory / "perception.log").open("w")
        command = [str(python), "-u", str(Path(__file__).with_name("perception_worker.py")),
                   "--weights", str(weights), "--device", device, "--ocr-border", str(ocr_border)]
        if text_features:
            command.extend(["--text-features", str(text_features.resolve())])
        self.process = subprocess.Popen(
            command,
            stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=self.log,
            text=True, bufsize=1)
        try:
            self.info = self.receive()
            if self.info.get("ready") is not True:
                raise RuntimeError("Perception worker did not initialize")
        except BaseException:
            self.close()
            raise

    def receive(self):
        if not select.select([self.process.stdout], [], [], 180)[0]:
            raise TimeoutError("Perception response timed out; inspect perception.log")
        line = self.process.stdout.readline()
        if not line:
            raise RuntimeError("Perception worker exited; inspect perception.log")
        return json.loads(line)

    def perceive(self, image, index):
        self.process.stdin.write(json.dumps({"image": str(image), "index": index}) + "\n")
        self.process.stdin.flush()
        return self.receive()

    def close(self):
        if self.process.poll() is None:
            self.process.terminate()
            try:
                self.process.wait(timeout=10)
            except subprocess.TimeoutExpired:
                self.process.kill()
                self.process.wait()
        self.log.close()


def run_case(app, args, name, worker):
    import numpy as np
    import omni.replicator.core as rep
    import omni.usd
    from PIL import Image, ImageDraw
    from roomnav.door_approach import ADVISORY_SCHEMA, DoorApproachPreview
    from scene import Corridor

    spec = CASES[name]
    directory = args.output / name
    directory.mkdir()
    context = omni.usd.get_context()
    context.new_stage()
    app.reset_render_settings()
    if not context.save_as_stage(str(directory / "scene.usda")):
        raise RuntimeError("Could not create USD scene")
    stage = context.get_stage()
    variant = spec.get("variant", "standard")
    scene = Corridor(stage, directory, args.font, variant=variant)
    pose = spec["pose"]
    scene.set_pose(pose)
    # Save the existing layer without replacing the stage and its prim handles.
    stage.GetRootLayer().Save()
    header = {"type": "start", "schema": ADVISORY_SCHEMA, "mode": "detector",
              "evidence_origin": "synthetic", "image_size": [args.width, args.height]}
    session = DoorApproachPreview(header)
    product = rep.create.render_product(scene.camera.GetPath(), (args.width, args.height))
    rgb = rep.AnnotatorRegistry.get_annotator("rgb")
    rgb.attach([product])
    rep.orchestrator.set_capture_on_play(False)
    rows, stationary, read_attempts = [], 0, 0
    try:
        with (directory / "events.jsonl").open("w") as events, (directory / "trace.jsonl").open("w") as trace:
            events.write(json.dumps(header) + "\n")
            for index in range(args.max_frames):
                scene.set_pose(pose)
                if index == spec.get("occlude_at"):
                    scene.occlude()
                for _ in range(4):
                    rep.orchestrator.step(rt_subframes=8, delta_time=1/60)
                    app.update()
                pixels = rgb.get_data()
                if pixels.shape != (args.height, args.width, 4):
                    raise RuntimeError(f"Invalid renderer output {pixels.shape}")
                image = Image.fromarray(np.asarray(pixels[:, :, :3], dtype=np.uint8))
                image_path = directory / f"rgb_{index:04}.png"
                image.save(image_path)
                started = time.monotonic()
                event = worker.perceive(image_path, index)
                output = session.process_event(event)
                action = output["advice"]["action"]
                proposed = move(pose, action)
                # This camera rig moves only within the unobstructed test
                # corridor. Keep the simulated actuator away from its wall.
                executed = (proposed != pose and index + 1 < args.max_frames
                            and -3.4 < proposed.x < 3.4 and -8 < proposed.y < -.7)
                next_pose = proposed if executed else pose
                row = {"input": event, "output": output, "pose_before": asdict(pose),
                       "pose_after": asdict(next_pose), "action_executed": executed,
                       "execution_source": "simulated_camera_actuator",
                       "ground_truth_doors": scene.ground_truth(),
                       "rgb_sha256": hashlib.sha256(image_path.read_bytes()).hexdigest(),
                       "perception_seconds": round(time.monotonic() - started, 4)}
                rows.append(row)
                events.write(json.dumps({"input": event, "output": output}) + "\n")
                events.flush()
                trace.write(json.dumps(row) + "\n")
                trace.flush()
                overlay = image.copy()
                draw = ImageDraw.Draw(overlay)
                for detection in event["detections"]:
                    box = detection["bbox_xyxy_norm"]
                    coords = [box[0]*args.width, box[1]*args.height,
                              box[2]*args.width, box[3]*args.height]
                    color = "lime" if detection["category"] == "door" else "cyan"
                    draw.rectangle(coords, outline=color, width=2)
                    draw.text(coords[:2], f"{detection['category']} {detection['score']:.2f}", fill=color)
                draw.rectangle((0, 0, args.width, 36), fill="black")
                draw.text((8, 4), f"{name}  frame {index}  {action}", fill="white")
                draw.text((8, 20), output["advice"]["reason"][:110], fill="white")
                overlay.save(directory / f"overlay_{index:04}.png")
                print(f"FRAME {name} {index} {action} {output['advice']['reason']}", flush=True)
                stationary = 0 if executed else stationary + 1
                if action == "STOP_AND_READ":
                    read_attempts += 1
                pose = next_pose
                if (readable_room(event, output["advice"]) or read_attempts >= 3
                        or session.halted or stationary >= 7):
                    break
            events.write(json.dumps({"type": "end", "event_count": len(rows)}) + "\n")
        scene.set_pose(pose)
        stage.GetRootLayer().Save()
    finally:
        rgb.detach([product])
        product.destroy()
    target_x = -1.0 if variant == "competing_doors" else 0.0
    result = evaluate(rows, target_room="LSS119", target_x=target_x, has_door=variant != "wall_only",
                      expected_failure=spec.get("expected_failure", False))
    result.update(case=name, scene_variant=variant,
                  input_domain="isaac_sim_rendered_rgb", policy="M3.2 visual_advisory",
                  target_x=target_x, first_pose=asdict(spec["pose"]))
    write_json(directory / "summary.json", result)
    subprocess.run(["ffmpeg", "-hide_banner", "-loglevel", "error", "-framerate", "2",
                    "-i", str(directory / "overlay_%04d.png"), "-c:v", "libx264",
                    "-pix_fmt", "yuv420p", str(directory / "demo.mp4")], check=True)
    print("RESULT " + json.dumps(result), flush=True)
    return result


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--perception-python", type=Path, required=True)
    parser.add_argument("--weights", type=Path, required=True)
    parser.add_argument("--font", type=Path, required=True)
    parser.add_argument("--device", default="cpu")
    parser.add_argument("--text-features", type=Path)
    parser.add_argument("--case", choices=CASES, action="append")
    parser.add_argument("--max-frames", type=int, default=30)
    parser.add_argument("--width", type=int, default=768)
    parser.add_argument("--height", type=int, default=432)
    parser.add_argument("--ocr-border", type=int, default=0)
    args = parser.parse_args()
    if args.max_frames < 3:
        parser.error("--max-frames must be at least 3")
    if args.width < 320 or args.height < 180 or args.width % 2 or args.height % 2:
        parser.error("Resolution must use even dimensions, at least 320 by 180")
    if args.ocr_border < 0:
        parser.error("--ocr-border must be nonnegative")
    for field in ("perception_python", "weights", "font"):
        # Resolving the interpreter symlink would bypass its virtualenv.
        value = getattr(args, field).absolute()
        if not value.is_file():
            parser.error(f"Missing {field}: {value}")
        setattr(args, field, value)
    if args.text_features is not None and not args.text_features.is_file():
        parser.error(f"Missing text feature cache: {args.text_features}")
    if args.case is not None and len(args.case) != len(set(args.case)):
        parser.error("Each case can only appear once")
    args.output = args.output.resolve()
    args.output.mkdir(parents=True)
    write_json(args.output / "manifest.json", {
        "cases": args.case or list(CASES), "max_frames": args.max_frames,
        "weights_file": args.weights.name,
        "weights_sha256": hashlib.sha256(args.weights.read_bytes()).hexdigest(),
        "source_sha256": {p.name: hashlib.sha256(p.read_bytes()).hexdigest()
                          for p in Path(__file__).parent.glob("*.py")},
        "roomnav_source_sha256": {p.name: hashlib.sha256(p.read_bytes()).hexdigest()
                                  for p in (Path(__file__).resolve().parents[2] / "src" / "roomnav").glob("*.py")},
        "turn_step_deg": 4, "forward_step_m": .45, "max_read_attempts": 3,
        "camera_height_m": 1.48, "resolution": [args.width, args.height],
        "policy_uses": "rendered RGB detector/OCR only",
        "actuator": "bounded kinematic camera rig, no robot dynamics",
    })
    # Isaac Sim must initialize before importing its rendering/USD modules.
    from isaacsim import SimulationApp
    app = SimulationApp({"headless": True, "width": args.width, "height": args.height,
                         "renderer": "RaytracedLighting", "multi_gpu": False})
    worker = None
    status = 0
    try:
        worker = PerceptionWorker(args.perception_python, args.weights, args.device, args.output,
                                  args.text_features, args.ocr_border)
        manifest_path = args.output / "manifest.json"
        manifest = json.loads(manifest_path.read_text())
        manifest.update(perception=worker.info, isaac_sim_version=version("isaacsim"),
                        frame_time_basis="simulation capture index / 2; not wall-clock latency")
        write_json(manifest_path, manifest)
        results = []
        for name in args.case or CASES:
            results.append(run_case(app, args, name, worker))
            write_json(args.output / "summary.json", results)
    except Exception:
        traceback.print_exc()
        status = 1
    finally:
        if worker is not None:
            worker.close()
        app.close(exit_code=status)


if __name__ == "__main__":
    main()
