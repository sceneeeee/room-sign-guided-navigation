# M4 episode evaluation

Freeze one target, initial view, corridor, frame budget, model-call budget, operator protocol and annotation set per comparison. Start a fresh session for every policy. A change of viewpoint depends on the policy's current choice and feedback, so compared policies may visit different views. The detailed scripted route is a regression test and deliberately fixes the visit order.

## Policies and inputs

`sequential` inspects visible doors from left to right. `rule` uses the existing M2 decision, observed target-like readings, conflicts and unvisited visual candidates. `qwen`, `vlm-text` and `vlm-rgb` use the constrained LLM planner with the same action schema and visible-track allowlist. The two VLM lanes should use the same checkpoint. Only the RGB lane receives the current JPEG (maximum side 640 pixels). An invalid response or timeout records a fallback. Exhausting the call budget uses the deterministic policy. No planner can grant motion, bind a physical door or generate independent confirmation.

```bash
export ROOMNAV_LLM_BASE_URL=http://127.0.0.1:11434/v1
export ROOMNAV_LLM_MODEL=qwen3:4b-instruct-2507-q4_K_M
.venv/bin/python -m roomnav.search_cli evaluate examples/m4/search_episode.json \
  --policies sequential rule qwen --budget 8 --model-timeout-s 10 --output outputs/m4-qwen
```

Set `ROOMNAV_VLM_BASE_URL` and `ROOMNAV_VLM_MODEL` for your installed multimodal endpoint, then include `vlm-text vlm-rgb` in the same command. The optional credentials are `ROOMNAV_LLM_API_KEY` and `ROOMNAV_VLM_API_KEY`. Use an endpoint permitted to receive the episode evidence. The RGB setting sends the current image. A missing backend configuration fails explicitly. Fixture-only regression and Rule require no network.

## Measurements

| Measure | Denominator or clock |
|---|---|
| Search success, wrong FOUND | Evaluate only when an independent reader was available under the run protocol and door truth can score the outcome. Otherwise both metrics are JSON `null` (N/A). `found_observed` and the count of independent confirmation events are separate facts. Observer-eligible failure and STOP remain in the episode set. |
| Doors and revisits | Unique explicitly bound physical IDs and subsequent bound visits. In Isaac also score selected detector tracks matched to projected doors (`IoU >= .5`) post hoc. A newly selected track alone is insufficient for a physical visit. |
| OCR and association | For each annotated room plate, require a unique current sign match at IoU ≥ .5. Count missing detections, failed reads and ambiguous associations as misses. OCR requires the correct complete reading. Association requires a stable/associated sign and its current owner's box matched to the correct door. Report numerator and denominator; a zero denominator yields null. |
| Track loss, intervention and actions | Count selected-lock losses, recorded operator or simulated interactions, feedback and actual proposed movement events. Report the simulator's pose changes independently. |
| Duration and decision latency | Use the source/episode clock for search duration and monotonic processing time for M2 plus M3. Keep detector, OCR, server receipt, capture clock if available, server UI preparation, browser update and operator times separately. Synthetic episode duration uses a logical clock. |
| Distance | Sum simulated camera translations from the execution trace in meters. Physical trials require independently measured actual and reference distances before any SPL calculation. |
| Termination and abstention | Preserve the recorded terminal state and reason, including frame budget. Count per-frame `STOP_REOBSERVE` reasons. A recorded budget termination in a run without a reader leaves success unevaluated. |

Save the inputs, outputs and metrics for each policy. Logs include nondeterministic model decisions as recorded planner responses. Replay recomputes deterministic transitions and checks their outputs, with no new inference. Timing and browser telemetry are observational. Clip-level detector smoke runs lack episode-level physical ground truth.

### Target evidence and choice attribution

- Target OCR counts frames containing a reading that parses to the target. The annotation-matched subset requires a unique sign match at IoU ≥ 0.5 with a target plate. A correct owner requires its door track to match the annotated target door uniquely. Stable/associated owners are counted separately from one-frame hypotheses.
- Correct selection counts every frame whose active track uniquely matches the annotated target door, including frames with unreadable text. The overlap with matched target OCR is reported separately. Verification comes from logged `VERIFY_TARGET` over distinct frame IDs, and confirmation comes from explicit `confirm_room` events.
- Model influence is measured at explicit `accept_intent` events. Recompute deterministic Rule with the same observed candidates, memory and inspected tracks at that event. An accepted choice that differs under a valid model result counts as a changed selection. An identical choice is reported as Rule-equivalent, with separate reasons for budget exhaustion, no model track used, or model agreement. Model call validity and error fallbacks are separate counters. This measures selection changes on recorded inputs. Policy performance would require independent outcome trials.

