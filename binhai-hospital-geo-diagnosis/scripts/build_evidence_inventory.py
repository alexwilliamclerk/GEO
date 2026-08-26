from __future__ import annotations

import argparse
import csv
import difflib
import json
import re
import zipfile
from collections import Counter, defaultdict
from datetime import datetime
from pathlib import Path

from PIL import ExifTags, Image


PLATFORM_MAP = {
    "doubao": "app_doubao",
    "ds": "app_deepseek",
    "tongyi": "app_tongyi",
    "yuanbao": "app_yuanbao",
}

SOURCE_RAW_ID = {
    "doubao": "R0026",
    "ds": "R0027",
    "tongyi": "R0028",
    "yuanbao": "R0029",
}

FILENAME_RE = re.compile(
    r"^BHYY-GEO-DX-001_(doubao|ds|tongyi|yuanbao)_"
    r"(Q0[1-5]|S0[1-4]|B0[1-4])_R([0-5])_"
    r"(202608(?:24|25))(?:_\d+)?\.(jpeg|jpg|png)$",
    re.IGNORECASE,
)


def normalize(text: str) -> str:
    return "".join(
        char
        for char in text
        if char.isalnum() or "\u4e00" <= char <= "\u9fff"
    )


def partial_similarity(query: str, text: str) -> float:
    query_norm = normalize(query)
    text_norm = normalize(text)
    query_length = len(query_norm)
    region = text_norm[: min(len(text_norm), query_length + 500)]
    if query_norm in region:
        return 1.0

    best = 0.0
    window_lengths = {
        max(1, query_length - 8),
        query_length,
        min(len(region), query_length + 8),
    }
    for window_length in window_lengths:
        for start in range(0, max(1, len(region) - window_length + 1), 2):
            ratio = difflib.SequenceMatcher(
                None,
                query_norm,
                region[start : start + window_length],
                autojunk=False,
            ).ratio()
            best = max(best, ratio)
    return best


def read_queries(path: Path) -> dict[str, dict[str, str]]:
    with path.open("r", encoding="utf-8-sig", newline="") as handle:
        return {row["query_id"]: row for row in csv.DictReader(handle)}


def read_zip_times(zip_paths: dict[str, Path]) -> dict[tuple[str, str], datetime]:
    result: dict[tuple[str, str], datetime] = {}
    for platform, path in zip_paths.items():
        with zipfile.ZipFile(path) as archive:
            for entry in archive.infolist():
                if entry.is_dir():
                    continue
                result[(platform, Path(entry.filename).name)] = datetime(*entry.date_time)
    return result


def exif_datetime(image: Image.Image) -> str:
    exif = image.getexif()
    for tag_id in (36867, 36868, 306):
        if tag_id in exif:
            return str(exif[tag_id])
    return ""


