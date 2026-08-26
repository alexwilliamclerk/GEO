from __future__ import annotations

import argparse
import csv
import zipfile
from pathlib import Path


def read_csv(path: Path) -> list[dict[str, str]]:
    with path.open("r", encoding="utf-8-sig", newline="") as handle:
        return list(csv.DictReader(handle))


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--run-root", required=True, type=Path)
    parser.add_argument("--task", required=True, type=Path)
    parser.add_argument("--output-csv", required=True, type=Path)
    parser.add_argument("--output-zip", required=True, type=Path)
    args = parser.parse_args()

    run_root = args.run_root.resolve()
    task_path = args.task.resolve()
    output_csv = args.output_csv.resolve()
    output_zip = args.output_zip.resolve()

    scored = [
        row
        for row in read_csv(run_root / "samples-scored.csv")
        if row["review_sample_id"]
    ]
    scored.sort(key=lambda row: int(row["review_sample_id"].replace("RV", "")))
    if len(scored) != 20:
        raise ValueError(f"Expected 20 frozen review rows, got {len(scored)}")

    review_rows: list[dict[str, str]] = []
    for row in scored:
        review_rows.append(
            {
                "review_sample_id": row["review_sample_id"],
                "sample_id": row["sample_id"],
                "platform": row["platform"],
                "round": row["round"],
                "query_id": row["query_id"],
                "raw_filename": row["raw_filename"],
                "w_target_mention": row["target_mention"],
                "w_recommendation_level": row["target_recommendation_level"],
                "w_competitor_c001": row["competitor_c001_mention"],
                "w_competitor_c002": row["competitor_c002_mention"],
                "w_urgent_guidance": row["urgent_guidance"],
                "w_diagnosis_boundary": row["diagnosis_boundary"],
                "review_result": "",
                "review_target_mention": "",
                "review_recommendation_level": "",
                "review_competitor_c001": "",
                "review_competitor_c002": "",
                "review_urgent_guidance": "",
                "review_diagnosis_boundary": "",
                "review_reason": "",
            }
        )

    output_csv.parent.mkdir(parents=True, exist_ok=True)
    with output_csv.open("w", encoding="utf-8-sig", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=list(review_rows[0].keys()))
        writer.writeheader()
        writer.writerows(review_rows)

    output_zip.parent.mkdir(parents=True, exist_ok=True)
    with zipfile.ZipFile(output_zip, "w", compression=zipfile.ZIP_DEFLATED) as archive:
        archive.write(task_path, task_path.name)
        archive.write(output_csv, output_csv.name)
        archive.write(run_root / "query-label-corrections.csv", "参考资料/query-label-corrections.csv")
        for row in scored:
            image_path = run_root / row["evidence_path"]
            archive.write(
                image_path,
                f"20条Core原始截图/{row['review_sample_id']}_{image_path.name}",
            )

    print(f"rows={len(review_rows)} zip={output_zip}")


if __name__ == "__main__":
    main()
