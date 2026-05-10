#!/usr/bin/env python3
# -*- coding: utf-8 -*-

import argparse
import csv
import json
import re
from pathlib import Path

ERROR_PATTERNS = [
    r"\berror:",
    r"\bfatal:",
    r"\bfailed\b",
    r"\bFAIL\b",
    r"\bFAILED\b",
    r"\*\*\*",
    r"undefined reference",
    r"No such file or directory",
    r"cannot find",
    r"not found",
    r"Could NOT find",
    r"Dependency .* not found",
    r"No package .* found",
    r"Package .* was not found",
    r"command not found",
    r"Traceback",
    r"Exception",
    r"Permission denied",
    r"Connection reset",
    r"GnuTLS recv error",
    r"Unable to locate package",
]

RULES = [
    {
        "rule_name": "network_or_proxy_error",
        "main_category": "环境与网络问题",
        "sub_category": "网络、代理或仓库访问失败",
        "owner_side": "测试环境侧",
        "action": "检查网络、代理、仓库地址和访问权限",
        "patterns": [
            r"Connection reset",
            r"Failed to connect",
            r"Could not resolve host",
            r"Temporary failure resolving",
            r"GnuTLS recv error",
            r"early EOF",
            r"unexpected disconnect",
            r"TLS connection",
            r"Proxy",
        ],
    },
    {
        "rule_name": "dependency_pkg_config_missing",
        "main_category": "依赖缺失",
        "sub_category": "pkg-config、CMake 或 Meson 依赖未找到",
        "owner_side": "测试环境侧",
        "action": "安装缺失的开发包，或关闭对应可选功能",
        "patterns": [
            r"No package '.*' found",
            r"Package .* was not found",
            r"Dependency .* not found",
            r"Run-time dependency .* found: NO",
            r"Could NOT find",
            r"required dependency .* not found",
        ],
    },
    {
        "rule_name": "dependency_header_or_library_missing",
        "main_category": "依赖缺失",
        "sub_category": "头文件或库文件缺失",
        "owner_side": "测试环境侧",
        "action": "安装对应 libxxx-dev 开发包，或检查库搜索路径",
        "patterns": [
            r"fatal error: .*: No such file or directory",
            r"cannot find -l",
            r"ld: cannot find",
            r"library .* not found",
            r"header .* not found",
        ],
    },
    {
        "rule_name": "tool_missing",
        "main_category": "依赖缺失",
        "sub_category": "构建工具或命令缺失",
        "owner_side": "测试环境侧",
        "action": "安装缺失命令对应的软件包",
        "patterns": [
            r"command not found",
            r"Program .* not found",
            r"not found in PATH",
            r"missing required tool",
            r"No such file or directory: '.*'",
        ],
    },
    {
        "rule_name": "doc_tool_missing",
        "main_category": "依赖缺失",
        "sub_category": "文档或手册生成工具缺失",
        "owner_side": "测试环境侧",
        "action": "安装文档工具，或通过构建参数关闭文档生成",
        "patterns": [
            r"asciidoctor",
            r"gtk-doc",
            r"gtkdoc",
            r"doxygen",
            r"sphinx-build",
            r"xsltproc",
            r"help2man",
            r"docbook",
            r"manpage",
        ],
    },
    {
        "rule_name": "autotools_macro_error",
        "main_category": "构建系统问题",
        "sub_category": "Autotools 宏或辅助脚本缺失",
        "owner_side": "构建配置侧",
        "action": "补充 autotools 工具，或执行 autoreconf/bootstrap",
        "patterns": [
            r"possibly undefined macro",
            r"AC_PROG_LIBTOOL",
            r"LT_INIT",
            r"AM_GNU_GETTEXT",
            r"AC_PROG_PKG_CONFIG",
            r"autoreconf: .* failed",
            r"libtoolize",
            r"config.guess",
            r"config.sub",
            r"install-sh",
            r"missing auxiliary files",
        ],
    },
    {
        "rule_name": "cmake_meson_config_error",
        "main_category": "构建系统问题",
        "sub_category": "CMake 或 Meson 配置失败",
        "owner_side": "构建配置侧",
        "action": "检查构建参数、依赖项和源码目录",
        "patterns": [
            r"CMake Error",
            r"Meson encountered an error",
            r"meson.build:.*ERROR",
            r"Unknown option",
            r"Configuring incomplete",
        ],
    },
    {
        "rule_name": "source_compile_error",
        "main_category": "源码编译错误",
        "sub_category": "源码语法、接口或兼容性错误",
        "owner_side": "软件包源码侧",
        "action": "记录为源码编译问题，通常需要补丁修复",
        "patterns": [
            r"error: .* undeclared",
            r"error: .* was not declared",
            r"error: no member named",
            r"error: invalid conversion",
            r"error: incompatible types",
            r"\[-Werror",
            r"all warnings being treated as errors",
        ],
    },
    {
        "rule_name": "link_error",
        "main_category": "链接错误",
        "sub_category": "符号未定义或链接库缺失",
        "owner_side": "构建配置侧或源码侧",
        "action": "检查链接参数、库依赖和 LTO 设置",
        "patterns": [
            r"undefined reference to",
            r"ld returned 1 exit status",
            r"collect2: error",
            r"LLVMgold.so",
            r"plugin needed to handle lto object",
            r"lto-wrapper failed",
        ],
    },
    {
        "rule_name": "test_failure",
        "main_category": "测试阶段失败",
        "sub_category": "单元测试或集成测试失败",
        "owner_side": "软件包源码侧或测试环境侧",
        "action": "查看失败用例，判断是环境限制还是软件自身问题",
        "patterns": [
            r"The following tests FAILED",
            r"tests failed out of",
            r"FAILED .* in ",
            r"AssertionError",
            r"FAIL:",
            r"ERROR: test",
            r"Test timeout",
        ],
    },
    {
        "rule_name": "permission_or_path_error",
        "main_category": "环境与路径问题",
        "sub_category": "权限、路径或文件生成失败",
        "owner_side": "测试环境侧",
        "action": "检查目录是否存在、是否可写、磁盘空间是否充足",
        "patterns": [
            r"Permission denied",
            r"Read-only file system",
            r"cannot create .* No such file or directory",
            r"No space left on device",
            r"cannot create directory",
        ],
    },
]

