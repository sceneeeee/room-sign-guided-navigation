"""Model-free conversion of captured detector logs into reviewable previews."""
from collections import Counter
from copy import deepcopy
import json
from pathlib import Path
import subprocess
import textwrap

from .door_approach import DoorApproachPreview, SCHEMA, replay_approach_log
from .offline_provenance import file_digest
from .offline_replay import replay_log
from .offline_video import FrameStamp, iter_video, probe_video
from .stream import parse_event_line


def read_records(path):
    return [parse_event_line(line) for line in Path(path).read_text(encoding="utf-8").splitlines() if line.strip()]


def convert_detector_log(source, *, manual_selections=(), frame_reviews=(), preview_schema=SCHEMA):
    """Validate the complete M3.1 log, then consume its inputs chronologically.

    Source outputs are only checked for reproducibility, never used as preview
    inputs. Manual events reference an already visible frame and take effect
    on the next capture. No future frame, GT box or feedback enters preview.
    """
    header = source[0]
    if header.get("mode") != "detector":
        raise ValueError("Approach requires detector input, not human GT")
    list(replay_log(source))
    if any(row["input"]["type"] != "frame" for row in source[1:-1]):
        raise ValueError("Source must contain frames only, without feedback/confirmation/selection")
    start = {"type": "start", "schema": preview_schema, "mode": "detector",
             "evidence_origin": header["evidence_origin"],
             "image_size": [header["video_stream"]["width"], header["video_stream"]["height"]],
             "source_provenance": deepcopy(header),
             "clock": "original_pts_offline_virtual_clock"}
    session = DoorApproachPreview(start)
    reviews = {}
    for review in frame_reviews:
        if (not isinstance(review, dict) or not isinstance(review.get("frame_id"), str)
                or review["frame_id"] in reviews):
            raise ValueError("Expected unique explicit current-frame source reviews")
        reviews[review["frame_id"]] = review
    selections = {}
    for event in manual_selections:
        if (not isinstance(event, dict) or set(event) != {"type", "frame_id", "track_id"}
                or event["type"] != "select_target" or event["frame_id"] in selections):
            raise ValueError("Expected one explicit select_target event per current frame")
        selections[event["frame_id"]] = event
    rows = [start]
    for row in source[1:-1]:
        event = deepcopy(row["input"])
        review = reviews.pop(event["frame"]["frame_id"], None)
        if review is not None:
            event["visual_review"] = deepcopy(review)
        rows.append({"input": event, "output": session.process_event(event)})
        selection = selections.pop(event["frame"]["frame_id"], None)
        if selection is not None:
            rows.append({"input": selection, "output": session.process_event(selection)})
    if selections or reviews:
        raise ValueError("Manual selection or source review refers to a frame absent from source")
    rows.append({"type": "end", "event_count": len(rows) - 1})
    list(replay_approach_log(rows))
    return rows


def summarize(rows):
    frames = [r for r in rows[1:-1] if r["input"]["type"] == "frame"]
    advice = [r["output"]["advice"] for r in frames]
    counts = Counter(a["action"] for a in advice)
    non_stop_count = sum(counts[action] for action in ("TURN_LEFT", "TURN_RIGHT", "FORWARD_SHORT"))
    actions_by_mode = {}
    for a in advice:
        actions_by_mode.setdefault(a["evidence_mode"], Counter())[a["action"]] += 1
    action_names = ("TURN_LEFT", "TURN_RIGHT", "FORWARD_SHORT", "STOP_AND_READ", "STOP_REOBSERVE")
    return {"frame_count": len(frames),
            "frames_with_detector_doors": sum(any(c["category"] == "door" for c in r["output"]["tracks"]) for r in frames),
            "actions": {action: counts[action] for action in action_names},
            "reasons": dict(Counter(a["reason"] for a in advice)),
            "selected_targets": sorted({(a["track_id"], a["selection_source"]) for a in advice if a["track_id"]}),
            "evidence_modes": dict(Counter(a["evidence_mode"] for a in advice)),
            "actions_by_evidence_mode": {mode: {action: values[action] for action in action_names}
                                         for mode, values in actions_by_mode.items()},
            "non_stop_frames_by_evidence_mode": {
                mode: sum(values[action] for action in ("TURN_LEFT", "TURN_RIGHT", "FORWARD_SHORT"))
                for mode, values in actions_by_mode.items()},
            "non_stop_frame_count": non_stop_count,
            "non_stop_supported_by_current_detector": all(
                any(c["category"] == "door" and c["track_id"] == r["output"]["advice"]["track_id"]
                    for c in r["output"]["tracks"])
                for r in frames if r["output"]["advice"]["action"] in ("TURN_LEFT", "TURN_RIGHT", "FORWARD_SHORT"))
                if non_stop_count else None,
            "motion_authorized": False, "execution_feedback_count": 0, "found_count": 0}


