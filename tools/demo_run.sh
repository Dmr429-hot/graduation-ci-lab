#!/bin/bash
# 演示用单包交互脚本（多轮版）：失败 → AI建议 → 人工修复 → 重跑 → ... 直到成功或3轮放弃
#
# 用法:
#   ./tools/demo_run.sh <包名> <仓库地址>

set -e
ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT_DIR"

PKG="${1:-}"
REPO="${2:-}"

if [ -z "$PKG" ] || [ -z "$REPO" ]; then
  echo "用法: $0 <包名> <仓库地址>"
  exit 1
fi

# 配置
MAX_ROUNDS=3

# 颜色
B='\033[1m'; G='\033[32m'; R='\033[31m'
Y='\033[33m'; C='\033[36m'; N='\033[0m'

banner() {
  echo
  echo -e "${C}────────────────────────────────────────────────────────────${N}"
  echo -e "${C}  $1${N}"
  echo -e "${C}────────────────────────────────────────────────────────────${N}"
}

pause() {
  echo
  read -p "$(echo -e "${Y}>>> $1 [回车继续]${N} ")" _
  echo
}

# 清理包痕迹
clean_pkg() {
  rm -rf "sources/$PKG"
  python3 - <<PYEOF
import csv
for name in ['suc.csv','fail.csv','other.csv']:
    p = f'results/{name}'
    try:
        with open(p, encoding='utf-8-sig') as f:
            rows = list(csv.DictReader(f))
            fields = list(rows[0].keys()) if rows else None
        if fields:
            keep = [r for r in rows if r.get('package','').strip() != '$PKG']
            with open(p, 'w', encoding='utf-8-sig', newline='') as f:
                w = csv.DictWriter(f, fieldnames=fields)
                w.writeheader(); w.writerows(keep)
    except (FileNotFoundError, IndexError):
        pass
PYEOF
}

# 跑一次构建
run_build() {
  local round=$1
  ./auto_one.sh "$PKG" "$REPO" > "/tmp/demo_${PKG}_round${round}.log" 2>&1 || true

  if grep -q "构建测试全部成功" "/tmp/demo_${PKG}_round${round}.log"; then
    return 0   # 成功
  else
    return 1   # 失败
  fi
}

# 展示分类
show_classification() {
  python3 - <<PYEOF
import csv
with open('results/fail.csv', encoding='utf-8-sig') as f:
    for r in csv.DictReader(f):
        if r['package'] == '$PKG':
            print(f"  包名:           {r['package']}")
            print(f"  构建系统:       {r['type']}")
            print(f"  失败阶段:       {r['failed_stage']}")
            print()
            print(f"  ◆ 主分类:       {r['main_category']}")
            print(f"  ◆ 子分类:       {r['sub_category']}")
            print(f"  ◆ 归属侧:       {r['owner_side']}")
            print(f"  ◆ 通用动作:     {r['action']}")
            print()
            if r.get('missing_package'):
                print(f"  抽取的关键信息:")
                print(f"    缺失的包:     {r['missing_package']}")
            if r.get('missing_header'):
                print(f"    缺失的头文件: {r['missing_header']}")
            if r.get('missing_library'):
                print(f"    缺失的库:     {r['missing_library']}")
            if r.get('missing_command'):
                print(f"    缺失的命令:   {r['missing_command']}")
            print()
            print(f"  关键日志片段:")
            excerpt = r['error_excerpt'][:400].replace('\\n', '\n')
            for line in excerpt.split('\n'):
                if line.strip():
                    print(f"    {line.strip()}")
            break
PYEOF
}

# 调AI拿建议，返回 0=apt_install成功获取 / 1=manual/skip / 2=AI调用失败
call_ai() {
  AI_OUTPUT=$(python3 tools/ai_helper.py --package "$PKG" --json 2>&1) || return 2

  DIAGNOSIS=$(echo "$AI_OUTPUT" | python3 -c "import sys, json; d=json.load(sys.stdin); print(d.get('diagnosis',''))" 2>/dev/null)
  FIX_TYPE=$(echo "$AI_OUTPUT" | python3 -c "import sys, json; d=json.load(sys.stdin); print(d.get('fix_type',''))" 2>/dev/null)
  COMMAND=$(echo "$AI_OUTPUT" | python3 -c "import sys, json; d=json.load(sys.stdin); print(d.get('command',''))" 2>/dev/null)
  CONFIDENCE=$(echo "$AI_OUTPUT" | python3 -c "import sys, json; d=json.load(sys.stdin); print(d.get('confidence',''))" 2>/dev/null)
  NOTES=$(echo "$AI_OUTPUT" | python3 -c "import sys, json; d=json.load(sys.stdin); print(d.get('notes',''))" 2>/dev/null)

  echo -e "  ◆ AI诊断:       ${B}${DIAGNOSIS}${N}"
  echo -e "  ◆ 修复类型:     ${FIX_TYPE}"
  echo -e "  ◆ 置信度:       ${CONFIDENCE}"
  if [ -n "$NOTES" ]; then
    echo -e "  ◆ 补充说明:     ${NOTES}"
  fi

  if [ "$FIX_TYPE" != "apt_install" ]; then
    echo
    echo -e "${Y}  AI判断需要手动修复（${FIX_TYPE}），无法通过apt自动修复${N}"
    echo -e "${Y}  详细建议：${COMMAND:-（详见ai_suggestions.csv）}${N}"
    return 1
  fi

  echo
  echo -e "  ${B}建议执行的命令:${N}"
  echo
  echo -e "      ${G}${COMMAND}${N}"
  return 0
}

