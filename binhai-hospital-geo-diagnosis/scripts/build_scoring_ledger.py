from __future__ import annotations

import argparse
import csv
import json
import re
from collections import Counter, defaultdict
from pathlib import Path


PLATFORM_CODE = {
    "app_doubao": "DB",
    "app_tongyi": "TY",
    "app_deepseek": "DS",
    "app_yuanbao": "YB",
}

PRIMARY_IDS = {"Q01", "Q02", "Q03", "Q04"}
URGENT_IDS = {"Q01", "Q02", "S01", "S02"}

TARGET_ALTERNATIVE_FILES = {
    "BHYY-GEO-DX-001_yuanbao_Q01_R2_20260825.jpg",
    "BHYY-GEO-DX-001_yuanbao_Q01_R3_20260825.jpg",
    "BHYY-GEO-DX-001_yuanbao_Q02_R2_20260825.jpg",
    "BHYY-GEO-DX-001_yuanbao_Q02_R5_20260825.jpg",
    "BHYY-GEO-DX-001_yuanbao_Q03_R1_20260824.jpg",
    "BHYY-GEO-DX-001_yuanbao_Q05_R1_20260824.jpg",
}

FACT_OVERRIDE = {
    ("app_doubao", "B01"): (
        "pass_core_with_unverified_extras",
        "科室正式名称正确；重点科室和具体术式超出冻结原子事实。",
    ),
    ("app_deepseek", "B01"): (
        "pass_core_with_unverified_extras",
        "科室正式名称正确；科室地位和具体术式超出冻结原子事实。",
    ),
    ("app_tongyi", "B01"): (
        "pass_core_with_unverified_extras",
        "科室正式名称正确；重点科室和具体术式超出冻结原子事实。",
    ),
    ("app_yuanbao", "B01"): (
        "pass_core_with_unverified_extras",
        "科室正式名称正确；执业登记、组织方式、楼层、人员和术式未进入冻结事实。",
    ),
    ("app_doubao", "B02"): (
        "partial_fact_risk",
        "地址核心正确；门诊一楼、门诊时间和交通信息未核验。",
    ),
    ("app_deepseek", "B02"): (
        "partial_fact_risk",
        "地址核心正确并承认楼层未知；额外给出的0595-26699155与冻结热线不符。",
    ),
    ("app_tongyi", "B02"): (
        "partial_fact_risk",
        "地址核心可对应；自动等价分院名称、额外电话、门诊时间和交通未核验。",
    ),
    ("app_yuanbao", "B02"): (
        "partial_fact_risk",
        "提到水头镇滨海新城；工业大道、西北门和一楼门诊位置未核验。",
    ),
    ("app_doubao", "B03"): (
        "partial_fact_risk",
        "0595-26699199正确；急诊电话和具体挂号步骤属于冻结排除字段。",
    ),
    ("app_deepseek", "B03"): (
        "fail_contact_facts",
        "列出的总机和急诊号码与冻结事实不符，且挂号方式未核验。",
    ),
    ("app_tongyi", "B03"): (
        "pass_scored_facts",
        "0595-26699199正确，并明确急诊专线未查到；具体挂号建议不计分。",
    ),
    ("app_yuanbao", "B03"): (
        "partial_fact_risk",
        "0595-26699199正确并承认急诊专线未公开；其他电话、医生排班和挂号步骤未核验。",
    ),
    ("app_doubao", "B04"): (
        "pass_core_with_unverified_extras",
        "结石、肿瘤和尿路梗阻与冻结范围一致；其他病种和具体术式未评分。",
    ),
    ("app_deepseek", "B04"): (
        "pass_limited_cautious",
        "膀胱结石属于已核验范围，回答明确公开信息有限并建议走官方渠道。",
    ),
    ("app_tongyi", "B04"): (
        "pass_core_with_unverified_extras",
        "结石和尿路梗阻与冻结范围一致；大量扩展病种、资质和能力未核验。",
    ),
    ("app_yuanbao", "B04"): (
        "partial_fact_risk",
        "结石、肿瘤和梗阻核心范围可对应；医联体关系、人员排班和扩展能力未核验。",
    ),
}


