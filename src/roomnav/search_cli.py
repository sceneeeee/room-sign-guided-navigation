"""Run a recorded M4 RGB episode, replay a decision log, or compare fixture episodes."""
import argparse
import json
from pathlib import Path

from .multidoor import MultiDoorSearchSession, TERMINAL, replay
from .offline_video import iter_video
from .rgb_perception import YOLOWorldDetector
from .search_io import EpisodeLog, rgb_command
from .search_policy import POLICIES, configured_backend


def run_video(args):
    if not args.weights.is_file():
        raise ValueError("Supply existing local detector weights")
    detector = YOLOWorldDetector(weights=str(args.weights), device=args.device)
    session = MultiDoorSearchSession(args.session, args.target, origin="recorded_video",
                                     policy=args.policy, budget=args.budget,
                                     backend=configured_backend(args.policy, args.budget), model_timeout_s=args.model_timeout_s)
    steps = json.loads(args.interactions.read_text()) if args.interactions else {}
    log = EpisodeLog(args.output)
    try:
        log.flush(session)
        for n, frame in enumerate(iter_video(args.video, stride=args.stride), 1):
            cmd, timing = rgb_command(frame, detector, origin=session.origin)
            session.apply(cmd, rgb=frame.rgb, timing=timing)
            for interaction in steps.pop(frame.stamp.frame_id, []):
                session.apply(dict(interaction, at_s=frame.stamp.timestamp_s), rgb=frame.rgb)
            log.flush(session)
            if session.recovery:
                session.apply({"type": "fail", "at_s": session.last_at, "reason": "missing_operator_reacquisition"})
                break
            if session.state in TERMINAL or n == args.frames:
                break
        if session.state not in TERMINAL:
            session.apply({"type": "fail", "at_s": max(0, session.last_at), "reason": "video_or_frame_budget_ended"})
        if steps:
            raise ValueError("Some operator interactions have no processed source frame")
    finally:
        log.flush(session)
        log.close()
    return session


def main():
    p = argparse.ArgumentParser(description=__doc__)
    subs = p.add_subparsers(dest="command", required=True)
    video = subs.add_parser("video")
    video.add_argument("video", type=Path)
    video.add_argument("--target", required=True)
    video.add_argument("--session", default="video")
    video.add_argument("--weights", type=Path, required=True)
    video.add_argument("--output", type=Path, required=True)
    video.add_argument("--device", default="cpu")
    video.add_argument("--stride", type=int, default=1)
    video.add_argument("--frames", type=int)
    video.add_argument("--interactions", type=Path)
    video.add_argument("--policy", choices=POLICIES, default="rule")
    video.add_argument("--budget", type=int, default=8)
    r = subs.add_parser("replay")
    r.add_argument("log", type=Path)
    isaac = subs.add_parser("score-isaac", help="Rescore saved Isaac artifacts without running the simulator")
    isaac.add_argument("directory", type=Path)
    ev = subs.add_parser("evaluate")
    ev.add_argument("episode", type=Path)
    ev.add_argument("--output", type=Path, required=True)
    ev.add_argument("--policies", choices=POLICIES, nargs="+", default=["sequential", "rule"])
    ev.add_argument("--budget", type=int, default=8)
    ev.add_argument("--scripted", action="store_true", help="Exercise the handoff regression route")
    for command in (video, ev):
        command.add_argument("--model-timeout-s", type=float, default=3.0)
    args = p.parse_args()
    if args.command == "score-isaac":
        from .search_episode import score_isaac
        print(json.dumps(score_isaac(args.directory), indent=2))
        return
    if args.command == "evaluate":
        from .search_episode import compare
        print(json.dumps(compare(args), indent=2))
        return
    if args.command == "replay":
        with args.log.open() as f:
            session = replay(json.loads(line) for line in f)
    else:
        session = run_video(args)
    print(json.dumps({"state": session.state.value, "visits": session.visit_history,
                      "reason": session.failure, "events": len(session.records)-1}))


if __name__ == "__main__":
    main()