def write_csv(path: Path, rows: list[dict[str, object]], fieldnames: list[str]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8-sig", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=fieldnames)
        writer.writeheader()
        writer.writerows(rows)


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--run-root", required=True, type=Path)
    parser.add_argument("--query-config", required=True, type=Path)
    parser.add_argument("--zip", action="append", default=[], help="platform=zip_path")
    args = parser.parse_args()

    run_root = args.run_root.resolve()
    query_config = args.query_config.resolve()
    zip_paths: dict[str, Path] = {}
    for item in args.zip:
        platform, raw_path = item.split("=", 1)
        zip_paths[platform] = Path(raw_path).resolve()

    queries = read_queries(query_config)
    zip_times = read_zip_times(zip_paths)
    evidence_rows: list[dict[str, object]] = []

    for image_path in sorted((run_root / "raw-images").glob("*/*")):
        if not image_path.is_file():
            continue
        match = FILENAME_RE.match(image_path.name)
        if not match:
            raise ValueError(f"Unexpected evidence filename: {image_path}")

        platform_folder, declared_query_id, round_text, filename_date, _ = match.groups()
        ocr_path = run_root / "derived-ocr" / platform_folder / image_path.with_suffix(".txt").name
        ocr_text = ocr_path.read_text(encoding="utf-8")
        declared_score = partial_similarity(queries[declared_query_id]["query_text"], ocr_text)
        candidate_scores = sorted(
            (
                partial_similarity(query["query_text"], ocr_text),
                query_id,
            )
            for query_id, query in queries.items()
        )
        best_score, best_query_id = candidate_scores[-1]
        second_score = candidate_scores[-2][0]

        if (
            best_query_id != declared_query_id
            and best_score >= 0.75
            and best_score - declared_score >= 0.08
        ):
            resolved_query_id = best_query_id
            label_status = "corrected_from_visible_prompt"
        elif declared_score >= 0.75:
            resolved_query_id = declared_query_id
            label_status = "matched"
        else:
            resolved_query_id = declared_query_id
            label_status = "needs_manual_prompt_check"

        with Image.open(image_path) as image:
            width, height = image.size
            image_format = image.format or ""
            image_exif_datetime = exif_datetime(image)

        normalized_ocr = normalize(ocr_text)
        resolved_query_length = len(normalize(queries[resolved_query_id]["query_text"]))
        answer_present = len(normalized_ocr) >= resolved_query_length + 20
        archive_time = zip_times[(platform_folder, image_path.name)]

        evidence_rows.append(
            {
                "platform": PLATFORM_MAP[platform_folder],
                "platform_folder": platform_folder,
                "source_raw_id": SOURCE_RAW_ID[platform_folder],
                "raw_filename": image_path.name,
                "declared_query_id": declared_query_id,
                "resolved_query_id": resolved_query_id,
                "query_label_status": label_status,
                "declared_question_similarity": f"{declared_score:.3f}",
                "resolved_question_similarity": f"{best_score:.3f}",
                "runner_up_similarity": f"{second_score:.3f}",
                "round": int(round_text),
                "filename_date": filename_date,
                "archive_entry_time": archive_time.isoformat(sep=" "),
                "exif_datetime": image_exif_datetime,
                "width": width,
                "height": height,
                "image_format": image_format,
                "image_bytes": image_path.stat().st_size,
                "ocr_character_count": len(normalized_ocr),
                "answer_present_estimate": int(answer_present),
                "target_mention_candidate": int("泉州滨海医院" in normalized_ocr),
                "competitor_c001_candidate": int(
                    "南安市医院" in normalized_ocr
                    or "泉州医学高等专科学校附属南安市医院" in normalized_ocr
                ),
                "competitor_c002_candidate": int(
                    "南安市中医院" in normalized_ocr
                    or "南安市第二医院" in normalized_ocr
                ),
                "evidence_path": image_path.relative_to(run_root).as_posix(),
                "ocr_path": ocr_path.relative_to(run_root).as_posix(),
            }
        )

    inventory_fields = list(evidence_rows[0].keys())
    write_csv(run_root / "evidence-inventory.csv", evidence_rows, inventory_fields)

    correction_rows = [
        {
            "platform": row["platform"],
            "round": row["round"],
            "raw_filename": row["raw_filename"],
            "declared_query_id": row["declared_query_id"],
            "resolved_query_id": row["resolved_query_id"],
            "basis": "visible prompt OCR; raw image preserved unchanged",
            "status": "proposed_metadata_correction",
        }
        for row in evidence_rows
        if row["query_label_status"] == "corrected_from_visible_prompt"
    ]
    write_csv(
        run_root / "query-label-corrections.csv",
        correction_rows,
        [
            "platform",
            "round",
            "raw_filename",
            "declared_query_id",
            "resolved_query_id",
            "basis",
            "status",
        ],
    )

    mention_rows: list[dict[str, object]] = []
    for platform in sorted(set(str(row["platform"]) for row in evidence_rows)):
        platform_rows = [row for row in evidence_rows if row["platform"] == platform]
        for query_id in ("Q01", "Q02", "Q03", "Q04", "Q05"):
            query_rows = [row for row in platform_rows if row["resolved_query_id"] == query_id]
            mention_rows.append(
                {
                    "platform": platform,
                    "scope": query_id,
                    "sample_count": len(query_rows),
                    "target_mention_candidates": sum(
                        int(row["target_mention_candidate"]) for row in query_rows
                    ),
                    "candidate_rate": (
                        f"{sum(int(row['target_mention_candidate']) for row in query_rows) / len(query_rows):.3f}"
                        if query_rows
                        else ""
                    ),
                    "status": "ocr_first_pass_requires_visual_confirmation",
                }
            )

        primary_rows = [
            row
            for row in platform_rows
            if row["resolved_query_id"] in {"Q01", "Q02", "Q03", "Q04"}
        ]
        mention_rows.append(
            {
                "platform": platform,
                "scope": "primary_Q01_Q04",
                "sample_count": len(primary_rows),
                "target_mention_candidates": sum(
                    int(row["target_mention_candidate"]) for row in primary_rows
                ),
                "candidate_rate": f"{sum(int(row['target_mention_candidate']) for row in primary_rows) / len(primary_rows):.3f}",
                "status": "ocr_first_pass_requires_visual_confirmation",
            }
        )

    write_csv(
        run_root / "first-pass-mention-summary.csv",
        mention_rows,
        [
            "platform",
            "scope",
            "sample_count",
            "target_mention_candidates",
            "candidate_rate",
            "status",
        ],
    )

    timing_rows: list[dict[str, object]] = []
    for platform in sorted(set(str(row["platform"]) for row in evidence_rows)):
        previous_start: datetime | None = None
        platform_rows = [row for row in evidence_rows if row["platform"] == platform]
        for round_number in range(1, 6):
            round_rows = [
                row
                for row in platform_rows
                if int(row["round"]) == round_number
                and str(row["resolved_query_id"]).startswith("Q")
            ]
            times = [datetime.fromisoformat(str(row["archive_entry_time"])) for row in round_rows]
            start_time = min(times)
            end_time = max(times)
            delta = (
                (start_time - previous_start).total_seconds() / 3600
                if previous_start is not None
                else None
            )
            if delta is None:
                interval_result = "first_round"
            elif delta >= 3:
                interval_result = "metadata_at_least_3h"
            else:
                interval_result = "metadata_below_3h"

            date_consistency = all(
                start_time.strftime("%Y%m%d") == str(row["filename_date"])
                or end_time.strftime("%Y%m%d") == str(row["filename_date"])
                for row in round_rows
            )
            timing_rows.append(
                {
                    "platform": platform,
                    "round": round_number,
                    "archive_start_time": start_time.isoformat(sep=" "),
                    "archive_end_time": end_time.isoformat(sep=" "),
                    "delta_from_previous_start_hours": "" if delta is None else f"{delta:.2f}",
                    "metadata_interval_result": interval_result,
                    "filename_date_consistent": int(date_consistency),
                    "evidence_quality": "archive_entry_timestamp_only_not_capture_proof",
                }
            )
            previous_start = start_time

    write_csv(
        run_root / "timing-qc.csv",
        timing_rows,
        [
            "platform",
            "round",
            "archive_start_time",
            "archive_end_time",
            "delta_from_previous_start_hours",
            "metadata_interval_result",
            "filename_date_consistent",
            "evidence_quality",
        ],
    )

    summary = {
        "measurement_config_id": "MC-BHYY-GEO-DX-001-20260824-01",
        "evidence_count": len(evidence_rows),
        "platform_counts": dict(Counter(str(row["platform"]) for row in evidence_rows)),
        "label_correction_count": len(correction_rows),
        "manual_prompt_check_count": sum(
            row["query_label_status"] == "needs_manual_prompt_check" for row in evidence_rows
        ),
        "answer_present_estimate_count": sum(
            int(row["answer_present_estimate"]) for row in evidence_rows
        ),
        "target_mention_candidate_count": sum(
            int(row["target_mention_candidate"]) for row in evidence_rows
        ),
        "notes": [
            "OCR text is derived evidence and never replaces the raw screenshot.",
            "Archive entry timestamps are not sufficient proof of capture time.",
            "Mention candidates require visual confirmation before final scoring.",
        ],
    }
    (run_root / "inventory-summary.json").write_text(
        json.dumps(summary, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )

    print(json.dumps(summary, ensure_ascii=False))


if __name__ == "__main__":
    main()
