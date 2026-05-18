#!/usr/bin/env python3
# -*- coding: utf-8 -*-

import argparse
import csv
import json
import re
from pathlib import Path

# === 错误锚点：用于定位日志中疑似错误的行 ===
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

# === 噪音行 ===
NOISE_PATTERNS = [
    re.compile(r"^\s*\[\s*\d+%\]"),
    re.compile(r"^\s*--\s+(Found|Check|Looking|Detecting|Performing|Configuring|Generating|Build files|Switching)"),
    re.compile(r"^\s*checking\b.+\.\.\.\s*(yes|ok|done)\s*$", re.IGNORECASE),
    re.compile(r"^\s*config\.status:", re.IGNORECASE),
    re.compile(r"^\s*libtool:\s*(compile|link):"),
    re.compile(r"^\s*$"),
    re.compile(r"^\s*-{2,}\s*$"),
    re.compile(r"^\s*Building\s+(C|CXX)\s+object\s"),
    re.compile(r"^\s*Linking\s+(C|CXX)\s+(executable|shared|static)"),
    re.compile(r"^\s*\[\d+/\d+\]\s+(Building|Generating|Linking|Compiling)"),
    re.compile(r"^\s*make(\[\d+\])?: \*\*\*"),
    re.compile(r"^\s*gmake(\[\d+\])?: \*\*\*"),
    re.compile(r"^\s*ninja: build stopped"),
    re.compile(r"^\s*cc1: all warnings being treated as errors"),
    re.compile(r"^\s*collect2:\s*error:\s*ld returned"),
    re.compile(r"^\s*(gcc|g\+\+|cc|c\+\+|clang|clang\+\+)\s+.*-[Iilo]\s"),
]

COMPILE_CMD_RE = re.compile(r"^\s*(gcc|g\+\+|cc|c\+\+|clang|clang\+\+|ld|ar)\s+.*-[Iilo]\s*\S")

def is_noise(line):
    return any(p.search(line) for p in NOISE_PATTERNS)

def clean_line(line, max_len=200):
    line = line.rstrip()
    if COMPILE_CMD_RE.match(line) and len(line) > max_len:
        return line[:80] + " ... [命令截断] ... " + line[-80:]
    if len(line) > max_len:
        return line[:max_len] + " ...[截断]"
    return line


# === 结构化字段抽取 ===
FACT_EXTRACTORS = [
    ("missing_header",  re.compile(r"fatal error:\s*(\S+\.h(?:pp|xx|\+\+)?)\s*:\s*No such file", re.IGNORECASE)),
    ("missing_header",  re.compile(r"^#include\s+[<\"](\S+\.h(?:pp|xx|\+\+)?)\s*[>\"].*\n.*No such file", re.MULTILINE)),
    ("missing_package", re.compile(r"Could NOT find\s+(\w+)")),
    ("missing_package", re.compile(r"Run-time dependency\s+(\S+)\s+found:\s*NO", re.IGNORECASE)),
    ("missing_package", re.compile(r"Build-time dependency\s+(\S+)\s+found:\s*NO", re.IGNORECASE)),
    ("missing_package", re.compile(r"Native dependency\s+(\S+)\s+found:\s*NO", re.IGNORECASE)),
    ("missing_package", re.compile(r"Dependency\s+[\"']?(\S+?)[\"']?\s+not found", re.IGNORECASE)),
    ("missing_package", re.compile(r"No package\s+[\"']?(\S+?)[\"']?\s+found", re.IGNORECASE)),
    ("missing_package", re.compile(r"Package\s+[\"']?(\S+?)[\"']?\s+was not found")),
    ("missing_library", re.compile(r"(?:ld|/usr/bin/ld):\s*cannot find\s+-l(\S+)")),
    ("missing_library", re.compile(r"library\s+[\"']?(\S+?)[\"']?\s+not found")),
    ("missing_command", re.compile(r"^(\S+):\s*command not found", re.MULTILINE)),
    ("missing_command", re.compile(r"Program\s+[\"']?(\S+?)[\"']?\s+not found")),
    ("missing_command", re.compile(r"missing required tool[: ]+(\S+)", re.IGNORECASE)),
]