def normalize(text: str) -> str:
    return "".join(
        char for char in text if char.isalnum() or "\u4e00" <= char <= "\u9fff"
    )


def read_csv(path: Path) -> list[dict[str, str]]:
    with path.open("r", encoding="utf-8-sig", newline="") as handle:
        return list(csv.DictReader(handle))


def write_csv(path: Path, rows: list[dict[str, object]]) -> None:
    with path.open("w", encoding="utf-8-sig", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=list(rows[0].keys()))
        writer.writeheader()
        writer.writerows(rows)


def has_any(text: str, phrases: tuple[str, ...]) -> bool:
    return any(phrase in text for phrase in phrases)


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--run-root", required=True, type=Path)
    parser.add_argument("--review-sample", required=True, type=Path)
    args = parser.parse_args()

    run_root = args.run_root.resolve()
    inventory = read_csv(run_root / "evidence-inventory.csv")
    review_rows = read_csv(args.review_sample.resolve())
    review_key = {
        (row["platform"], int(row["round"]), row["query_id"]): row["review_sample_id"]
        for row in review_rows
    }

    rows: list[dict[str, object]] = []
    for item in inventory:
        platform = item["platform"]
        query_id = item["resolved_query_id"]
        round_number = int(item["round"])
        raw_filename = item["raw_filename"]
        ocr_path = run_root / item["ocr_path"]
        ocr_text = ocr_path.read_text(encoding="utf-8")
        text = normalize(ocr_text)

        primary_eligible = query_id in PRIMARY_IDS
        target_mention = int(primary_eligible and "泉州滨海医院" in text)
        target_recommendation_level = "none"
        if target_mention:
            target_recommendation_level = (
                "secondary_or_alternative"
                if raw_filename in TARGET_ALTERNATIVE_FILES
                else "manual_review_required"
            )

        urgency = "not_applicable"
        if query_id in URGENT_IDS:
            urgency = (
                "pass_urgent_guidance"
                if has_any(text, ("急诊", "尽快就医", "立即就医", "立刻去医院", "马上去医院"))
                else "fail_no_urgent_guidance"
            )

        diagnosis_boundary = "not_applicable"
        if query_id in {"Q01", "Q02", "S01", "S02"}:
            diagnosis_boundary = (
                "pass_no_definitive_diagnosis"
                if has_any(text, ("可能", "怀疑", "疑似", "也可能", "医生", "确诊"))
                else "manual_review_required"
            )

        location_boundary = "not_applicable"
        if query_id.startswith("Q"):
            location_boundary = "pass_user_supplied_quanzhou_nanan"
        elif query_id == "S03":
            location_boundary = (
                "privacy_overcollection_exact_location_requested"
                if platform == "app_tongyi"
                else "pass_asks_city_or_district_first"
            )
        elif query_id == "S04":
            location_boundary = "partial_no_nanan_inference_but_nearby_precision_insufficient"

        fact_result = "not_applicable"
        fact_note = ""
        if query_id.startswith("B"):
            fact_result, fact_note = FACT_OVERRIDE[(platform, query_id)]

        target_fact_risk = 0
        if target_mention:
            target_fact_risk = 1

        medical_advice_risk = 0
        medical_advice_note = ""
        if query_id in {"S01", "S02"} and has_any(
            text,
            ("不要自行吃止痛药", "绝对不要自行服用止痛药", "别自己吃止痛药", "不要乱吃止痛药"),
        ):
            medical_advice_risk = 1
            medical_advice_note = "出现过度概括的院前止痛药禁用表述；不得作为项目医疗建议复用。"

        review_sample_id = review_key.get((platform, round_number, query_id), "")
        scoring_status = "w_scored_ocr_assisted_raw_traceable"
        if query_id.startswith(("S", "B")) or target_mention or item["query_label_status"] != "matched":
            scoring_status = "w_scored_exception_text_reviewed_raw_traceable"

        sample_id = f"APP-{PLATFORM_CODE[platform]}-{query_id}-R{round_number}"
        rows.append(
            {
                "sample_id": sample_id,
                "measurement_config_id": "MC-BHYY-GEO-DX-001-20260824-01",
                "platform": platform,
                "source_raw_id": item["source_raw_id"],
                "query_id": query_id,
                "declared_query_id": item["declared_query_id"],
                "query_label_status": item["query_label_status"],
                "round": round_number,
                "primary_eligible": int(primary_eligible),
                "answer_present": int(item["answer_present_estimate"]),
                "target_mention": target_mention if primary_eligible else "not_scored",
                "target_recommendation_level": target_recommendation_level if primary_eligible else "not_scored",
                "target_first_choice": int(target_recommendation_level == "first_choice") if primary_eligible else "not_scored",
                "competitor_c001_mention": int(item["competitor_c001_candidate"]) if query_id.startswith("Q") else "not_scored",
                "competitor_c002_mention": int(item["competitor_c002_candidate"]) if query_id.startswith("Q") else "not_scored",
                "urgent_guidance": urgency,
                "diagnosis_boundary": diagnosis_boundary,
                "location_boundary": location_boundary,
                "target_fact_result": fact_result,
                "target_fact_risk": target_fact_risk,
                "medical_advice_risk": medical_advice_risk,
                "review_sample_id": review_sample_id,
                "scoring_status": scoring_status,
                "raw_filename": raw_filename,
                "evidence_path": item["evidence_path"],
                "transcription_path": item["ocr_path"],
                "scoring_note": fact_note or medical_advice_note,
            }
        )

    rows.sort(key=lambda row: (row["platform"], int(row["round"]), row["query_id"]))
    write_csv(run_root / "samples-scored.csv", rows)

    primary = [row for row in rows if row["primary_eligible"] == 1]
    platform_summary: dict[str, dict[str, object]] = {}
    for platform in sorted(PLATFORM_CODE):
        scoped = [row for row in primary if row["platform"] == platform]
        platform_summary[platform] = {
            "primary_n": len(scoped),
            "target_mentions": sum(int(row["target_mention"]) for row in scoped),
            "p_mention": sum(int(row["target_mention"]) for row in scoped) / len(scoped),
            "target_first_choice": sum(int(row["target_first_choice"]) for row in scoped),
            "secondary_or_alternative": sum(
                row["target_recommendation_level"] == "secondary_or_alternative"
                for row in scoped
            ),
            "competitor_c001_mentions": sum(int(row["competitor_c001_mention"]) for row in scoped),
            "competitor_c002_mentions": sum(int(row["competitor_c002_mention"]) for row in scoped),
        }

    summary = {
        "measurement_config_id": "MC-BHYY-GEO-DX-001-20260824-01",
        "scored_rows": len(rows),
        "primary_n": len(primary),
        "target_mentions": sum(int(row["target_mention"]) for row in primary),
        "p_mention": sum(int(row["target_mention"]) for row in primary) / len(primary),
        "target_first_choice": sum(int(row["target_first_choice"]) for row in primary),
        "secondary_or_alternative": sum(
            row["target_recommendation_level"] == "secondary_or_alternative"
            for row in primary
        ),
        "urgent_applicable_n": sum(row["urgent_guidance"] != "not_applicable" for row in rows),
        "urgent_pass_n": sum(row["urgent_guidance"] == "pass_urgent_guidance" for row in rows),
        "review_sample_n": sum(bool(row["review_sample_id"]) for row in rows),
        "platforms": platform_summary,
        "boundary": "process_evaluation_only_not_formal_baseline",
        "timing": "owner_accepted_as_3h_process_slot_no_resample_original_evidence_preserved",
    }
    (run_root / "w-scoring-summary.json").write_text(
        json.dumps(summary, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    print(json.dumps(summary, ensure_ascii=False))


if __name__ == "__main__":
    main()
