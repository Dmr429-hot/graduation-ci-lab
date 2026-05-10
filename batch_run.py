#!/usr/bin/env python3
# -*- coding: utf-8 -*-

import csv
import subprocess
from pathlib import Path

ROOT = Path(__file__).resolve().parent
CSV_FILE = ROOT / "data" / "packages.csv"
AUTO_ONE = ROOT / "auto_one.sh"

def main():
    if not CSV_FILE.exists():
        print(f"找不到输入文件: {CSV_FILE}")
        print("请先创建 data/packages.csv")
        return

    with CSV_FILE.open("r", encoding="utf-8-sig", newline="") as f:
        reader = csv.DictReader(f)

        for row in reader:
            package = (row.get("package") or "").strip()
            repo = (row.get("repo") or "").strip()

            if not package or not repo:
                continue

            print("\n" + "=" * 80)
            print(f"开始处理: {package}")
            print("=" * 80)

            ret = subprocess.run([str(AUTO_ONE), package, repo])
            print(f"软件包 {package} 结束，返回码: {ret.returncode}")

if __name__ == "__main__":
    main()
