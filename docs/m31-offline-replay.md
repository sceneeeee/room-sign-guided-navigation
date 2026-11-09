# M3.1 RGB offline processing and reproducible replay

This branch adds a local integration scaffold for Issue #5. It reuses
`YOLOWorldDetector` (including YOLOE loading), `ConservativeTracker`,
`RoomOCRSample` / `M1Session`, `StreamSession`, and `VisualGuidanceController`.
M0 and M2 are unchanged. PR #6's `read_review` and `detection_manifest` remain
the annotation validation/export boundary; there is no second annotation parser.

The feature branch starts at PR #6's merge,
`aaa1bb4926584f8bc19ef48452252283d786670c`. Local validation on 2026-11-09
decoded both supplied MP4s completely (2,499 frames each), ran a paired
21-frame detector/OCR comparison and 45 actual frames in three continuous
clips. The 1080p HEVC/HLG version yielded LSS101, LSS124 and LSS119 OCR;
all actual M3 guidance remained STOP_REOBSERVE. These are sampled offline
integration results. See the [validation summary](m31-validation-summary.md).

The original MOV and 20 original reviewed keyframe images remain unavailable.
There are zero verified review-to-video bindings. Matching the supplied MP4s
to each other does not validate their correspondence to those review images.

## Evidence boundaries

| Input | Use | What it establishes |
| --- | --- | --- |
| Synthetic events / generated test video | Integration and decoder tests | Software/state-machine contracts only |
| Human-verified boxes | Isolate downstream tracking / fresh ROI OCR / M1 / M2 / M3 | Oracle interface behavior; never automatic detection performance |
| Actual YOLO-World / YOLOE proposals | Process an original local recording when supplied | Detector candidates, not GT or verified physical identity |
| Independent operator confirmation | Separate explicit event | Only route to M2 `FOUND` |

The existing review contains 21 instances / 20 keyframes, 18 complete frames
and 2 incomplete frames. Human-box mode uses only the complete-frame export,
including its exclusion of unknown/unassociated room signs. It is a **partial
oracle input**, not exhaustive sign supervision. Empty/unbound annotations are
not silently counted as successful automatic detections. AI-generated boxes
are rejected in human-box mode. GT room text and the review's historical raw
OCR are never fed to M1 as fresh OCR or converted to operator confirmation.

Full-video integrity decoding is distinct from full-frame model inference.
No full-frame inference benchmark, GT accuracy, real-time latency or field
acceptance is claimed. M3.3 was not attempted. A prerecorded scene never reacts
to the guidance, even when the event log contains simulated motion feedback.

## Commands

From the repository root, use the existing environment (or install the package
and its existing vision/detection extras). Processing requires FFmpeg/ffprobe
and numpy; event replay itself uses the Python standard library. Model loading
is lazy. Actual-detector mode requires a local checkpoint. Existing model/OCR
dependencies may require their separately provisioned support assets.

First run the supplied **synthetic input fixture**, which requires no video,
model inference, weights or network. This fixture includes invented `SYN`
labels and a synthetic operator event. Choose a new output name on each run;
the CLI refuses to overwrite an existing file.

```bash
PYTHONPATH=src .venv/bin/python -m roomnav.offline_cli fixture \
  --input tests/fixtures/m31_synthetic_inputs.jsonl \
  --output outputs/m31/synthetic-events.jsonl
PYTHONPATH=src .venv/bin/python -m roomnav.offline_cli replay \
  outputs/m31/synthetic-events.jsonl
```

With the privately supplied local MP4, detector mode can run:

```bash
PYTHONPATH=src YOLO_AUTOINSTALL=False OMP_NUM_THREADS=4 \
YOLO_CONFIG_DIR=outputs/m31/ultralytics \
.venv/bin/python -m roomnav.offline_cli process \
  --mode detector --target LSS119 --weights /private/models/yoloe-11s-seg.pt \
  --video data/field_sessions/1f_raw/session_01/video/route_LSS101_LSS124_LSS119_raw.mp4 \
  --device cpu --confidence 0.1 --image-size 640 --stride 120 \
  --output outputs/m31/detector-events.jsonl
```

