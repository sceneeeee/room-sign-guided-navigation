"""Bounded camera motion and evaluation for an Isaac-only experiment.

The policy receives RGB-derived perception events. Simulator geometry is only
used by the actuator's collision envelope and the separate result evaluator.
"""

from dataclasses import asdict, dataclass, replace
import math

from roomnav.baseline import parse_room_number
from roomnav.rgb_perception import box_iou


@dataclass(frozen=True)
class Pose:
    x: float = 0.0
    y: float = -4.8
    yaw_deg: float = 0.0


def move(pose, action, *, turn_deg=4.0, forward_m=0.45):
    """Yaw zero faces +Y, positive yaw turns left. Only move on policy advice."""
    if action == "TURN_LEFT":
        return replace(pose, yaw_deg=pose.yaw_deg + turn_deg)
    if action == "TURN_RIGHT":
        return replace(pose, yaw_deg=pose.yaw_deg - turn_deg)
    if action == "FORWARD_SHORT":
        angle = math.radians(pose.yaw_deg)
        return replace(pose, x=pose.x - math.sin(angle) * forward_m,
                       y=pose.y + math.cos(angle) * forward_m)
    return pose


def readable_room(event, advice):
    """Accept one supported OCR label at a read opportunity, without a goal."""
    if advice["action"] != "STOP_AND_READ":
        return False
    indices = {link["sign_index"] for link in advice["plate_evidence"]}
    labels = {parse_room_number(reading["raw_text"])
              for region in event["ocr"] if region["sign_index"] in indices
              for reading in region["readings"]
              if reading["confidence"] is not None and reading["confidence"] >= .8}
    labels.discard(None)
    return len(labels) == 1


def evaluate(rows, *, target_room, target_x, has_door, expected_failure):
    """Score saved poses/OCR after control ends; never feed truth to the policy."""
    actions = [row["output"]["advice"]["action"] for row in rows]
    final = rows[-1]
    pose = Pose(**final["pose_before"])
    labels = []
    for link in final["output"]["advice"]["plate_evidence"]:
        region = next(r for r in final["input"]["ocr"] if r["sign_index"] == link["sign_index"])
        labels.extend(parse_room_number(r["raw_text"]) for r in region["readings"]
                      if r["confidence"] is not None and r["confidence"] >= .8)
    labels = {label for label in labels if label is not None}
    read_target = labels == {parse_room_number(target_room)}
    distance = math.hypot(pose.x - target_x, pose.y)
    target_bearing = math.degrees(math.atan2(pose.x - target_x, -pose.y))
    error = abs((pose.yaw_deg - target_bearing + 180) % 360 - 180)
    matches = []
    for row in rows:
        selected = row["output"]["advice"]["track_id"]
        track = next((t for t in row["output"]["tracks"] if t["track_id"] == selected), None)
        if track:
            matches.append(max((box_iou(track["bbox_xyxy_norm"], truth["bbox_xyxy_norm"])
                                for truth in row["ground_truth_doors"]
                                if truth["visible"] and truth["door_index"] == 0), default=0.))
    final_target_iou = 0.
    selected = final["output"]["advice"]["track_id"]
    track = next((t for t in final["output"]["tracks"] if t["track_id"] == selected), None)
    if track:
        final_target_iou = max((box_iou(track["bbox_xyxy_norm"], truth["bbox_xyxy_norm"])
                                for truth in final["ground_truth_doors"]
                                if truth["visible"] and truth["door_index"] == 0), default=0.)
    # A read attempt, correct associated OCR, physical door, and facing that
    # door are all required. A wall with a sign cannot score as a valid arrival.
    success = bool(has_door and actions[-1] == "STOP_AND_READ" and read_target
                   and error <= 12 and final_target_iou >= .5 and min(matches, default=0.) >= .5)
    return {
        "success": success,
        "expected_failure": expected_failure,
        "outcome": "read_target" if success else "stopped_without_verified_read",
        "actions": {action: actions.count(action) for action in sorted(set(actions))},
        "frames": len(rows),
        "final_pose": asdict(pose),
        "distance_to_door_m": round(distance, 4),
        "bearing_error_deg": round(error, 4),
        "final_selected_door_iou": round(final_target_iou, 4),
        "minimum_selected_door_iou": round(min(matches), 4) if matches else None,
        "approach_then_read": success and any(row["action_executed"]
            and row["output"]["advice"]["action"] == "FORWARD_SHORT" for row in rows),
        "ocr_matches_target": read_target,
        "policy_read_on_wall": not has_door and "STOP_AND_READ" in actions,
        "forward_on_wall": not has_door and "FORWARD_SHORT" in actions,
        "final_reason": final["output"]["advice"]["reason"],
        "halt_reason": final["output"]["advice"]["halt_reason"],
    }
