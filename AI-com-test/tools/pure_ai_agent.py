#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
纯 AI 方案 - 用于对比实验

让 AI 全权决定如何构建测试一个软件包：
- 不用任何规则分类
- AI 看到日志后自己决定下一步命令
- 最多 N 轮循环

用途：和"规则+AI"方案做对比，论文用。

用法:
  python3 tools/pure_ai_agent.py                  # 跑所有 packages.csv 的包
  python3 tools/pure_ai_agent.py --package bluez  # 跑单个包
  python3 tools/pure_ai_agent.py --max-rounds 5   # 改最大轮数
"""

import argparse
import csv
import json
import os
import re
import shutil
import subprocess
import sys
import time
import urllib.request
from pathlib import Path
from datetime import datetime

ROOT = Path(__file__).resolve().parents[1]
CSV_FILE = ROOT / "data" / "packages.csv"
SOURCES_DIR = ROOT / "sources"
RESULTS_DIR = ROOT / "results"
LOG_DIR = ROOT / "logs" / "pure_ai"
LOG_DIR.mkdir(parents=True, exist_ok=True)

API_KEY = os.environ.get("DEEPSEEK_API_KEY", "")
API_URL = "https://api.deepseek.com/chat/completions"
PROXY = os.environ.get("HTTPS_PROXY") or os.environ.get("https_proxy") or ""

MAX_ROUNDS = 15               # AI 最多自由决策几轮（构建包通常需 8-12 轮）
PER_CMD_TIMEOUT = 300         # 单条命令超时（5分钟）
PER_PKG_TIMEOUT = 25 * 60     # 单包总超时（25分钟）
LOG_TRUNCATE = 4000           # 喂给 AI 的命令输出截到多长

# 命令白名单：只允许跑这些类型的命令
ALLOWED_CMD_PREFIXES = [
    "git ", "cd ", "ls", "cat ", "head ", "tail ", "pwd", "echo ",
    "cmake", "meson", "make", "ninja", "ctest",
    "./configure", "./autogen.sh", "./bootstrap",
    "autoreconf", "automake", "autoconf", "libtoolize",
    "sudo apt", "apt-get", "apt ",
    "pkg-config", "python3 ", "pip ", "pip3 ",
    "find ", "grep ", "test ",
    "which ", "type ", "command -v",
]

# 黑名单：含这些字符的命令一律拒绝执行（即使在白名单里）
DANGEROUS_PATTERNS = [
    r"rm\s+-rf?\s+/",       # rm -rf /
    r"rm\s+-rf?\s+~",       # rm -rf ~
    r"\bdd\s+",             # dd 命令
    r"mkfs",                # 格式化
    r"shutdown",
    r"reboot",
    r">/dev/sd",            # 写设备
    r"chmod\s+777\s+/",     # 大范围权限
    r":(){:|:&};:",         # fork bomb
    r"curl.*\|\s*sh",       # 管道执行远程脚本
    r"wget.*\|\s*sh",
]


def is_safe_command(cmd):
    cmd_strip = cmd.strip()
    if not cmd_strip:
        return False, "空命令"
    # 黑名单检查
    for pat in DANGEROUS_PATTERNS:
        if re.search(pat, cmd_strip):
            return False, f"匹配危险模式: {pat}"
    # 白名单检查
    for prefix in ALLOWED_CMD_PREFIXES:
        if cmd_strip.startswith(prefix):
            return True, "ok"
    return False, "命令前缀不在白名单"


def call_ai(messages, log_file):
    """调 DeepSeek API。返回 (content_dict, token_usage_dict)"""
    if not API_KEY:
        raise RuntimeError("DEEPSEEK_API_KEY 未设置")

    body = {
        "model": "deepseek-chat",
        "messages": messages,
        "temperature": 0.0,
        "max_tokens": 800,
        "response_format": {"type": "json_object"},
    }
    data = json.dumps(body).encode()
    req = urllib.request.Request(
        API_URL,
        data=data,
        headers={
            "Content-Type": "application/json",
            "Authorization": f"Bearer {API_KEY}",
        },
    )

    handler = urllib.request.ProxyHandler({"https": PROXY, "http": PROXY}) if PROXY else urllib.request.ProxyHandler({})
    opener = urllib.request.build_opener(handler)

    for attempt in range(3):
        try:
            resp = opener.open(req, timeout=60).read().decode()
            obj = json.loads(resp)
            content = obj["choices"][0]["message"]["content"]
            usage = obj.get("usage", {})
            log_file.write(f"\n[AI回复]\n{content}\n[token使用] {usage}\n")
            log_file.flush()
            return json.loads(content), usage
        except Exception as e:
            log_file.write(f"\n[AI调用失败 第{attempt+1}次] {e}\n")
            time.sleep(2 ** attempt)
    raise RuntimeError("AI 调用 3 次失败")


def run_shell(cmd, cwd, log_file):
    """执行一条 shell 命令，返回 (returncode, output_text, duration_sec)"""
    log_file.write(f"\n[执行命令] (cwd={cwd})\n  $ {cmd}\n")
    log_file.flush()

    t0 = time.time()
    try:
        result = subprocess.run(
            cmd,
            shell=True,
            cwd=cwd,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            timeout=PER_CMD_TIMEOUT,
            text=True,
            errors="ignore",
        )
        elapsed = time.time() - t0
        output = result.stdout or ""
        log_file.write(output[:LOG_TRUNCATE])
        log_file.write(f"\n[返回码 {result.returncode}, 耗时 {elapsed:.1f}秒]\n")
        log_file.flush()
        return result.returncode, output, elapsed
    except subprocess.TimeoutExpired:
        elapsed = time.time() - t0
        log_file.write(f"\n[超时 {PER_CMD_TIMEOUT}秒]\n")
        log_file.flush()
        return -1, "[命令超时]", elapsed
    except Exception as e:
        elapsed = time.time() - t0
        log_file.write(f"\n[执行异常] {e}\n")
        log_file.flush()
        return -1, str(e), elapsed


SYSTEM_PROMPT = """你是 Linux 软件包构建专家，需要从零开始把一个开源软件包构建并测试通过。