def _annotate(rgb, output):
    from PIL import Image, ImageDraw, ImageFont

    frame = Image.fromarray(rgb).convert("RGB")
    width, height = frame.size
    draw = ImageDraw.Draw(frame)
    font = ImageFont.load_default(size=22)
    advice = output["advice"]
    for label_index, candidate in enumerate(output["tracks"]):
        x1, y1, x2, y2 = candidate["bbox_xyxy_norm"]
        active = candidate["track_id"] == advice["track_id"]
        color = "#ffdc40" if active else "#39bfff" if candidate["category"] == "door" else "#fa7272"
        left, top = min(width-1, round(x1*width)), min(height-1, round(y1*height))
        rect = (left, top, max(left, round(x2*width)-1), max(top, round(y2*height)-1))
        draw.rectangle(rect, outline=color, width=6 if active else 3)
        label = f"{candidate['track_id']} {candidate['category']} {candidate['score']:.3f}"
        if active:
            label += " TARGET"
        # Overlapping detector boxes often share the same top edge. Stagger
        # labels so the selected ID remains legible alongside low-score boxes.
        position = (min(rect[0]+4, width-400), min(height-30, rect[1]+4+label_index*28))
        draw.rectangle(draw.textbbox(position, label, font=font), fill="black")
        draw.text(position, label, fill=color, font=font)
    canvas = Image.new("RGB", (width, height + 512), "#151a20")
    canvas.paste(frame)
    draw = ImageDraw.Draw(canvas)
    lines = [f"{advice['frame_id']}  PTS={advice['pts']} x {advice['time_base']} = {advice['timestamp_s']:.6f}s",
             f"{advice['action']} / {advice['state']} | target={advice['track_id']} source={advice['selection_source']}",
             *textwrap.wrap(advice["reason"], 125)]
    diagnostics = output.get("diagnostics")
    if diagnostics:
        target = next((c for c in diagnostics.get("door_candidates", []) if c["track_id"] == advice["track_id"]), None)
        if target:
            lines.append(f"Target cx={target['center_x']:.3f} w={target['width']:.3f} h={target['height']:.3f} score={target['score']:.3f}")
        lines.append(f"Plate={diagnostics.get('plate_status')} | evidence={advice['evidence_mode']}")
        blockers = diagnostics["action_blockers"]
        lines.extend(textwrap.wrap("FORWARD blocked: "+", ".join(blockers["FORWARD_SHORT"]), 125))
        lines.extend(textwrap.wrap("READ blocked: "+", ".join(blockers["STOP_AND_READ"]), 125))
    lines.append("PRERECORDED ADVICE ONLY | motion_authorized=false | no execution evidence / no confirmed room")
    for i, line in enumerate(lines):
        draw.text((12, height + 10 + i*32), line, fill="white", font=font)
    return canvas


