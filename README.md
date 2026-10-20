# Room & Sign-Guided Indoor Navigation

[中文说明](README.zh-CN.md) · **Project status (2026-10-21): M0 field-capture workflow merged into `main` via PR #1; M2.1–M2.4 implemented on `feat/m2-reasoning`; M1/M3/M4 pending**

An **RGB-first, human-in-the-loop** research testbed for reading room numbers and signage and deciding where to look next when the target room's location is unknown.

## Research question and scope

Can an agent use **observed** room labels, directional signs and lightweight corridor annotations to search more efficiently than checking doors one by one?

A person carries a phone/USB RGB camera and follows high-level observation suggestions. The intended workflow is camera → OCR → physical door association → landmark memory → rule/text-LLM suggestion → human observation. **This full workflow has not yet been integrated or field-validated.** A mobile robot, depth, LiDAR, SLAM and Nav2 are outside the current scope.

## Development snapshot — 2026-10-21

**Branch boundaries matter:**

- **`main` (this branch):** includes the M0 browser/Gradio/RapidOCR pipeline with timed capture, local frame/OCR logs, manual review and CSV/keyframe export **merged via PR #1**. New samples are saved as lossless PNG; the original 2026-10-18 field frames remain JPEG. The branch does **not** include M2.4 streaming or the full M1/M3 navigation pipeline.
- **[PR #1 — M0 field capture and results](https://github.com/sceneeeee/room-sign-guided-navigation/pull/1):** **merged into `main` on 2026-10-20** (merge commit [`c247107`](https://github.com/sceneeeee/room-sign-guided-navigation/commit/c2471077cba6be5b393ffd4fb905c9e46d26f672)). Its review corrections fixed the LSS101 exact-match denominator, switched future frame storage to PNG, and pinned/tested Gradio 6.30.0 for the capture UI.
- **[`feat/m2-reasoning`](https://github.com/sceneeeee/room-sign-guided-navigation/tree/feat/m2-reasoning):** includes M2.1–M2.4 (M2.4 implementation commit `65c7e26`), still **separate from `main`**. The commands and M2 evaluation reports below refer to that branch. It has **not yet incorporated the newly merged M0 capture/review changes**.

**Verified progress and limitations:**

- **M0 — limited field evidence:** On 2026-10-18, a phone/Camo → MacBook browser → SSH → Linux RapidOCR pipeline sampled **6 frames in one stationary LSS101 trial**, at approximately 2 m, frontal view and 1.0 m camera height. The operator confirmed all 6 frames were of the physical `LSS101` sign. Full-label exact match was **5/6 sampled frames (83.3%)**; one frame produced no text. Recorded server OCR processing was **0.54–1.09 s per frame**, excluding end-to-end transport/display time. This is one door under one condition, not a general accuracy result. See PR #1 and its reviewed field report. **The six samples are correlated frames from one Record, not six independent trials.** The historical trial used JPEG, while future recordings use PNG.
- **M1 — pending:** full room-label extraction, explicit same-door cross-frame association and deduplication, trustworthy `frame_id` / `observation_id` / `landmark_id` output. Initial experiments may use manual association; equal OCR text does not prove the same physical door.
- **M2 — implemented standalone on the feature branch:** M2.1 evidence-aware `LandmarkMemory`; M2.2 Rule baseline; M2.3a constrained text-LLM decisions and safety fallback; M2.3b local Ollama/Qwen3-4B; M2.3c frozen 20-scenario evaluator; M2.3d selectable Prompt v2/v3 (**v2 remains the default**, v3 strategy performance is mixed); M2.4 JSONL/stdin streaming events, incremental Rule decisions and the repeated-frame eligibility fix. **194 unit tests passed locally in WSL**. The FB541 → FB543 → FB545 replay reaches `REOBSERVE` → `VERIFY_TARGET` → `FOUND` only after explicit operator confirmation; it is **synthetic**, not a live corridor run.
- **Offline evaluation (historical Prompt v2):** on 20 manually authored *single-step* cases, the original Rule achieved **19/20** legal and acceptable actions; Qwen achieved **10/20** acceptable, **20/20** legal, **0** fallbacks and **17/20** abstentions. The fixed M2.4 Rule reaches **20/20** on the **same already-seen** suite; this is a bug-fix regression, **not** an independent generalization result. The 5 Prompt v3 development requests also do not establish better field navigation.
- **M3/M4 — pending:** no real multi-door human-in-the-loop search or controlled comparisons of search success, inspected doors, time or distance. M2.4 is not yet connected to live M1 perception. A VLM is not required for the present text-OCR + Rule/text-LLM baseline and has not been deployed.

## Architecture — planned end-to-end loop

~~~text
Phone/Camo or webcam → MacBook browser → SSH → remote RapidOCR
                                              [M0; capture workflow merged via PR #1]
             ↓ raw OCR and actual sampled frames
Full label extraction + same-door tracking/association
                                              [M1; not implemented]
             ↓ explicit Observation JSONL
                (segment_id, frame_id, observation_id, landmark_id)
LandmarkMemory → Rule / optional text Qwen planner
                                              [M2.1–M2.4; standalone feature branch]
             ↓ next observation suggestion, not a physical route
Human observes another door → repeat         [M3; not integrated]
             ↓
Controlled policy/field comparison            [M4; pending]
~~~

The live demo **samples browser webcam frames** (nominally every 1.5 s), not a continuous 30-FPS feed. Each Record has about 5 s for camera settling and about 12 s of OCR/local saving before automatic stop. New samples are stored as lossless PNG with raw OCR and an independent manual review record; legacy 2026-10-18 JPEG samples are unchanged. The recorded Gradio UI version is pinned to **6.30.0**. OCR is **unverified evidence**, not physical door identity or navigation confirmation. `segment_id` and `side` can be operator annotated; no automatic mapping or robot motion is claimed.

## Quick start

**M0 app on this branch** (Python 3.10+, remote inference computer):

~~~bash
python3 -m venv .venv
source .venv/bin/activate
pip install -e '.[vision,ui]'
python apps/live_demo.py
~~~

On the field MacBook, keep an SSH tunnel open:

~~~bash
ssh -N -L 7860:127.0.0.1:7860 USER@GPU_HOST
~~~

Open `http://127.0.0.1:7860` in the MacBook browser and allow camera access. The camera is accessed by the **browser**; SSH forwards the web connection. Fill in the condition before Record, then review the saved frames and OCR after the automatic stop. Displayed latency is server OCR execution time, not end-to-end latency. See [setup notes](docs/setup.zh-CN.md).

For **M2.4 standalone replay**, switch to its branch first; these commands are **not available on `main`**:

~~~bash
git switch feat/m2-reasoning
pip install -e .
python -m roomnav.stream_cli --target FB545 --input examples/m24_stream_demo.jsonl
python -m unittest discover -s tests -q
~~~

The streaming CLI defaults to **Rule-only** and makes no automatic Ollama requests. Human confirmation must be a separate operator event. Still-image OCR is available as a diagnostic tool through `scripts/ocr_image.py` and `apps/photo_demo.py`.

## Milestones and owners

| Stage | Work | Status |
| --- | --- | --- |
| **M0 — Capture/OCR** | Remote OCR, timed field capture, local PNG frames/OCR, human review and export | **Implemented on `main` via merged PR #1**; one stationary LSS101 condition field-tested (5/6 frame-level exact matches), wider validation pending |
| **M1 — Perception** | Full room-ID extraction, stable IDs for repeated physical signs, cross-door separation | **Next for Member B**; not complete |
| **M2 — Memory/strategy** | Rule/text-LLM, frozen evaluation, replayable JSONL observation stream | **M2.1–M2.4 complete on feature branch**; field input still missing |
| **M3 — Human search loop** | Connect M1 to M2.4; person executes suggested observation actions | Not integrated |
| **M4 — Comparison** | Sequential door checking vs Rule vs text-LLM, with success/cost/failure metrics | Not started |

- **Member A — reasoning/evaluation:** M2 ownership, Rule and text-Qwen comparisons, M1/M2 adapter integration, and M3/M4 experimental design.
- **Member B — perception/field:** M0 collection and reproducible field records, followed by M1 room-label extraction, physical landmark association, live observation IDs and real-corridor tests.
- **Joint:** agree on the Observation interface, preserve raw vs confirmed evidence, review failure cases, and run a multi-door field demonstration.

**Immediate next step:** Member B starts M1 on a fresh branch based on the latest `main`: extract complete room labels, produce genuine `frame_id` and unique `observation_id`, associate repeated views of the **same physical door** with stable `landmark_id`, and output replayable JSONL conforming to the [M2.4 Observation contract](https://github.com/sceneeeee/room-sign-guided-navigation/blob/feat/m2-reasoning/docs/m24-streaming.zh-CN.md). Validate on **at least three distinct doors** and include a missed/misread example. Member A then integrates M1 with the standalone M2.4 stream. Neither M1 nor M3 is complete yet.

## References and documentation

- [ReasonNav — Human-like Navigation in a World Built for Humans](https://reasonnav.github.io/) (CoRL 2025)
- [ReasonNav source code](https://github.com/ReasonNav/ReasonNav)
- [Experimental protocol (中文)](docs/experiment.zh-CN.md)
- [Roadmap and team responsibilities (中文)](docs/roadmap.zh-CN.md)
- [Perception/reasoning interface on `main` (中文)](docs/interfaces.zh-CN.md)
- [M0 field-capture implementation — merged PR #1](https://github.com/sceneeeee/room-sign-guided-navigation/pull/1)
- [Reviewed LSS101 field record (中文)](docs/field-tests/2026-10-18-1f.md)
- [1F field-test SOP (中文)](docs/field-test-sop-1f.zh-CN.md)
- [M2.3c frozen offline evaluation, feature branch (中文)](https://github.com/sceneeeee/room-sign-guided-navigation/blob/feat/m2-reasoning/docs/m2-offline-evaluation.zh-CN.md)
- [M2.3d Prompt v3 report, feature branch (中文)](https://github.com/sceneeeee/room-sign-guided-navigation/blob/feat/m2-reasoning/docs/m23d-prompt-v3.zh-CN.md)
- [M2.4 streaming/Rule fix, feature branch (中文)](https://github.com/sceneeeee/room-sign-guided-navigation/blob/feat/m2-reasoning/docs/m24-streaming.zh-CN.md)

**Research integrity:** Synthetic single-step scores and narrow field-OCR tests must not be represented as real-world room-search success, model superiority or a new algorithm.

## Data and license

Avoid committing identifiable raw camera footage, credentials or personal information. No open-source license has been selected; add one before public reuse or distribution.
