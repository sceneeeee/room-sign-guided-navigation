"""Stage reviewed field observations and selected keyframes for human approval."""

import csv
import json
from collections import Counter
from pathlib import Path
from shutil import copy2

from roomnav import field_logging

FULL_COLUMNS = (
    "sample_id", "recorded_at", "source", "condition", "room_truth",
    "raw_ocr", "verdict", "notes", "image",
)
PUBLIC_COLUMNS = (
    "sample_id", "source", "condition", "room_truth", "raw_ocr", "verdict",
)


def _write_csv(path: Path, rows: list[dict], columns: tuple[str, ...]) -> None:
    with path.open("w", encoding="utf-8-sig", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=columns, extrasaction="ignore")
        writer.writeheader()
        writer.writerows(rows)


def export_session(session_id: str, keyframe_ids: list[str], output_dir: Path) -> dict:
    source_dir = field_logging._session_dir(session_id)
    if not (source_dir / "samples.jsonl").is_file():
        raise FileNotFoundError(f"No saved samples for session {session_id}")
    samples = field_logging.list_samples(session_id)
    judgments = {}
    judgment_file = source_dir / "judgments.jsonl"
    if judgment_file.exists():
        for line in judgment_file.read_text(encoding="utf-8").splitlines():
            if line.strip():
                item = json.loads(line)
                judgments[item["sample_id"]] = item

    rows = []
    for sample in samples:
        judgment = judgments.get(sample["sample_id"], {})
        rows.append({
            "sample_id": sample["sample_id"],
            "recorded_at": sample["recorded_at"],
            "source": sample["source"],
            "condition": sample.get("condition", ""),
            "room_truth": judgment.get("room_truth", ""),
            "raw_ocr": sample["raw_ocr"],
            "verdict": judgment.get("verdict", ""),
            "notes": judgment.get("notes", ""),
            "image": sample["image"],
        })

    sample_ids = {row["sample_id"] for row in rows}
    selected = list(dict.fromkeys(keyframe_ids))
    for sample_id in selected:
        if sample_id not in sample_ids:
            raise ValueError(f"Unknown keyframe sample ID: {sample_id}")
        if sample_id not in judgments:
            raise ValueError(f"Review keyframe sample {sample_id} in the UI before exporting it")

    output_dir = Path(output_dir)
    output_dir.mkdir(parents=True, exist_ok=False)
    _write_csv(output_dir / "review_full.csv", rows, FULL_COLUMNS)
    _write_csv(output_dir / "review_public_candidate.csv", [r for r in rows if r["verdict"]], PUBLIC_COLUMNS)

    keyframe_dir = output_dir / "keyframes"
    keyframe_dir.mkdir()
    for sample_id in selected:
        sample = field_logging.get_sample(session_id, sample_id)
        copy2(sample["image_path"], keyframe_dir / f"frame_{sample_id}.jpg")

    counts = Counter(row["verdict"] for row in rows)
    condition_counts = Counter(row["condition"] or "未填写条件" for row in rows)
    eligible = [row for row in rows if row["room_truth"] and row["verdict"] in {"exact", "wrong", "no_text"}]
    exact = sum(row["verdict"] == "exact" for row in eligible)
    condition_summary = "".join(f"- {condition}：{count} 帧\n" for condition, count in condition_counts.items())
    summary = (
        f"# 现场会话 {session_id}\n\n"
        f"- 保存样本：{len(rows)}\n"
        f"- 已人工判断：{len(rows) - counts['']}；未判断：{counts['']}\n"
        f"- 人工判断分布：准确 {counts['exact']}、错误 {counts['wrong']}、无文字 {counts['no_text']}、不确定 {counts['uncertain']}\n"
        f"- 有实物真值且可计入的完整编号 Exact Match：{exact}/{len(eligible)}\n"
        f"- 已选关键帧：{', '.join(selected) if selected else '无'}\n\n"
        "## 按试次记录的帧数\n\n"
        f"{condition_summary}\n"
        "以上为待审阅导出；公开前检查 CSV 文字、每张图片及现场采集许可。"
        "未判断样本和不确定样本不计入 Exact Match 分母。\n"
    )
    (output_dir / "summary.md").write_text(summary, encoding="utf-8")
    return {"samples": len(rows), "reviewed": len(rows) - counts[""], "keyframes": len(selected), "output_dir": str(output_dir)}
