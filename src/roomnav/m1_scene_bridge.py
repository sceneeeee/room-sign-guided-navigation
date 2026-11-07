"""Bridge strict M1 OCR observations to the perspective-corrected 1F scene."""
from __future__ import annotations
from .floor_scene import FloorScene

class M1SceneBridge:
    def __init__(self, scene: FloorScene | None = None):
        self.scene = scene or FloorScene()

    def enrich(self, observation: dict) -> dict:
        """Attach static-scene candidates without changing OCR evidence."""
        text = str(observation.get("text", "")).strip()
        ctx = self.scene.observation_context(text)
        out = dict(observation)
        out["scene_match"] = {
            "status": "matched" if ctx.get("room_id") else "unknown_room",
            "room_id": ctx.get("room_id"),
            "room_status": ctx.get("room_status"),
            "candidate_portals": ctx.get("portals", []),
            "candidate_routes": ctx.get("candidate_routes", []),
            "source_raster": ctx.get("source_raster"),
            "route_execution": ctx.get("route_execution"),
        }
        return out
