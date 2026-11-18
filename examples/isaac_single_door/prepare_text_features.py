"""Cache the fixed YOLOE prompts on a machine holding MobileCLIP weights."""

import argparse
import hashlib
import json
from pathlib import Path

from roomnav.rgb_perception import YOLOWorldDetector


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--encoder", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    import clip
    import torch

    prompts = list(YOLOWorldDetector.PROMPTS)
    encoder = torch.jit.load(str(args.encoder), map_location="cpu").eval()
    with torch.inference_mode():
        features = encoder(clip.tokenize(prompts)).float()
    value = {"schema": "roomnav-mobileclip-text-features-1", "prompts": prompts,
             "encoder_sha256": hashlib.sha256(args.encoder.read_bytes()).hexdigest(),
             "torch_version": torch.__version__, "features": features.tolist()}
    with args.output.open("x") as stream:
        json.dump(value, stream, allow_nan=False)
        stream.write("\n")


if __name__ == "__main__":
    main()
