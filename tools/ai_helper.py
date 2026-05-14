#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
AI助手：调用 DeepSeek API 分析构建失败日志，给出 apt install 建议

使用方式 1（单包，给演示用）:
    python3 tools/ai_helper.py --package libxfce4ui

使用方式 2（编程接入）:
    from tools.ai_helper import suggest_for_package
    result = suggest_for_package('libxfce4ui')

环境变量:
    DEEPSEEK_API_KEY  必须，DeepSeek的API key
    HTTPS_PROXY       可选，如果需要代理访问API（你的VM需要）
"""

import argparse
import csv
import json
import os
import sys
import time
import urllib.request
import urllib.error
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
RESULTS_DIR = ROOT / "results"

DEEPSEEK_API_URL = "https://api.deepseek.com/chat/completions"
DEEPSEEK_MODEL = "deepseek-chat"

# 系统prompt：强制AI输出结构化JSON
SYSTEM_PROMPT = """你是Linux软件包构建专家，专门分析构建失败日志并给出修复建议。

用户会给你一个软件包的构建失败信息，你的任务是：
1. 判断失败的根本原因
2. 给出最简单的修复方案（优先：apt安装缺失的开发包）
3. 严格按JSON格式回复，不要有任何其他文字、Markdown标记或解释

JSON格式（必须严格遵守）：
{
  "diagnosis": "一句话诊断（中文，30字内）",
  "fix_type": "apt_install" 或 "manual_fix" 或 "skip",
  "apt_packages": ["包名1", "包名2"],
  "command": "完整的修复命令（如 sudo apt install -y libxxx-dev）",
  "confidence": "high" 或 "medium" 或 "low",
  "notes": "补充说明（可选，中文，50字内）"
}

规则：
- fix_type=apt_install 时，apt_packages必填，command写完整 sudo apt install -y 命令
- 缺什么开发包就装对应的 -dev 包（如缺 gtk+-3.0 → libgtk-3-dev，缺 libpng.h → libpng-dev）
- 如果是源码bug、上游问题、网络问题等 apt 无法解决的，fix_type=manual_fix 或 skip
- 不确定时 confidence=low，宁可保守也别给错误的包名
- 只返回JSON对象，不要加 ```json``` 之类的markdown标记"""


def get_api_key():
    """从环境变量读取API key"""
    key = os.environ.get("DEEPSEEK_API_KEY", "").strip()
    if not key:
        # 兼容另一个常用变量名
        key = os.environ.get("DEEPSEEK_KEY", "").strip()
    if not key:
        print("错误: 请设置环境变量 DEEPSEEK_API_KEY", file=sys.stderr)
        print("  export DEEPSEEK_API_KEY=\"sk-xxx\"", file=sys.stderr)
        sys.exit(1)
    return key


def get_proxies():
    """读取代理配置"""
    proxy = os.environ.get("https_proxy") or os.environ.get("HTTPS_PROXY", "")
    return proxy.strip() if proxy else None


def call_deepseek(user_message: str, max_retries=3):
    """调用 DeepSeek API，返回原始文本"""
    api_key = get_api_key()
    proxy = get_proxies()

    payload = {
        "model": DEEPSEEK_MODEL,
        "messages": [
            {"role": "system", "content": SYSTEM_PROMPT},
            {"role": "user", "content": user_message},
        ],
        "temperature": 0.0,
        "max_tokens": 500,
        "response_format": {"type": "json_object"},
    }

    data = json.dumps(payload).encode("utf-8")
    req = urllib.request.Request(
        DEEPSEEK_API_URL,
        data=data,
        headers={
            "Content-Type": "application/json",
            "Authorization": f"Bearer {api_key}",
        },
    )

    # 处理代理
    if proxy:
        proxy_handler = urllib.request.ProxyHandler({"https": proxy, "http": proxy})
        opener = urllib.request.build_opener(proxy_handler)
    else:
        opener = urllib.request.build_opener()

    last_error = None
    for attempt in range(max_retries):
        try:
            with opener.open(req, timeout=60) as resp:
                body = resp.read().decode("utf-8")
                obj = json.loads(body)
                content = obj["choices"][0]["message"]["content"]
                return content
        except urllib.error.HTTPError as e:
            last_error = f"HTTP {e.code}: {e.read().decode('utf-8', errors='ignore')[:200]}"
        except Exception as e:
            last_error = f"{type(e).__name__}: {e}"

        if attempt < max_retries - 1:
            time.sleep(2 ** attempt)  # 指数退避: 1, 2, 4 秒

    raise RuntimeError(f"DeepSeek调用失败（{max_retries}次重试后）: {last_error}")


def build_user_message(package: str, fail_row: dict) -> str:
    """根据fail.csv里的一行构造给AI的输入"""
    return f"""请分析以下软件包构建失败的情况，给出修复建议。

