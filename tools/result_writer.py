#!/usr/bin/env python3
# -*- coding: utf-8 -*-

import argparse
import csv
import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
RESULTS_DIR = ROOT / "results"

SUC_FIELDS = [
    "package",
    "type",
]

FAIL_FIELDS = [
    "package",
    "type",
    "failed_stage",
    "main_category",
    "sub_category",
    "owner_side",
    "action",
    "rule_name",
    "error_excerpt",
    "error_hit_count",
    "matched_patterns",
]

OTHER_FIELDS = [
    "package",
    "type",
    "reason",
]

def read_json(path: str) -> dict:
    if not path:
        return {}

    p = Path(path)
    if not p.exists():
        return {}

    try:
        return json.loads(p.read_text(encoding="utf-8", errors="ignore"))
    except Exception:
        return {}

def read_text(path: str, limit: int = 3000) -> str:
    if not path:
        return ""

    p = Path(path)
    if not p.exists():
        return ""

    text = p.read_text(encoding="utf-8", errors="ignore")
    text = text.replace("\r", " ").replace("\n", " \\n ")
    text = " ".join(text.split())

    if len(text) > limit:
        text = text[:limit] + " ..."

    return text

def remove_package_from_csv(csv_path: Path, package: str, fields: list):
    if not csv_path.exists():
        return

    try:
        with csv_path.open("r", encoding="utf-8-sig", newline="") as f:
            reader = csv.DictReader(f)
            rows = [row for row in reader if row.get("package") != package]
    except Exception:
        rows = []

    with csv_path.open("w", encoding="utf-8-sig", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=fields)
        writer.writeheader()

        for row in rows:
            writer.writerow({field: row.get(field, "") for field in fields})

def remove_package_from_all(package: str):
    RESULTS_DIR.mkdir(parents=True, exist_ok=True)

    remove_package_from_csv(RESULTS_DIR / "suc.csv", package, SUC_FIELDS)
    remove_package_from_csv(RESULTS_DIR / "fail.csv", package, FAIL_FIELDS)
    remove_package_from_csv(RESULTS_DIR / "other.csv", package, OTHER_FIELDS)

def append_row(csv_path: Path, fields: list, row: dict):
    RESULTS_DIR.mkdir(parents=True, exist_ok=True)

    exists = csv_path.exists() and csv_path.stat().st_size > 0

    with csv_path.open("a", encoding="utf-8-sig", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=fields)

        if not exists:
            writer.writeheader()

        writer.writerow({field: row.get(field, "") for field in fields})

def write_success(args):
    row = {
        "package": args.package,
        "type": args.type,
    }

    remove_package_from_all(args.package)
    append_row(RESULTS_DIR / "suc.csv", SUC_FIELDS, row)

def write_other(args):
    row = {
        "package": args.package,
        "type": args.type,
        "reason": args.reason,
    }

    remove_package_from_all(args.package)
    append_row(RESULTS_DIR / "other.csv", OTHER_FIELDS, row)

def write_fail(args):
    summary = read_json(args.summary_file)

    error_file = args.error_excerpt_file or summary.get("error_excerpt_file", "")
    error_excerpt = read_text(error_file)

    matched_patterns = summary.get("matched_patterns", "")
    if isinstance(matched_patterns, list):
        matched_patterns = " ; ".join(matched_patterns)

    package = args.package or summary.get("package", "")

    row = {
        "package": package,
        "type": args.type,
        "failed_stage": args.stage or summary.get("stage", ""),
        "main_category": summary.get("main_category", ""),
        "sub_category": summary.get("sub_category", ""),
        "owner_side": summary.get("owner_side", ""),
        "action": summary.get("action", ""),
        "rule_name": summary.get("rule_name", ""),
        "error_excerpt": error_excerpt,
        "error_hit_count": summary.get("error_hit_count", ""),
        "matched_patterns": matched_patterns,
    }

    remove_package_from_all(package)
    append_row(RESULTS_DIR / "fail.csv", FAIL_FIELDS, row)

def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--status", required=True, choices=["suc", "fail", "other"])
    parser.add_argument("--package", required=True)
    parser.add_argument("--type", default="")
    parser.add_argument("--stage", default="")
    parser.add_argument("--reason", default="")
    parser.add_argument("--summary-file", default="")
    parser.add_argument("--error-excerpt-file", default="")
    args = parser.parse_args()

    RESULTS_DIR.mkdir(parents=True, exist_ok=True)

    if args.status == "suc":
        write_success(args)
    elif args.status == "fail":
        write_fail(args)
    elif args.status == "other":
        write_other(args)

    print(f"[结果写入完成] {args.status}: {args.package}")

if __name__ == "__main__":
    main()