def extract_facts(text):
    facts = {}
    for field, pat in FACT_EXTRACTORS:
        if field in facts and facts[field]:
            continue
        m = pat.search(text)
        if m:
            facts[field] = m.group(1)
    return facts


# === 规则表（22 个子分类）===
RULES = [
    # 网络/代理 (priority 5)
    {
        "rule_name": "network_or_proxy_error",
        "priority": 5,
        "main_category": "环境与网络问题",
        "sub_category": "网络、代理或仓库访问失败",
        "owner_side": "测试环境侧",
        "action": "检查网络、代理、仓库地址和访问权限",
        "patterns": [
            r"Connection reset by peer",
            r"Failed to connect to",
            r"Could not resolve host",
            r"Temporary failure resolving",
            r"GnuTLS recv error",
            r"unexpected disconnect while reading sideband",
            r"TLS connection was non-properly terminated",
            r"proxy connect.*failed",
            r"HTTP proxy error",
            r"curl: \(\d+\)",
            r"RPC 失败.*curl",
            r"early EOF",
            r"fetch-pack: unexpected disconnect",
        ],
    },
    # 依赖缺失 - pkg-config (priority 10)
    {
        "rule_name": "dependency_pkgconfig_missing",
        "priority": 10,
        "main_category": "依赖缺失",
        "sub_category": "pkg-config 包未找到",
        "owner_side": "测试环境侧",
        "action": "安装对应的 -dev 开发包",
        "patterns": [
            r"No package '.*' found",
            r"Package .* was not found",
            r"Package '.*' .* was not found",
        ],
    },
    # 依赖缺失 - CMake (priority 10)
    {
        "rule_name": "dependency_cmake_missing",
        "priority": 10,
        "main_category": "依赖缺失",
        "sub_category": "CMake 包未找到",
        "owner_side": "测试环境侧",
        "action": "安装对应的 -dev 开发包，或检查 CMAKE_PREFIX_PATH",
        "patterns": [
            r"Could NOT find",
            r"Could not find a package configuration file",
            r"CMake Error.*Could not find",
        ],
    },
    # 依赖缺失 - Meson (priority 10)
    {
        "rule_name": "dependency_meson_missing",
        "priority": 10,
        "main_category": "依赖缺失",
        "sub_category": "Meson 依赖未找到",
        "owner_side": "测试环境侧",
        "action": "安装对应的 -dev 开发包，或关闭对应可选功能",
        "patterns": [
            r"Run-time dependency .* found: NO",
            r"Build-time dependency .* found: NO",
            r"Native dependency .* found: NO",
            r"Dependency .* not found",
            r"required dependency .* not found",
        ],
    },
    # 依赖缺失 - 头文件 (priority 11)
    {
        "rule_name": "dependency_header_missing",
        "priority": 11,
        "main_category": "依赖缺失",
        "sub_category": "头文件缺失",
        "owner_side": "测试环境侧",
        "action": "安装提供该头文件的 -dev 包",
        "patterns": [
            r"fatal error: .*\.h(?:pp|xx|\+\+)?: No such file or directory",
            r"fatal error: .*\.h(?:pp|xx|\+\+)?: 没有那个文件或目录",
            r"header .* not found",
            r"cannot open source file .*\.h",
        ],
    },
    # 依赖缺失 - 链接库 (priority 12)
    {
        "rule_name": "dependency_library_missing",
        "priority": 12,
        "main_category": "依赖缺失",
        "sub_category": "链接库缺失",
        "owner_side": "测试环境侧",
        "action": "安装对应的运行时库或 -dev 包",
        "patterns": [
            r"(?:ld|/usr/bin/ld):\s*cannot find\s+-l",
            r"library .* not found",
            r"cannot find library",
        ],
    },
    # 依赖缺失 - 工具 (priority 15)
    {
        "rule_name": "tool_missing",
        "priority": 15,
        "main_category": "依赖缺失",
        "sub_category": "构建工具或命令缺失",
        "owner_side": "测试环境侧",
        "action": "安装提供该命令的软件包",
        "patterns": [
            r"command not found", r"Program .* not found",
            r"not found in PATH", r"missing required tool",
        ],
    },
    # 依赖缺失 - Python 模块 (priority 16)
    {
        "rule_name": "python_module_missing",
        "priority": 16,
        "main_category": "依赖缺失",
        "sub_category": "Python 模块缺失",
        "owner_side": "测试环境侧",
        "action": "用 pip 或 apt 安装对应 python 模块",
        "patterns": [
            r"ModuleNotFoundError: No module named",
            r"ImportError: No module named",
            r"ImportError: cannot import name",
        ],
    },
    # 依赖缺失 - Perl 模块 (priority 17)
    {
        "rule_name": "perl_module_missing",
        "priority": 17,
        "main_category": "依赖缺失",
        "sub_category": "Perl 模块缺失",
        "owner_side": "测试环境侧",
        "action": "用 cpan 或 apt 安装对应 perl 模块（如 libxxx-perl）",
        "patterns": [
            r"Can't locate .* in @INC",
            r"Can't locate .*\.pm in @INC",
        ],
    },
    # 依赖缺失 - 文档工具 (priority 20)
    {
        "rule_name": "doc_tool_missing",
        "priority": 20,
        "main_category": "依赖缺失",
        "sub_category": "文档或手册生成工具缺失",
        "owner_side": "测试环境侧",
        "action": "安装文档工具，或通过构建参数关闭文档生成",
        "patterns": [
            r"asciidoctor", r"gtk-doc", r"gtkdoc", r"doxygen",
            r"sphinx-build", r"xsltproc", r"help2man", r"docbook", r"manpage",
        ],
    },
    # 工具版本过低 (priority 25)
    {
        "rule_name": "tool_version_too_low",
        "priority": 25,
        "main_category": "构建系统问题",
        "sub_category": "构建工具版本过低",
        "owner_side": "测试环境侧",
        "action": "升级 CMake / Meson / Python 等工具到要求版本",
        "patterns": [
            r"CMake .* or higher is required",
            r"requires CMake .* or higher",
            r"Meson version .* required",
            r"meson .* or newer is required",
            r"Python .* or newer required",
            r"requires Python",
        ],
    },
    # Autotools 宏 (priority 30)
    {
        "rule_name": "autotools_macro_error",
        "priority": 30,
        "main_category": "构建系统问题",
        "sub_category": "Autotools 宏或辅助脚本缺失",
        "owner_side": "构建配置侧",
        "action": "补充 autotools 工具，或执行 autoreconf/bootstrap",
        "patterns": [
            r"possibly undefined macro", r"AC_PROG_LIBTOOL", r"LT_INIT",
            r"AM_GNU_GETTEXT", r"AC_PROG_PKG_CONFIG",
            r"autoreconf: .* failed", r"libtoolize",
            r"config\.guess", r"config\.sub", r"install-sh",
            r"missing auxiliary files",
        ],
    },
    # 源码 - 未声明 (priority 35)
    {
        "rule_name": "source_undeclared",
        "priority": 35,
        "main_category": "源码编译错误",
        "sub_category": "未声明的标识符或变量",
        "owner_side": "软件包源码侧",
        "action": "记录为源码问题，检查是否缺头文件或宏定义",
        "patterns": [
            r"error: .* undeclared",
            r"error: .* was not declared",
            r"error: use of undeclared identifier",
            r"error: implicit declaration of function",
        ],
    },
    # 源码 - 类型不匹配 (priority 36)
    {
        "rule_name": "source_type_mismatch",
        "priority": 36,
        "main_category": "源码编译错误",
        "sub_category": "类型不匹配或接口不兼容",
        "owner_side": "软件包源码侧",
        "action": "记录为源码问题，可能是上游 API 变更引起",
        "patterns": [
            r"error: incompatible types",
            r"error: invalid conversion",
            r"error: cannot convert",
            r"error: conflicting types for",
            r"error: assignment .* incompatible",
            r"error: too few arguments",
            r"error: too many arguments",
            r"error: no member named",
        ],
    },
    # 源码 - 语法错误 (priority 37)
    {
        "rule_name": "source_syntax_error",
        "priority": 37,
        "main_category": "源码编译错误",
        "sub_category": "语法错误或编译标志错误",
        "owner_side": "软件包源码侧",
        "action": "记录为源码问题，可能是编译器版本或编译选项不兼容",
        "patterns": [
            r"error: expected .* before",
            r"error: expected .* at end of input",
            r"error: redefinition of",
            r"error: storage size of .* isn't known",
            r"error: dereferencing pointer to incomplete type",
            r"error: 'for' loop initial declarations",
            r"\[-Werror",
            r"all warnings being treated as errors",
        ],
    },
    # 源码 - 编码错误 (priority 38)
    {
        "rule_name": "encoding_error",
        "priority": 38,
        "main_category": "源码编译错误",
        "sub_category": "字符编码或编码识别错误",
        "owner_side": "软件包源码侧",
        "action": "检查源码文件编码，必要时设置 LANG/LC_ALL",
        "patterns": [
            r"UnicodeDecodeError",
            r"UnicodeEncodeError",
            r"invalid byte sequence",
            r"illegal byte sequence",
            r"'ascii' codec can't",
        ],
    },
    # 链接错误 (priority 45)
    {
        "rule_name": "link_error",
        "priority": 45,
        "main_category": "链接错误",
        "sub_category": "符号未定义或链接失败",
        "owner_side": "构建配置侧",
        "action": "检查链接参数、库依赖和 LTO 设置",
        "patterns": [
            r"undefined reference to", r"ld returned 1 exit status",
            r"collect2: error", r"LLVMgold\.so",
            r"plugin needed to handle lto object", r"lto-wrapper failed",
        ],
    },
    # CMake 配置 (priority 50)
    {
        "rule_name": "cmake_config_error",
        "priority": 50,
        "main_category": "构建系统问题",
        "sub_category": "CMake 配置语法或参数错误",
        "owner_side": "构建配置侧",
        "action": "检查 CMakeLists.txt 或传入的 -D 参数",
        "patterns": [
            r"CMake Error at",
            r"CMake Error:",
            r"CMake Warning .* fatal",
            r"Configuring incomplete",
        ],
    },
    # Meson 配置 (priority 50)
    {
        "rule_name": "meson_config_error",
        "priority": 50,
        "main_category": "构建系统问题",
        "sub_category": "Meson 配置语法或参数错误",
        "owner_side": "构建配置侧",
        "action": "检查 meson.build 或传入的构建选项",
        "patterns": [
            r"Meson encountered an error",
            r"meson\.build:.*ERROR",
            r"Unknown option",
            r"Invalid version of dependency",
        ],
    },
    # 源码文件缺失 (priority 55)
    {
        "rule_name": "source_files_missing",
        "priority": 55,
        "main_category": "构建系统问题",
        "sub_category": "源码文件或目录缺失",
        "owner_side": "构建配置侧",
        "action": "检查 git submodule、源码完整性或解压步骤",
        "patterns": [
            r"No such file or directory: '.*'",
            r"missing\s+Makefile",
            r"no rule to make target",
            r"empty source directory",
        ],
    },
    # 测试失败 (priority 60)
    {
        "rule_name": "test_failure",
        "priority": 60,
        "main_category": "测试阶段失败",
        "sub_category": "单元测试或集成测试失败",
        "owner_side": "软件包源码侧",
        "action": "查看失败用例，判断是环境限制还是软件自身问题",
        "patterns": [
            r"The following tests FAILED", r"tests failed out of",
            r"FAILED .* in ", r"AssertionError", r"FAIL:",
            r"ERROR: test",
        ],
    },
    # 测试超时 (priority 65)
    {
        "rule_name": "test_timeout",
        "priority": 65,
        "main_category": "测试阶段失败",
        "sub_category": "测试超时",
        "owner_side": "软件包源码侧",
        "action": "适当调高超时阈值，或检查测试是否死锁",
        "patterns": [
            r"Test timeout",
            r"timeout .* expired",
            r"timed out after",
        ],
    },
    # 权限/路径 (priority 70)
    {
        "rule_name": "permission_or_path_error",
        "priority": 70,
        "main_category": "环境与路径问题",
        "sub_category": "权限、路径或磁盘空间问题",
        "owner_side": "测试环境侧",
        "action": "检查目录权限、是否可写、磁盘空间是否充足",
        "patterns": [
            r"Permission denied", r"Read-only file system",
            r"cannot create .* No such file or directory",
            r"No space left on device", r"cannot create directory",
        ],
    },
    # make 兜底 (priority 90)
    {
        "rule_name": "make_aggregate_error",
        "priority": 90,
        "main_category": "构建系统问题",
        "sub_category": "make 汇总错误（根因未识别）",
        "owner_side": "待人工判断",
        "action": "make 报错通常是其他根因引起，建议人工查看完整日志",
        "patterns": [
            r"make(\[\d+\])?: \*\*\* .* Error \d+",
            r"gmake(\[\d+\])?: \*\*\* .* 错误 \d+",
            r"ninja: build stopped",
        ],
    },
]


