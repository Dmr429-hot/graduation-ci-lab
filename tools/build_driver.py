#!/usr/bin/env python3
# -*- coding: utf-8 -*-

import argparse
import subprocess
import sys
from pathlib import Path

SUPPORTED_TYPES = {
    "cmake",
    "meson",
    "autotools-configure",
    "autotools-autogen",
    "autotools-bootstrap",
    "autotools-autoreconf",
    "makefile",
    "perl",
    "python",
    "rust",
    "go",
}

def call_result_writer(
    package: str,
    build_system: str,
    status: str,
    stage: str = "",
    summary_file: Path = None,
    error_excerpt_file: Path = None,
    reason: str = "",
):
    writer = Path(__file__).resolve().parent / "result_writer.py"

    cmd = [
        sys.executable,
        str(writer),
        "--status",
        status,
        "--package",
        package,
        "--type",
        build_system,
    ]

    if stage:
        cmd += ["--stage", stage]

    if summary_file:
        cmd += ["--summary-file", str(summary_file)]

    if error_excerpt_file:
        cmd += ["--error-excerpt-file", str(error_excerpt_file)]

    if reason:
        cmd += ["--reason", reason]

    subprocess.run(cmd, check=False)

def run_stage(
    package: str,
    build_system: str,
    stage: str,
    command: str,
    cwd: Path,
    outdir: Path,
    analyzer: Path,
):
    log_file = outdir / f"{stage}.log"

    print()
    print(f"========== 阶段: {stage} ==========")
    print(f"工作目录: {cwd}")
    print(f"命令: {command}")

    with log_file.open("w", encoding="utf-8", errors="ignore") as f:
        proc = subprocess.Popen(
            command,
            cwd=str(cwd),
            shell=True,
            executable="/bin/bash",
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
            errors="ignore",
        )

        assert proc.stdout is not None

        for line in proc.stdout:
            print(line, end="")
            f.write(line)

        status = proc.wait()

    result = "success" if status == 0 else "fail"

    subprocess.run([
        sys.executable,
        str(analyzer),
        "--package", package,
        "--stage", stage,
        "--result", result,
        "--log", str(log_file),
        "--outdir", str(outdir),
    ], check=False)

    summary_file = outdir / f"{stage}_summary.json"
    error_excerpt_file = outdir / f"{stage}_error_excerpt.txt"

    if status != 0:
        print()
        print(f"[失败] {stage} 阶段失败。")
        print(f"失败结果会写入 results/fail.csv")

        call_result_writer(
            package=package,
            build_system=build_system,
            status="fail",
            stage=stage,
            summary_file=summary_file,
            error_excerpt_file=error_excerpt_file,
        )

        sys.exit(status)

def autotools_commands(kind: str):
    prepare = ""

    if kind == "autotools-autogen":
        prepare = "chmod +x ./autogen.sh && ./autogen.sh"
    elif kind == "autotools-bootstrap":
        prepare = """
if [ -x ./bootstrap.sh ]; then
  ./bootstrap.sh
elif [ -x ./bootstrap ]; then
  ./bootstrap
else
  echo '没有找到可执行 bootstrap 脚本'
  exit 10
fi
"""
    elif kind == "autotools-autoreconf":
        prepare = "autoreconf -fi"
    elif kind == "autotools-configure":
        prepare = "echo '源码已经包含 configure，跳过 autoreconf'"

    configure = """
chmod +x ./configure || true
./configure --prefix="$PWD/_install"
"""

    build = """
JOBS="${JOBS:-$(nproc 2>/dev/null || echo 2)}"
make -j"$JOBS"
"""

    test = """
if make -n check >/dev/null 2>&1; then
  make check
elif make -n test >/dev/null 2>&1; then
  make test
else
  echo '没有发现 make check / make test，跳过测试阶段'
fi
"""

    return [
        ("prepare", prepare),
        ("configure", configure),
        ("build", build),
        ("test", test),
    ]

