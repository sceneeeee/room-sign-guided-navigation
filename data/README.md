# Local experiment data (not version-controlled)

Place consented photographs, video clips and annotation files here during development.
Raw images/videos and identifying information should not be committed by default.
Keep example synthetic data in `examples/`; use anonymized aggregate metrics in papers.

The live OCR UI stores each submitted frame and its raw OCR result under
`data/live_sessions/<session_id>/`, with human review in `judgments.jsonl`.
This directory is ignored by Git. Keep the session ID to reopen records.

## M1 public manifest

当前 M1 关键帧交付见 [`docs/field-tests/2026-11-07-m1-keyframes.jsonl`](../docs/field-tests/2026-11-07-m1-keyframes.jsonl) 和 [`docs/field-tests/2026-11-07-m1-data-manifest.json`](../docs/field-tests/2026-11-07-m1-data-manifest.json)。清单保留真实帧 ID、时间戳换算、原始 OCR 和标注来源；未人工确认的 bbox、房号或物理门关联必须为空或 `unknown`。公开仓库只保留脱敏标注格式、manifest、统计信息和少量无敏感示例；原始视频、现场抽帧、完整 OCR 输出和可能包含个人/学生信息的地图或照片不应提交。