def read_lines(path):
    if not path.exists():
        return []
    return path.read_text(encoding="utf-8", errors="ignore").splitlines()


def is_error_line(line):
    return any(re.search(p, line, re.IGNORECASE) for p in ERROR_PATTERNS)


def find_root_cause_line(lines, rules):
    hits = [(i, line) for i, line in enumerate(lines) if is_error_line(line)]
    if not hits:
        return None, None

    sorted_rules = sorted(rules, key=lambda r: r["priority"])

    for rule in sorted_rules:
        for i, line in hits:
            for pat in rule["patterns"]:
                if re.search(pat, line, re.IGNORECASE | re.MULTILINE):
                    return i, rule

    return hits[0][0], None


def extract_error_excerpt(lines, before=3, after=15, max_lines_total=20):
    hits = [i for i, line in enumerate(lines) if is_error_line(line)]
    hit_count = len(hits)

    if not hits:
        tail_lines = [clean_line(l) for l in lines[-30:] if not is_noise(l)]
        return "\n".join(tail_lines).strip(), 0

    root_idx, _ = find_root_cause_line(lines, RULES)
    if root_idx is None:
        root_idx = hits[0]

    start = max(0, root_idx - before)
    end = min(len(lines), root_idx + after + 1)

    window = []
    for i in range(start, end):
        line = lines[i]
        if is_noise(line):
            continue
        window.append(clean_line(line))

    dedup = []
    for line in window:
        if not dedup or dedup[-1] != line:
            dedup.append(line)

    if len(dedup) > max_lines_total:
        dedup = dedup[:max_lines_total] + ["...[更多行已省略]"]

    return "\n".join(dedup), hit_count


