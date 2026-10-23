# RGB door candidates + camera-relative guidance (M1.2/M3 experimental UI)

This is **not** the verified M0 Gradio recorder, nor an end-to-end navigation demo.
The original \`apps/live_demo.py\` remains unchanged on this branch. This
entrypoint is a separate, optional way to test a detector and conservative
short-term tracking using the *same RGB camera*.

## WSL setup and repository synchronization (run locally)

The assistant cannot remotely access the user's Windows/WSL filesystem or
invoke Codex on their machine. Use these commands in the actual WSL terminal:

~~~bash
mkdir -p ~/projects && cd ~/projects
git clone https://github.com/sceneeeee/room-sign-guided-navigation.git
cd room-sign-guided-navigation
git fetch origin
git switch --track origin/feat/online-rgb-guidance-integration
python3 -m venv .venv
source .venv/bin/activate
python -m pip install --upgrade pip
python -m pip install -e '.[vision,ui,detection,development]'
python -m unittest discover -s tests -v
python apps/rgb_guidance_demo.py
~~~

If the repo already exists locally, **do not reset away local work**:

~~~bash
cd ~/projects/room-sign-guided-navigation
git status --short
git fetch origin
git switch -c local/rgb-guidance-test --track origin/feat/online-rgb-guidance-integration
~~~

For a MacBook browser connecting to a remote WSL/Linux host, keep SSH forwarding
on the MacBook:

~~~bash
ssh -N -L 7861:127.0.0.1:7861 USER@GPU_HOST
~~~

Then open \`http://127.0.0.1:7861\` in **the browser on the camera machine**.
A browser webcam requires the device's camera permission. A remote HTTP tunnel
must stay bound to loopback; avoid exposing the UI to public networks.

## Model behavior and limits

- Uses **YOLO-World** with text classes \`door\`, \`room number sign\`,
  \`directional sign\` as *candidate proposals*. Weights
  \`yolov8s-world.pt\` load/download on first detection. Validate
  Ultralytics runtime version/model support and dataset performance locally.
- Input Gradio video is **sampled approximately once per second**; this is
  NOT a guaranteed real-time 30-FPS pipeline. The current UI timestamps frame
  **receipt at the server**, not camera-side capture. End-to-end transport,
  queuing and human interface latencies are not yet measured.
- Uses IoU-based **continuous, short-term** image tracks. IDs can change on
  abrupt viewpoint changes. They are never promoted to permanent physical
  \`landmark_id\` automatically; no learned re-ID, camera pose or SLAM.
- On screen, pick an actually visible track; the controller subsequently
  proposes short relative turns or a conservative \`FORWARD_SHORT\` only if
  the target is in view and aligned. **Human controls all motion and
  independently assesses safety**. A stopped/unsafe response blocks future
  motions.
- In this version \`readable=False\` until a real quality estimator and
  OCR-to-door crop association is integrated. Therefore the UI cannot yet
  complete \`STOP_AND_READ\` or \`FOUND\` automatically. It neither reads nor
  verifies room plates and must not be reported as complete room search.
- No VLM runs here. M2.4 \`StreamSession\` is not connected to the live demo;
  M1 adapter remains a tested standalone component. Issue #2 supplies future
  real-world validation footage and manual truths.

## Suggested local diagnostics with Codex CLI

On the real WSL terminal you can run:

~~~bash
codex
~~~

Suggested instruction to paste into Codex:

> Review \`apps/rgb_guidance_demo.py\`, \`src/roomnav/rgb_perception.py\`,
> \`src/roomnav/visual_tracking.py\`, and \`src/roomnav/visual_guidance.py\`.
> First run the repository offline tests. Then, if I grant camera/GPU access,
> test the Gradio stream using our phone/Camo feed. Check YOLO-World model
> loading, BGR/RGB handling, duplicate/stale frames, dropdown selection,
> Gradio 6.30.0 callback compatibility, action-feedback gating, VRAM use and
> end-to-end latency. Do not change M0, do not commit/push, do not infer
> physical door IDs from short tracks or declare FOUND from candidate boxes.
> Report reproducible errors, proposed fixes, and actual measurements.

## Next milestones

1. Add genuine camera capture timestamps, end-to-end latency and reliable
   queue/backpressure handling before interpreting the prompt rate as
   continuous guidance.
2. Add cropped OCR and door-sign association using recorded real images;
   forward only defensible raw observations to M2.4.
3. Connect M2 high-level visible-target selection and optional VLM
   decision-making to the visual skill system.
4. Evaluate precision/recall/identity switches on Issue #2 annotated data,
   then a supervised safe human-in-the-loop corridor session.
