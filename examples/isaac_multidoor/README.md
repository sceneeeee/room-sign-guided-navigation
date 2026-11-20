# Four-door Isaac RGB search

The renderer extends the single-door teaching corridor with LSS101, LSS124, LSS118 and LSS119, a numbering gap, a directional board, an adjacent-wall plate, an extra blank plate and a partial occluder. The bounded camera sweep can reverse and revisit doors. A single RGB camera supplies the policy host. Only the separate evaluator reads projected door/plate geometry after the run.

Run the host with the perception environment and pass the simulator Python command after `--renderer`. Both processes need access to the same new output directory. Keep the host's working directory at the repository root so YOLOE can find the local MobileCLIP cache. Example for a native Linux renderer:

```bash
out=outputs/m4-isaac-rule
.venv/bin/python examples/isaac_multidoor/run.py --weights yoloe-11s-seg.pt \
  --output "$out" --frames 64 --policy rule --renderer \
  /path/to/isaac/python.sh examples/isaac_multidoor/render.py --output "$out" \
  --font /usr/share/fonts/truetype/dejavu/DejaVuSans.ttf
```

For WSL hosting a Windows Isaac installation, supply Windows paths to the renderer. This is the tested split-runtime configuration:

```bash
out=outputs/m4-isaac-windows
.venv/bin/python examples/isaac_multidoor/run.py --weights yoloe-11s-seg.pt \
  --output "$out" --frames 64 --policy rule --renderer \
  /mnt/c/Windows/System32/cmd.exe /d /c 'D:\issacsim-4.2\python.bat' -X utf8 \
  "$(wslpath -w "$PWD/examples/isaac_multidoor/render.py")" \
  --output "$(wslpath -w "$PWD/$out")" --font 'C:\Windows\Fonts\arial.ttf' --/app/vulkan=false
```

The local Windows 4.2 Vulkan viewport crashed during initialization. Direct3D 12 with UTF-8 completed the run. USD authored its stage on a native temporary filesystem and copied the scene and texture assets into the shared directory. Installation, actual model/encoder weights and local graphics drivers are external prerequisites.

Use the same initial pose, frame limit and independent reader conditions for each `--policy`. Use `--supervised` on the host to inspect the latest saved RGB and displayed tracks. Enter a JSON interaction or press Enter on each frame. For example, bind a reviewed pair with `{"type":"bind","track_id":"track00001","sign_track_id":"track00002","physical_door_id":"A","actor":"reader1"}`. At a verification opportunity use the selected landmark ID and type the independently read number. The host simulates bounded execution feedback. Headless runs deliberately have no independent reader. Synthetic CI uses the separate deterministic fixture command in the handoff.

## Files and observed outcome

- `rgb_*.png`, `corridor.usda` and `textures/` preserve rendered inputs and authored assets.
- `episode.jsonl` contains raw detector/OCR output, tracks, plate ownership, M1 observations, M2 intent, M3 advice and simulated feedback. Run `python -m roomnav.search_cli replay PATH/episode.jsonl` in the host environment.
- `execution.jsonl` records before/after poses, time and camera execution. `evaluator.jsonl` holds projected annotation boxes and labels. Keep both out of planner prompts. Projection scores concern authored plate extents and can include partial occlusion.
- `manifest.json`, `isaac.log` and `score.json` preserve run settings, local detector hash, runtime output and episode scoring.

On Windows Isaac 4.2, the host ran real CPU YOLOE and RapidOCR on all three policy runs. Each used the same corridor, initial pose and 64-frame limit. Qwen used the local Ollama backend with eight calls and a ten-second deadline. All eight responses validated without supplying a track. Rule supplied the accepted track choices.

| Policy | Unique doors selected | Repeated selections | Movement hints | Track losses | Simulated translation | Correct plate OCR | Correct associations |
|---|---:|---:|---:|---:|---:|---:|---:|
| Sequential | 4 | 5 | 5 | 2 | 7.2 m | 24/104 | 49/104 |
| Rule | 3 | 5 | 2 | 2 | 8.0 m | 23/110 | 32/110 |
| Qwen | 4 | 4 | 6 | 2 | 7.2 m | 25/108 | 41/108 |

Translation includes the simulated observation sweeps. All three runs had zero operator-bound landmarks, zero independent confirmations and zero observed `FOUND` events. Each reached the frame budget. Verified success and wrong-`FOUND` outcomes are N/A because the run protocol lacked an independent reader. All ten accepted Qwen choices matched deterministic Rule on the same recorded state. Renderer nondeterminism can change detections on a new run. The [post-run evaluation protocol](../../docs/m4-evaluation.md#saved-isaac-multi-door-runs) gives the read-only scoring commands and stage-by-stage results.
