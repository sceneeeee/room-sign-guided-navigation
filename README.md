# Room & Sign-Guided Indoor Navigation

[中文说明](README.zh-CN.md) · **Status: initial research scaffold — no validated results yet**

An **RGB-first, human-in-the-loop** testbed for reading room numbers and signage and reasoning about where to search for a room whose location is unknown.

## Current research question

Can an agent use **previously observed** room labels, directional signage and lightweight topological notes to search for a target room more efficiently than simply checking doors one by one?

The initial prototype is **not a mobile-robot deployment**. A person carries a phone/USB RGB camera; a MacBook presents the experiment and may connect to a remote GPU computer via SSH. The system recognizes text, records landmarks and suggests the next place to observe. The person executes the decision.

## Architecture — video-first

~~~text
MacBook + USB RGB webcam → browser Gradio camera sampling [M0, starter code]
           ↓ SSH tunnel to remote GPU server
RapidOCR returns raw scene text                      [M0, unvalidated on-site]
           ↓
Door / room-label detection + multi-frame confirmation [M1 planned]
           ↓
RoomObservation → LandmarkMemory                      [minimal scaffold; integration pending]
           ↓
Room-number rules / VLM planning → next observation   [rules isolated; VLM planned]
           ↓
Human follows suggestion and captures more evidence    [M3 planned]
~~~

The live demo **samples browser webcam frames** (default every 1.5 s) for remote OCR; it is not a 30-FPS video pipeline. It has not been tested on the team's physical camera/network, and returns **raw text**, not verified room labels or navigation decisions. No 3-D localization is claimed; `segment_id` / `side` may be operator annotated.

## Quick start — live webcam (M0)

Python 3.10+. On the **remote inference computer**:

~~~bash
python3 -m venv .venv
source .venv/bin/activate
pip install -e '.[vision,ui]'
python apps/live_demo.py
~~~

On the **field MacBook**, keep this SSH tunnel running:

~~~bash
ssh -N -L 7860:127.0.0.1:7860 USER@GPU_HOST
~~~

Open `http://127.0.0.1:7860` in the MacBook browser and grant webcam permission. The USB camera is accessed by the **MacBook browser**, not automatically passed as a USB device through SSH. Gradio sends sampled images to remote RapidOCR. The displayed duration measures **server OCR time only**, not network/queue latency. If SSH terminates in Windows while OCR runs inside WSL2, check that the Windows endpoint can reach the service.

Still photos remain **regression/debugging inputs**, not a prerequisite research stage:

~~~bash
python scripts/ocr_image.py /path/to/door.jpg
python apps/photo_demo.py
roomnav-baseline --target EB104 --observations examples/observations_eb.jsonl
python -m unittest discover -s tests -v
~~~

The webcam demo does **not** yet implement door detection, room-number validation, tracking/deduplication, VLM reasoning or navigation. See [setup instructions](docs/setup.zh-CN.md).

## Video-first milestones

1. **M0 — Live feed:** MacBook webcam → Gradio frame samples → SSH → remote OCR → displayed output. Starter implemented; real-world validation pending.
2. **M1 — Video recognition:** detect doorplates, read full alphanumeric labels, confirm across frames, reject ambiguities and deduplicate.
3. **M2 — Landmark memory + reasoning:** feed confirmed observations into memory; compare numbering rules vs VLM suggestions on the same evidence.
4. **M3 — Human-in-the-loop field search:** person follows model suggestions; Member B leads on-site testing and Codex-assisted debugging.
5. **M4 — Controlled evaluation:** compare sequential-door search, numbering heuristics and VLM decisions (success rate, doors checked, distance/time).

Photo and saved-video data are retained for reproducible **regression tests**, not separate preliminary milestones.

Robot integration (Frogbot/SLAM/Nav2) and depth sensing are **out of scope for the current phase**.

## Team responsibilities and live field debugging

- **Member A — reasoning and evaluation:** design `LandmarkMemory`, implement numbering-rule and VLM planning baselines, define next-observation actions, and evaluate search efficiency and failure cases.
- **Member B — perception and on-site testing:** prioritize live-camera acquisition and field tests (M0), followed by doorplate OCR, cross-frame deduplication, and photo/saved-video regression; **lead real-world corridor tests** with a USB RGB camera and MacBook connected over SSH to the remote GPU computer. During **live video-stream debugging**, use **Codex as an interactive coding assistant** to inspect errors/logs, edit capture and inference scripts, adjust processing parameters, rerun the pipeline, and immediately verify changes on the next observation.
- **Joint responsibilities:** agree on the observation JSON contract, connect perception outputs to the reasoning module, review field-test failures, and maintain reproducible experiment records.

**Field-test iteration (planned):** camera capture → remote inference → inspect recognized labels, latency and failures → Member B uses Codex to propose/implement a code adjustment → rerun and verify → record the code version and result. Codex assists development **during** testing; it is **not** the navigation agent and does not automatically control the camera or change the running system without the operator's review. A basic Gradio live-frame OCR prototype is implemented; physical-camera field tests and the Codex-assisted integration workflow are still unverified.

## Links

- [ReasonNav — Human-like Navigation in a World Built for Humans](https://reasonnav.github.io/) (CoRL 2025)
- [ReasonNav source code](https://github.com/ReasonNav/ReasonNav)
- [Experiment protocol (中文)](docs/experiment.zh-CN.md)
- [Roadmap and two-person responsibilities (中文)](docs/roadmap.zh-CN.md)
- [Perception / reasoning interface (中文)](docs/interfaces.zh-CN.md)

**Research integrity:** adapting existing open-source tools is not in itself a novel algorithm. Robustness to OCR errors, irregular numbering and exploration cost is a candidate research question, not an established contribution.

## Data and license

Do not commit identifiable raw camera footage or secrets. No open-source license has been selected; add one before public reuse or distribution.