def write_preview_artifacts(rows, directory, *, video=None, frames_dir=None, demo_video=False):
    """Write a new output directory; never overwrite prior experiments.

    When --video is supplied, visualization uses freshly decoded original RGB
    and verifies every source stamp and the full video hash. --frames-dir is an
    optional cached visualization source only, explicitly labeled as such.
    """
    frames = [r for r in rows[1:-1] if r["input"]["type"] == "frame"]
    wanted = {r["input"]["frame"]["frame_id"]: r for r in frames}
    images = {}
    provenance = rows[0]["source_provenance"]
    if video is not None:
        if file_digest(video) != provenance.get("video_sha256"):
            raise ValueError("Video SHA256 differs from captured detector source")
        metadata = probe_video(video)
        for row in frames:
            stamp = FrameStamp.from_dict(row["input"]["frame"])
            if stamp != metadata["stamps"][stamp.frame_index]:
                raise ValueError("Captured frame disagrees with original video PTS")
        for frame in iter_video(video, metadata=metadata):
            row = wanted.get(frame.stamp.frame_id)
            if row is not None:
                images[frame.stamp.frame_id] = _annotate(frame.rgb, row["output"])
            if len(images) == len(wanted):
                break
    elif frames_dir is not None:
        import numpy as np
        from PIL import Image
        for fid, row in wanted.items():
            with Image.open(Path(frames_dir) / f"{fid}.png") as image:
                if list(image.size) != rows[0]["image_size"]:
                    raise ValueError("Cached visualization size disagrees with source")
                images[fid] = _annotate(np.asarray(image.convert("RGB")), row["output"])
    directory = Path(directory)
    directory.mkdir(parents=True, exist_ok=False)
    with (directory / "events.jsonl").open("x", encoding="utf-8") as handle:
        for row in rows:
            handle.write(json.dumps(row, ensure_ascii=False, sort_keys=True, allow_nan=False) + "\n")
    summary = summarize(rows)
    summary["visualization_source"] = "verified_original_video_decode" if video else "cached_rgb_unverified" if frames_dir else "none"
    summary["demo_video"] = "not_requested"
    if images:
        from PIL import Image
        rendered = directory / "frames"
        rendered.mkdir()
        thumbs = []
        for i, row in enumerate(frames):
            fid = row["input"]["frame"]["frame_id"]
            image = images[fid]
            image.save(rendered / f"{i:04d}.jpg", quality=90)
            thumb = image.copy()
            thumb.thumbnail((640, 430))
            thumbs.append(thumb)
        sheet = Image.new("RGB", (640 * 3, 430 * ((len(thumbs) + 2)//3)), "#151a20")
        for i, thumb in enumerate(thumbs):
            sheet.paste(thumb, ((i % 3)*640, (i//3)*430))
        sheet.save(directory / "contact-sheet.jpg", quality=93)
        if demo_video:
            result = subprocess.run([
                "ffmpeg", "-v", "error", "-nostdin", "-framerate", "2", "-i", str(rendered / "%04d.jpg"),
                "-vf", "scale=1280:-2", "-c:v", "libx264", "-pix_fmt", "yuv420p",
                str(directory / "demo.mp4")], capture_output=True, text=True)
            summary["demo_video"] = "created_2fps_presentation_source_pts_overlaid" if result.returncode == 0 else "encoding_failed"
            if result.returncode:
                (directory / "ffmpeg-error.log").write_text(result.stderr, encoding="utf-8")
    (directory / "summary.json").write_text(json.dumps(summary, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    lines = ["# Recorded-video Door Approaching Preview", "", json.dumps(summary, ensure_ascii=False, indent=2), "",
             "All advice has motion_authorized=false. Track selection is temporary; plate links are image hypotheses.",
             "STOP_AND_READ permits another OCR attempt; it does not confirm room identity. No FOUND or execution feedback.",
             "Demo playback is 2 FPS presentation; overlaid PTS retain the original capture times.", "",
             "| PTS / seconds | track / selection | geometry / score | OCR association | action | blockers TURN / FORWARD / READ |",
             "| --- | --- | --- | --- | --- | --- |"]
    for row in frames:
        a = row["output"]["advice"]
        d = row["output"]["diagnostics"]
        target = next((c for c in d["door_candidates"] if c["track_id"] == a["track_id"]), None)
        geometry = (f"cx={target['center_x']:.3f}, w={target['width']:.3f}, h={target['height']:.3f}, score={target['score']:.4f}, delta={target['change']}"
                    if target else "target not visible")
        blocks = d["action_blockers"]
        lines.append(f"| {a['pts']} × {a['time_base']} / {a['timestamp_s']:.6f} | {a['track_id']} / {a['selection_source']} | {geometry} | {d['plate_status']} | {a['action']}: {a['reason']} | LEFT: {blocks['TURN_LEFT']}; RIGHT: {blocks['TURN_RIGHT']}; FWD: {blocks['FORWARD_SHORT']}; READ: {blocks['STOP_AND_READ']} |")
    with (directory / "frame-reasons.jsonl").open("x", encoding="utf-8") as handle:
        for row in frames:
            handle.write(json.dumps({"frame": row["input"]["frame"], "advice": row["output"]["advice"],
                                     **row["output"]["diagnostics"]}, ensure_ascii=False, sort_keys=True) + "\n")
    (directory / "report.md").write_text("\n".join(lines) + "\n", encoding="utf-8")
    return summary