# 执行修复命令
execute_fix() {
  # 检查是否已经装上（用户可能在另一终端装了）
  APT_PKGS=$(echo "$AI_OUTPUT" | python3 -c "
import sys, json
d = json.load(sys.stdin)
pkgs = d.get('apt_packages', [])
print(' '.join(pkgs) if isinstance(pkgs, list) else pkgs)
" 2>/dev/null)

  ALL_INSTALLED=true
  for p in $APT_PKGS; do
    if ! dpkg -l "$p" 2>/dev/null | grep -q "^ii"; then
      ALL_INSTALLED=false
      break
    fi
  done

  if [ "$ALL_INSTALLED" = true ] && [ -n "$APT_PKGS" ]; then
    echo -e "  ${G}● 包已安装（您在另一终端完成了修复）${N}"
  else
    echo "  自动执行: $COMMAND"
    echo
    eval "$COMMAND"
  fi
}

# ════════════════════════════════════════════════════
# 主流程开始
# ════════════════════════════════════════════════════

clear

banner "演示：${PKG} 构建测试 + 失败分析 + AI辅助修复 (多轮版)"
echo
echo -e "  包名:     ${B}${PKG}${N}"
echo -e "  仓库:     ${REPO}"
echo
echo "  本演示流程:"
echo "    1. 构建测试 → 失败 → 失败分类 → AI建议 → 修复 → 重跑"
echo "    2. 如果仍失败 → 再次AI建议 → 再次修复 → 再重跑"
echo -e "    3. 最多 ${B}${MAX_ROUNDS}${N} 轮，体现真实工程中的迭代调试过程"

pause "开始演示"

# 初始清理
clean_pkg

# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# 多轮循环
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

for round in $(seq 1 $MAX_ROUNDS); do

  banner "第 ${round} 轮 / 共 ${MAX_ROUNDS} 轮"

  # 第1步：构建
  if [ "$round" -eq 1 ]; then
    echo "首次构建测试..."
  else
    echo "上一轮已安装相关依赖，本轮重新构建..."
  fi
  echo "执行: ./auto_one.sh $PKG $REPO"
  echo "（构建中...）"
  echo

  if run_build $round; then
    # 成功，退出循环
    banner "构建成功"
    echo -e "${G}━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━${N}"
    echo -e "${G}  ${PKG} 在第 ${round} 轮迭代后构建成功${N}"
    echo -e "${G}━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━${N}"
    echo
    echo "  闭环验证:"
    echo "    构建失败 → 系统分类 → AI建议 → 人工修复 → 重新构建成功"
    echo "    总迭代轮次: ${round}"
    echo
    echo "  当前状态:"
    grep "^${PKG}," results/suc.csv 2>/dev/null
    echo
    echo "  日志位置: /tmp/demo_${PKG}_round*.log"
    exit 0
  fi

  # 失败
  FAIL_STAGE=$(grep -oP "失败\]\s*\K\w+" "/tmp/demo_${PKG}_round${round}.log" | head -1)
  echo -e "  ${R}● 构建失败${N}（阶段：${FAIL_STAGE:-未知}）"

  pause "查看本轮失败分类"

  # 第2步：分类
  banner "本轮失败分类"
  show_classification

  pause "调用AI生成修复建议"

  # 第3步：AI建议
  banner "AI修复建议（DeepSeek）"
  echo "  调用中..."
  echo

  call_ai
  ai_ret=$?

  if [ "$ai_ret" -eq 1 ]; then
    # AI说不能apt修复
    echo
    echo "  本次演示到此结束（需要人工介入）。"
    exit 0
  elif [ "$ai_ret" -eq 2 ]; then
    echo
    echo -e "${R}  AI调用失败，请检查 DEEPSEEK_API_KEY 和网络${N}"
    exit 1
  fi

  echo

  pause "执行修复命令（按回车自动执行，或先在另一终端手动执行）"

  # 第4步：修复
  banner "执行修复"
  execute_fix

  if [ "$round" -lt "$MAX_ROUNDS" ]; then
    pause "进入下一轮构建测试"
  fi

done

# 跑完 MAX_ROUNDS 轮仍失败
echo
banner "经过 ${MAX_ROUNDS} 轮迭代仍未成功"
echo
echo "  系统已经尽力分析并修复，但 ${PKG} 仍需人工介入。"
echo
echo "  可能原因:"
echo "    - 该包依赖很多，需要 ${MAX_ROUNDS}+ 个包"
echo "    - 存在 apt 无法解决的源码问题"
echo "    - 上游依赖版本不匹配"
echo
echo "  详细日志:"
for i in $(seq 1 $MAX_ROUNDS); do
  echo "    第${i}轮: /tmp/demo_${PKG}_round${i}.log"
done
echo
echo "  这本身也是一个有意义的演示结果：系统准确诊断了多重依赖问题。"
