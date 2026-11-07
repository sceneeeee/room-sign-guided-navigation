"""Static 1F scene lookup for M1 room/sign observations.

The scene is a semantic prior. Geometry comes from the perspective-corrected
raster named by the scene JSON; no raw photo is used for route lookup.
"""
from __future__ import annotations
import json
from pathlib import Path
from typing import Any

DEFAULT_SCENE = Path(__file__).resolve().parents[2] / "data/maps/1f/floor_1f_experiment_scene.json"

class FloorScene:
    def __init__(self, scene_path: str | Path = DEFAULT_SCENE):
        self.scene_path = Path(scene_path)
        self.data: dict[str, Any] = json.loads(self.scene_path.read_text(encoding="utf-8"))
        src = self.data.get("source", {})
        if not src.get("rectified_raster", "").endswith("floor_1f_base_modified.tif"):
            raise ValueError("scene must use the perspective-corrected floor_1f_base_modified.tif")
        self._rooms = {r["label"].upper(): r for r in self.data.get("rooms", [])}

    @property
    def source_raster(self) -> Path:
        return self.scene_path.parent / self.data["source"]["rectified_raster"]

    def room(self, label: str, eligible_only: bool = False) -> dict[str, Any] | None:
        room = self._rooms.get(label.strip().upper())
        if eligible_only and room and room.get("target_status") != "eligible":
            return None
        return room

    def portals_for(self, label: str) -> list[dict[str, Any]]:
        room = self.room(label, eligible_only=True)
        if not room:
            return []
        return [p for p in self.data.get("portals", []) if p.get("space_a") == room["id"]]

    def candidate_routes_to(self, label: str) -> list[dict[str, Any]]:
        if self.room(label, eligible_only=True) is None:
            return []
        target = f"room_{label.strip().upper()}"
        return [r for r in self.data.get("candidate_routes", []) if r.get("goal") == target]

    def observation_context(self, label: str) -> dict[str, Any]:
        room = self.room(label)
        if not room:
            return {"target": label, "status": "unknown_room", "routes": []}
        return {
            "target": room["label"],
            "room_id": room["id"],
            "room_status": room["status"],
            "portals": self.portals_for(label),
            "candidate_routes": self.candidate_routes_to(label),
            "route_execution": self.data["observation_contract"]["route_execution"],
            "source_raster": str(self.source_raster),
        }
