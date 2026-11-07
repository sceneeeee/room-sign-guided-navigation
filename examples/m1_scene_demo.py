"""Replay one real-looking M1 observation against the rectified 1F scene."""
from roomnav.m1_scene_bridge import M1SceneBridge

observation = {
    "type": "observation",
    "kind": "room",
    "text": "LSS119",  # replace with actual OCR text from M1
    "segment_id": "corridor_lss",
    "side": "right",
    "frame_id": "demo:frame_0001",
    "landmark_id": None,
}

result = M1SceneBridge().enrich(observation)
print("目标房间:", result["scene_match"]["room_id"])
print("候选门:", [p["id"] for p in result["scene_match"]["candidate_portals"]])
for route in result["scene_match"]["candidate_routes"]:
    print("候选路线:", " -> ".join(route["via"]), "状态:", route["status"])
print("底图:", result["scene_match"]["source_raster"])
print("执行策略:", result["scene_match"]["route_execution"])
