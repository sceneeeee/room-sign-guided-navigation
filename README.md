# Room & Sign-Guided Indoor Navigation

[中文说明](README.zh-CN.md) · **Status: initial research scaffold — no validated results yet**

An **RGB-first, human-in-the-loop** testbed for reading room numbers and signage and reasoning about where to search for a room whose location is unknown.

## Current research question

Can an agent use **previously observed** room labels, directional signage and lightweight topological notes to search for a target room more efficiently than simply checking doors one by one?

The initial prototype is **not a mobile-robot deployment**. A person carries a phone/USB RGB camera; a MacBook presents the experiment and may connect to a remote GPU computer via SSH. The system recognizes text, records landmarks and suggests the next place to observe. The person executes the decision.

## Architecture

```text
RGB photo / video / browser camera sample
       |
Perception: room label + sign detection, OCR / VLM     [planned]
       |
RoomObservation JSONL -> LandmarkMemory               [minimal scaffold]
       |
Reasoning policy: numbering-rule baseline             [implemented, conservative]
                  VLM planner                          [planned]
       |
Human action: observe / continue / backtrack / verify [planned UI]
```

No 3-D localization is claimed. `segment_id` and `side` may be manually annotated in the human-in-the-loop phase. The rule baseline states its assumptions and abstains on insufficient evidence.

## Quick start

Requires Python 3.10+.

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -e .
roomnav-baseline --target EB104 --observations examples/observations_eb.jsonl
python -m unittest discover -s tests -v
```

Optional **single-photo OCR** and browser interface:

```bash
pip install -e '.[vision,ui]'
python scripts/ocr_image.py /path/to/door.jpg
python apps/photo_demo.py
```

Run the Gradio app on the remote GPU host and access it from a MacBook through `ssh -L 7860:127.0.0.1:7860 USER@HOST`, then open `http://localhost:7860` locally. This app currently performs raw OCR only; door detection and high-level VLM inference are not implemented yet.

## Planned phases

1. **M0 · Photos:** collect real doorplate images, establish OCR/VLM exact-match baselines.
2. **M1 · Reasoning:** store ordered observations, implement simple rules and VLM planner; compare against sequential-door search.
3. **M2 · Recorded video:** sample frames, detect candidate doors, recognize and deduplicate repeated observations.
4. **M3 · Field loop:** MacBook + camera + remote inference, human executes recommended observations.
5. **M4 · Evaluation:** search success, number of inspected doors, distance/time, failure analysis.

Robot integration (Frogbot/SLAM/Nav2) and depth sensing are **out of scope for the current phase**.

## Links

- [ReasonNav — Human-like Navigation in a World Built for Humans](https://reasonnav.github.io/) (CoRL 2025)
- [ReasonNav source code](https://github.com/ReasonNav/ReasonNav)
- [Experiment protocol (中文)](docs/experiment.zh-CN.md)
- [Roadmap and two-person responsibilities (中文)](docs/roadmap.zh-CN.md)
- [Perception / reasoning interface (中文)](docs/interfaces.zh-CN.md)

**Research integrity:** adapting existing open-source tools is not in itself a novel algorithm. Robustness to OCR errors, irregular numbering and exploration cost is a candidate research question, not an established contribution.

## Data and license

Do not commit identifiable raw camera footage or secrets. No open-source license has been selected; add one before public reuse or distribution.