def read_lines(path: Path):
    if not path.exists():
        return []
    return path.read_text(encoding="utf-8", errors="ignore").splitlines()

def is_error_line(line: str):
    return any(re.search(p, line, re.IGNORECASE) for p in ERROR_PATTERNS)

def extract_error_excerpt(lines, before=4, after=8):
    hits = [i for i, line in enumerate(lines) if is_error_line(line)]

    if not hits:
        tail = lines[-120:] if len(lines) > 120 else lines
        return "\n".join(tail), 0

    ranges = []

    for i in hits:
        start = max(0, i - before)
        end = min(len(lines), i + after + 1)
        ranges.append((start, end))

    merged = []

    for start, end in ranges:
        if not merged or start > merged[-1][1] + 1:
            merged.append([start, end])
        else:
            merged[-1][1] = max(merged[-1][1], end)

    merged = merged[-12:]

    out = []

    for start, end in merged:
        out.append(f"\n----- lines {start + 1}-{end} -----")
        for i in range(start, end):
            out.append(f"{i + 1}: {lines[i]}")

    return "\n".join(out).strip(), len(hits)

def normalize_template(text: str):
    text = re.sub(r"/[A-Za-z0-9._+\-=/]+", "<PATH>", text)
    text = re.sub(r"\b[0-9a-f]{7,40}\b", "<HASH>", text)
    text = re.sub(r"\b\d+\.\d+(\.\d+)?\b", "<VERSION>", text)
    text = re.sub(r"\b\d+\b", "<NUM>", text)
    text = re.sub(r"'[^']+'", "'<STR>'", text)
    text = re.sub(r'"[^"]+"', '"<STR>"', text)
    return text

def classify(text: str):
    best_rule = None
    best_score = 0
    best_patterns = []

    for rule in RULES:
        matched = []

        for p in rule["patterns"]:
            if re.search(p, text, re.IGNORECASE | re.MULTILINE):
                matched.append(p)

        if len(matched) > best_score:
            best_score = len(matched)
            best_rule = rule
            best_patterns = matched

    if best_rule is None:
        return {
            "rule_name": "unclassified",
            "main_category": "未分类错误",
            "sub_category": "规则未覆盖",
            "owner_side": "待人工判断",
            "action": "保留日志片段，后续补充分类规则",
            "matched_patterns": [],
        }

    return {
        "rule_name": best_rule["rule_name"],
        "main_category": best_rule["main_category"],
        "sub_category": best_rule["sub_category"],
        "owner_side": best_rule["owner_side"],
        "action": best_rule["action"],
        "matched_patterns": best_patterns,
    }

def write_csv(path: Path, row: dict):
    fields = [
        "package",
        "stage",
        "result",
        "main_category",
        "sub_category",
        "owner_side",
        "action",
        "rule_name",
        "log_file",
        "error_excerpt_file",
        "template_excerpt_file",
    ]

    exists = path.exists()

    with path.open("a", encoding="utf-8-sig", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=fields)

        if not exists:
            writer.writeheader()

        writer.writerow({k: row.get(k, "") for k in fields})

def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--package", required=True)
    parser.add_argument("--stage", required=True)
    parser.add_argument("--result", required=True, choices=["success", "fail"])
    parser.add_argument("--log", required=True)
    parser.add_argument("--outdir", required=True)
    args = parser.parse_args()

    log_file = Path(args.log).resolve()
    outdir = Path(args.outdir).resolve()
    outdir.mkdir(parents=True, exist_ok=True)

    lines = read_lines(log_file)
    full_text = "\n".join(lines)

    error_excerpt, hit_count = extract_error_excerpt(lines)
    template_excerpt = normalize_template(error_excerpt)

    error_file = outdir / f"{args.stage}_error_excerpt.txt"
    template_file = outdir / f"{args.stage}_template_excerpt.txt"
    summary_file = outdir / f"{args.stage}_summary.json"
    csv_file = outdir / "summary.csv"

    error_file.write_text(error_excerpt, encoding="utf-8")
    template_file.write_text(template_excerpt, encoding="utf-8")

    if args.result == "success":
        cls = {
            "rule_name": "success",
            "main_category": "成功",
            "sub_category": "无失败",
            "owner_side": "无",
            "action": "无需处理",
            "matched_patterns": [],
        }
    else:
        cls = classify(full_text + "\n" + error_excerpt)

    summary = {
        "package": args.package,
        "stage": args.stage,
        "result": args.result,
        "log_file": str(log_file),
        "error_excerpt_file": str(error_file),
        "template_excerpt_file": str(template_file),
        "error_hit_count": hit_count,
        **cls,
    }

    summary_file.write_text(
        json.dumps(summary, ensure_ascii=False, indent=2),
        encoding="utf-8"
    )

    write_csv(csv_file, summary)

    print(json.dumps(summary, ensure_ascii=False, indent=2))

if __name__ == "__main__":
    main()
