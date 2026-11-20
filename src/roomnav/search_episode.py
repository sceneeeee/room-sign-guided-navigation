"""Deterministic, action-conditioned fixture environment and post-episode scoring.

The fixture supplies simulated perception and separate operator interactions.
Model policies still require a configured real backend. No fake planner is shipped.
"""
from collections import Counter
from dataclasses import asdict
import json
from math import hypot
from pathlib import Path
from statistics import mean

from .baseline import parse_room_number
from .multidoor import MultiDoorSearchSession, State, TERMINAL, replay
from .offline_video import FrameStamp
from .rgb_perception import Detection, box_iou
from .search_io import EpisodeLog
from .search_policy import POLICIES, SearchPolicy, configured_backend


def scene_item(category, box, handle=None, text=None):
    return category, tuple(box), handle, text


class FixtureEpisode:
    def __init__(self, definition, policy, budget, scripted=False, *, model_timeout_s=3.0):
        self.definition = definition
        self.session = MultiDoorSearchSession("fixture", definition["target"], origin="synthetic",
                                              policy=policy, budget=budget, model_timeout_s=model_timeout_s)
        self.doors = definition["doors"]
        self.script = list(definition["scripted_visits"]) if scripted else []
        self.scripted = scripted
        self.index = 0
        self.truth = {}
        self.visits = []
        self.losses = set()
        self.image = None

    def command(self, kind, **kwargs):
        s = self.session
        return s.apply(dict(kwargs, type=kind, at_s=max(0, s.last_at)+.001,
                            frame_id=s.stamp.frame_id if s.stamp else None, actor_source="fixture_operator"), rgb=self.image)

    def frame(self, items):
        self.index += 1
        if self.index > self.definition["frame_budget"]:
            self.command("fail", reason="frame_budget_exhausted")
            raise StopIteration
        stamp = FrameStamp(self.index, self.index, "1/4")
        detections = [asdict(Detection(c, b, .94)) for c,b,_,_ in items]
        ocr = [{"sign_index": i, "error": "OCRFailure" if text is None else None,
                "readings": [] if text is None else [{"raw_text": text, "confidence": .96}]}
               for i, (kind, _, _, text) in enumerate(items) if kind == "sign"]
        event = {"type": "frame", "frame": stamp.to_dict(), "mode": "detector", "manual_links": [],
                 "review_frame_id": None, "perception_error": None, "detections": detections, "ocr": ocr}
        if self.session.policy.name == "vlm-rgb":
            import numpy as np
            from PIL import Image, ImageDraw
            image = Image.new("RGB", (640, 360), "silver")
            draw = ImageDraw.Draw(image)
            for kind, (x,y,r,b), _, text in items:
                rect = (x*640,y*360,r*640,b*360)
                draw.rectangle(rect, fill="saddlebrown" if kind=="door" else "white")
                if kind == "sign" and text:
                    draw.text(rect[:2], text, fill="black")
            self.image = np.asarray(image)
        s = self.session
        out = s.apply({"type": "frame", "at_s": stamp.timestamp_s, "perception": event,
                       "source_frame": stamp.to_dict(), "image_size": [640,360]}, rgb=self.image)
        self.truth[stamp.frame_id] = items
        if s.pending_id is not None:
            self.command("feedback", action_id=s.pending_id, completed=True, safe=True)
        return out

    def overview(self):
        items = []
        for i, door in enumerate(self.doors):
            x = .015+.245*i
            items.append(scene_item("door", [x,.1,x+.20,.94], door["handle"]))
            items.append(scene_item("sign", [x+.03,.32,x+.14,.39], door["handle"], door["overview"]))
        items.append(scene_item("sign", [.02,.01,.25,.07], None, "LSS101 / LSS124 →"))
        return items

    def inspect(self, door):
        s, handle = self.session, door["handle"]
        self.visits.append(handle)
        # The simulated operator changes view only after choosing a visible candidate.
        self.command("next")
        self.frame([])
        box = [.31,.08,.69,.94]
        x = .705 if door["wall"] else .45
        def detail(text, small=False):
            sign = [x,.34,x+(.025 if small else .095),.34+(.02 if small else .08)]
            return [scene_item("door", box, handle), scene_item("sign", sign, handle, text)]
        out = self.frame(detail(None, small=True))
        track = out["candidates"][0]["track_id"]
        self.command("select", track_id=track)
        self.command("visual_confirm", track_id=track, actor="fixture_visual_observer")
        if handle in self.definition["lose_first_approach"] and handle not in self.losses:
            self.losses.add(handle)
            self.frame([])
            if not s.recovery:
                raise RuntimeError("Loss did not latch")
            self.command("next")
            out = self.frame(detail(None, small=True))
            track = out["candidates"][0]["track_id"]
            self.command("select", track_id=track)
            self.command("visual_confirm", track_id=track, actor="fixture_visual_observer")
        for _ in range(3):
            self.frame(detail("?", small=True))
        for i, reading in enumerate([door["reads"][0], *door["reads"]]):
            self.frame(detail(reading))
            if i == 1:
                self.command("bind", track_id=track, sign_track_id=s.evidence.current[1].track_id,
                             physical_door_id=handle, actor="fixture_association_observer")
        if s.state == State.VERIFY_TARGET and handle in self.definition["operator_cards"]:
            self.command("confirm_room", landmark_id=s.plate.landmark_id,
                         text=self.definition["operator_cards"][handle], actor="fixture_independent_reader")
        if s.state not in TERMINAL:
            self.command("next")
            self.frame([])

    def run(self):
        s = self.session
        try:
            while s.state not in TERMINAL:
                self.frame(self.overview())
                out = self.frame(self.overview())
                tracks = [c for c in out["candidates"] if c["category"]=="door"]
                if self.scripted:
                    handle = self.script.pop(0) if self.script else None
                else:
                    # Execute exactly the recommended visible track, including a revisit.
                    chosen = out["intent"]["suggested_track"]
                    slot = next((i for i,c in enumerate(tracks) if c["track_id"] == chosen), None)
                    handle = self.doors[slot]["handle"] if slot is not None else None
                    if handle is not None:
                        self.command("accept_intent", track_id=chosen)
                if handle is None:
                    self.command("fail", reason="no_observation_intent")
                    break
                self.inspect(next(d for d in self.doors if d["handle"]==handle))
        except StopIteration:
            pass
        return s


