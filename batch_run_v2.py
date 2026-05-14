#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
批量跑构建测试 - 增强版

特性：
1. 每个包跑完删源码（节约磁盘）
2. 进度展示
3. 单包超时控制
4. 断点续跑（自动跳过已在CSV里的包）
5. 跑完汇总统计
"""

import csv
import subprocess
import time
import sys
import shutil
from pathlib import Path
from datetime import datetime

ROOT = Path(__file__).resolve().parent
CSV_FILE = ROOT / "data" / "packages.csv"
AUTO_ONE = ROOT / "auto_one.sh"
SOURCES_DIR = ROOT / "sources"
RESULTS_DIR = ROOT / "results"
BATCH_LOG_DIR = ROOT / "logs" / "batch"

PER_PACKAGE_TIMEOUT = 15 * 60   # 15分钟

def load_processed_packages():
    processed = set()
    for csv_name in ["suc.csv", "fail.csv", "other.csv"]:
        path = RESULTS_DIR / csv_name
        if not path.exists():
            continue
        try:
            with path.open("r", encoding="utf-8-sig") as f:
                reader = csv.DictReader(f)
                for row in reader:
                    pkg = (row.get("package") or "").strip()
                    if pkg:
                        processed.add(pkg)
        except Exception as e:
            print(f"[警告] 读取 {path} 失败: {e}")
    return processed

def cleanup_source(package):
    src = SOURCES_DIR / package
    if src.exists():
        try:
            shutil.rmtree(src)
            return True
        except Exception as e:
            print(f"[警告] 清理 {src} 失败: {e}")
    return False

def format_duration(seconds):
    if seconds < 60:
        return f"{seconds:.0f}秒"
    return f"{seconds/60:.1f}分"

def print_summary():
    print()
    print("=" * 70)
    print("批量运行结束统计")
    print("=" * 70)

    counts = {}
    fail_categories = {}

    for csv_name, label in [("suc.csv", "成功"), ("fail.csv", "失败"), ("other.csv", "其他")]:
        path = RESULTS_DIR / csv_name
        if not path.exists():
            counts[label] = 0
            continue
        try:
            with path.open("r", encoding="utf-8-sig") as f:
                rows = list(csv.DictReader(f))
            counts[label] = len(rows)
            if csv_name == "fail.csv":
                for row in rows:
                    key = (row.get("main_category", "未分类"), row.get("sub_category", "-"))
                    fail_categories[key] = fail_categories.get(key, 0) + 1
        except Exception:
            counts[label] = 0

    total = sum(counts.values())
    print(f"总处理包数: {total}")
    for label, n in counts.items():
        pct = (n / total * 100) if total else 0
        print(f"  {label}: {n} ({pct:.1f}%)")

    if fail_categories:
        print()
        print("失败原因分布（按主分类/子分类）:")
        sorted_cats = sorted(fail_categories.items(), key=lambda x: -x[1])
        for (main, sub), n in sorted_cats:
            print(f"  [{n:>3}] {main} / {sub}")

    print("=" * 70)

def main():
    if not CSV_FILE.exists():
        print(f"找不到输入文件: {CSV_FILE}")
        sys.exit(1)

    BATCH_LOG_DIR.mkdir(parents=True, exist_ok=True)
    batch_log = BATCH_LOG_DIR / f"batch_{datetime.now().strftime('%Y%m%d_%H%M%S')}.log"

    with CSV_FILE.open("r", encoding="utf-8-sig") as f:
        rows = [row for row in csv.DictReader(f)
                if (row.get("package") or "").strip() and (row.get("repo") or "").strip()]

    processed = load_processed_packages()
    total = len(rows)
    todo = [row for row in rows if row["package"].strip() not in processed]
    skip_count = total - len(todo)

    print(f"========== 批量构建测试 ==========")
    print(f"包列表总数: {total}")
    print(f"已处理 (跳过): {skip_count}")
    print(f"待处理: {len(todo)}")
    print(f"单包超时: {PER_PACKAGE_TIMEOUT//60} 分钟")
    print(f"批次日志: {batch_log}")
    print()

    if not todo:
        print("所有包都已处理过。如果想全部重跑，请清空 results/*.csv")
        print_summary()
        return

    start_time = time.time()

    with batch_log.open("w", encoding="utf-8") as logf:
        for idx, row in enumerate(todo, 1):
            package = row["package"].strip()
            repo = row["repo"].strip()
            pkg_start = time.time()

            header = f"\n[{idx}/{len(todo)}] {package}"
            print(header)
            print(f"          {repo}")
            logf.write(header + "\n")
            logf.flush()

            try:
                result = subprocess.run(
                    [str(AUTO_ONE), package, repo],
                    timeout=PER_PACKAGE_TIMEOUT,
                    stdout=subprocess.PIPE,
                    stderr=subprocess.STDOUT,
                    text=True,
                    errors="ignore",
                )
                logf.write(result.stdout or "")
                logf.write(f"\n[返回码 {result.returncode}]\n")
                logf.flush()

                out = result.stdout or ""
                if "构建测试全部成功" in out:
                    status = "成功"
                elif "[失败]" in out and "阶段失败" in out:
                    status = "失败"
                elif "[不构建]" in out:
                    status = "其他/不支持"
                elif result.returncode == 0:
                    status = "成功"
                else:
                    status = "失败"

            except subprocess.TimeoutExpired:
                status = "超时被强制终止"
                logf.write(f"\n[超时 {PER_PACKAGE_TIMEOUT} 秒被终止]\n")
                logf.flush()
            except Exception as e:
                status = f"异常: {e}"
                logf.write(f"\n[异常 {e}]\n")
                logf.flush()

            pkg_elapsed = time.time() - pkg_start
            print(f"   -> {status}  (耗时 {format_duration(pkg_elapsed)})")

            if cleanup_source(package):
                print(f"   已清理 sources/{package}")

    total_elapsed = time.time() - start_time
    print()
    print(f"全部完成，总耗时 {format_duration(total_elapsed)}")
    print_summary()

if __name__ == "__main__":
    main()
