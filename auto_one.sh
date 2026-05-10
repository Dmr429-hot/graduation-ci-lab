#!/usr/bin/env bash
set -euo pipefail

PKG="${1:-}"
REPO="${2:-}"

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
SRC_DIR="$ROOT_DIR/sources/$PKG"
TOOLS_DIR="$ROOT_DIR/tools"
RESULTS_DIR="$ROOT_DIR/results"

if [ -z "$PKG" ] || [ -z "$REPO" ]; then
  echo "用法:"
  echo "  ./auto_one.sh 软件包名 上游仓库链接"
  echo
  echo "示例:"
  echo "  ./auto_one.sh dhcpdump https://github.com/dhcpdump-org/dhcpdump.git"
  exit 1
fi

mkdir -p "$RESULTS_DIR"
mkdir -p "$ROOT_DIR/sources"

TMP_DIR="$(mktemp -d -t "${PKG}.XXXXXX")"

cleanup() {
  rm -rf "$TMP_DIR"
}

trap cleanup EXIT

echo "========== 软件包: $PKG =========="
echo "========== 仓库: $REPO =========="
echo "========== 源码目录: $SRC_DIR =========="
echo "========== 临时日志目录: $TMP_DIR =========="
echo "========== 最终结果目录: $RESULTS_DIR =========="

echo
echo "========== 1. 克隆源码 =========="

if [ -d "$SRC_DIR/.git" ]; then
  echo "[跳过] 源码目录已存在: $SRC_DIR"
  echo "[提示] 如果想重新克隆，请先删除: rm -rf $SRC_DIR"
else
  rm -rf "$SRC_DIR"

  set +e
  git clone --recursive "$REPO" "$SRC_DIR" 2>&1 | tee "$TMP_DIR/clone.log"
  CLONE_STATUS=${PIPESTATUS[0]}
  set -e

  if [ "$CLONE_STATUS" -ne 0 ]; then
    echo "[失败] git clone 失败，开始分析日志。"

    python3 "$TOOLS_DIR/analyze_ci_log.py" \
      --package "$PKG" \
      --stage clone \
      --result fail \
      --log "$TMP_DIR/clone.log" \
      --outdir "$TMP_DIR"

    python3 "$TOOLS_DIR/result_writer.py" \
      --status fail \
      --package "$PKG" \
      --type "not_cloned" \
      --stage clone \
      --summary-file "$TMP_DIR/clone_summary.json" \
      --error-excerpt-file "$TMP_DIR/clone_error_excerpt.txt"

    exit "$CLONE_STATUS"
  fi
fi

echo
echo "========== 2. 更新子模块 =========="
git -C "$SRC_DIR" submodule update --init --recursive 2>&1 | tee "$TMP_DIR/submodule.log" || true

echo
echo "========== 3. 识别构建方式 =========="
python3 "$TOOLS_DIR/detect_build_system.py" "$SRC_DIR" | tee "$TMP_DIR/build_system.json"

BUILD_SYSTEM="$(python3 - "$TMP_DIR/build_system.json" <<'PYEOF'
import json
import sys

with open(sys.argv[1], "r", encoding="utf-8") as f:
    data = json.load(f)

print(data.get("build_system", "unknown"))
PYEOF
)"

echo
echo "识别结果: $BUILD_SYSTEM"

case "$BUILD_SYSTEM" in
  cmake|meson|autotools-configure|autotools-autogen|autotools-bootstrap|autotools-autoreconf|makefile|perl|python|rust|go)
    echo "[进入构建] 当前类型支持自动构建: $BUILD_SYSTEM"
    ;;
  *)
    echo "[不构建] 当前类型暂不进入构建流程: $BUILD_SYSTEM"

    python3 "$TOOLS_DIR/result_writer.py" \
      --status other \
      --package "$PKG" \
      --type "$BUILD_SYSTEM" \
      --reason "当前构建类型无法识别或暂不支持自动构建"

    exit 0
    ;;
esac

echo
echo "========== 4. 自动构建与测试 =========="
python3 "$TOOLS_DIR/build_driver.py" \
  --package "$PKG" \
  --source "$SRC_DIR" \
  --build-system "$BUILD_SYSTEM" \
  --outdir "$TMP_DIR"

echo
echo "========== 完成 =========="
echo "最终结果目录: $RESULTS_DIR"
echo
echo "查看结果:"
echo "  cat results/suc.csv"
echo "  cat results/fail.csv"
echo "  cat results/other.csv"
