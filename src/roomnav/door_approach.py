"""Recorded RGB advice, isolated from M2 facts and execution control.

No acknowledgement, physical identity, distance estimate or FOUND exists here.
Only current detector boxes enter the existing short-term tracker. Image-space
plate links remain hypotheses. Schema2 requires current human source review;
schema3 permits bounded visual advice without creating permanent associations.
"""
from copy import deepcopy
from dataclasses import asdict, replace
import json
from math import hypot, isfinite

from .baseline import parse_room_number
from .inspection import propose_inspection
from .ocr import OCRReading
from .offline_video import FrameStamp
from .rgb_perception import Detection, box_iou
from .sign_evidence import room_source_context
from .visual_guidance import MIN_DETECTOR_SCORE, camera_relative_advice
from .visual_tracking import ConservativeTracker

SCHEMA = "roomnav-m32-door-approach-2"
ADVISORY_SCHEMA = "roomnav-m32-door-approach-3"
FRAME_FIELDS = {"type", "frame", "mode", "detections", "manual_links",
                "review_frame_id", "ocr", "perception_error"}


def _quality_failures(candidate, *, require_score=True):
    x1, y1, x2, y2 = candidate.bbox_xyxy_norm
    failures = []
    if candidate.category != "door":
        failures.append("not_door_candidate")
    if require_score and (candidate.score is None or candidate.score < MIN_DETECTOR_SCORE):
        failures.append("detector_confidence_below_threshold")
    if x2 - x1 < .06 or y2 - y1 < .2:
        failures.append("door_box_too_small")
    if not .08 <= candidate.center_x <= .92:
        failures.append("door_center_near_image_edge")
    return failures


def _eligible(candidate):
    return not _quality_failures(candidate)


def _geometry_change(previous, target):
    if previous is None:
        return None
    a, b = previous.bbox_xyxy_norm, target.bbox_xyxy_norm
    dx, dy = target.center_x - previous.center_x, (b[1]+b[3]-a[1]-a[3])/2
    width_ratio, height_ratio = (b[2]-b[0])/(a[2]-a[0]), (b[3]-b[1])/(a[3]-a[1])
    return {"center_dx": dx, "center_dy": dy, "center_displacement": hypot(dx, dy),
            "width_ratio": width_ratio, "height_ratio": height_ratio,
            "iou": box_iou(a, b),
            "unstable": hypot(dx, dy) > .18 or not .55 < width_ratio < 1.8
                        or not .55 < height_ratio < 1.8}


def _plate_evidence(target, candidates, regions, image_size):
    """Conservative current-frame containment, with competing doors included.

    Low-scoring competing boxes also make containment ambiguous. Do not use
    OCR on another door, a wall sign, poster or directional board as readable.
    Lack of negative OCR markers alone is not a semantic door verification.
    """
    links = []
    width, height = image_size
    for i, sign in enumerate(candidates):
        if sign.category != "sign":
            continue
        sx1, sy1, sx2, sy2 = sign.bbox_xyxy_norm
        owners = [c for c in candidates if c.category == "door"
                  and c.bbox_xyxy_norm[0] <= sx1 and sx2 <= c.bbox_xyxy_norm[2]
                  and c.bbox_xyxy_norm[1] <= sy1 and sy2 <= c.bbox_xyxy_norm[3]]
        if target not in owners:
            continue
        region = regions[i]
        readings = [OCRReading(**r) for r in region["readings"]]
        labels = {parse_room_number(r.raw_text) for r in readings}
        labels.discard(None)
        context = room_source_context(readings)
        quality = (sign.score is not None and sign.score >= MIN_DETECTOR_SCORE
                   and .01 <= sx2 - sx1 <= .35 and .008 <= sy2 - sy1 <= .35
                   and sx1 > .005 and sx2 < .995 and sy1 > .005 and sy2 < .995)
        readable = (quality and (sx2 - sx1) * width >= 32 and (sy2 - sy1) * height >= 16
                    and any(parse_room_number(r.raw_text) is not None
                            and r.confidence is not None and r.confidence >= .8 for r in readings))
        links.append({"sign_index": i, "sign_track_id": sign.track_id,
                      "source": "current_frame_containment_hypothesis",
                      "association_verified": False,
                      "ambiguous": len(owners) != 1 or len(labels) > 1,
                      "context": context, "quality": quality,
                      "ocr_failed": region["error"] is not None or not readings,
                      "readable": readable})
    if not links:
        return "no_associated_plate_cue", links
    if len(links) != 1 or any(link["ambiguous"] for link in links):
        return "ambiguous_door_sign_or_ocr", links
    link = links[0]
    if link["context"] != "unverified":
        return "non_room_sign_context", links
    if link["ocr_failed"]:
        return "associated_plate_ocr_failed", links
    if not link["quality"]:
        return "associated_plate_low_quality", links
    return "readable" if link["readable"] else "needs_observation", links


