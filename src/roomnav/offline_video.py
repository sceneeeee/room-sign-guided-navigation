"""Local FFmpeg RGB decoding with original presentation timestamps.

No FPS-derived timestamps, image rotation, rescaling or frame renumbering.
Missing, negative or non-increasing PTS fail explicitly at this boundary.
"""
from dataclasses import asdict, dataclass
from fractions import Fraction
import json
from pathlib import Path
import subprocess
import tempfile


@dataclass(frozen=True)
class FrameStamp:
    frame_index: int
    pts: int
    time_base: str

    def __post_init__(self):
        if type(self.frame_index) is not int or self.frame_index < 0:
            raise ValueError("frame_index must be a nonnegative integer")
        if type(self.pts) is not int or self.pts < 0:
            raise ValueError("Original nonnegative integer PTS required")
        if not isinstance(self.time_base, str) or Fraction(self.time_base) <= 0:
            raise ValueError("Positive time_base required")

    @property
    def frame_id(self):
        return f"video_frame_{self.frame_index:08d}"

    @property
    def timestamp_s(self):
        return float(self.pts * Fraction(self.time_base))

    def to_dict(self):
        return {**asdict(self), "frame_id": self.frame_id, "timestamp_s": self.timestamp_s}

    @classmethod
    def from_dict(cls, data):
        stamp = cls(data["frame_index"], data["pts"], data["time_base"])
        if data != stamp.to_dict():
            raise ValueError("Frame identity/time must agree with original PTS")
        return stamp


@dataclass(frozen=True)
class VideoFrame:
    stamp: FrameStamp
    rgb: object


def probe_video(path: Path) -> dict:
    path = Path(path)
    if not path.is_file():
        raise FileNotFoundError(f"RGB video unavailable: {path}")
    result = subprocess.run([
        "ffprobe", "-v", "error", "-select_streams", "v:0", "-show_streams",
        "-show_format", "-show_frames", "-show_entries",
        "stream=index,codec_name,width,height,r_frame_rate,avg_frame_rate,time_base,start_time,duration,nb_frames:format=duration:frame=pts,pkt_pts",
        "-of", "json", str(path.resolve()),
    ], check=True, capture_output=True, text=True)
    data = json.loads(result.stdout)
    # ffprobe can include nested MOV tags despite -show_entries. Keep only
    # timing/codec fields, excluding device/location tags and private paths.
    raw_stream = data["streams"][0]
    fields = ("index", "codec_name", "width", "height", "r_frame_rate", "avg_frame_rate",
              "time_base", "start_time", "duration", "nb_frames")
    stream = {key: raw_stream[key] for key in fields if key in raw_stream}
    # FFmpeg 4 names the original frame presentation timestamp pkt_pts;
    # newer releases expose pts. Neither is a best-effort/FPS estimate.
    stamps = [FrameStamp(i, f.get("pts", f.get("pkt_pts")), stream["time_base"])
              for i, f in enumerate(data["frames"])]
    if not stamps:
        raise ValueError("No timestamped video frames")
    if any(b.timestamp_s <= a.timestamp_s for a, b in zip(stamps, stamps[1:])):
        raise ValueError("Video requires strictly increasing original PTS")
    duration = data.get("format", {}).get("duration")
    return {"stream": stream, "format": {"duration": duration},
            "stamps": stamps}


def iter_video(path: Path, *, metadata=None, stride: int = 1):
    """Decode in presentation order; skipped frames keep their original IDs."""
    import numpy as np

    if type(stride) is not int or stride < 1:
        raise ValueError("stride must be a positive integer")
    metadata = probe_video(path) if metadata is None else metadata
    stream = metadata["stream"]
    width, height = stream["width"], stream["height"]
    size = width * height * 3
    with tempfile.TemporaryFile() as errors:
        process = subprocess.Popen([
            "ffmpeg", "-v", "error", "-nostdin", "-noautorotate", "-i", str(Path(path).resolve()),
            "-map", "0:v:0", "-vsync", "0", "-f", "rawvideo", "-pix_fmt", "rgb24", "pipe:1",
        ], stdout=subprocess.PIPE, stderr=errors)
        try:
            for stamp in metadata["stamps"]:
                payload = process.stdout.read(size)
                if len(payload) != size:
                    raise ValueError("Decoded frame count differs from ffprobe; truncated RGB frame")
                if stamp.frame_index % stride == 0:
                    yield VideoFrame(stamp, np.frombuffer(payload, dtype=np.uint8).reshape(height, width, 3))
            if process.stdout.read(1):
                raise ValueError("Decoder produced extra frames beyond ffprobe")
            if process.wait() != 0:
                errors.seek(0)
                raise RuntimeError(errors.read().decode(errors="replace"))
        finally:
            process.stdout.close()
            if process.poll() is None:
                process.terminate()
            process.wait()