【软件包】{package}
【构建系统】{fail_row.get('type', '未知')}
【失败阶段】{fail_row.get('failed_stage', '未知')}
【分类系统判断】
  - 主分类: {fail_row.get('main_category', '')}
  - 子分类: {fail_row.get('sub_category', '')}
  - 归属侧: {fail_row.get('owner_side', '')}
【匹配的规则】{fail_row.get('rule_name', '')}

【错误日志片段】
{fail_row.get('error_excerpt', '(无)')[:2000]}

请按JSON格式给出修复建议。"""


def parse_ai_response(text: str) -> dict:
    """把AI的JSON文本解析成dict，失败返回空dict"""
    text = text.strip()
    # 去除可能的markdown包裹
    if text.startswith("```"):
        lines = text.split("\n")
        text = "\n".join(line for line in lines if not line.strip().startswith("```"))
    try:
        return json.loads(text)
    except json.JSONDecodeError as e:
        print(f"  [警告] AI返回的JSON解析失败: {e}", file=sys.stderr)
        print(f"  [原始返回] {text[:300]}", file=sys.stderr)
        return {
            "diagnosis": "AI返回格式错误",
            "fix_type": "manual_fix",
            "apt_packages": [],
            "command": "",
            "confidence": "low",
            "notes": text[:200],
        }


def suggest_for_package(package: str) -> dict:
    """对单个失败包生成建议。返回完整结果字典"""
    fail_csv = RESULTS_DIR / "fail.csv"
    if not fail_csv.exists():
        return {"error": f"找不到 {fail_csv}"}

    fail_row = None
    with fail_csv.open("r", encoding="utf-8-sig") as f:
        for row in csv.DictReader(f):
            if row.get("package", "").strip() == package:
                fail_row = row
                break

    if not fail_row:
        return {"error": f"在 fail.csv 中找不到 {package}"}

    user_msg = build_user_message(package, fail_row)
    raw = call_deepseek(user_msg)
    suggestion = parse_ai_response(raw)

    return {
        "package": package,
        "raw_response": raw,
        **suggestion,
    }


def print_pretty(result: dict):
    """漂亮地打印结果，演示时好看"""
    print()
    print("=" * 60)
    print(f"  AI 修复建议  -  {result.get('package', '?')}")
    print("=" * 60)

    if "error" in result:
        print(f"  [错误] {result['error']}")
        print("=" * 60)
        return

    diagnosis = result.get("diagnosis", "")
    fix_type = result.get("fix_type", "")
    command = result.get("command", "")
    packages = result.get("apt_packages", [])
    confidence = result.get("confidence", "")
    notes = result.get("notes", "")

    print(f"  诊断: {diagnosis}")
    print(f"  类型: {fix_type}  (置信度: {confidence})")
    if packages:
        print(f"  缺失包: {', '.join(packages)}")
    if command:
        print()
        print(f"  → 建议执行:")
        print(f"      {command}")
    if notes:
        print()
        print(f"  备注: {notes}")
    print("=" * 60)
    print()


def main():
    parser = argparse.ArgumentParser(description="对失败包调用AI生成修复建议")
    parser.add_argument("--package", help="只对单个包生成建议（演示用）")
    parser.add_argument("--json", action="store_true", help="输出完整JSON（编程接入用）")
    args = parser.parse_args()

    if not args.package:
        print("用法: python3 tools/ai_helper.py --package <包名>", file=sys.stderr)
        print("  批量模式请使用 batch_ai_suggest.py", file=sys.stderr)
        sys.exit(1)

    result = suggest_for_package(args.package)

    if args.json:
        print(json.dumps(result, ensure_ascii=False, indent=2))
    else:
        print_pretty(result)


if __name__ == "__main__":
    main()
