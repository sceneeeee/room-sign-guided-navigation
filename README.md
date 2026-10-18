# Room & Sign-Guided Indoor Navigation

[中文说明](README.zh-CN.md) · **Status (2026-10-18): M0 limited field test in open PR #1; M2.1–M2.4 implemented here; M1/M3/M4 pending**

An **RGB-first, human-in-the-loop** testbed for reading room numbers and signage and reasoning about where to search for a room whose location is unknown.

## Current research question

Can an agent use **previously observed** room labels, directional signage and lightweight topological notes to search for a target room more efficiently than simply checking doors one by one?

The initial prototype is **not a mobile-robot deployment**. A person carries a phone/USB RGB camera; a MacBook presents the experiment and may connect to a remote GPU computer via SSH. The system recognizes text, records landmarks and suggests the next place to observe. The person executes the decision.

## Development snapshot — 2026-10-18

**Branch boundary:** `main` contains the original M0 webcam/RapidOCR starter, while **this `feat/m2-reasoning` branch** contains M2.1–M2.4, **not yet merged into `main`**. Separately, [PR #1](https://github.com/sceneeeee/room-sign-guided-navigation/pull/1) adds M0 recording, review/export tools and limited field results; it is **still open** and its code is **not present here**. The M2 commands and reports below run on this development branch.

- **Implemented on this branch:** landmark/evidence memory (M2.1), evidence-aware Rule baseline (M2.2), constrained text-LLM planning/fallback (M2.3a), local Ollama/Qwen3-4B (M2.3b), the 20-case frozen single-step evaluator (M2.3c), versioned Prompt v2/v3 (M2.3d) and JSONL/stdin observation streaming with Rule decisions (M2.4). **194 unit tests passed locally in WSL** after M2.4.
- **Historical offline comparison (20 synthetic single-step scenarios, Qwen3-4B-Instruct-2507 Q4_K_M, Prompt v2):** The **original** Rule had **19/20 (95%)** legal and acceptable actions, with one repeated-frame eligibility defect; Qwen had **10/20 (50%)** acceptable actions, **20/20** legal outputs, **0** fallbacks and **17/20 (85%)** abstentions; mean model latency was about **1.89 s**. The **fixed M2.4 Rule** scores **20/20 on the same previously exposed suite**: a bug-fix regression, **not** an independent generalization result.
- **M0 one-condition field test (separate open PR #1):** A phone/Camo → MacBook → SSH → Linux RapidOCR test captured **6 frames in one stationary LSS101 trial** at about **2 m**, frontal view and **1.0 m camera height**. Raw OCR contained full `LSS101` in **5/6** frames; one produced no text. Server OCR time was **0.54–1.09 s/frame**, excluding network/display latency. Only **2/6** frames had per-frame `room_truth` filled in; **2/2** on that restricted subset **must not be presented as overall 100% accuracy**. PR #1 code and results have not been merged into this branch.
- **M2.3d development check:** Both LLM CLIs now accept `--prompt-version v2|v3`; v2 remains the default and evidence gates are unchanged. Five new Qwen development requests found one improved conflict response, continued trend failure and one rejected cross-segment response with safe fallback. These are exposed development cases, not a blind evaluation; see the [Prompt v3 report](docs/m23d-prompt-v3.zh-CN.md).
- **M2.4 streaming:** JSONL/file or stdin events update the existing Landmark Memory and emit one Rule decision per event. Explicit operator events alone authorize FOUND. Rule version `m2.4-distinct-captures-v1` fixes repeated-frame target eligibility using the existing LLM gate; historical evaluation artifacts and both prompts remain preserved. See the [interface and corridor replay](docs/m24-streaming.zh-CN.md).
- **Limitations:** The M0 field test covers one door/condition, and M2's results use manually authored **single-step synthetic evidence**, not real corridor-search success, traveled distance or demonstrated model superiority. **M1 door/sign extraction and physical cross-frame association, live M1→M2 connection, M3/M4 field loop and controlled evaluation are pending.** Prompt v3 strategy goals remain only partly met; no VLM has been deployed or is required for the current Rule/text-LLM baseline.

See the [feature-branch M2.3c evaluation protocol](https://github.com/sceneeeee/room-sign-guided-navigation/blob/feat/m2-reasoning/docs/m2-offline-evaluation.zh-CN.md) for methodology and historical metrics. Next: connect explicit M1 associations to the stream interface and record field observations for replay and human review.

```bash
python -m roomnav.stream_cli --target FB545 --input examples/m24_stream_demo.jsonl
```

## Architecture — video-first

~~~text
Phone/Camo or USB webcam → browser Gradio frame sampling [M0; PR #1 field test open]
           ↓ SSH tunnel to remote GPU server
RapidOCR returns raw scene text                     [M0: one-condition LSS101 test in PR #1]
           ↓
Door / room-label detection + multi-frame confirmation [M1 planned]
           ↓
RoomObservation → LandmarkMemory                      [M2.1 standalone; video integration pending]
           ↓
Evidence-aware rules / text LLM → next observation   [M2 standalone; VLM planned]
           ↓
Human follows suggestion and captures more evidence    [M3 planned]
~~~

The current branch's live demo **samples browser webcam frames** (nominally every 1.5 s) for remote OCR; it is not a 30-FPS video pipeline. The team field-tested the phone/Camo/SSH OCR chain under one condition in **open PR #1**, but its enhanced timed-capture and review UI is **not merged here**. OCR returns **raw, unverified text**, not a physical door association or navigation decision. No 3-D localization is claimed; `segment_id` / `side` may be operator annotated.

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

The webcam demo does **not** yet implement door detection, room-number validation, tracking/deduplication, VLM reasoning or navigation. The independent M2.2 CLI handles OCR conflicts and separates facts, assumptions and uncertainties. See [setup instructions](docs/setup.zh-CN.md).

## Video-first milestones

1. **M0 — Live feed:** Gradio camera samples → SSH → remote OCR; one stationary LSS101 field trial (6 frames, 5 with full OCR text) recorded in **open PR #1**, not yet merged.
2. **M1 — Video recognition:** extract full alphanumeric labels, provide real `frame_id` and `observation_id`, explicitly associate repeat views of the **same physical door** by `landmark_id`, and keep different doors separate (manual association is acceptable initially). **Next for Member B**.
3. **M2 — Landmark memory + reasoning:** M2.1–M2.4 run standalone, including incremental Rule decisions from JSONL/stdin. A local Qwen3-4B text model was evaluated on 20 frozen corridor snapshots; opt-in Prompt v3 has mixed development results. M1 video adaptation, further strategy work, VLM and field search remain pending.
4. **M3 — Human-in-the-loop field search:** connect live M1 observations to M2.4; a person follows observation suggestions and confirms the target explicitly. Not yet tested.
5. **M4 — Controlled evaluation:** compare sequential-door search, Rule and text-LLM policies (success rate, doors checked, distance/time). Not yet started.

Photo and saved-video data are retained for reproducible **regression tests**, not separate preliminary milestones.

Robot integration (Frogbot/SLAM/Nav2) and depth sensing are **out of scope for the current phase**.

## Team responsibilities and live field debugging

- **Member A — reasoning and evaluation:** design `LandmarkMemory`, implement numbering-rule and VLM planning baselines, define next-observation actions, and evaluate search efficiency and failure cases.
- **Member B — perception and on-site testing:** prioritize live-camera acquisition and field tests (M0), followed by doorplate OCR, cross-frame deduplication, and photo/saved-video regression; **lead real-world corridor tests** with a USB RGB camera and MacBook connected over SSH to the remote GPU computer. During **live video-stream debugging**, use **Codex as an interactive coding assistant** to inspect errors/logs, edit capture and inference scripts, adjust processing parameters, rerun the pipeline, and immediately verify changes on the next observation.
- **Joint responsibilities:** agree on the observation JSON contract, connect perception outputs to the reasoning module, review field-test failures, and maintain reproducible experiment records.

**Field-test iteration:** camera capture → remote inference → inspect recognized labels, latency and failures → Codex-assisted diagnosis/changes → rerun and record versioned results. **One stationary M0 test** is documented in PR #1; repeat-condition, multi-door and M1→M2 field tests are not yet verified. Codex is a development/debugging tool, **not** the navigation agent; automated changes still require operator review.

## Links

- [ReasonNav — Human-like Navigation in a World Built for Humans](https://reasonnav.github.io/) (CoRL 2025)
- [ReasonNav source code](https://github.com/ReasonNav/ReasonNav)
- [Experiment protocol (中文)](docs/experiment.zh-CN.md)
- [Roadmap and two-person responsibilities (中文)](docs/roadmap.zh-CN.md)
- [Perception / reasoning interface (中文)](docs/interfaces.zh-CN.md)
- [M0 field-recording and LSS101 results — open PR #1](https://github.com/sceneeeee/room-sign-guided-navigation/pull/1)
- [M2.2 evidence-aware rule baseline (中文)](docs/m2-rule-reasoning.zh-CN.md)
- [M2.3b OpenAI-compatible text inference setup (中文)](docs/m2-llm-serving.zh-CN.md)
- [M2.3c frozen offline policy evaluation (中文)](docs/m2-offline-evaluation.zh-CN.md)
- [M2.3d Prompt v3 development report (中文)](docs/m23d-prompt-v3.zh-CN.md)
- [M2.4 streaming interface and Rule fix (中文)](docs/m24-streaming.zh-CN.md)

**Research integrity:** adapting existing open-source tools is not in itself a novel algorithm. Robustness to OCR errors, irregular numbering and exploration cost is a candidate research question, not an established contribution.

## Data and license

Do not commit identifiable raw camera footage or secrets. No open-source license has been selected; add one before public reuse or distribution.