Use a local `yoloe-*.pt` checkpoint to select the existing YOLOE adapter. Optional
`--video`, `--device`, `--confidence`, `--image-size` and `--stride` arguments
are available. The header records video/checkpoint hashes, prompts, inference
settings and a runtime inventory: library/FFmpeg versions, package-default OCR
config hash, locally provisioned OCR/text-encoder model hashes, Torch threads
when loaded, and RoomNav source hashes. Inventory lists available assets before
inference; it does not assert every file was loaded. Missing assets/config are
explicit and must be provisioned separately for perception reproduction.
No model download is performed by metadata capture. Inference is not guaranteed
bitwise deterministic; event
replay verifies the recorded perception inputs without invoking models.
Use `--evidence-origin synthetic` when processing an artificially generated
video. Nested device/location metadata is excluded from the event header.

For human-box mode, first validate using PR #6's existing interface:

```bash
PYTHONPATH=src .venv/bin/python scripts/validate_m1_review.py \
  docs/field-tests/2026-11-07-m1-human-reviewed.jsonl \
  --image-root data/field_sessions/1f_raw/session_01/review/keyframes_issue2 \
  --manifest outputs/m31/review-manifest.jsonl
```

Then create a **private verified binding JSONL**. Each row has exactly these
fields: `frame_index` (zero-based decoded source-video index), `pts` (original
integer presentation timestamp), `time_base` (original rational stream time
base), and `review_frame_id` (the existing review keyframe ID). Determine these
bindings by inspecting the decoded original RGB and source timestamps. No real
bindings are supplied because the original review images and extraction
provenance remain missing, even though the MP4s are available.

**Do not map review IDs directly to video frame indices.** The review IDs come
from a prior 2 FPS extraction, and the manifest explicitly calls its timestamps
provisional. Do not derive source timestamps from `(frame_number-1)/2`, FPS,
or the older all-zero M1 observations. Every binding is checked against the
decoded source PTS and original image dimensions. Only explicitly bound frames
are processed; missing or stride-skipped bindings cause an incomplete run.

```bash
PYTHONPATH=src .venv/bin/python -m roomnav.offline_cli process \
  --mode human_verified_boxes --target LSS119 \
  --video /private/verified-bound-source.mp4 \
  --image-root data/field_sessions/1f_raw/session_01/review/keyframes_issue2 \
  --bindings data/m3/verified-review-video-bindings.jsonl \
  --output outputs/m31/human-box-events.jsonl
```

Human boxes carry a constant `score=1.0` to satisfy the existing detection
interface. The header identifies this as a placeholder, not detector confidence.
This mode re-runs ROI OCR on source RGB; unreadable crops, empty OCR, exceptions,
and conflicting readings remain failures/evidence in the log.

## Timing, association and safety

The decoder preserves zero-based source indices as `video_frame_00000000`,
integer original PTS and rational `time_base`. M1 receives `pts * time_base`
seconds, retaining a nonzero start time. Sampling keeps original indices. No
frames are auto-rotated, resized or repeated. Negative, missing or non-increasing
PTS are rejected instead of repaired. ffprobe captures duration, coded size,
codec, FPS metadata and per-frame PTS; FPS metadata never drives frame timing.
Unsupported/inconsistent decoding fails rather than attaching guessed times.
The M3 clock uses source PTS as an **offline virtual clock**; it measures no
processing, transport, UI or end-to-end latency.

