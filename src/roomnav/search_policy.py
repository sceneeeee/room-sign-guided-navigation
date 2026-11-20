"""M2 policies recommend what to observe. Only M3 can suggest alignment/approach."""
import base64
from hashlib import sha256
from dataclasses import asdict
from io import BytesIO
import json
import os

from .baseline import decide_next, parse_room_number
from .llm_planner import LLMPlanner
from .openai_compatible import OpenAICompatibleModel

POLICIES = ("sequential", "rule", "qwen", "vlm-text", "vlm-rgb")


def configured_backend(name, budget):
    if name in ("sequential", "rule") or budget == 0:
        return None
    if name.startswith("vlm-"):
        return OpenAICompatibleModel(base_url=os.environ.get("ROOMNAV_VLM_BASE_URL", ""),
                                     model=os.environ.get("ROOMNAV_VLM_MODEL", ""),
                                     api_key=os.environ.get("ROOMNAV_VLM_API_KEY"))
    return OpenAICompatibleModel.from_env()


class FrameModel:
    """An immutable request context, including only evidence available this step."""
    def __init__(self, backend, candidates, rgb):
        self.transport = backend
        self.backend, self.model = backend.backend, backend.model
        self.visible = {c.track_id for c in candidates if c.category == "door" and c.detector_confidence >= .3}
        self.suggested_track = None
        self.context = [asdict(c) for c in candidates]
        self.jpeg = None
        if rgb is not None:
            from PIL import Image
            image = Image.fromarray(rgb)
            image.thumbnail((640, 640))
            buffer = BytesIO()
            image.save(buffer, "JPEG", quality=80)
            self.jpeg = "data:image/jpeg;base64," + base64.b64encode(buffer.getvalue()).decode("ascii")

    def generate(self, *, system_prompt, user_prompt, timeout_s):
        evidence = json.loads(user_prompt)
        evidence["current_visual_candidates"] = self.context
        system_prompt = system_prompt.replace("exactly these fields", "these required fields")
        args = dict(system_prompt=system_prompt + "\nOne optional JSON field next_track_id may name a currently visible door to inspect. Omit it when uncertain.",
                    timeout_s=timeout_s, user_prompt=json.dumps(evidence, allow_nan=False))
        if self.jpeg:
            raw = self.transport.generate_with_image(**args, jpeg_data_url=self.jpeg)
        else:
            raw = self.transport.generate(**args)
        from .stream import parse_event_line
        try:
            value = parse_event_line(raw)
        except ValueError:
            return raw  # Retain the existing planner's validation/fallback behavior.
        if "next_track_id" in value:
            choice = value.pop("next_track_id")
            if not isinstance(choice, str) or choice not in self.visible:
                return raw  # Extra/invalid field will fail strict M2 validation.
            self.suggested_track = choice
            return json.dumps(value)
        return raw