def get_plan(build_system: str, source: Path, outdir: Path):
    build_root = outdir / "build"
    build_root.mkdir(parents=True, exist_ok=True)

    if build_system == "cmake":
        build_dir = build_root / "cmake"

        return [
            (
                "configure",
                f'rm -rf "{build_dir}" && cmake -S "{source}" -B "{build_dir}" -DCMAKE_BUILD_TYPE=Release'
            ),
            (
                "build",
                f'cmake --build "{build_dir}" --parallel "$(nproc 2>/dev/null || echo 2)"'
            ),
            (
                "test",
                f'ctest --test-dir "{build_dir}" --output-on-failure'
            ),
        ]

    if build_system == "meson":
        build_dir = build_root / "meson"

        return [
            (
                "configure",
                f'rm -rf "{build_dir}" && meson setup "{build_dir}" "{source}" --prefix "{build_dir}/_install"'
            ),
            (
                "build",
                f'meson compile -C "{build_dir}"'
            ),
            (
                "test",
                f'meson test -C "{build_dir}" --print-errorlogs'
            ),
        ]

    if build_system.startswith("autotools"):
        return autotools_commands(build_system)

    if build_system == "makefile":
        return [
            (
                "build",
                'JOBS="${JOBS:-$(nproc 2>/dev/null || echo 2)}"; make -j"$JOBS"'
            ),
            (
                "test",
                """
if make -n check >/dev/null 2>&1; then
  make check
elif make -n test >/dev/null 2>&1; then
  make test
else
  echo '没有发现 make check / make test，跳过测试阶段'
fi
"""
            ),
        ]

    if build_system == "perl":
        return [
            (
                "configure",
                """
if [ -f Makefile.PL ]; then
  perl Makefile.PL
elif [ -f Build.PL ]; then
  perl Build.PL
else
  echo '未找到 Makefile.PL 或 Build.PL'
  exit 10
fi
"""
            ),
            (
                "build",
                """
if [ -f Makefile ]; then
  make
elif [ -x ./Build ]; then
  ./Build
else
  echo '未找到 Perl 构建入口'
  exit 10
fi
"""
            ),
            (
                "test",
                """
if [ -f Makefile ]; then
  make test
elif [ -x ./Build ]; then
  ./Build test
else
  echo '未找到 Perl 测试入口'
fi
"""
            ),
        ]

    if build_system == "python":
        return [
            (
                "build",
                "python3 -m compileall ."
            ),
            (
                "test",
                """
if [ -d tests ] || ls test*.py >/dev/null 2>&1; then
  python3 -m pytest -q
else
  echo '没有发现 Python 测试目录，跳过测试阶段'
fi
"""
            ),
        ]

    if build_system == "rust":
        return [
            ("build", "cargo build"),
            ("test", "cargo test"),
        ]

    if build_system == "go":
        return [
            ("build", "go build ./..."),
            ("test", "go test ./..."),
        ]

    return []

def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--package", required=True)
    parser.add_argument("--source", required=True)
    parser.add_argument("--build-system", required=True)
    parser.add_argument("--outdir", required=True)
    args = parser.parse_args()

    package = args.package
    source = Path(args.source).resolve()
    outdir = Path(args.outdir).resolve()
    analyzer = Path(__file__).resolve().parent / "analyze_ci_log.py"
    build_system = args.build_system

    outdir.mkdir(parents=True, exist_ok=True)

    if not source.exists():
        print(f"源码目录不存在: {source}")

        call_result_writer(
            package=package,
            build_system=build_system,
            status="other",
            reason="源码目录不存在，无法进入构建流程",
        )

        sys.exit(2)

    if build_system not in SUPPORTED_TYPES:
        print(f"[不构建] 当前构建类型暂不支持: {build_system}")

        call_result_writer(
            package=package,
            build_system=build_system,
            status="other",
            reason="当前构建类型无法识别或暂不支持自动构建",
        )

        sys.exit(0)

    plan = get_plan(build_system, source, outdir)

    if not plan:
        print(f"[不构建] 没有找到构建计划: {build_system}")

        call_result_writer(
            package=package,
            build_system=build_system,
            status="other",
            reason="没有匹配到可执行的构建计划",
        )

        sys.exit(0)

    for stage, command in plan:
        run_stage(
            package=package,
            build_system=build_system,
            stage=stage,
            command=command,
            cwd=source,
            outdir=outdir,
            analyzer=analyzer,
        )

    call_result_writer(
        package=package,
        build_system=build_system,
        status="suc",
    )

    print()
    print("========== 构建测试全部成功 ==========")
    print("成功结果已写入 results/suc.csv")

if __name__ == "__main__":
    main()
