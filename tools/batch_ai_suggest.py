#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
批量对 fail.csv 里所有失败包调用 AI 生成修复建议
跳过：
  - clone 阶段失败（按用户要求不重跑）
  - 已经在 ai_suggestions.csv 里的（断点续跑）
输出: results/ai_suggestions.csv
"""

import csv
import json
import sys
import time
from pathlib import Path

# 导入同目录的 ai_helper
sys.path.insert(0, str(Path(__file__).resolve().parent))
import ai_helper

ROOT = Path(__file__).resolve().parents[1]
RESULTS_DIR = ROOT / "results"
FAIL_CSV = RESULTS_DIR / "fail.csv"
OUT_CSV = RESULTS_DIR / "ai_suggestions.csv"

FIELDS = [
    "package",
    "failed_stage",
    "main_category",
    "sub_category",
    "diagnosis",
    "fix_type",
    "apt_packages",
    "command",
    "confidence",
    "notes",
]


def load_existing():
    """加载已经有建议的包，支持断点续跑"""
    done = set()
    if OUT_CSV.exists():
        with OUT_CSV.open("r", encoding="utf-8-sig") as f:
            for row in csv.DictReader(f):
                pkg = row.get("package", "").strip()
                if pkg:
                    done.add(pkg)
    return done


def write_row(row: dict):
    """追加一行到输出CSV"""
    exists = OUT_CSV.exists() and OUT_CSV.stat().st_size > 0
    with OUT_CSV.open("a", encoding="utf-8-sig", newline="") as f:
        w = csv.DictWriter(f, fieldnames=FIELDS)
        if not exists:
            w.writeheader()
        # 列表转字符串
        out = dict(row)
        if isinstance(out.get("apt_packages"), list):
            out["apt_packages"] = ";".join(out["apt_packages"])
        w.writerow({k: out.get(k, "") for k in FIELDS})


def main():
    if not FAIL_CSV.exists():
        print(f"找不到 {FAIL_CSV}", file=sys.stderr)
        sys.exit(1)

    with FAIL_CSV.open("r", encoding="utf-8-sig") as f:
        all_fails = list(csv.DictReader(f))

    # 过滤：跳过 clone 失败的包（按用户要求）
    targets = [r for r in all_fails if r.get("failed_stage") != "clone"]
    skipped_clone = len(all_fails) - len(targets)

    # 跳过已有建议的
    done = load_existing()
    todo = [r for r in targets if r.get("package", "").strip() not in done]
    skipped_done = len(targets) - len(todo)

    print(f"========== 批量 AI 建议 ==========")
    print(f"fail.csv 总数: {len(all_fails)}")
    print(f"  跳过 clone 失败: {skipped_clone}")
    print(f"  跳过已有建议: {skipped_done}")
    print(f"  待处理: {len(todo)}")
    print()

    if not todo:
        print("所有包都已有建议。如需重生成请删除 ai_suggestions.csv")
        return

    start = time.time()
    success = 0
    fail = 0

    for i, row in enumerate(todo, 1):
        pkg = row.get("package", "").strip()
        stage = row.get("failed_stage", "")
        cat = row.get("main_category", "")

        print(f"[{i}/{len(todo)}] {pkg}  ({stage} - {cat})")

        try:
            user_msg = ai_helper.build_user_message(pkg, row)
            raw = ai_helper.call_deepseek(user_msg)
            sug = ai_helper.parse_ai_response(raw)

            write_row({
                "package": pkg,
                "failed_stage": stage,
                "main_category": cat,
                "sub_category": row.get("sub_category", ""),
                **sug,
            })

            success += 1
            diag = sug.get("diagnosis", "")[:40]
            ft = sug.get("fix_type", "")
            cmd = sug.get("command", "")[:60]
            print(f"   → {diag}")
            print(f"   → {ft}: {cmd}")
        except Exception as e:
            fail += 1
            print(f"   [失败] {e}")
            write_row({
                "package": pkg,
                "failed_stage": stage,
                "main_category": cat,
                "sub_category": row.get("sub_category", ""),
                "diagnosis": f"AI调用失败: {e}",
                "fix_type": "error",
                "apt_packages": "",
                "command": "",
                "confidence": "low",
                "notes": "",
            })

        # 简单限速：每次调用之间停0.5秒，避免API打满
        time.sleep(0.5)

    elapsed = time.time() - start
    print()
    print("=" * 50)
    print(f"完成! 成功 {success} / 失败 {fail}，耗时 {elapsed:.0f} 秒")
    print(f"结果保存在: {OUT_CSV}")
    print("=" * 50)


if __name__ == "__main__":
    main()
