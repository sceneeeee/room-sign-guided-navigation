# Local experiment data (not version-controlled)

Place consented photographs, video clips and annotation files here during development.
Raw images/videos and identifying information should not be committed by default.
Keep example synthetic data in `examples/`; use anonymized aggregate metrics in papers.

The live OCR UI stores each submitted frame and its raw OCR result under
`data/live_sessions/<session_id>/`, with human review in `judgments.jsonl`.
This directory is ignored by Git. Keep the session ID to reopen records.
