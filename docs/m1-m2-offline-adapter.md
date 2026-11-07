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

`roomnav.m1_pipeline.M1Session` provides the M1-side replay boundary: it
keeps rejected OCR records for review, accepts complete room identifiers,
and deduplicates only the explicit `session_id:frame_id:region_id` capture
identity. It does not merge equal strings from different physical doors.
The current tests are **synthetic** and validate the contract only; they do
not count as a real-corridor M1 result. Connect to the annotated field frames
once Issue #2 data are available.
