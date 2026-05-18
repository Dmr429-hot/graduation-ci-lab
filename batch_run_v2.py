#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
批量跑构建测试 - 带 AI 闭环修复版

特性：
1. 每个包跑完删源码（节约磁盘）
2. 进度展示
3. 单包超时控制
4. 断点续跑（自动跳过已在CSV里的包）
5. AI 闭环修复：依赖缺失类的失败自动调AI拿建议、装包、重跑（最多3轮）
6. 跑完汇总统计
"""

import csv
import json
import os
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
TOOLS_DIR = ROOT / "tools"
AI_HELPER = TOOLS_DIR / "ai_helper.py"
RESULT_WRITER = TOOLS_DIR / "result_writer.py"
ANALYZE = TOOLS_DIR / "analyze_ci_log.py"
BUILD_DRIVER = TOOLS_DIR / "build_driver.py"

PER_PACKAGE_TIMEOUT = 15 * 60      # 15分钟
MAX_AI_ROUNDS = 3                  # AI 最多重试轮数
AI_LOOP_ENABLED = True             # 是否启用AI闭环（出问题可关掉）


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


def read_fail_record(package):
    """从 fail.csv 读最新的失败记录（用于AI输入）"""
    fail_csv = RESULTS_DIR / "fail.csv"
    if not fail_csv.exists():
        return None
    try:
        with fail_csv.open(encoding="utf-8-sig") as f:
            for row in csv.DictReader(f):
                if row.get("package") == package:
                    return row
    except Exception:
        pass
    return None


def call_ai_for_suggestion(package, logf):
    """调 ai_helper.py 拿建议，返回 dict 或 None"""
    if not AI_HELPER.exists():
        logf.write(f"[AI闭环] ai_helper.py 不存在，跳过\n")
        return None
    try:
        result = subprocess.run(
            ["python3", str(AI_HELPER), "--package", package, "--json"],
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            timeout=60,
        )
        if result.returncode != 0:
            logf.write(f"[AI闭环] ai_helper 退出非零: {result.stderr}\n")
            return None
        return json.loads(result.stdout)
    except subprocess.TimeoutExpired:
        logf.write(f"[AI闭环] ai_helper 超时\n")
        return None
    except json.JSONDecodeError as e:
        logf.write(f"[AI闭环] AI 返回非 JSON: {e}\n")
        return None
    except Exception as e:
        logf.write(f"[AI闭环] 调用 AI 异常: {e}\n")
        return None


def install_packages(apt_packages, logf):
    """装包。本地批量跑时用 sudo（你的VM有sudo）"""
    if not apt_packages:
        return False, "no packages to install"
    cmd = ["sudo", "apt", "install", "-y"] + apt_packages
    logf.write(f"[AI闭环] 执行: {' '.join(cmd)}\n")
    try:
        result = subprocess.run(
            cmd,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
            timeout=10*60,
        )
        logf.write(result.stdout)
        if result.returncode != 0:
            return False, f"apt failed (rc={result.returncode})"
        return True, "ok"
    except subprocess.TimeoutExpired:
        return False, "apt timeout"
    except Exception as e:
        return False, str(e)


def rerun_full_build(package, repo, logf):
    """重新跑完整构建（clone + 构建）。返回 (success: bool, full_output: str)"""
    cleanup_source(package)
    try:
        result = subprocess.run(
            [str(AUTO_ONE), package, repo],
            timeout=PER_PACKAGE_TIMEOUT,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
            errors="ignore",
        )
        out = result.stdout or ""
        logf.write(out)
        logf.write(f"\n[返回码 {result.returncode}]\n")
        logf.flush()
        success = "构建测试全部成功" in out
        return success, out
    except subprocess.TimeoutExpired:
        logf.write(f"\n[超时 {PER_PACKAGE_TIMEOUT} 秒被终止]\n")
        return False, "timeout"
    except Exception as e:
        logf.write(f"\n[异常 {e}]\n")
        return False, str(e)


def update_csv_with_ai_info(package, ai_rounds, ai_packages, ai_status):
    """给 suc.csv 或 fail.csv 里这个包补 AI 相关字段"""
    for csv_name, base_fields in [
        ("suc.csv",   ["package", "type"]),
        ("fail.csv",  None),  # fail的字段动态读
    ]:
        csv_path = RESULTS_DIR / csv_name
        if not csv_path.exists():
            continue
        try:
            with csv_path.open(encoding="utf-8-sig") as f:
                rows = list(csv.DictReader(f))
                if not rows:
                    continue
                fields = list(rows[0].keys())

            # 确保有 AI 三列
            for col in ["ai_rounds_used", "ai_packages_installed", "ai_final_status"]:
                if col not in fields:
                    fields.append(col)

            updated = False
            for row in rows:
                if row.get("package") == package:
                    row["ai_rounds_used"] = str(ai_rounds)
                    row["ai_packages_installed"] = ";".join(ai_packages) if ai_packages else ""
                    row["ai_final_status"] = ai_status
                    updated = True

            if updated:
                with csv_path.open("w", encoding="utf-8-sig", newline="") as f:
                    w = csv.DictWriter(f, fieldnames=fields)
                    w.writeheader()
                    for r in rows:
                        w.writerow({k: r.get(k, "") for k in fields})
        except Exception:
            pass


def try_ai_loop(package, repo, logf):
    """
    AI 闭环：依赖缺失类失败时调AI补依赖+重跑。
    返回: (final_success: bool, ai_rounds: int, ai_packages: list, ai_status: str)

    ai_status 可能值:
      - "fixed":         AI修复成功
      - "exhausted":     AI跑了3轮还失败
      - "not_applicable":不是依赖缺失类，没调AI
      - "ai_no_apt":     AI说不是apt问题（manual_fix/skip）
      - "ai_failed":     AI调用失败
      - "install_failed":装包失败
    """
    if not AI_LOOP_ENABLED:
        return False, 0, [], "disabled"

    fail_rec = read_fail_record(package)
    if not fail_rec:
        return False, 0, [], "no_fail_record"

    main_cat = fail_rec.get("main_category", "")
    if main_cat != "依赖缺失":
        logf.write(f"[AI闭环] 主分类={main_cat}，不是依赖缺失，跳过AI闭环\n")
        return False, 0, [], "not_applicable"

    all_installed = []

    for round_num in range(1, MAX_AI_ROUNDS + 1):
        logf.write(f"\n[AI闭环] ===== 第 {round_num}/{MAX_AI_ROUNDS} 轮 =====\n")
        print(f"   [AI闭环 第{round_num}/{MAX_AI_ROUNDS}轮] 调用AI...")

        # 1. 调AI
        ai_result = call_ai_for_suggestion(package, logf)
        if not ai_result:
            logf.write("[AI闭环] AI调用失败，退出\n")
            return False, round_num - 1, all_installed, "ai_failed"

        fix_type = ai_result.get("fix_type", "")
        apt_packages = ai_result.get("apt_packages", [])
        diagnosis = ai_result.get("diagnosis", "")

        logf.write(f"[AI闭环] AI诊断: {diagnosis}\n")
        logf.write(f"[AI闭环] fix_type: {fix_type}, packages: {apt_packages}\n")
        print(f"   [AI闭环] {diagnosis} → fix_type={fix_type}")

        if fix_type != "apt_install" or not apt_packages:
            logf.write("[AI闭环] AI判断不能apt修复，退出\n")
            return False, round_num, all_installed, "ai_no_apt"

        # 2. 装包
        print(f"   [AI闭环] 安装: {' '.join(apt_packages)}")
        ok, msg = install_packages(apt_packages, logf)
        if not ok:
            logf.write(f"[AI闭环] 装包失败: {msg}\n")
            return False, round_num, all_installed, "install_failed"

        all_installed.extend(apt_packages)

        # 3. 重新构建
        print(f"   [AI闭环] 重新构建...")
        success, _ = rerun_full_build(package, repo, logf)

        if success:
            logf.write(f"[AI闭环] 第 {round_num} 轮修复成功\n")
            print(f"   [AI闭环] 第 {round_num} 轮修复成功")
            return True, round_num, all_installed, "fixed"

        # 失败，看看新的失败原因还是不是依赖缺失
        new_fail = read_fail_record(package)
        if not new_fail:
            logf.write("[AI闭环] 重跑后没找到fail记录，异常\n")
            return False, round_num, all_installed, "no_fail_record"

        new_cat = new_fail.get("main_category", "")
        if new_cat != "依赖缺失":
            logf.write(f"[AI闭环] 第 {round_num} 轮后主分类变为 {new_cat}，不再是依赖缺失，退出\n")
            print(f"   [AI闭环] 主分类变为 {new_cat}，AI 闭环停止")
            return False, round_num, all_installed, "category_changed"

        logf.write(f"[AI闭环] 第 {round_num} 轮后仍是依赖缺失，进入下一轮\n")

    # 3 轮跑完仍失败
    logf.write(f"[AI闭环] {MAX_AI_ROUNDS} 轮后仍未修复\n")
    print(f"   [AI闭环] {MAX_AI_ROUNDS} 轮后仍未修复")
    return False, MAX_AI_ROUNDS, all_installed, "exhausted"


def print_summary():
    print()
    print("=" * 70)
    print("批量运行结束统计")
    print("=" * 70)

    counts = {}
    fail_categories = {}
    ai_stats = {"fixed": 0, "exhausted": 0, "not_applicable": 0,
                "ai_no_apt": 0, "ai_failed": 0, "install_failed": 0, "category_changed": 0}

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
            for row in rows:
                status = row.get("ai_final_status", "")
                if status in ai_stats:
                    ai_stats[status] += 1
        except Exception:
            counts[label] = 0

    total = sum(counts.values())
    print(f"总处理包数: {total}")
    for label, n in counts.items():
        pct = (n / total * 100) if total else 0
        print(f"  {label}: {n} ({pct:.1f}%)")

    if any(v > 0 for v in ai_stats.values()):
        print()
        print("AI 闭环统计:")
        print(f"  AI 修复成功:   {ai_stats['fixed']}")
        print(f"  AI 3轮耗尽:    {ai_stats['exhausted']}")
        print(f"  AI 判定非apt:  {ai_stats['ai_no_apt']}")
        print(f"  装包失败:      {ai_stats['install_failed']}")
        print(f"  AI调用失败:    {ai_stats['ai_failed']}")
        print(f"  分类切换退出:  {ai_stats['category_changed']}")
        print(f"  不适用(非依赖):{ai_stats['not_applicable']}")

    if fail_categories:
        print()
        print("失败原因分布:")
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

    print(f"========== 批量构建测试 (AI闭环{'开' if AI_LOOP_ENABLED else '关'}) ==========")
    print(f"包列表总数: {total}")
    print(f"已处理 (跳过): {skip_count}")
    print(f"待处理: {len(todo)}")
    print(f"单包超时: {PER_PACKAGE_TIMEOUT//60} 分钟")
    print(f"AI 最大重试轮数: {MAX_AI_ROUNDS}")
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

            # 第一次跑
            success, _ = rerun_full_build(package, repo, logf)

            ai_rounds = 0
            ai_packages = []
            ai_status = ""

            if success:
                status = "成功"
                ai_status = "not_applicable"
            else:
                # 尝试 AI 闭环
                ai_fixed, ai_rounds, ai_packages, ai_status = try_ai_loop(package, repo, logf)
                if ai_fixed:
                    status = f"成功 (AI修复, {ai_rounds}轮)"
                else:
                    if ai_status == "exhausted":
                        status = f"失败 (AI {MAX_AI_ROUNDS}轮未修复)"
                    elif ai_status == "not_applicable":
                        # 不是依赖缺失类，正常失败
                        status = "失败"
                    elif ai_status == "ai_no_apt":
                        status = "失败 (AI判定非apt问题)"
                    else:
                        status = f"失败 ({ai_status})"

            # 更新 CSV，补 AI 字段
            update_csv_with_ai_info(package, ai_rounds, ai_packages, ai_status)

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