### Saved Isaac multi-door runs

```bash
.venv/bin/python -m roomnav.search_cli score-isaac outputs/m4-engineering/isaac-sequential
.venv/bin/python -m roomnav.search_cli score-isaac outputs/m4-engineering/isaac-reviewed
.venv/bin/python -m roomnav.search_cli score-isaac outputs/m4-engineering/isaac-qwen
```

The command reads the manifest, episode, evaluator and execution records and returns scores on stdout. Saved inputs and historical score files remain intact.

| 64-frame run | Target OCR, raw / matched | Correct owner, any / stable | Target selected | OCR with target selected | `VERIFY_TARGET` / independent confirmation / observed `FOUND` | Verified success |
|---|---:|---:|---:|---:|---:|---|
| Sequential | 3 / 3 | 3 / 2 | 11 | 3 | 0 / 0 / 0 | N/A |
| Rule | 1 / 1 | 1 / 0 | 0 | 0 | 0 / 0 / 0 | N/A |
| Qwen | 9 / 9 | 9 / 6 | 16 | 6 | 0 / 0 / 0 | N/A |

Sequential read LSS119 on frames 54–56 and had the target selected on all three. Rule read LSS119 on frame 54 while another door was selected. Qwen read it on frames 40–42, 48–50, 59 and 62–63. The target was selected on six of those nine frames.

All three reached `simulation_frame_budget` without an independent reader. The most frequent abstentions were turn geometry/stability for Sequential (23 frames) and Qwen (26), and explicit selection required for Rule (20). Two track-loss abstentions occurred in each run. The scorer returns the full reason distribution.

Qwen returned eight valid responses on frames 0–7. Each lacked a track recommendation. The ten accepted recommendations all matched the same-input Rule choice. Two followed a model result with no track used, and eight followed budget exhaustion. The accepted-choice model influence count is zero. These saved runs cannot establish a verified success rate or a Qwen advantage.

Strict replay passed on the three scored Isaac artifacts, the final prerecorded RGB smoke and the saved browser safety logs. Earlier development artifacts can share the same schema tag but diverge under current transition logic or lack recorded model responses. Retain them as historical diagnostics and use the three listed directories to reproduce the table.

## Engineering evidence, 2026-11-18

| Evidence | Outcome | Interpretation |
|---|---|---|
| Scripted synthetic LSS119 regression | Independent fixture reader confirmed after A, B, C, A, D, including one loss/reacquisition | Exercises memory, feedback and determinism using supplied proposals and OCR. |
| Same action-conditioned synthetic episode | Sequential visited four doors. Rule and local Qwen each visited one. All three required the fixture reader. The 10-second offline deadline yielded eight validated Qwen calls and zero fallbacks. A separate three-second run with concurrent renderer load timed out on all eight calls. | One visible target cue favors early selection. These outcomes cannot estimate general search success or detector accuracy. |
| Real YOLOE and RapidOCR on Isaac RGB | Sequential, Rule and Qwen each reached a 64-frame budget with no independent reader. Their selections matched four, three and four unique authored doors. | Real inference on rendered RGB. Verified success and wrong `FOUND` outcomes are N/A. |
| Real prerecorded corridor RGB | Six original-PTS frames, detector proposals on all six, sign-ROI OCR on five. No bindings or FOUND. | Ingress smoke without reviewed M4 operator actions. |
| Gradio localhost | The STOP/download API smoke replayed. Headless Edge also streamed nine frames through its webcam control using a simulated media device. A rival door appeared during pending execution and latched STOP. | Service/transport check. Webcam capture and operator ergonomics await the joint trial. |
| VLM and handheld trials | The image request/validation path has test-only backend coverage. No deployed VLM or handheld M4 result. | Run the paired multimodal comparison with a configured VLM. Collect physical evidence separately. |

Historical M2 reports evaluate single decisions and retain their original scope. Before field comparison, preregister target order and stopping rules, counterbalance policies/operators, retain aborted episodes and score labels independently of policy output.
