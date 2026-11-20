# M4 teammate handoff

Use the feature branch and run commands from the repository root. Keep the existing M3.2 entry point for historical reproductions. The M4 live entry point needs a browser webcam source or Camo presented as a webcam to the desktop browser. Use the loopback URL printed by Gradio.

## Environment and reproducible smoke

Use the project Python environment. For a new environment, install the repository extras with `python -m pip install -e '.[vision,detection,ui]'`. The tested local environment used Python 3.13. FFmpeg/ffprobe are required for video. Provision the matching YOLOE checkpoint, MobileCLIP text encoder and RapidOCR ONNX cache privately before going offline. The YOLOE class prompts come from the existing detector. Run the following with new output paths for each run.

```bash
.venv/bin/python -m unittest discover -s tests
.venv/bin/python -m compileall -q src apps scripts examples tests
git diff --check
.venv/bin/python -m roomnav.search_cli evaluate examples/m4/search_episode.json \
  --output outputs/m4-scripted --policies rule --scripted
.venv/bin/python -m roomnav.search_cli replay outputs/m4-scripted/rule.jsonl
.venv/bin/python -m roomnav.search_cli evaluate examples/m4/search_episode.json \
  --output outputs/m4-comparison --policies sequential rule
```

## Supervised live run

```bash
OMP_NUM_THREADS=2 MKL_NUM_THREADS=2 OPENBLAS_NUM_THREADS=2 \
  .venv/bin/python apps/multidoor_search.py --weights yoloe-11s-seg.pt --device cpu
```

1. Enter the target, policy and model-call budget. Click **New session / reset**, then click **Click to Access Webcam** and the camera’s **Record / 录制** control to start streaming. The app warms the detector and OCR before serving. The overlay shows temporary IDs. The JSON panel includes the current target, recommendation, landmark, evidence and visits.
2. Wait for automatic acquisition when there is one unambiguous door, or enter the displayed door ID and click **Manually select door**. **Confirm visual target** records the operator's visual judgment of that door. It does not confirm its room number.
3. Judge every movement for safety. **Action completed safely** acknowledges the displayed pending action. The next hint requires fresh RGB uploaded with the browser acknowledgement of that action. Queued older uploads are discarded. **Refuse / unsafe** ends a pending movement and latches the session. Use **Emergency STOP** at any point.
4. Observe the plate, its sign track ID and the selected door. Enter an opaque physical ID such as `door-A` and your name. **Associate selected door and sign** records a manual binding after two unambiguous views. Reuse an ID only when you independently recognize that physical door. OCR retries automatically on new frames. Review the raw readings/confidences and the landmark vote history.
5. For another door, finish outstanding feedback, click **Finish or release lost target**, acquire new RGB and select the next visible door. The same session retains the target and memory. After `VERIFY_TARGET`, independently read the actual sign, type the number and click **Confirm independent room reading**. Record a different number when that is what you read. Use **End search / no more doors** to record an exhausted search.
6. **Prepare log download** exports a consistent JSONL snapshot. Keep failed and stopped episodes. Reset starts another target/log. Camera frames are processed in memory by the application. Gradio can cache interface media locally. Treat logs, cache files and any images as private experiment material.

An untrusted webcam timestamp stays absent. The live app uses monotonic server receipt for freshness, and capture-to-display latency remains unknown. UI telemetry uses a separate browser clock. Do not subtract these clock domains.

Each live turn or short-step suggestion has a six-second window for execution and acknowledgment. Two seconds without a valid RGB update can end that window earlier. Timeouts revoke the suggestion and stop the session. After a timeout, camera clear or STOP, start a new session and acquire fresh RGB. After a browser disconnect, reload the page before starting the session. Late acknowledgment requests received by the server are audited as rejected. The browser clears movement advice immediately on STOP and ignores older updates even if they arrive later.

The browser displays pending advice from the next status poll after inference commits. That poll carries its own browser timestamp. Status polls cannot extend an action's original deadline. If a new frame arrives while the lease still awaits feedback, the display continues to identify the same pending action.

## Recorded RGB

```bash
.venv/bin/python -m roomnav.search_cli video /private/route.mp4 --target LSS119 \
  --session route01 --weights yoloe-11s-seg.pt --device cpu --stride 4 \
  --output outputs/route01.jsonl
.venv/bin/python -m roomnav.search_cli replay outputs/route01.jsonl
```

Choose a stride that keeps source-frame gaps below 1.5 seconds. `--frames` bounds a smoke run. A video alone can generate proposals, raw OCR and advice. To replay actual supervised interactions use `--interactions private-actions.json`. The file maps original `video_frame_NNNNNNNN` identifiers to arrays of commands applied immediately after that frame. A command uses the displayed temporary ID, physical ID or action ID, exactly as in the live log. Example entries within a frame's array include `{"type":"feedback","action_id":1,"completed":true,"safe":true}` and `{"type":"select","frame_id":"video_frame_00000008","track_id":"track00003"}`. Keep independent operator inputs separate from detector annotations. Never synthesize acknowledgements for prerecorded motion.

## Evidence before fieldwork

A deterministic fixture exercises multiple doors, conflicting OCR, a wall plate, a failed read, a revisit and explicit reacquisition. The real local YOLOE/RapidOCR smoke processed six timestamped frames of the private route. Multiple doors and sign ROIs were observed. It reached its frame budget with zero physical bindings. The real-model results from Isaac belong to the rendered corridor domain. The Gradio transport smoke uses a stored image. A second smoke streamed an Isaac clip through the webcam control in headless Edge using a simulated media device. None of these runs establishes real handheld success.

The timing regression used headless Edge, a virtual webcam and synthetic perception with a 1.2-second delay on the issuing frame. It exercised action display, acknowledgment, STOP with a late poll and feedback, the RGB watchdog, and reopening a browser session. Use the [field preflight](m4-field-preflight.md) to verify the intended phone or Camo path separately.

Before the joint physical experiment, check the intended camera orientation, frame cadence, model cache, operator controls, independent room reading, STOP and exported log on the teammate's machine. Agree on opaque physical IDs, route direction, reference annotations and the independent reader. Record the first supervised full route as field evidence, including any failed approach or abort.

See the [architecture audit](m4-architecture.md), [evaluation protocol](m4-evaluation.md) and [simulation commands](../examples/isaac_multidoor/README.md).