def rate(correct, total):
    return {"correct": correct, "total": total, "accuracy": correct/total if total else None}


def matched_door(candidate, annotations):
    """A detector track has a scoreable door identity only on a unique match."""
    if candidate is None:
        return None
    matches = [handle for kind, box, handle, _ in annotations
               if kind == "door" and box_iou(box, candidate["bbox_xyxy_norm"]) >= .5]
    return matches[0] if len(matches) == 1 else None


def score_selections(session):
    """Attribute accepted choices on the identical recorded Rule input."""
    if session.policy.name not in POLICIES[2:]:
        return None
    rule = SearchPolicy("rule")
    counts, reasons = Counter(), Counter()

    def count_selection(state, row):
        if row["input"]["type"] != "accept_intent":
            return
        model = state.intent["model"] or {}
        actual = row["input"]["track_id"]
        local = rule.plan(state.stream, state.evidence.current, state.inspected)["suggested_track"]
        counts["accepted_intents"] += 1
        if actual == local:
            counts["rule_equivalent_choices"] += 1
            status = model.get("status", "no_model_result")
            if status != "ok":
                reasons[status] += 1
            elif model.get("suggested_track") != actual:
                reasons["no_model_track_used"] += 1
            else:
                reasons["model_agreed_with_rule"] += 1
        elif model.get("status") == "ok" and model.get("suggested_track") == actual:
            counts["model_changed_choices"] += 1
        else:
            counts["non_model_different_choices"] += 1

    replay(session.records, on_event=count_selection)
    return {key: counts[key] for key in ("accepted_intents", "rule_equivalent_choices",
                                        "model_changed_choices", "non_model_different_choices")} | {
        "rule_equivalent_reasons": dict(sorted(reasons.items()))}


