# M1 -> M2.4 offline OCR adapter (prototype)

`roomnav.m1_adapter.RoomOCRSample` accepts actual OCR text and capture
metadata. It emits the existing **strict M2.4 Observation JSON object** only
when `parse_room_number` recognizes the *complete* raw room identifier.

- Empty, partial, uncertain or multi-label raw OCR is **not fabricated into**
  a complete room. Preserve such failures in M0 field logs for independent
  review.
- `session_id:frame_id:region_id` becomes a stable observation ID; multiple
  regions of a single capture share `session_id:frame_id` provenance.
- `landmark_id` is omitted unless a *separate M1 annotator/continuous tracker*
  explicitly provided a physical-door ID plus association provenance. The
  `continuous_track` source only supports association within a continuous,
  unbroken track; it is not cross-corridor place recognition.
- The adapter does **not** establish a mapping between signs and doors,
  infer door distance, or validate physical room truth. Its confidence is the
  underlying uncalibrated OCR model score.
- Current M2 `FOUND` remains **operator-confirmation only**. Even repeated
  matching observations enter `VERIFY_TARGET`, not FOUND.

Synthetic unit tests instantiate `StreamSession` and exercise repeated-frame
gating, duplicate replay, incomplete OCR, distinct physical doors and
association failure. Field replay coverage is documented separately below.

`roomnav.m1_pipeline.M1Session` provides the M1-side replay boundary: it
keeps rejected OCR records for review, accepts complete room identifiers,
and deduplicates only the explicit `session_id:frame_id:region_id` capture
identity. An identical retry is a duplicate; the same observation ID with
different event content raises `ValueError` before changing accepted evidence,
matching `StreamSession`. It does not merge equal strings from different
physical doors. Ingestion never generates operator confirmations.

## Three-door field replay (2026-11-07)

Issue #3 integration uses the committed
[`observations.jsonl`](field-tests/2026-11-07-m1/observations.jsonl) directly
with the existing M2.4 entry point, without a new event format:

```bash
PYTHONPATH=src .venv/bin/python -m roomnav.stream_cli \
  --target LSS119 --input docs/field-tests/2026-11-07-m1/observations.jsonl

# Equivalent stdin replay:
cat docs/field-tests/2026-11-07-m1/observations.jsonl | \
  PYTHONPATH=src .venv/bin/python -m roomnav.stream_cli --target LSS119
```

All IDs retain the `session_01:` prefix. The field report supplies the
**manual physical-door associations** in the following table. Door IDs happen
to contain room labels; this is supplied annotation, not a naming rule for
automatically associating equal OCR text.

| Arrival | OCR | Frame suffix | Landmark suffix | Observation suffix | Action for LSS119 |
| --- | --- | --- | --- | --- | --- |
| 1 | LSS101 | frame_000001 | door_LSS101 | frame_000001:door_sign | INSUFFICIENT_EVIDENCE |
| 2 | LSS124 | frame_000029 | door_LSS124 | frame_000029:door_sign | INSUFFICIENT_EVIDENCE |
| 3 | LSS119 | frame_000161 | door_LSS119 | frame_000161:door_sign | REOBSERVE |

There are three unique frames, three unique observations and three distinct
associated landmarks, each with **one** OCR reading. Memory counts grow
1 → 2 → 3; unassociated and confirmation counts remain zero. Replaying the
same IDs is idempotent and cannot manufacture another capture. Every decision
can cite only landmarks already received.

All three `timestamp_s` values are **0.0 placeholders**, not measured capture
times. Confidence remains `null`. Replay preserves the raw bytes, timestamps,
IDs, OCR and arrival order; it does not derive timestamps from frame suffixes,
2 FPS sampling, or video duration. These records cannot support end-to-end
latency, speed or independent trial metrics. The source RGB frames and full
OCR logs are absent from this checkout; replay validates the published event
contract, not OCR accuracy or the manual annotation independently.

The published order is **101 → 124 → 119**, with steps +23 and −5. Do not sort
or rewrite it into a monotonic corridor sequence. For an absent target LSS120,
all three replies are INSUFFICIENT_EVIDENCE: each landmark is still single
frame, so the rule cannot establish a reliable numbering trend. A separate
synthetic `SYN101 → SYN124 → SYN119` two-capture-per-door test reaches the
explicit nonmonotonic-sequence rejection; it adds no field evidence.

Manual association and reported OCR review are **not** a submitted
`operator_confirmation`. No record in this replay authorizes FOUND. A matching
single-frame target returns REOBSERVE; VERIFY_TARGET still requires qualifying
capture evidence. Only a separate explicit operator action can confirm the
target. This is offline field-event integration, not a live M3 search or a
successful autonomous room-finding trial.

## Map isolation and pending data dependency

Core RGB perception, M1 ingestion and M2.4 replay do not invoke `FloorScene`
or `M1SceneBridge`, and do not read pre-annotated target locations. Scene lookup
is an independent map tool. Its `scene_match` enrichment is rejected by the
strict stream schema. Tests fail explicitly if replay tries to instantiate
either scene tool or use a network socket.

The upstream scene tests originally loaded the default
`data/maps/1f/floor_1f_experiment_scene.json`, which is missing from a clean
checkout because `/data/*` is ignored. The tests now explicitly inject
`tests/fixtures/synthetic_floor_scene.json` (SYN labels, no real geometry).
There is no production fallback or fake map under `data/`; the default scene
tool still raises on a missing file. The data owner must later repair the real
map distribution/dependency and validate the raster, scene geometry and portal
connections separately. Passing these tests does not validate a real floor map.

Integration coverage lives in `tests/test_m1_field_replay.py`; scene lookup and
bridge tests use only the isolated synthetic fixture. Existing M0/M2/M3 and
adapter regressions are retained. Run the full suite with:

```bash
PYTHONPATH=src .venv/bin/python -m unittest discover -s tests -v
```
