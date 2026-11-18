"""Check saved RGB evidence, policy replay, pose transitions and summaries."""

import argparse
import gzip
import hashlib
import json
from pathlib import Path

from control import Pose, evaluate, move
from roomnav.door_approach import replay_approach_log


def read_lines(path):
    if not path.is_file():
        path = path.with_suffix(path.suffix + ".gz")
    opener = gzip.open if path.suffix == ".gz" else open
    with opener(path, "rt", encoding="utf-8") as stream:
        return [json.loads(line) for line in stream]


def verify(root, *, logs_only=False):
    manifest = json.loads((root / "manifest.json").read_text())
    summaries = json.loads((root / "summary.json").read_text())
    if [r["case"] for r in summaries] != manifest["cases"]:
        raise ValueError("Run is incomplete or has unexpected cases")
    for summary in summaries:
        case = root / summary["case"]
        records = read_lines(case / "events.jsonl")
        replayed = list(replay_approach_log(records))
        rows = read_lines(case / "trace.jsonl")
        if len(rows) != summary["frames"] or len(replayed) != len(rows):
            raise ValueError("Frame counts disagree")
        pose = Pose(**summary["first_pose"])
        for index, (row, replay) in enumerate(zip(rows, replayed)):
            if replay != {"input": row["input"], "output": row["output"]}:
                raise ValueError("Trace disagrees with policy replay")
            if not logs_only:
                image = case / f"rgb_{index:04}.png"
                if hashlib.sha256(image.read_bytes()).hexdigest() != row["rgb_sha256"]:
                    raise ValueError("RGB evidence was changed")
            if Pose(**row["pose_before"]) != pose:
                raise ValueError("Camera trajectory is discontinuous")
            expected = move(pose, row["output"]["advice"]["action"])
            if row["action_executed"]:
                if Pose(**row["pose_after"]) != expected or expected == pose:
                    raise ValueError("Motion disagrees with policy command")
            elif row["pose_after"] != row["pose_before"]:
                raise ValueError("Camera moved without a command")
            if row["execution_source"] != "simulated_camera_actuator":
                raise ValueError("Unexpected execution evidence source")
            pose = Pose(**row["pose_after"])
        rebuilt = evaluate(rows, target_room="LSS119", target_x=summary["target_x"],
                           has_door=summary["scene_variant"] != "wall_only",
                           expected_failure=summary["expected_failure"])
        if any(summary[key] != value for key, value in rebuilt.items()):
            raise ValueError("Saved summary disagrees with evidence")
        if json.loads((case / "summary.json").read_text()) != summary:
            raise ValueError("Per-case and run summaries disagree")
        print(f"VERIFIED {summary['case']} {summary['outcome']}")
    print("Validation scope: " + ("policy, trajectory and scores from saved logs" if logs_only else
                                  "policy, trajectory, scores and original RGB hashes"))
    return summaries


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("run", type=Path)
    parser.add_argument("--logs-only", action="store_true",
                        help="Replay the compact review bundle without original RGB files")
    args = parser.parse_args()
    verify(args.run, logs_only=args.logs_only)