def score(session, *, truth=None, labels=None, confirmed_handle=None, simulated_path_m=None,
          observer_available=True):
    """Evaluator-only annotation tables. Unknown denominators remain null."""
    rows = [r for r in session.records if "input" in r]
    reads = right_reads = associations = right_associations = lost = feedback = 0
    latency = []
    target = parse_room_number(session.stream.target)
    target_frames = {key: [] for key in ("ocr_hit", "annotated_ocr_hit", "correct_owner_hypothesis_or_stable",
                                       "correct_owner_stable", "selected_target", "ocr_on_selected_target")}
    verify_frames = set()
    confirmations = 0
    abstentions = Counter()
    operator_events = [r["input"] for r in rows if r["input"]["type"] not in ("frame","fail")]
    human_count = sum(cmd.get("actor_source", "operator") == "operator" for cmd in operator_events)
    for row in rows:
        cmd, out = row["input"], row["output"]
        if cmd["type"] == "feedback":
            feedback += 1
        if cmd["type"] == "confirm_room":
            confirmations += 1
        if out["state"] == "VERIFY_TARGET":
            frame_id = cmd["perception"]["frame"]["frame_id"] if cmd["type"] == "frame" else cmd.get("frame_id")
            if frame_id:
                verify_frames.add(frame_id)
        if cmd["type"] != "frame":
            continue
        lost += int(out["recovery_required"] or out["failure_reason"] == "selected_track_lost_no_automatic_replacement")
        timing = row["timing"]
        if "m2_s" in timing and "m3_s" in timing:
            latency.append(timing["m2_s"]+timing["m3_s"])
        stamp = cmd["perception"]["frame"]
        frame = stamp["frame_index"]
        annotations = (truth or {}).get(stamp["frame_id"], [])
        candidates = out["candidates"]
        tracks = {c["track_id"]: c for c in candidates}
        if out["advice"]["action"] == "STOP_REOBSERVE":
            abstentions[out["advice"]["reason"]] += 1
        if any(parse_room_number(r["raw_text"]) == target for c in candidates for r in c["ocr"]):
            target_frames["ocr_hit"].append(frame)
        selected = matched_door(tracks.get(out["active_track"]), annotations)
        selected_target = (selected is not None and labels is not None and selected in labels
                           and parse_room_number(labels[selected]) == target)
        if selected_target:
            target_frames["selected_target"].append(frame)
        annotated_target_hits = set()
        target_owner_hypothesis = target_owner_stable = False
        for kind, box, handle, _ in annotations:
            if kind != "sign" or handle is None or not labels or handle not in labels:
                continue
            reads += 1
            associations += 1
            signs = [c for c in candidates if c["category"]=="sign" and box_iou(box,c["bbox_xyxy_norm"]) >= .5]
            if len(signs) != 1:
                continue
            c = signs[0]
            right_reads += {r["raw_text"].strip().upper() for r in c["ocr"]} == {labels[handle]}
            correct_owner = matched_door(tracks.get(c["door_track_id"]), annotations) == handle
            stable = correct_owner and c["association_status"] in ("associated", "stable_hypothesis")
            right_associations += stable
            if parse_room_number(labels[handle]) == target and any(
                    parse_room_number(r["raw_text"]) == target for r in c["ocr"]):
                annotated_target_hits.add(handle)
                target_owner_hypothesis |= correct_owner and c["association_status"] == "hypothesis"
                target_owner_stable |= stable
        if annotated_target_hits:
            target_frames["annotated_ocr_hit"].append(frame)
        if target_owner_hypothesis or target_owner_stable:
            target_frames["correct_owner_hypothesis_or_stable"].append(frame)
        if target_owner_stable:
            target_frames["correct_owner_stable"].append(frame)
        if selected in annotated_target_hits:
            target_frames["ocr_on_selected_target"].append(frame)
    found = session.state == State.FOUND
    known = labels is not None and confirmed_handle in labels
    correct = parse_room_number(labels[confirmed_handle]) == target if known else None
    evaluated = observer_available and (known if found else bool(labels))
    model_results = {m["call"]: m for row in rows if (m := row.get("planner_response")) and "call" in m}
    return {"policy": session.policy.name, "provenance": session.origin, "state": session.state.value,
            "observer_available": observer_available, "success_evaluated": evaluated,
            "verification_unavailable_reason": (None if evaluated else "no_independent_reader"
                                                  if not observer_available else "missing_door_truth"),
            "success": (correct if found else False) if evaluated else None,
            "wrong_found": (not correct if found else False) if evaluated else None,
            "found_observed": found, "independent_confirmations": confirmations,
            "verify_target_frame_ids": sorted(verify_frames),
            "target_frames": target_frames, "abstention_reasons": dict(sorted(abstentions.items())),
            "doors_visited": len(set(session.visit_history)), "repeated_visits": len(session.visit_history)-len(set(session.visit_history)),
            "ocr": rate(right_reads, reads), "association": rate(right_associations, associations), "track_loss": lost,
            "human_interactions": human_count, "simulated_interactions": len(operator_events)-human_count,
            "execution_feedback": feedback, "action_count": session.movement_count,
            "episode_duration_s": rows[-1]["input"]["at_s"]-rows[0]["input"]["at_s"] if rows else 0,
            "mean_decision_latency_s": mean(latency) if latency else None,
            "failure_category": session.failure, "simulated_path_m": simulated_path_m,
            "model_calls": session.policy.calls, "model_selection": score_selections(session),
            "model_valid_calls": sum(m["status"] == "ok" for m in model_results.values()),
            "model_valid_calls_without_track": sum(m["status"] == "ok" and not m.get("suggested_track")
                                                   for m in model_results.values()),
            "model_fallback_calls": sum(m["status"] == "fallback" for m in model_results.values()),
            "model_fallback_reasons": dict(Counter(m.get("error") or "unspecified"
                                                   for m in model_results.values() if m["status"] == "fallback"))}