class SearchPolicy:
    def __init__(self, name="rule", *, budget=8, backend=None, timeout_s=3.0):
        if name not in POLICIES or type(budget) is not int or not 0 <= budget <= 100:
            raise ValueError("Unknown policy or invalid model call budget")
        self.name, self.budget, self.backend = name, budget, backend
        if type(timeout_s) not in (int, float) or not 0 < timeout_s <= 60:
            raise ValueError("Choose a model deadline above zero and at most 60 seconds")
        self.timeout_s = timeout_s
        self.calls = 0
        self.sequential_cursor = {}
        self.replaying = False
        self.last_key = None
        self.last_model = None

    @staticmethod
    def visible_doors(candidates):
        return sorted((c for c in candidates if c.category == "door" and c.detector_confidence >= .3),
                      key=lambda c: c.bbox_xyxy_norm[0])

    @staticmethod
    def layout(doors):
        # A screen-space sweep cursor, with no assertion of physical identity.
        return tuple(round((c.bbox_xyxy_norm[0]+c.bbox_xyxy_norm[2])/2, 1) for c in doors)

    def selected(self, candidates, track):
        if self.name != "sequential":
            return
        doors = self.visible_doors(candidates)
        for i, door in enumerate(doors):
            if door.track_id == track and len(doors) > 1:
                self.sequential_cursor[self.layout(doors)] = (i+1) % len(doors)

    def plan(self, stream, candidates, inspected, *, rgb=None, recorded=None):
        rule = decide_next(stream.target, stream.memory, segment_id=stream.active_segment)
        doors = self.visible_doors(candidates)
        by_track = {c.track_id: c for c in doors}
        available = [c.track_id for c in doors if c.track_id not in inspected]
        labels = {c.door_track_id: c.room_reading().raw_text for c in candidates
                  if c.association_status in ("associated", "stable_hypothesis") and c.room_reading()}
        key = None
        model = None
        intent = rule.to_dict()
        if self.name in POLICIES[2:]:
            if not self.replaying:
                visual_key = [(c.track_id, c.category, c.detector_confidence, c.bbox_xyxy_norm,
                               c.association_status, c.landmark_id, c.ocr, c.ocr_error) for c in candidates]
                image_key = sha256(rgb.tobytes()).digest() if self.name == "vlm-rgb" and rgb is not None else None
                key = (len(stream.memory.observations), tuple(stream.memory.confirmations.values()),
                       tuple(available), visual_key, image_key)
            if self.replaying:
                if not isinstance(recorded, dict):
                    raise ValueError("Missing recorded model response at planning step")
                call = recorded.get("call", self.calls)
                if type(call) is not int or not self.calls <= call <= min(self.calls+1, self.budget):
                    raise ValueError("Invalid recorded model call sequence")
                self.calls = call
                model = recorded
            elif self.last_key == key:
                model = self.last_model
            elif self.calls >= self.budget:
                model = {"status": "budget_exhausted"}
            elif self.name == "vlm-rgb" and rgb is None:
                model = {"status": "image_unavailable"}
            else:
                self.calls += 1
                backend = self.backend or configured_backend(self.name, self.budget)
                adapter = FrameModel(backend, candidates, rgb if self.name == "vlm-rgb" else None)
                result = LLMPlanner(adapter, timeout_s=self.timeout_s).plan(stream.target, stream.memory,
                                                               segment_id=stream.active_segment)
                model = {"status": "fallback" if result.fallback_used else "ok",
                         "model": result.model, "error": result.error_kind,
                         "suggested_track": adapter.suggested_track if not result.fallback_used else None,
                         "call": self.calls, "decision": result.decision.to_dict()}
            self.last_key, self.last_model = key, model
            if model.get("decision") and rule.action not in ("VERIFY_TARGET", "FOUND"):
                intent = model["decision"]
        if self.name == "sequential" and rule.action not in ("VERIFY_TARGET", "FOUND"):
            cursor = self.sequential_cursor.get(self.layout(doors), 0)
            available = [d.track_id for d in doors[cursor:]+doors[:cursor] if d.track_id not in inspected]
            intent = {"action": "OBSERVE_NEXT", "reason": "Inspect visible doors from left to right."}
        elif self.name != "sequential":
            target = parse_room_number(stream.target)
            priorities = [t for t, label in labels.items() if t in by_track and parse_room_number(label) == target]
            if intent["action"] in ("REOBSERVE", "VERIFY_TARGET"):
                priorities += [c.door_track_id for c in candidates
                               if c.landmark_id in intent.get("evidence_landmark_ids", ())]
            if intent["action"] == "CHECK_MISSING_OR_OTHER_SIDE":
                available.reverse()
            if model and model.get("suggested_track") in available:
                priorities.insert(0, model["suggested_track"])
            available = list(dict.fromkeys(priorities + available))
        direction = [r.raw_text for c in candidates if c.context == "directional_sign" for r in c.ocr]
        return {"decision": intent, "suggested_track": next(iter(available), None),
                "unvisited_visible_tracks": [t for t in by_track if t not in inspected],
                "direction_sign_readings": direction, "model": model}
