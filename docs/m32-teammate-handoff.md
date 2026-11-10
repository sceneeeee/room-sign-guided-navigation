# M3.2 teammate handoff — frozen implementation, M3.3 field work pending

Work from `feat/m32-door-approaching`, based on PR #7 merge `ef5b3f0`. Do not combine the separate open PR #8 field-data branch without review. This delivery does not close Issue #5.

## Environment

Package metadata supports Python ≥3.10. This delivery was actually tested on **Python 3.13.13, Ubuntu 22.04.5 x86_64 under WSL2**, CPU inference/cached perception. Python 3.11 passed earlier M3.1 CI, not this delivery's full environment. Native Windows, macOS (Intel/Apple Silicon), other Python versions and GPU execution of this M3.2 delivery remain untested.

From the repository root, create your own environment; never copy the developer's `.venv`:

```bash
python3 -m venv .venv
source .venv/bin/activate
python -m pip install --upgrade pip
python -m pip install -e '.[vision,ui,development]'
```

Native Windows activation would be `.venv\Scripts\Activate.ps1`; that platform has not been validated. Install system `ffmpeg` and `ffprobe` before full video integration tests (Ubuntu: `sudo apt install ffmpeg`). NumPy, Pillow and Gradio are needed for the complete tests, even though base installation `pip install -e .` has no mandatory dependencies. Tests do not initialize a real detector/OCR model; optional FFmpeg tests skip when the executables are missing. A localhost HTTP test requires permission to open a loopback socket.

For actual detector execution, additionally install:

```bash
python -m pip install -e '.[detection]'
```

This extra installs Ultralytics and CLIP from a GitHub archive. PyPI/GitHub access is needed; PyTorch wheels must match the teammate's OS/accelerator. Cached-log replay and synthetic smoke need no detector, weights, network or GPU. The exact fresh dependency installation commands above were inspected against `pyproject.toml`, **not performed as a new clean download/install during finalization**. Actual tested versions: NumPy 2.5.4, Pillow 12.3.0, Gradio 6.30.0, RapidOCR 3.10.0, ONNX Runtime 1.31.0, Torch 2.14.1, Ultralytics 8.4.184. Only Gradio is pinned in project extras; this is not a fully locked environment.

CPU is supported: recorded processing exposes `--device cpu`; the existing live detector factory leaves device selection to Ultralytics and does not expose a GPU/CPU UI selector. On a machine without a compatible GPU, use the CPU PyTorch build; if device auto-selection fails, deployment configuration of that factory remains necessary. No GPU speed/latency claim is made.

## Verified offline smoke (no private assets)

Run each output command with a fresh destination: writers intentionally refuse overwriting. These commands were executed successfully during finalization, using the same relative paths under `outputs/m32-final-delivery/` instead of the example fresh directory below:

```bash
python -m roomnav.offline_cli fixture \
  --input tests/fixtures/m32_detector_smoke_inputs.jsonl \
  --output outputs/teammate-smoke/synthetic-detector.jsonl
python -m roomnav.offline_cli replay outputs/teammate-smoke/synthetic-detector.jsonl
python -m roomnav.offline_cli approach \
  --input outputs/teammate-smoke/synthetic-detector.jsonl \
  --output outputs/teammate-smoke/advice --preview-policy visual_advisory
python -m roomnav.offline_cli approach-replay outputs/teammate-smoke/advice/events.jsonl
python -m unittest discover -s tests -v
python -m unittest discover -s tests -p test_live_approach.py -v
python -m unittest discover -s tests -p test_offline_replay.py -v
python -m compileall -q src tests apps scripts examples
git diff --check
```

The five-frame **synthetic** fixture produces one FORWARD_SHORT, one STOP_AND_READ, three STOP_REOBSERVE, no feedback or FOUND. It is not human-reviewed/oracle perception or field success. Existing M3.1 fixture also works via `fixture --input tests/fixtures/m31_synthetic_inputs.jsonl`; that fixture is explicitly synthetic human-box/oracle mode and cannot be fed to `approach`, which requires detector-mode inputs.

Artifacts: `events.jsonl` contains inputs and derived outputs; `summary.json` counts actions by evidence mode; `report.md` and `frame-reasons.jsonl` explain frame/PTS, candidate geometry, chosen track, OCR hypotheses and abstention. `--video PATH --demo-video` additionally verifies original video hash/PTS and creates frames, contact sheet and MP4; these are optional and require the matching private video plus FFmpeg. Without video, no visualization is expected.

With an authorized **saved recorded detector log**, substitute its path:

```bash
python -m roomnav.offline_cli replay outputs/private-transfer/lss119-revised-events.jsonl
python -m roomnav.offline_cli approach \
  --input outputs/private-transfer/lss119-revised-events.jsonl \
  --output outputs/teammate-recorded --preview-policy visual_advisory
python -m roomnav.offline_cli approach-replay outputs/teammate-recorded/events.jsonl
```

The same operations were verified locally against the original cached recorded logs. Python `convert_detector_log` defaults to strict schema2 for backward compatibility; CLI without review files defaults to schema3. `--preview-policy strict_source` selects V2. A legacy `--frame-reviews` file without explicit policy retains V2. Both schemas replay deterministically; V1 previews are explicitly rejected, with historical frozen code retained privately. Recreate V3 from original M3.1 inputs rather than rewriting a historical log header.

## Existing live entrypoint, and what is unfinished

