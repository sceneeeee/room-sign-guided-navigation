# Room & Sign-Guided Indoor Navigation

[中文说明](README.zh-CN.md) · **Project status (2026-10-21): M0 field-capture workflow merged into `main` via PR #1; M2.1–M2.4 implemented on `feat/m2-reasoning`; M1/M3/M4 pending**

An **RGB-first, human-in-the-loop** research testbed for reading room numbers and signage and deciding where to look next when the target room's location is unknown.

## Research question and scope

Can an agent use **observed** room labels, directional signs and lightweight corridor annotations to search more efficiently than checking doors one by one?

A person carries a phone/USB RGB camera and follows high-level observation suggestions. The intended workflow is camera → OCR → physical door association → landmark memory → rule/text-LLM suggestion → human observation. **This full workflow has not yet been integrated or field-validated.** Robot hardware, depth, LiDAR, SLAM and Nav2 are outside this **current handheld implementation**, but robot motion integration is planned in later stages.

## Updated direction (2026-10-23) — RGB-driven, landmark-guided closed-loop room search

**Planning update, not a claim of implementation.** The research target is an unfamiliar indoor floor, a requested room identifier (e.g., FB545), and a **single RGB camera as the search/perception input**. The system should autonomously identify candidate doors and signage, guide an observer toward a selected door, read its plate, update an evidence-aware landmark memory, and choose where to look next. The search policy must not rely on pre-annotated room locations, RGB-D, depth input, or SLAM pose/map information.

**Two execution stages:** (1) a person holds the RGB camera and follows bounded visual movement/observation prompts, handling collision avoidance and safety; (2) the same high-level RGB perception/reasoning stack is adapted to a robot, with LiDAR SLAM, odometry, Nav2, and obstacle avoidance **only as motion-execution infrastructure**. Human compliance and robot integration are **not yet demonstrated**; actuator substitution is a design objective requiring its own validation. Camera-relative image guidance is **not** a metric map pose or proof of a collision-free route.

### Target architecture (future, not current code)

~~~text
RGB video (phone now; robot camera later)
  -> visible door/sign candidates + door tracking + image-quality checks
  -> RapidOCR room IDs and directional-sign evidence
  -> door/sign association + distinct-frame evidence -> LandmarkMemory
  -> high-level search: Rule / text Qwen / optional event-triggered VLM
     (inspect a visible door, observe a sign/branch, reobserve, verify, abstain)
  -> visual guidance skill: SEARCH -> SELECT -> ALIGN -> APPROACH -> READ -> UPDATE
  -> execution and safety adapter: human (now) / robot motion stack (later)
  -> fresh RGB + action feedback -> repeat until independently verified goal
~~~

**Design separation:** the detector proposes visible doors (including unread doors); the OCR supplies *raw text evidence*; memory tracks distinct physical entities and conflicts; the planner selects a visible/observed target or requests more viewing; the local guidance skill issues short, revisable, camera-relative actions. A generic VLM is **not** assumed to replace the detector, tracker, OCR, or local safety controller. Plain pretrained COCO YOLO weights do not provide a standard door class; evaluate an open-vocabulary detector or door-trained weights and fine-tune only if needed. Initial VLM use is for keyframes/events (e.g., a new door, junction, ambiguous sign), not for every camera frame.

**Interface change required:** existing M2.4 Observation events are limited to nonempty room/sign text and do not accept bounding boxes or unlabelled-door tracks. Add a separate visual-candidate/track event stream containing frame provenance, normalized box, short-lived track ID and optional associated physical landmark ID; emit the existing strict Observation event **only after actual OCR evidence and justified association**. Do not convert a track ID into a permanent landmark ID without evidence. Retain independent operator confirmation for current FOUND semantics; a future automated goal claim requires an independently evaluated protocol.

### Revised milestones and acceptance criteria