Actual detector mode associates a sign with a door only when its center lies
inside exactly one door box. This is logged as an image-space hypothesis, and
does **not** produce a physical `landmark_id`. Adjacent/overlapping/ambiguous
doors remain unassociated. Temporary tracks are never physical door IDs and
equal OCR labels are never an identity association. Human-box mode may reuse
explicit reviewed physical IDs per bound frame; these are oracle supervision.
Tracking loss never silently transfers a selected target to a new track.
Selected-track physical association is rechecked against M2's current evidence
on every `VERIFY_TARGET` frame; a changed/missing identity stops guidance.

Raw OCR text and confidence are retained per sign ROI and per reading. M1's
generic room parser filters syntax; it does not verify semantic sign origin.
New detector CLI runs use `exclude_non_room_context_v2` for known negative
whole-ROI contexts, including multi-room directional boards split across OCR
lines. Rejected board/poster readings remain in the raw input and M1 rejection
records, with per-ROI context in output. The earlier `v1` policy remains frozen
for old logs. A capture/region cannot change source context within an M1 session.
These are conservative prototype checks, not general sign understanding.
Missing confidence stays null;
OCR failures and empty crops/readings stay explicit. `INSUFFICIENT_EVIDENCE`,
`REOBSERVE`, OCR/association failures, target loss and `FOUND` stop M3 motion.
With `VERIFY_TARGET`, a unique visible reviewed target sign may be selected
for bounded alignment / `STOP_AND_READ`; this is oracle interface behavior.
Actual detector mode conservatively remains unassociated and cannot establish
a physical numbering trend or autonomously return `FOUND`. General semantic
candidate choice and reliable identity/reacquisition belong to M3.2.

`--inspection-proposals` optionally logs advisory `INSPECT_DOOR(track_id)` in
a separate `inspection` output. It never selects the controller target or
authorizes motion. A wall misclassified as a high-score door can still receive
this advisory; ten stable frames do not verify an entity. See the
[M3.2 prototype contract and limits](m32-inspection-interface.md).

ReplaySession also accepts explicit `stop`, `select_target` (current visible
track/frame only), `execution_feedback`, and M2 `operator_confirmation` events.
The video CLI only emits frame events and never invents operator events.
Feedback requires `simulated=true`, matching pending action ID and boolean
`completed` / `safe`. No feedback is inferred from advancing the recording.
STOP or unsafe simulated execution is latched for the session; restart the
session to clear it. Other safety halts require explicit target selection to
resume an already selected track. Duplicate frames add no votes or motions;
conflicting IDs or invalid ordering are rejected and halt pending motion.

## Event log and validation

Each local JSONL starts with a versioned provenance header. Each subsequent
row records `input` and derived `output`: source frame/PTS, mode, candidate
boxes/scores, ROI OCR text/confidence/errors, manual association provenance,
short-term tracks/lost tracks, M1 results, M2 events/decisions and M3 guidance.
The final `end` row records the event count. Failed/partial files lack that
marker and cannot pass replay verification. Replaying recomputes all state and
compares every derived output. This detects software/output drift; it is not a
cryptographic authenticity or human-annotation audit.

Logs may contain room labels and evidence. Keep them under ignored `outputs/`
or a private local directory. No original RGB, checkpoints, maps or generated
logs are included in source changes. Original review/field evidence is unchanged.

```bash
PYTHONPATH=src .venv/bin/python -m unittest discover -s tests -v
PYTHONPATH=src .venv/bin/python -m compileall -q src tests apps scripts examples
```

New tests cover PTS/frame order and sampling on a generated synthetic VFR video,
PR #6 review adaptation, mode separation, ROI extraction/confidence/failures,
duplicate/conflicting observations, lost tracking, ambiguous associations, M2
insufficient evidence, M3 STOP/REOBSERVE, simulated execution and independent
operator confirmation, the ten-frame wall false positive, source-context
conflicts and preserved directional evidence. Private recorded detector logs
have also been replay-verified. Recover the original review images/provenance
before GT bindings or a real human-box run; continue M3.2 candidate validation
and explicit execution authorization separately. Issue #5 remains open for
the unfinished semantic policy and M3.3 human-in-the-loop field acceptance.