After installing detector/UI/OCR dependencies and acquiring weights, from the repository root:

```bash
python apps/rgb_guidance_demo.py
```

It binds **127.0.0.1:7861**, without public sharing. Open `http://127.0.0.1:7861` on that computer. Import/build and callbacks were tested with fake RGB/detector inputs; a real browser-camera/model launch on the teammate's machine was **not** validated here.

**V3 is active by default in the UI**: its state factory constructs `LiveDoorApproachSession`. The old `GuidanceSession` remains for compatibility. Current controls select an actual detected door track, show tracked boxes and frame advice, and record “已安全完成本次动作”, refusal, and emergency STOP. Motion hints wait for explicit human feedback; later frames are never synthesized feedback. Reject/STOP/loss latch the session. There is no actuator and every mode has `motion_authorized=false`.

Manual track selection only chooses an observation target. It does **not** call the separate `confirm_visual_target(confirmed_by=...)` interface, verify physical identity, verify sign ownership or confirm a room. V3 may suggest observation of unknown doors using conservative visual support. STOP_AND_READ means attempt OCR, not confirmed room or FOUND.

**Still missing before a complete handheld trial:** dedicated visual-confirmation and new-session/restart UI controls, trustworthy camera timestamps, durable export/complete replay of live feedback/timing events, and measured camera→UI→human latency. The interfaces exist, but those UI controls are not wired. After a latched STOP, reload/start a new session; selecting another track does not resume it. Do not bypass the latch for a trial.

The factory currently loads `yolov8s-world.pt` from the working directory, whereas historical recorded evidence used `yoloe-11s-seg.pt`: these are different configurations, not interchangeable performance evidence. No live weight/device configuration control was added. RapidOCR initializes lazily; missing models can cause downloads at first use. CPU inference may exceed freshness limits and legitimately STOP; do not disable those limits to demonstrate progress.

A locally installed Camo virtual camera can be selected in the **computer browser** after configuring phone→computer Camo independently. Browser permission, orientation and non-mirrored text must be checked. Direct phone-browser capture is not ready merely by visiting the current loopback URL: it needs a reachable, secure camera-enabled deployment (HTTPS or an appropriate local setup) and timestamp/transport work. Do not expose an unauthenticated public service or assume HTTP LAN camera permissions. These connection paths are deployment work, not a validated M3.3 workflow.

## Private assets: transfer separately

Use an access-controlled team drive or authenticated SFTP/SCP, agree on retention/consent and verify SHA256 after transfer. Never put private assets in Git or public PR attachments.

| Asset | Suggested destination | Requirement |
| --- | --- | --- |
| Original authorized MP4 | `data/field_sessions/1f_raw/session_01/video/route_LSS101_LSS124_LSS119_raw.mp4` | Optional for cached advice; required for original-RGB overlays/re-inference |
| Recorded M3.1 detector/OCR logs | `outputs/private-transfer/` | Needed for exact recorded replay without models |
| Human-reviewed images/bindings | `data/field_sessions/1f_raw/session_01/review/` | Optional oracle/GT work; not needed for smoke/tests; original reviewed-frame/video binding remains incomplete |
| Model weights | `weights/` for CLI, or repository root for the current live factory filename | Required for actual inference only |

Known local SHA256 values (check transferred files, do not rename different weights to impersonate them):

```text
original MP4    93166fdc59275c43534e40cf57436fae35e77bc6c4db1500b34f25c1c9cc2e7d
yolov8s-world.pt 095f5266bb9b654bd5ad9e21e9cdeda78e0f2c8460f5d652eaf04bab7ee251cf
yoloe-11s-seg.pt 8e439445c87338b79d9ce21dec109f4621e26df67e94d26ea1a98c1e64dce3e3
mobileclip_blt.ts a67804d1b0f07b8b9a20c1761ec0847f34660f5fa338ec70e8f3fce68ed95e54
```

YOLOE/text encoders and RapidOCR may need additional cached assets; these three files are not a complete offline bundle. Obtain licensed weights from their providers or private approved transfer, and provision caches for an offline machine. Successful fixture/replay does not validate model installation. Never copy credentials, absolute-path configurations or another machine's environment.

## Validation boundaries and next action

The previous run reported **396 passing tests**; finalization reran **396/396**, no skips, with FFmpeg available. A separate exported source tree containing no private data, weights, environments or prior outputs also passed **396/396** using the installed dependency environment. Existing tests use public/synthetic inputs, fake inference and temporary generated media; no private video/weights are required. The full suite includes M0/M1/M2/M3.1 regressions, deterministic replay and live contracts. A restricted sandbox needed permission for the localhost socket test; it was rerun, not skipped.

Real recorded evidence supports four manual TURN_LEFT suggestions and two automatic TURN_RIGHT suggestions. There is no validated full real-door approach-to-read sequence, no completed handheld Door Approaching experiment, no autonomous physical motion and no automatic FOUND. PR #8's separate OCR collection does not establish this workflow's acceptance.

Start with smoke/replay, then deployment preflight, then finish the missing UI/timestamp/logging work on a reviewed follow-up branch. For field data, a 10–20s single-door clip (settle, slowly center, human-judged approach, settle/read) is enough; no need to rerecord the full route. Keep failed/aborted trials and distinguish automatic target choice, manual track choice, human visual confirmation and independent room confirmation. See [policy/interface details](m32-live-interface.md) and [recorded evidence](m32-door-approaching.md).