# 你能做的事
- 用 git clone 仓库
- 进入源码目录后识别构建系统（cmake/meson/autotools/make）
- 调用对应工具构建（configure / make / cmake --build / meson setup / meson compile）
- 跑测试（make test / ctest / meson test）
- 失败时用 apt install 装缺失的依赖（命令前缀必须是 sudo apt install -y）

# 工作流程
每一轮你会收到：
- 当前已经执行的命令历史
- 上一条命令的输出

# 效率要求（你只有 15 轮）
- 不要做 which / type 这种确认工具是否存在的命令（默认都装了）
- clone 后直接看 ls 一次，然后立刻 meson setup / cmake / ./configure
- 配置成功后立刻 build/compile，不要 ls 重复看目录
- 失败时优先 sudo apt install 装 dev 包，装完立刻重跑 setup

你需要决定下一条命令，并按下面 JSON 格式严格回复：

{
  "thought": "一句话说明你想做什么",
  "command": "下一条要执行的 shell 命令",
  "is_done": false,
  "is_success": false,
  "reason": ""
}

# 完成判断
当且仅当你认为整个构建测试已经通过（或永久失败放弃）时：
- is_done = true
- is_success = true/false
- reason = "成功/失败的简短原因"
- command 留空

# 安全约束
- 不要执行 rm -rf、dd、mkfs、shutdown 等危险命令
- 不要尝试改系统配置文件
- 不要用 curl|sh、wget|sh 之类下载远程脚本执行
- 单条命令不要超过 300 字符
- 装包用 sudo apt install -y 包名（不要交互模式）

# 重要约束
- 你在一个工作目录里跑，git clone 应该 clone 到当前目录的子目录
- 不要尝试 cd 到很深的路径（最多到 sources/包名/）
- 失败时优先考虑装包，而不是改源码
- 装一次包之后要重新跑构建命令（configure 或 cmake 或 meson setup）