| Stage | Planned extension | Acceptance / evidence |
| --- | --- | --- |
| **M0** | Retain timed OCR/review; add timestamped continuous RGB sessions, end-to-end latency and action/execution logging | Replayable sequence of captured frames, displayed prompts and human responses; no future-frame leakage |
| **M1** | Complete original room-ID extraction and trustworthy observation IDs; then door/sign detection, short-term tracking, OCR-to-physical-door association | At least 3 distinct real doors incl. a miss/misread; unread doors can still be candidates; explicit uncertain associations |
| **M2** | Preserve existing Rule, text Qwen, memory, tests and results; add visible-target selection, constrained VLM option, structured decisions and fallback | Each decision references actual observed evidence/candidate IDs; unsupported goals/directions rejected; old regression suites preserved |
| **M3** | Integrate M0/M1/M2 with a bounded human visual-guidance state machine, stale-frame rejection, lost-target recovery and manual safety override | Person follows system-generated prompts to select, align with, approach and read multiple doors without choosing semantic targets independently |
| **M4** | Controlled field experiments plus separate perception/strategy evaluations: sequential, Rule, text LLM, VLM text-only, VLM image+text | Report verified target-finding success, wrong claims, door visits, duplicates, time, interventions, tracking/association errors and latency; distance/SPL only if measured |
| **M5 (later)** | Robust unknown corridors, junctions, signs, numbering anomalies, revisits and failure recovery | Held-out floor/corridor trials without advance semantic-map labels or operator-selected search directions |
| **M6–M7 (later)** | Robot execution adapter, single-door then multi-door/branch closed loop; integrate LiDAR SLAM + Nav2 for safe motion | RGB still selects/reads semantic targets; robot executes safely; evaluate domain shift and intervention rate separately |

**Order of implementation:** finish the original **M1 -> M2.4 real-observation replay** first; demonstrate a single door **detect -> track -> visually align -> approach in short safe steps -> read -> update**; then connect multi-door planning and run VLM comparisons. M0 and M2 histories are preserved rather than renamed as finished visual-navigation work.

### Closest prior work and limits of direct reuse

- [Follow the Signs: Using Textual Cues and LLMs to Guide Efficient Robot Navigation (2026, arXiv)](https://arxiv.org/abs/2601.06652): **very close goal** (sign/room-number patterns, semantic exploration, target rooms). Grid-world study plus a Spot demonstration, **but Spot used pre-annotated occupancy/semantic grids** rather than live RGB sign/room detection. Useful high-level reasoning baseline, not an RGB end-to-end implementation.
- [Mobile Robot Navigation in Indoor Environments Using Object and Character Recognition (ICRA 2000)](https://doi.org/10.1109/ROBOT.2000.844076): direct earlier example of a robot navigating to a **specified room number** by visually recognizing doors/characters; the core task itself is not new.
- [Indoor Mobile Robot Navigation Using Doorplate Landmarks (2006)](https://doi.org/10.1504/IJVAS.2006.012204) and [Room Searching Robot Based on Door Detection and Room Number Recognition (2021)](https://doi.org/10.1007/978-981-33-6926-9_4): traditional camera/doorplate-based navigation and room search; not modern pretrained RGB/VLM reasoning across unfamiliar layouts.
- [Visual Semantic Navigation Based on Deep Learning for Indoor Mobile Robots (2018)](https://doi.org/10.1155/2018/1627185): robot experiments with only RGB sensor input and correction while moving, but models are trained to recognize specific rooms/corridors in the experimental setting, not unknown room-number search.
- [Efficient Exploration for Room Finding Using Wall-Following Based Path Planning (2009)](https://doi.org/10.5302/J.ICROS.2009.15.12.1232): highlights why paths that stay far from walls can miss small room numbers, and why revisiting missed doors matters; uses conventional exploration/SLAM.
- [ReasonNav (CoRL 2025)](https://reasonnav.github.io/): closest modern landmark/room/sign reasoning inspiration, but its full robot uses spatial landmarks, mapping and navigation.
- [NaVid (RSS 2024)](https://arxiv.org/abs/2402.15852), [NaVILA (RSS 2025)](https://arxiv.org/abs/2412.04453), [Uni-NaVid (RSS 2025)](https://arxiv.org/abs/2412.06224): RGB video navigation, action interfaces and temporal history, with navigation-specific training/compute requirements; **not** direct solutions to unknown room-number search.
- [SignScene (2026, arXiv)](https://arxiv.org/abs/2602.12686): interpreting directional signs and grounding them in possible paths, with spatial representation beyond raw RGB.
- [Automatic Sign Reading and Localization for Semantic Mapping with an Office Robot (2022)](https://arxiv.org/abs/2209.11432): useful detection/OCR reference, but explicitly RGB-D plus SLAM semantic mapping rather than RGB-first search.

**Literature boundary:** prior art clearly covers numbered-room navigation, signs and RGB-only control. The surveyed work does not supply a single verified ready-to-reproduce solution matching **all** of: live RGB door/sign discovery, unknown-building room-number reasoning, closed-loop door approach, no pre-annotated semantic map for search, and a replaceable human/robot execution adapter. This is a scoped literature finding, **not a claim of novelty**; eventual research contribution depends on measurable improvements against fair baselines.

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

## Original text-only baseline loop (historical; superseded by the updated target architecture above)

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

## Original baseline milestones and owners (development status, not revised scope)

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