class DoorApproachPreview:
    """A lock belongs to one temporary track; loss never selects a replacement.

    Two consecutive uniquely eligible captures without plausible competing
    door boxes are required for automatic selection, even if rivals score low.
    Manual selection chooses a currently visible door, bypassing ambiguity but
    never bypassing quality checks. One forward hint per track/session is the
    maximum; later prerecorded frames cannot replenish that budget.
    """
    def __init__(self, header):
        self.advisory = header.get("schema") == ADVISORY_SCHEMA
        if (header.get("type") != "start" or header.get("schema") not in (SCHEMA, ADVISORY_SCHEMA)
                or header.get("mode") != "detector"
                or header.get("evidence_origin") not in (
                    ("recorded_video", "synthetic", "live_rgb") if self.advisory else ("recorded_video", "synthetic"))):
            raise ValueError("Preview requires current schema and detector provenance; regenerate legacy previews from M3.1 inputs")
        size = header.get("image_size")
        if (not isinstance(size, list) or len(size) != 2
                or any(type(v) is not int or v <= 0 for v in size)):
            raise ValueError("Positive image_size [width, height] required")
        self.header = deepcopy(header)
        self.tracker = ConservativeTracker()
        self.visible = []
        self.stamp = None
        self.target_track_id = None
        self.selection_source = None
        self.halted = False
        self.halt_reason = None
        self._review = None
        self._last_index = -1
        self._last_time = -1
        self._seen = set()
        self._streak = {}
        self._forward_hinted = set()
        self._centered_streak = {}
        self._sign_streak = {}
        self._visual_confirmation = None
        self._motion_advice_enabled = True

    def process_event(self, event):
        try:
            result = self._process_event(event)
            return json.loads(json.dumps(result, allow_nan=False))
        except (KeyError, TypeError, ValueError):
            self._halt("invalid_input")
            raise

    def _halt(self, reason):
        if not self.halted:
            self.halt_reason = reason
        self.halted = True
        self._visual_confirmation = None

    def halt(self, reason="operator_stop"):
        """Latch a safety stop from an enclosing session; never acknowledge motion."""
        self._halt(reason)

    def _source_reviewed(self, evidence):
        return (self._review is not None and self._review["status"] == "door_and_plate"
                and self._review["track_id"] == self.target_track_id and len(evidence) == 1
                and self._review["sign_track_id"] == evidence[0]["sign_track_id"])

    def _decision(self, stamp, action, reason, *, evidence=None, state="OBSERVE"):
        result = {"frame_id": stamp.frame_id, "frame": stamp.to_dict(),
                "timestamp_s": stamp.timestamp_s, "pts": stamp.pts, "time_base": stamp.time_base,
                "track_id": self.target_track_id, "selection_source": self.selection_source,
                "action": action, "state": state, "reason": reason,
                "halt_reason": self.halt_reason,
                "evidence_mode": ("real_detector" if self.header["evidence_origin"] == "recorded_video" else "synthetic")
                                 + ("_human_reviewed" if self._review else "_manual_track" if self.selection_source == "manual" else "_automatic"),
                "motion_authorized": False, "requires_execution_feedback": False,
                "action_id": None, "plate_evidence": evidence or [],
                "visual_review": deepcopy(self._review)}
        if self.advisory:
            result.update(advice_only=True,
                          target_evidence_stage=("human_confirmed_visual_target" if self._visual_confirmation else
                                                 "temporary_tracked_observation_target" if self.target_track_id else
                                                 "unverified_visual_candidate"),
                          visual_target_confirmation=deepcopy(self._visual_confirmation),
                          physical_landmark_id=None, room_number_confirmed=False)
            prefix = "live_detector" if self.header["evidence_origin"] == "live_rgb" else (
                "real_detector" if self.header["evidence_origin"] == "recorded_video" else "synthetic")
            result["evidence_mode"] = prefix + (
                "_human_visual_confirmed" if self._visual_confirmation else "_human_reviewed" if self._review
                else "_manual_track" if self.selection_source == "manual" else "_automatic")
        return result

    def _process_event(self, event):
        if not isinstance(event, dict):
            raise ValueError("Expected preview event object")
        kind = event.get("type")
        if kind == "confirm_visual_target" and self.advisory:
            if (set(event) != {"type", "frame_id", "track_id", "confirmed_by"}
                    or not isinstance(event["confirmed_by"], str) or not event["confirmed_by"].strip()
                    or self.halted or self.stamp is None or event["frame_id"] != self.stamp.frame_id
                    or event["track_id"] != self.target_track_id
                    or not any(c.track_id == event["track_id"] and _eligible(c) for c in self.visible)):
                raise ValueError("Visual confirmation requires the current selected eligible door")
            self._visual_confirmation = deepcopy(event)
            return {"visual_target_confirmation": deepcopy(event), "motion_authorized": False,
                    "physical_landmark_id": None, "room_number_confirmed": False}
        if kind == "select_target":
            if set(event) != {"type", "frame_id", "track_id"}:
                raise ValueError("Invalid manual selection")
            if self.halted:
                raise ValueError("Preview is halted; start a new session before selection")
            if self.stamp is None or event["frame_id"] != self.stamp.frame_id:
                raise ValueError("Manual selection requires current frame")
            target = next((c for c in self.visible if c.track_id == event["track_id"]
                           and c.category == "door"), None)
            if target is None:
                raise ValueError("Manual selection requires currently visible detector door")
            if self.advisory and self.target_track_id != target.track_id:
                self._visual_confirmation = None
            self.target_track_id, self.selection_source = target.track_id, "manual"
            # Manual selection cannot clear an explicit stop/loss latch. A new
            # session is required; this also prevents repeated budget resets.
            return {"selected_track_id": target.track_id, "selection_source": "manual",
                    "manual_target_selection": True, "frame_id": self.stamp.frame_id,
                    "frame": self.stamp.to_dict(), "pts": self.stamp.pts,
                    "time_base": self.stamp.time_base, "timestamp_s": self.stamp.timestamp_s,
                    "track_id": target.track_id, "reason": "operator_selected_current_detector_track",
                    "motion_authorized": False, "halted": self.halted}
        if kind == "stop":
            if set(event) != {"type"} or self.stamp is None:
                raise ValueError("STOP requires an existing preview frame")
            self._halt("operator_stop")
            return {"advice": self._decision(self.stamp, "STOP_REOBSERVE", "operator_stop")}
        optional = {"now_s", "visual_review"} | ({"motion_advice_enabled"} if self.advisory else set())
        if kind != "frame" or set(event) - optional != FRAME_FIELDS:
            raise ValueError("Only detector frames, manual selection and stop are supported; no execution feedback")
        if event["mode"] != "detector" or event["manual_links"] or event["review_frame_id"] is not None:
            raise ValueError("Preview cannot consume human GT boxes/links")
        stamp = FrameStamp.from_dict(event["frame"])
        self._motion_advice_enabled = event.get("motion_advice_enabled", True)
        if type(self._motion_advice_enabled) is not bool:
            raise ValueError("motion_advice_enabled must be boolean")
        self._review = None  # A current-frame source review never propagates.
        now = event.get("now_s", stamp.timestamp_s)
        if type(now) not in (int, float) or not isfinite(now):
            raise ValueError("Invalid preview clock")
        bad_time = (stamp.frame_id in self._seen or stamp.frame_index <= self._last_index
                    or stamp.timestamp_s <= self._last_time)
        if bad_time or now < stamp.timestamp_s or now - stamp.timestamp_s > 2:
            time_reason = "duplicate_or_out_of_order_frame" if bad_time else "stale_or_future_frame"
            self._halt(time_reason)
            return {"tracks": [], "advice": self._decision(
                stamp, "STOP_REOBSERVE", time_reason),
                "diagnostics": {"input_valid": False, "halt_reason": self.halt_reason,
                                "target_visible": False, "door_candidates": [],
                                "eligible_track_ids": [], "contender_track_ids": [],
                                "unique_selection_streak": {}, "plate_status": "invalid_frame",
                                "plate_links": [], "current_source_reviewed": False, "ocr": [],
                                "action_blockers": {
                                    action: list(dict.fromkeys([self.halt_reason, time_reason])) for action in
                                    ("TURN_LEFT", "TURN_RIGHT", "FORWARD_SHORT", "STOP_AND_READ")}}}
        detections = [Detection(**d) for d in event["detections"]]
        error = event["perception_error"]
        if error is not None and (not isinstance(error, str) or not error or detections):
            raise ValueError("Invalid perception failure")
        regions = {}
        for region in event["ocr"]:
            if set(region) != {"sign_index", "readings", "error"}:
                raise ValueError("Invalid OCR region")
            i = region["sign_index"]
            if (type(i) is not int or not 0 <= i < len(detections)
                    or detections[i].category != "sign" or i in regions):
                raise ValueError("OCR requires unique current sign index")
            if region["error"] is not None and (
                    not isinstance(region["error"], str) or not region["error"] or region["readings"]):
                raise ValueError("Invalid OCR failure")
            for reading in region["readings"]:
                OCRReading(**reading)
            regions[i] = region
        if set(regions) != {i for i, d in enumerate(detections) if d.category == "sign"}:
            raise ValueError("Every sign requires current OCR status")
        gap = self._last_time >= 0 and stamp.timestamp_s - self._last_time > 1.5
        self._last_index, self._last_time = stamp.frame_index, stamp.timestamp_s
        self._seen.add(stamp.frame_id)
        self.stamp = stamp
        previous_tracks = {c.track_id: c for c in self.visible}
        self.visible = self.tracker.update(stamp.frame_id, stamp.timestamp_s, detections)
        eligible = [c for c in self.visible if _eligible(c)]
        # A low-score but plausible visible door remains an unresolved
        # competitor. Its confidence dropping is not evidence it disappeared.
        contenders = [c for c in self.visible if not _quality_failures(c, require_score=False)]
        unique = eligible if len(eligible) == len(contenders) == 1 else []
        self._streak = {c.track_id: self._streak.get(c.track_id, 0) + 1 for c in unique}
        self._review = self._validate_review(event.get("visual_review"), stamp)
        if self.advisory:
            centered, signs = {}, {}
            for c in eligible:
                change = _geometry_change(previous_tracks.get(c.track_id), c)
                small_change = (change is not None and change["center_displacement"] <= .04
                                and .85 <= change["width_ratio"] <= 1.15
                                and .85 <= change["height_ratio"] <= 1.15)
                if camera_relative_advice(c)[0] == "FORWARD_SHORT":
                    centered[c.track_id] = self._centered_streak.get(c.track_id, 0) + 1 if small_change else 1
                _, links = _plate_evidence(c, self.visible, regions, self.header["image_size"])
                if len(links) == 1 and links[0]["quality"] and not links[0]["ambiguous"] and links[0]["context"] == "unverified":
                    key = (c.track_id, links[0]["sign_track_id"])
                    signs[key] = self._sign_streak.get(key, 0) + 1
            self._centered_streak, self._sign_streak = centered, signs
        result = self._advise(stamp, eligible, contenders, regions, error, gap, previous_tracks)
        return {"tracks": [asdict(c) for c in self.visible], "advice": result,
                "diagnostics": self._diagnostics(regions, previous_tracks, eligible, contenders, result)}

    def _validate_review(self, review, stamp):
        if review is None:
            return None
        if (not isinstance(review, dict) or set(review) != {
                "frame_id", "track_id", "sign_track_id", "status", "reviewed_by", "source"}
                or review["frame_id"] != stamp.frame_id or review["source"] != "human_current_frame"
                or review["status"] not in ("door_and_plate", "not_door", "uncertain")
                or not isinstance(review["reviewed_by"], str) or not review["reviewed_by"].strip()):
            raise ValueError("Invalid current-frame human source review")
        target = next((c for c in self.visible if c.track_id == review["track_id"] and c.category == "door"), None)
        sign = next((c for c in self.visible if c.track_id == review["sign_track_id"] and c.category == "sign"), None)
        if target is None or (sign is None and (review["status"] == "door_and_plate" or review["sign_track_id"] is not None)):
            raise ValueError("Source review must reference current detector door/sign tracks")
        return deepcopy(review)

    def _advise(self, stamp, eligible, contenders, regions, error, gap, previous_tracks):
        def stop(reason, *, evidence=None, state="OBSERVE"):
            return self._decision(stamp, "STOP_REOBSERVE", reason, evidence=evidence, state=state)

        if self.halted:
            return stop("preview_halted_start_new_session")
        if error is not None or gap:
            self._halt("perception_error" if error else "capture_gap_or_fast_turn")
            return stop("perception_error" if error else "capture_gap_or_fast_turn")
        if self._review:
            if self._review["status"] == "not_door":
                self._halt("review_rejected_non_door")
                return stop("review_rejected_non_door")
            if self._review["status"] == "uncertain":
                return stop("review_source_uncertain")
        if self.target_track_id is None:
            if len(contenders) > 1:
                return stop("ambiguous_visible_doors")
            proposal = propose_inspection(stamp.frame_id, stamp.timestamp_s, eligible, now_s=stamp.timestamp_s)
            if proposal.intent != "INSPECT_DOOR":
                return stop(proposal.reason)
            if self._streak.get(proposal.track_id, 0) < 2:
                return stop("candidate_needs_second_capture")
            self.target_track_id, self.selection_source = proposal.track_id, "automatic"
        target = next((c for c in self.visible if c.track_id == self.target_track_id), None)
        if target is None:
            self._halt("selected_track_lost_no_automatic_replacement")
            return stop("selected_track_lost_no_automatic_replacement")
        previous = previous_tracks.get(target.track_id)
        change = _geometry_change(previous, target)
        if change is not None and change["unstable"]:
            self._halt("unstable_target_or_fast_camera_turn")
            return stop("unstable_target_or_fast_camera_turn")
        if not _eligible(target):
            return stop("selected_door_low_confidence_small_or_edge")
        if self.selection_source == "automatic" and (len(eligible) != 1 or len(contenders) != 1):
            return stop("ambiguous_visible_doors")
        plate, evidence = _plate_evidence(target, self.visible, regions, self.header["image_size"])
        if plate in (("ambiguous_door_sign_or_ocr", "non_room_sign_context") if self.advisory else
                     ("ambiguous_door_sign_or_ocr", "non_room_sign_context", "associated_plate_ocr_failed")):
            return stop(plate, evidence=evidence)
        if self.advisory:
            if not self._motion_advice_enabled:
                return stop("awaiting_human_feedback")
            action, reason = camera_relative_advice(target)
            if action in ("TURN_LEFT", "TURN_RIGHT"):
                return self._decision(stamp, action, reason, evidence=evidence, state="ALIGNING")
            gates = self._advisory_gates(target, evidence, regions, contenders)
            if not gates["STOP_AND_READ"]:
                return self._decision(stamp, "STOP_AND_READ", "Candidate sign ROI permits an OCR attempt; source and room remain unverified",
                                      evidence=evidence, state="READ_OPPORTUNITY")
            if gates["FORWARD_SHORT"]:
                return stop(gates["FORWARD_SHORT"][0], evidence=evidence, state="ALIGN")
            self._forward_hinted.add(target.track_id)
            return self._decision(stamp, "FORWARD_SHORT", "Stable centered observation target; one human-judged hint, no path or distance inference",
                                  evidence=evidence, state="ALIGN")
        action, reason = camera_relative_advice(replace(target, readable=plate == "readable"))
        if action in ("FORWARD_SHORT", "STOP_AND_READ") and plate in ("readable", "needs_observation"):
            if not self._source_reviewed(evidence):
                return stop("door_plate_source_unverified", evidence=evidence, state="ALIGN")
        if action == "FORWARD_SHORT":
            box = target.bbox_xyxy_norm
            if plate != "needs_observation":
                return stop(plate, evidence=evidence, state="ALIGN")
            if box[0] <= .01 or box[2] >= .99:
                return stop("centered_but_door_clipped", evidence=evidence, state="ALIGN")
            if target.track_id in self._forward_hinted:
                return stop("forward_preview_budget_exhausted_no_execution_evidence", evidence=evidence, state="ALIGN")
            self._forward_hinted.add(target.track_id)
            reason = "Aligned; one human-judged short-step hint only; no distance or path clearance inferred"
        return self._decision(stamp, action, reason, evidence=evidence,
                              state="ALIGN" if action == "FORWARD_SHORT" else
                              "READ_OPPORTUNITY" if action == "STOP_AND_READ" else "ALIGNING")

    def _advisory_gates(self, target, links, regions, contenders, *, emitted_forward=False):
        common = []
        if self._centered_streak.get(target.track_id, 0) < 3:
            common.append("needs_three_stable_centered_captures")
        if len(contenders) != 1:
            common.append("competing_visible_doors")
        if not self._motion_advice_enabled:
            common.append("awaiting_human_feedback")
        forward, read = list(common), list(common)
        cue = (len(links) == 1 and links[0]["quality"] and not links[0]["ambiguous"]
               and links[0]["context"] == "unverified"
               and self._sign_streak.get((target.track_id, links[0]["sign_track_id"]), 0) >= 2)
        ready = False
        if cue:
            sign = self.visible[links[0]["sign_index"]]
            x1, y1, x2, y2 = sign.bbox_xyxy_norm
            width, height = self.header["image_size"]
            ready = (x2-x1)*width >= 32 and (y2-y1)*height >= 16
            if regions[links[0]["sign_index"]]["error"] is not None:
                forward.append("associated_plate_ocr_failed")
        else:
            read.append("needs_stable_candidate_sign_roi")
        if not ready:
            read.append("candidate_sign_roi_too_small_or_absent")
        else:
            forward.append("candidate_sign_roi_ready_stop_instead")
        if not cue and self._visual_confirmation is None and not self._source_reviewed(links):
            forward.append("needs_visual_support_beyond_door_score")
        if target.bbox_xyxy_norm[0] <= .01 or target.bbox_xyxy_norm[2] >= .99:
            forward.append("centered_but_door_clipped")
        if target.track_id in self._forward_hinted and not emitted_forward:
            forward.append("forward_preview_budget_exhausted_no_execution_evidence")
        return {"FORWARD_SHORT": forward, "STOP_AND_READ": read}

    def _diagnostics(self, regions, previous_tracks, eligible, contenders, advice):
        """Explain every gate, including gates masked by an earlier STOP.

        Uses the same quality/geometry/plate helpers as the decision path;
        these are image measurements, never estimated travel or execution.
        """
        doors = []
        for index, c in enumerate(self.visible):
            if c.category != "door":
                continue
            x1, y1, x2, y2 = c.bbox_xyxy_norm
            candidate_plate, candidate_links = _plate_evidence(c, self.visible, regions, self.header["image_size"])
            doors.append({"detection_index": index, "track_id": c.track_id,
                          "bbox_xyxy_norm": c.bbox_xyxy_norm, "score": c.score,
                          "center_x": c.center_x, "center_y": (y1+y2)/2,
                          "width": x2-x1, "height": y2-y1,
                          "quality_blockers": _quality_failures(c),
                          "plate_status": candidate_plate, "plate_links": candidate_links,
                          "change": _geometry_change(previous_tracks.get(c.track_id), c)})
        target = next((c for c in self.visible if c.track_id == self.target_track_id), None)
        plate, links = (_plate_evidence(target, self.visible, regions, self.header["image_size"])
                        if target else ("no_visible_selected_door", []))
        common = []
        if self.halted:
            common.append(self.halt_reason)
        if target is None:
            common.append("no_selected_target" if self.target_track_id is None else "selected_track_not_visible")
            if self.target_track_id is None:
                common.append(advice["reason"])
        else:
            common.extend(_quality_failures(target))
            change = _geometry_change(previous_tracks.get(target.track_id), target)
            if change is not None and change["unstable"]:
                common.append("unstable_target_or_fast_camera_turn")
        if self.selection_source == "automatic" and len(contenders) != 1:
            common.append("ambiguous_visible_doors")
        if plate in (("ambiguous_door_sign_or_ocr", "non_room_sign_context") if self.advisory else
                     ("ambiguous_door_sign_or_ocr", "non_room_sign_context", "associated_plate_ocr_failed")):
            common.append(plate)
        if advice["reason"] in ("review_source_uncertain", "review_rejected_non_door"):
            common.append(advice["reason"])
        blockers = {action: list(dict.fromkeys(common)) for action in
                    ("TURN_LEFT", "TURN_RIGHT", "FORWARD_SHORT", "STOP_AND_READ")}
        if target:
            geometric_action = camera_relative_advice(target)[0]
            for action in ("TURN_LEFT", "TURN_RIGHT"):
                if action != geometric_action:
                    blockers[action].append("target_not_left" if action == "TURN_LEFT" else "target_not_right")
            for action in ("FORWARD_SHORT", "STOP_AND_READ"):
                if geometric_action in ("TURN_LEFT", "TURN_RIGHT"):
                    blockers[action].append("target_not_centered")
                if not self.advisory and not self._source_reviewed(links):
                    blockers[action].append("door_plate_source_unverified")
            if target.bbox_xyxy_norm[0] <= .01 or target.bbox_xyxy_norm[2] >= .99:
                blockers["FORWARD_SHORT"].append("centered_but_door_clipped")
            if target.track_id in self._forward_hinted and advice["action"] != "FORWARD_SHORT":
                blockers["FORWARD_SHORT"].append("forward_preview_budget_exhausted_no_execution_evidence")
        if self.advisory and target:
            gates = self._advisory_gates(target, links, regions, contenders, emitted_forward=advice["action"] == "FORWARD_SHORT")
            for action in gates:
                blockers[action] = list(dict.fromkeys(common + gates[action] + (
                    ["target_not_centered"] if camera_relative_advice(target)[0] in ("TURN_LEFT", "TURN_RIGHT") else [])))
        elif plate != "needs_observation":
            blockers["FORWARD_SHORT"].append("already_readable_stop_instead" if plate == "readable" else plate)
        if not self.advisory and plate != "readable":
            blockers["STOP_AND_READ"].append("plate_not_readable" if plate == "needs_observation" else plate)
        return {"input_valid": True, "door_candidates": doors, "target_visible": target is not None,
                "eligible_track_ids": [c.track_id for c in eligible],
                "contender_track_ids": [c.track_id for c in contenders],
                "unique_selection_streak": dict(self._streak),
                "plate_status": plate, "plate_links": links,
                "current_source_reviewed": bool(self._source_reviewed(links)),
                "ocr": [deepcopy(regions[i]) for i in sorted(regions)],
                "halt_reason": self.halt_reason, "action_blockers": blockers}


def replay_approach_log(records):
    iterator = iter(records)
    session = DoorApproachPreview(next(iterator))
    count = 0
    for row in iterator:
        if row.get("type") == "end":
            if row != {"type": "end", "event_count": count} or count == 0 or next(iterator, None) is not None:
                raise ValueError("Invalid preview completion marker")
            return
        if set(row) != {"input", "output"} or session.process_event(row["input"]) != row["output"]:
            raise ValueError("Preview replay output mismatch")
        count += 1
        yield row
    raise ValueError("Incomplete preview log")