def score_isaac(directory):
    """Read an Isaac run and rescore it without touching its saved artifacts."""
    directory = Path(directory)
    manifest = json.loads((directory / "manifest.json").read_text())
    if type(manifest.get("supervised")) is not bool:
        raise ValueError("Isaac manifest must record whether an independent reader was available")
    with (directory / "episode.jsonl").open() as stream:
        session = replay(map(json.loads, stream))
    truth, labels = {}, {}
    with (directory / "evaluator.jsonl").open() as stream:
        for row in map(json.loads, stream):
            index, items = row["index"], row["items"]
            truth[FrameStamp(index, index, "1/2").frame_id] = items
            labels.update({handle: label for kind, _, handle, label in items if kind == "door"})
    with (directory / "execution.jsonl").open() as stream:
        path = sum(hypot(row["pose_after"]["x"] - row["pose_before"]["x"],
                         row["pose_after"]["y"] - row["pose_before"]["y"])
                   for row in map(json.loads, stream))
    confirmed_handle = None
    if session.state == State.FOUND:
        view = session.snapshot()
        active = next((c for c in view["candidates"] if c["track_id"] == view["active_track"]), None)
        confirmed_handle = matched_door(active, truth.get(session.stamp.frame_id, []))
    result = score(session, truth=truth, labels=labels, confirmed_handle=confirmed_handle,
                   simulated_path_m=path, observer_available=manifest["supervised"])
    inspected = []
    last_handle = None
    for row in session.records:
        kind = row.get("input", {}).get("type")
        if kind == "next":
            last_handle = None
        if kind != "frame":
            continue
        view = row["output"]
        door = next((c for c in view["candidates"] if c["track_id"] == view["active_track"]), None)
        handle = matched_door(door, truth.get(row["input"]["perception"]["frame"]["frame_id"], []))
        if handle is not None and handle != last_handle:
            last_handle = handle
            inspected.append(handle)
    result.update(evaluator_selected_doors=inspected, unique_selected_doors=len(set(inspected)),
                  repeated_selections=len(inspected) - len(set(inspected)))
    return result


def compare(args):
    definition = json.loads(Path(args.episode).read_text())
    args.output.mkdir(parents=True, exist_ok=False)
    results = []
    for policy in args.policies:
        episode = FixtureEpisode(definition, policy, args.budget, args.scripted, model_timeout_s=args.model_timeout_s)
        episode.session.policy.backend = configured_backend(policy, args.budget)
        s = episode.run()
        log = EpisodeLog(args.output / (policy+".jsonl"))
        try:
            log.flush(s)
        finally:
            log.close()
        replay(s.records)
        results.append(score(s, truth=episode.truth, labels=definition["evaluator"]["room_labels"],
                             confirmed_handle=episode.visits[-1] if episode.visits else None,
                             observer_available=bool(definition["operator_cards"])))
    (args.output / "scores.json").write_text(json.dumps(results, indent=2)+"\n")
    return results