只输出 JSON，不要任何 markdown 标记。
"""


def run_one_package(package, repo, log_file_path):
    """跑一个包，让 AI 全权决策。返回结果字典。"""
    pkg_log = open(log_file_path, "w", encoding="utf-8")
    pkg_log.write(f"========== 包: {package} ==========\n")
    pkg_log.write(f"仓库: {repo}\n")
    pkg_log.write(f"开始时间: {datetime.now()}\n")
    pkg_log.flush()

    # 清理可能存在的源码
    src_dir = SOURCES_DIR / package
    if src_dir.exists():
        shutil.rmtree(src_dir)

    SOURCES_DIR.mkdir(parents=True, exist_ok=True)

    # 初始用户消息
    initial_msg = (
        f"请构建并测试软件包：{package}\n"
        f"仓库地址：{repo}\n"
        f"工作目录：{SOURCES_DIR}（你可以先 git clone 到这里的子目录）\n\n"
        f"请给出第一条命令。"
    )
    messages = [
        {"role": "system", "content": SYSTEM_PROMPT},
        {"role": "user", "content": initial_msg},
    ]

    cwd = str(SOURCES_DIR)
    total_tokens = 0
    commands_run = []
    t_start = time.time()
    final_status = "unknown"
    final_reason = ""

    for round_num in range(1, MAX_ROUNDS + 1):
        pkg_log.write(f"\n========== 第 {round_num}/{MAX_ROUNDS} 轮 ==========\n")
        pkg_log.flush()

        # 总超时检查
        if time.time() - t_start > PER_PKG_TIMEOUT:
            final_status = "pkg_timeout"
            final_reason = f"包总超时 {PER_PKG_TIMEOUT//60} 分钟"
            break

        # 调 AI
        try:
            ai_result, usage = call_ai(messages, pkg_log)
        except Exception as e:
            final_status = "ai_failed"
            final_reason = f"AI调用失败: {e}"
            break

        total_tokens += usage.get("total_tokens", 0)

        thought = ai_result.get("thought", "")
        command = ai_result.get("command", "")
        is_done = ai_result.get("is_done", False)
        is_success = ai_result.get("is_success", False)
        reason = ai_result.get("reason", "")

        # AI 主动结束
        if is_done:
            final_status = "fixed" if is_success else "ai_gave_up"
            final_reason = reason
            pkg_log.write(f"\n[AI 结束] is_success={is_success}, reason={reason}\n")
            break

        if not command:
            final_status = "no_command"
            final_reason = "AI 没有给出下一步命令"
            break

        # 命令安全检查
        safe, msg = is_safe_command(command)
        if not safe:
            pkg_log.write(f"\n[拒绝执行] {msg}: {command}\n")
            # 把拒绝原因告诉 AI 让它换一个
            messages.append({"role": "assistant", "content": json.dumps(ai_result, ensure_ascii=False)})
            messages.append({"role": "user", "content": f"刚才的命令被拒绝执行：{msg}\n请换一个安全的命令。"})
            continue

        # 处理 cd（subprocess 默认每次都新 shell，cd 不会持久）
        cd_match = re.match(r"cd\s+(\S+)\s*(&&\s*(.+))?$", command.strip())
        if cd_match:
            new_dir = cd_match.group(1)
            rest_cmd = cd_match.group(3)
            # 把 cd 目标设为后续 cwd
            target = Path(new_dir) if Path(new_dir).is_absolute() else Path(cwd) / new_dir
            if target.exists() and target.is_dir():
                cwd = str(target.resolve())
                pkg_log.write(f"\n[cd] 切换到: {cwd}\n")
                if rest_cmd:
                    # 执行 cd 后面的命令
                    rc, output, dur = run_shell(rest_cmd, cwd, pkg_log)
                    commands_run.append({"cmd": rest_cmd, "cwd": cwd, "rc": rc, "duration": dur})
                else:
                    rc, output, dur = 0, f"已切换到 {cwd}", 0
            else:
                rc, output, dur = 1, f"目录不存在: {target}", 0
                pkg_log.write(f"\n[cd 失败] 目录不存在: {target}\n")
        else:
            rc, output, dur = run_shell(command, cwd, pkg_log)
            commands_run.append({"cmd": command, "cwd": cwd, "rc": rc, "duration": dur})

        # 构造给 AI 的反馈
        truncated = output[:LOG_TRUNCATE]
        if len(output) > LOG_TRUNCATE:
            truncated += f"\n...[输出过长截断，原始 {len(output)} 字符]"
        feedback = (
            f"上一条命令返回码: {rc}\n"
            f"当前目录: {cwd}\n"
            f"输出（截断）:\n{truncated}\n\n"
            f"请给出下一条命令，或在已经成功/放弃时 is_done=true。"
        )
        messages.append({"role": "assistant", "content": json.dumps(ai_result, ensure_ascii=False)})
        messages.append({"role": "user", "content": feedback})

        # 简单成功判断：如果连续两次 rc=0 且命令是 make test/ctest/meson test，认为成功
        # （AI 也可能自己判断，这里是兜底）

    else:
        # for-else: 没 break 说明用完了所有轮数
        final_status = "rounds_exhausted"
        final_reason = f"用完 {MAX_ROUNDS} 轮"

    total_elapsed = time.time() - t_start

    # 清理源码
    if src_dir.exists():
        try:
            shutil.rmtree(src_dir)
        except Exception:
            pass

    pkg_log.write(f"\n========== 总结 ==========\n")
    pkg_log.write(f"最终状态: {final_status}\n")
    pkg_log.write(f"原因: {final_reason}\n")
    pkg_log.write(f"执行命令数: {len(commands_run)}\n")
    pkg_log.write(f"消耗 token: {total_tokens}\n")
    pkg_log.write(f"总耗时: {total_elapsed:.1f}秒\n")
    pkg_log.close()

    return {
        "package": package,
        "repo": repo,
        "final_status": final_status,
        "reason": final_reason,
        "rounds_used": round_num,
        "commands_count": len(commands_run),
        "total_tokens": total_tokens,
        "duration_sec": round(total_elapsed, 1),
        "log_file": str(log_file_path),
    }


def write_csv_row(csv_path, row):
    fields = [
        "package", "repo", "final_status", "reason",
        "rounds_used", "commands_count", "total_tokens",
        "duration_sec", "log_file",
    ]
    exists = csv_path.exists() and csv_path.stat().st_size > 0
    with csv_path.open("a", encoding="utf-8-sig", newline="") as f:
        w = csv.DictWriter(f, fieldnames=fields)
        if not exists:
            w.writeheader()
        w.writerow({k: row.get(k, "") for k in fields})


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--package", help="只跑一个包（包名）")
    parser.add_argument("--repo", help="单包模式的仓库地址")
    parser.add_argument("--max-rounds", type=int, default=MAX_ROUNDS, help=f"AI 最大轮数（默认 {MAX_ROUNDS}）")
    parser.add_argument("--output", default=str(RESULTS_DIR / "pure_ai_results.csv"), help="结果CSV路径")
    args = parser.parse_args()

    # 用模块级变量赋值（避免 SyntaxError）
    import sys as _sys
    _sys.modules[__name__].MAX_ROUNDS = args.max_rounds

    if not API_KEY:
        print("错误：环境变量 DEEPSEEK_API_KEY 未设置")
        sys.exit(1)

    RESULTS_DIR.mkdir(parents=True, exist_ok=True)
    output_csv = Path(args.output)

    # 决定要跑哪些包
    if args.package:
        if not args.repo:
            # 从 packages.csv 找
            with CSV_FILE.open(encoding="utf-8-sig") as f:
                rows = [r for r in csv.DictReader(f) if r.get("package", "").strip() == args.package]
            if not rows:
                print(f"错误：在 packages.csv 找不到 {args.package}，请用 --repo 显式指定")
                sys.exit(1)
            todo = [(rows[0]["package"], rows[0]["repo"])]
        else:
            todo = [(args.package, args.repo)]
    else:
        # 跑全部
        with CSV_FILE.open(encoding="utf-8-sig") as f:
            todo = [(r["package"].strip(), r["repo"].strip())
                    for r in csv.DictReader(f)
                    if r.get("package", "").strip() and r.get("repo", "").strip()]

    print(f"========== 纯 AI Agent 实验 ==========")
    print(f"包数量: {len(todo)}")
    print(f"AI 最大轮数: {MAX_ROUNDS}")
    print(f"单包总超时: {PER_PKG_TIMEOUT//60} 分钟")
    print(f"结果 CSV: {output_csv}")
    print(f"日志目录: {LOG_DIR}")
    print()

    # 加载已有结果，断点续跑
    done = set()
    if output_csv.exists():
        with output_csv.open(encoding="utf-8-sig") as f:
            for r in csv.DictReader(f):
                done.add(r.get("package", "").strip())
    print(f"已跑过: {len(done)} 个，跳过\n")

    t_total = time.time()
    stats = {"fixed": 0, "ai_gave_up": 0, "rounds_exhausted": 0,
             "pkg_timeout": 0, "ai_failed": 0, "no_command": 0}

    for idx, (pkg, repo) in enumerate(todo, 1):
        if pkg in done:
            continue
        print(f"\n[{idx}/{len(todo)}] {pkg}")
        log_path = LOG_DIR / f"{pkg}_{datetime.now().strftime('%Y%m%d_%H%M%S')}.log"
        try:
            result = run_one_package(pkg, repo, log_path)
        except Exception as e:
            print(f"  异常: {e}")
            result = {
                "package": pkg, "repo": repo,
                "final_status": "exception", "reason": str(e),
                "rounds_used": 0, "commands_count": 0,
                "total_tokens": 0, "duration_sec": 0,
                "log_file": str(log_path),
            }
        write_csv_row(output_csv, result)
        status = result["final_status"]
        stats[status] = stats.get(status, 0) + 1
        print(f"  → {status} | 轮次={result['rounds_used']} | 命令={result['commands_count']} | token={result['total_tokens']} | 耗时={result['duration_sec']}秒")

    total_elapsed = (time.time() - t_total) / 60
    print(f"\n========== 实验结束 ==========")
    print(f"总耗时: {total_elapsed:.1f} 分钟")
    print(f"结果分布:")
    for k, v in stats.items():
        print(f"  {k}: {v}")
    print(f"\n结果 CSV: {output_csv}")


if __name__ == "__main__":
    main()
