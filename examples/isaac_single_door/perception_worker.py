"""Run the repository detector and OCR in a separate Python environment."""

import argparse
from contextlib import redirect_stdout
import hashlib
import json
from importlib.metadata import version
import os
from pathlib import Path
import sys


def main():
    os.environ["YOLO_AUTOINSTALL"] = "false"
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--weights", type=Path, required=True)
    parser.add_argument("--device", default="cpu")
    parser.add_argument("--text-features", type=Path)
    parser.add_argument("--ocr-border", type=int, default=0)
    args = parser.parse_args()
    if args.ocr_border < 0:
        parser.error("--ocr-border must be nonnegative")
    # Libraries may log to stdout. Reserve it for the JSON request/reply pipe.
    with redirect_stdout(sys.stderr):
        import numpy as np
        from PIL import Image
        from roomnav.offline_perception import DetectorSource, perceive_frame
        from roomnav.offline_video import FrameStamp, VideoFrame
        from roomnav.ocr import get_ocr_engine, read_roi
        from roomnav.rgb_perception import YOLOWorldDetector

        model = None
        cache = None
        if args.text_features:
            import torch
            from ultralytics import YOLOE

            cache = json.loads(args.text_features.read_text())
            if (cache.get("schema") != "roomnav-mobileclip-text-features-1"
                    or cache.get("prompts") != list(YOLOWorldDetector.PROMPTS)):
                raise ValueError("Cached text features must match the exact detector prompts")
            features = torch.tensor(cache["features"], dtype=torch.float32).unsqueeze(0)
            if features.shape != (1, 3, 512) or not torch.isfinite(features).all():
                raise ValueError("Invalid cached text features")

            class CachedYOLOE(YOLOE):
                def set_classes(self, classes, embeddings=None):
                    if classes != cache["prompts"]:
                        raise ValueError("Cached detector prompts cannot be changed")
                    with torch.inference_mode():
                        projected = self.model.model[-1].get_tpe(features)
                    return super().set_classes(classes, projected)

            model = CachedYOLOE(str(args.weights))
        detector = YOLOWorldDetector(weights=str(args.weights), device=args.device, model=model)
        source = DetectorSource(detector)
        get_ocr_engine()
    encoder = Path("mobileclip_blt.ts")
    print(json.dumps({"ready": True,
                      "mobileclip_sha256": cache["encoder_sha256"] if cache else (
                          hashlib.sha256(encoder.read_bytes()).hexdigest() if encoder.is_file() else None),
                      "cached_text_features_sha256": hashlib.sha256(args.text_features.read_bytes()).hexdigest() if cache else None,
                      "ocr_border_pixels": args.ocr_border,
                      "versions": {name: version(name) for name in
                                   ("torch", "torchvision", "ultralytics", "rapidocr", "onnxruntime", "numpy")},
                      "detector": {"prompts": detector.prompts, "categories": detector.categories,
                                   "confidence": detector.confidence, "image_size": detector.image_size,
                                   "device": detector.device}}), flush=True)
    def read_crop(crop):
        if args.ocr_border:
            crop = np.pad(crop, ((args.ocr_border, args.ocr_border),
                                 (args.ocr_border, args.ocr_border), (0, 0)),
                          constant_values=255)
        return read_roi(crop)

    for line in sys.stdin:
        request = json.loads(line)
        with redirect_stdout(sys.stderr):
            with Image.open(request["image"]) as image:
                rgb = np.array(image.convert("RGB"))
            stamp = FrameStamp(request["index"], request["index"], "1/2")
            result = perceive_frame(VideoFrame(stamp, rgb), source, ocr=read_crop)
        print(json.dumps(result, allow_nan=False), flush=True)


if __name__ == "__main__":
    main()