def normalize_template(text):
    text = re.sub(r"/[A-Za-z0-9._+\-=/]+", "<PATH>", text)
    text = re.sub(r"\b[0-9a-f]{7,40}\b", "<HASH>", text)
    text = re.sub(r"\b\d+\.\d+(\.\d+)?\b", "<VERSION>", text)
    text = re.sub(r"\b\d+\b", "<NUM>", text)
    text = re.sub(r"'[^']+'", "'<STR>'", text)
    text = re.sub(r'"[^"]+"', '"<STR>"', text)
    return text


def classify(text):
    matched_rules = []
    for rule in RULES:
        matched = []
        for p in rule["patterns"]:
            if re.search(p, text, re.IGNORECASE | re.MULTILINE):
                matched.append(p)
        if matched:
            matched_rules.append({
                "rule": rule,
                "matched_patterns": matched,
                "hit_count": len(matched),
            })

    if not matched_rules:
        return {
            "rule_name": "unclassified",
            "main_category": "未分类错误",
            "sub_category": "规则未覆盖",
            "owner_side": "待人工判断",
            "action": "保留日志片段，后续补充分类规则",
            "matched_patterns": [],
            "all_matched_rules": [],
        }

    matched_rules.sort(key=lambda x: x["rule"]["priority"])
    best = matched_rules[0]
    rule = best["rule"]
    all_names = [m["rule"]["rule_name"] for m in matched_rules]

    return {
        "rule_name": rule["rule_name"],
        "main_category": rule["main_category"],
        "sub_category": rule["sub_category"],
        "owner_side": rule["owner_side"],
        "action": rule["action"],
        "matched_patterns": best["matched_patterns"],
        "all_matched_rules": all_names,
    }


def write_csv(path, row):
    fields = [
        "package", "stage", "result",
        "main_category", "sub_category", "owner_side", "action",
        "rule_name",
        "missing_header", "missing_package", "missing_library", "missing_command",
        "log_file", "error_excerpt_file", "template_excerpt_file",
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
    facts = extract_facts(full_text)

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
        "missing_header": facts.get("missing_header", ""),
        "missing_package": facts.get("missing_package", ""),
        "missing_library": facts.get("missing_library", ""),
        "missing_command": facts.get("missing_command", ""),
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
