"""RGB ingress and append-only episode logs for live, video and simulation adapters."""
from hashlib import sha256
import json
from pathlib import Path
from time import monotonic

from .offline_perception import DetectorSource, perceive_frame
from .offline_video import VideoFrame
from .ocr import read_roi


def rgb_command(frame, detector, *, origin, received_s=None, capture=None, clock=monotonic, ocr=read_roi):
    """Video uses original PTS. A webcam without trusted metadata only has receipt time."""
    timing = {"server_received_s": received_s, "camera_capture": capture}
    event = perceive_frame(frame, DetectorSource(detector), ocr=ocr, timing=timing)
    h, w = frame.rgb.shape[:2]
    live = origin == "live_rgb"
    source = capture["stamp"] if live and capture else None if live else frame.stamp
    if capture:
        timing["camera_capture"] = {"clock_id": capture["clock_id"], "timestamp_s": source.timestamp_s}
    cmd = {"type": "frame", "perception": event, "image_size": [w, h],
           "source_frame": source.to_dict() if source else None,
           "capture_clock": capture["clock_id"] if capture else None,
           "rgb_sha256": sha256(frame.rgb.tobytes()).hexdigest(),
           "at_s": clock() if live else frame.stamp.timestamp_s}
    return cmd, timing


class EpisodeLog:
    def __init__(self, path):
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.file = self.path.open("x", encoding="utf-8")
        self.position = 0

    def flush(self, session):
        self.flush_records(session.records)

    def flush_records(self, records):
        self.append_records(records[self.position:])

    def append_records(self, records):
        for row in records:
            self.file.write(json.dumps(row, allow_nan=False, separators=(",", ":")) + "\n")
            self.position += 1
        self.file.flush()

    def close(self):
        self.file.close()
