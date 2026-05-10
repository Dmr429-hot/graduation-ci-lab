#!/usr/bin/env python3
# -*- coding: utf-8 -*-

import json
import sys
from pathlib import Path

RULES = [
    ("cmake", ["CMakeLists.txt"]),
    ("meson", ["meson.build"]),
    ("autotools-configure", ["configure"]),
    ("autotools-autogen", ["autogen.sh"]),
    ("autotools-bootstrap", ["bootstrap.sh", "bootstrap"]),
    ("autotools-autoreconf", ["configure.ac", "configure.in", "Makefile.am"]),
    ("makefile", ["Makefile", "makefile", "GNUmakefile"]),
    ("perl", ["Makefile.PL", "Build.PL"]),
    ("python", ["pyproject.toml", "setup.py", "setup.cfg"]),
    ("rust", ["Cargo.toml"]),
    ("go", ["go.mod"]),
]

IGNORE_DIRS = {
    ".git", ".hg", ".svn",
    "build", "dist", "__pycache__",
    "node_modules", ".github"
}

def collect_files(root: Path):
    top_files = set()
    all_files = set()

    for p in root.iterdir():
        if p.is_file():
            top_files.add(p.name)

    for p in root.rglob("*"):
        if any(part in IGNORE_DIRS for part in p.parts):
            continue
        if p.is_file():
            all_files.add(p.name)

    return top_files, all_files

def main():
    if len(sys.argv) != 2:
        print("用法: detect_build_system.py 源码目录", file=sys.stderr)
        sys.exit(1)

    root = Path(sys.argv[1]).resolve()

    if not root.exists():
        print(json.dumps({
            "source_dir": str(root),
            "build_system": "missing-source-dir",
            "matched": []
        }, ensure_ascii=False, indent=2))
        sys.exit(2)

    top_files, all_files = collect_files(root)

    matched = []

    for build_type, markers in RULES:
        top_hit = [m for m in markers if m in top_files]
        all_hit = [m for m in markers if m in all_files]

        if top_hit:
            matched.append({
                "type": build_type,
                "level": "top",
                "matched_files": top_hit
            })
        elif all_hit:
            matched.append({
                "type": build_type,
                "level": "recursive",
                "matched_files": all_hit[:10]
            })

    build_system = matched[0]["type"] if matched else "unknown"

    print(json.dumps({
        "source_dir": str(root),
        "build_system": build_system,
        "matched": matched
    }, ensure_ascii=False, indent=2))

if __name__ == "__main__":
    main()
