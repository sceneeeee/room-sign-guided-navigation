# M3 online prototype: bounded RGB visual guidance

This is an **offline-testable controller**, NOT a validated walking assistant or
an autonomous navigation controller. Real door detection, short-term tracking,
OCR/readability scoring, per-frame timestamps and a human execution UI are
upstream integrations still to be developed.

- `VisualCandidate`: a single **captured frame** ID, capture timestamp in a
  known clock domain, temporary track ID, normalized bbox, optional associated
  physical landmark ID, and a *visual readability* flag. Track ID is **not** a
  verified physical door identity. Bounding boxes are image coordinates, not
  depth or a collision-free destination.
- `VisualGuidanceController.select_target(track_id)` receives an externally
  selected visible candidate; the controller **does not** invent a semantic target.
- `step(frame_id, timestamp_s, candidates, now_s=...)` returns a bounded
  action or `STOP_REOBSERVE`/`WAIT_EXECUTION`. Clocks must be comparable;
  duplicate, out-of-order, stale and future frames cannot issue new motion.
- `acknowledge(action_id, completed=..., safe=...)` is mandatory after every
  turn or short forward instruction. The **human** handles obstacle detection,
  trajectory, actual step length, consent to move, and emergency stop. After
  acknowledgement, another **new frame** is required before more motion.
- `STOP_AND_READ` only requests **independent OCR**, never asserts a room
  number, physical association, FOUND, target reached, or distance.
- `halt()` blocks all motion suggestions until a new target is chosen by
  the planner. An unrecognized/lost track stops automatic approach.

### Quick offline unit tests

~~~bash
python -m unittest tests.test_visual_guidance -v
python -m unittest discover -s tests -q
~~~

### Integration dependencies

1. M1.2 detector/tracker emits real visible candidates and captures quality
   assessments; ensure `frame_id` and timestamps come from acquisition.
2. M2 selects an **existing visible track** or declines to choose, without
   equating that track with a confirmed permanent landmark.
3. M0 live UI timestamps frames and shows motion prompts; an operator can
   veto unsafe motion. UI must collect movement completion feedback.
4. M1 OCR/door-sign association emits a **separate strict M2.4 Observation**
   with actual raw OCR text, only when the physical association is defensible.
5. On real samples, measure detection/track reliability, latency, human
   interventions and end-to-end search; no such claims are made here.
