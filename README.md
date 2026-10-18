# Room & Sign-Guided Indoor Navigation

[中文说明](README.zh-CN.md) · **Status: main is an M0/RapidOCR prototype; M2.1–M2.3c is on an unmerged development branch**

An **RGB-first, human-in-the-loop** testbed for reading room numbers and signage and reasoning about where to search for a room whose location is unknown.

## Current research question

Can an agent use **previously observed** room labels, directional signage and lightweight topological notes to search for a target room more efficiently than simply checking doors one by one?

The initial prototype is **not a mobile-robot deployment**. A person carries a phone/USB RGB camera; a MacBook presents the experiment and may connect to a remote GPU computer via SSH. The system recognizes text, records landmarks and suggests the next place to observe. The person executes the decision.

## Development snapshot — 2026-10-14

**Branch boundary:** The default `main` branch contains the M0 webcam/RapidOCR starter and early reasoning scaffolding. M2.1–M2.3c are implemented on [`feat/m2-reasoning`](https://github.com/sceneeeee/room-sign-guided-navigation/tree/feat/m2-reasoning) and **have not been merged into `main`**. The M2 commands and experiments below refer to the feature branch, not to executable features on `main`.

- **Implemented on the feature branch:** landmark/evidence memory (M2.1), evidence-aware rule baseline (M2.2), constrained text-LLM planning and fallback (M2.3a), local Ollama/Qwen3-4B integration (M2.3b), and a 20-scenario frozen single-step policy evaluator (M2.3c). The 154 unit tests passed in five consecutive local WSL runs.
- **Recorded offline comparison (20 synthetic scenarios, Qwen3-4B-Instruct-2507 Q4_K_M, Prompt v2):** Rule-based policy had **19/20 (95%)** legal and acceptable actions, with one repeated-frame eligibility defect. Qwen had **10/20 (50%)** acceptable actions, **20/20 (100%)** legal outputs, **0** fallbacks and **17/20 (85%)** abstentions; mean model latency was about **1.89 s**.
- **Limitations:** These are manually authored **single-step simulation** outcomes, not live corridor search success, traveled distance, or evidence that a model is superior. Field-camera integration, M1 door/sign perception and cross-frame association, the full M3/M4 loop, and VLM deployment remain incomplete. **Prompt v3 has not been developed.**

See the [feature-branch M2.3c evaluation protocol](https://github.com/sceneeeee/room-sign-guided-navigation/blob/feat/m2-reasoning/docs/m2-offline-evaluation.zh-CN.md) for methodology and metrics. Future work: investigate over-abstention without relaxing evidence checks, then integrate verified perception outputs into memory and field testing.

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

The live demo **samples browser webcam frames** (nominally every 1.5 s) for remote OCR; it is not a 30-FPS video pipeline. Fill in the observation condition before Record; the first 5 s are for settling, followed by about 12 s of OCR and local saving before the page stops the recording. Each processed frame and raw OCR result is saved locally on the Linux server for human review. It returns **raw text**, not verified room labels or navigation decisions. No 3-D localization is claimed; `segment_id` / `side` may be operator annotated.

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
