"""Local-only M3.1 processing and deterministic, model-free JSONL replay."""
import argparse
import json
from pathlib import Path
import subprocess

from .offline_perception import DetectorSource, HumanBoxSource, perceive_frame
from .offline_provenance import file_digest as _digest, runtime_manifest
from .offline_replay import ReplaySession, SCHEMA, replay_log
from .offline_video import iter_video, probe_video
from .rgb_perception import YOLOWorldDetector
from .sign_evidence import CONTEXT_POLICY
from .stream import parse_event_line

DEFAULT_VIDEO = Path("data/field_sessions/1f_raw/session_01/video/route_LSS101_LSS124_LSS119.MOV")
DEFAULT_REVIEW = Path("docs/field-tests/2026-11-07-m1-human-reviewed.jsonl")


def _write(handle, record):
    handle.write(json.dumps(record, ensure_ascii=False, sort_keys=True, allow_nan=False) + "\n")


def process_video(args):
    # Check input before creating outputs or initializing/downloading models.
    metadata = probe_video(args.video)
    if args.mode == "human_verified_boxes":
        if args.bindings is None or args.image_root is None:
            raise ValueError("Human-box mode requires --bindings and --image-root")
        source = HumanBoxSource(args.review, image_root=args.image_root,
                                bindings=args.bindings, session_id=args.session_id)
        provenance = {"review_sha256": _digest(args.review), "bindings_sha256": _digest(args.bindings),
                      "box_score_semantics": "constant 1.0 for interface only; not detector confidence"}
    else:
        if args.bindings is not None or args.image_root is not None:
            raise ValueError("Detector mode cannot consume review bindings")
        if args.weights is None or not args.weights.is_file():
            raise ValueError("Detector mode requires existing local --weights; no implicit weight download")
        source = DetectorSource(YOLOWorldDetector(weights=str(args.weights), device=args.device,
                                                 confidence=args.confidence, image_size=args.image_size))
        provenance = {"weights_sha256": _digest(args.weights), "weights_name": args.weights.name,
                      "confidence": args.confidence, "image_size": args.image_size,
                      "prompts": source.detector.prompts, "categories": source.detector.categories,
                      "device": args.device}
    header = {"type": "start", "schema": SCHEMA, "mode": args.mode,
              "evidence_origin": args.evidence_origin, "session_id": args.session_id,
              "target": args.target, "segment_id": args.segment_id,
              "video_sha256": _digest(args.video), "video_stream": metadata["stream"],
              "video_format": metadata["format"], "stride": args.stride,
              "perception": provenance, "runtime": runtime_manifest(),
              "clock": "original_pts_offline_virtual_clock"}
    if args.mode == "detector":
        header["ocr_context_policy"] = CONTEXT_POLICY
    if args.inspection_proposals:
        header["inspection_proposals"] = True
    session = ReplaySession(header)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    count = 0
    with args.output.open("x", encoding="utf-8") as handle:
        _write(handle, header)
        for frame in iter_video(args.video, metadata=metadata, stride=args.stride):
            event = perceive_frame(frame, source)
            if event is None:
                continue
            _write(handle, {"input": event, "output": session.process_event(event)})
            count += 1
        source.finish()
        if not count:
            raise ValueError("No frames processed")
        _write(handle, {"type": "end", "event_count": count})
    print(json.dumps({"status": "processed", "frames": count, "mode": args.mode,
                      "evidence_origin": args.evidence_origin, "output": str(args.output),
                      "real_world_validation": False}))


def process_fixture(args):
    """Run explicitly synthetic event inputs, useful without video/models."""
    with args.input.open(encoding="utf-8") as handle:
        rows = [parse_event_line(line) for line in handle if line.strip()]
    if not rows or rows[0].get("evidence_origin") != "synthetic":
        raise ValueError("Fixture command accepts explicitly synthetic inputs only")
    session = ReplaySession(rows[0])
    args.output.parent.mkdir(parents=True, exist_ok=True)
    with args.output.open("x", encoding="utf-8") as handle:
        _write(handle, rows[0])
        for event in rows[1:]:
            _write(handle, {"input": event, "output": session.process_event(event)})
        if len(rows) < 2:
            raise ValueError("Fixture has no events")
        _write(handle, {"type": "end", "event_count": len(rows) - 1})
    print(json.dumps({"status": "processed", "events": len(rows) - 1,
                      "evidence_origin": "synthetic", "output": str(args.output),
                      "real_world_validation": False}))


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest="command", required=True)
    process = commands.add_parser("process")
    process.add_argument("--video", type=Path, default=DEFAULT_VIDEO)
    process.add_argument("--mode", choices=("human_verified_boxes", "detector"), required=True)
    process.add_argument("--evidence-origin", choices=("recorded_video", "synthetic"), default="recorded_video")
    process.add_argument("--review", type=Path, default=DEFAULT_REVIEW)
    process.add_argument("--bindings", type=Path)
    process.add_argument("--image-root", type=Path)
    process.add_argument("--weights", type=Path)
    process.add_argument("--device", default="cpu")
    process.add_argument("--confidence", type=float, default=.3)
    process.add_argument("--image-size", type=int, default=640)
    process.add_argument("--stride", type=int, default=1)
    process.add_argument("--inspection-proposals", action="store_true",
                         help="Log advisory INSPECT_DOOR proposals; never authorize motion")
    process.add_argument("--session-id", default="session_01")
    process.add_argument("--segment-id", default="corridor_lss")
    process.add_argument("--target", required=True)
    process.add_argument("--output", type=Path, default=Path("outputs/m31/events.jsonl"))
    replay = commands.add_parser("replay")
    replay.add_argument("log", type=Path)
    fixture = commands.add_parser("fixture")
    fixture.add_argument("--input", type=Path, required=True)
    fixture.add_argument("--output", type=Path, required=True)
    args = parser.parse_args(argv)
    try:
        if args.command == "process":
            if args.stride < 1:
                raise ValueError("stride must be positive")
            process_video(args)
        elif args.command == "fixture":
            process_fixture(args)
        else:
            with args.log.open(encoding="utf-8") as handle:
                rows = (parse_event_line(line) for line in handle if line.strip())
                count = sum(1 for _ in replay_log(rows))
            print(json.dumps({"status": "verified", "events": count, "log": str(args.log),
                              "validation": "deterministic_event_replay_only"}))
        return 0
    except (OSError, ValueError, KeyError, IndexError, StopIteration, RuntimeError, subprocess.SubprocessError) as exc:
        parser.error(str(exc))


if __name__ == "__main__":
    raise SystemExit(main())
