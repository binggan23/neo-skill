#!/usr/bin/env bash
# 一键本地检查入口（与 .github/workflows/ci.yml 同一套命令）。
#
# 用法：
#   ./scripts/check.sh            # 静态检查 + 按文件分批跑测试
#   ./scripts/check.sh --fast     # 只跑静态检查（秒级，提交前用）
#   ./scripts/check.sh --static   # 只跑 ruff + mypy
#   ./scripts/check.sh --tests    # 只跑测试
#
# 为什么分批：单个测试文件就要几十秒到一分半（每个用例都起 CLI 子进程），
# 一条 `pytest tests/` 要 6-10 分钟。分批能立刻看到哪一批坏了，
# 也避免把整应用/大夹具一次挂进内存（AGENTS.md「测试与内存」）。
set -uo pipefail
cd "$(dirname "$0")/.."

RUN_STATIC=1
RUN_TESTS=1
case "${1:-}" in
  --fast|--static) RUN_TESTS=0 ;;
  --tests)         RUN_STATIC=0 ;;
  "")              ;;
  *) echo "未知参数：$1" >&2; exit 2 ;;
esac

fail=0
step() { printf '\n\033[1m== %s ==\033[0m\n' "$*"; }
ok()   { printf '  \033[32mOK\033[0m %s\n' "$*"; }
bad()  { printf '  \033[31mFAIL\033[0m %s\n' "$*"; fail=1; }

if [ "$RUN_STATIC" = 1 ]; then
  step "语法检查（compileall）"
  if python3 -m compileall -q skills/neo-skill/runtime >/dev/null; then
    ok "compileall"
  else
    bad "compileall"
  fi

  step "ruff（未用导入/变量、盲 except、bugbear）"
  if command -v ruff >/dev/null 2>&1; then
    if ruff check skills/neo-skill/runtime tests; then
      ok "ruff"
    else
      bad "ruff"
    fi
  else
    printf '  \033[33mSKIP\033[0m ruff 未安装（pip install ruff）\n'
  fi

  step "mypy（类型检查）"
  MYPY=""
  if command -v mypy >/dev/null 2>&1; then
    MYPY=mypy
  elif python3 -c "import mypy" >/dev/null 2>&1; then
    MYPY="python3 -m mypy"
  fi
  if [ -n "$MYPY" ]; then
    if $MYPY; then
      ok "mypy"
    else
      bad "mypy"
    fi
  else
    printf '  \033[33mSKIP\033[0m mypy 未安装（pip install mypy）\n'
  fi
fi

if [ "$RUN_TESTS" = 1 ]; then
  step "黑盒测试（按文件分批，单批失败立即中断）"
  for file in tests/test_*.py; do
    name="$(basename "$file")"
    if python3 -m pytest "$file" -q >/tmp/check-$$.log 2>&1; then
      ok "$name"
    else
      bad "$name"
      tail -30 /tmp/check-$$.log | sed 's/^/    /'
      rm -f /tmp/check-$$.log
      exit 1
    fi
  done
  rm -f /tmp/check-$$.log
fi

echo
if [ "$fail" = 0 ]; then
  printf '\033[32m全部通过\033[0m\n'
else
  printf '\033[31m有检查失败\033[0m\n'
fi
exit "$fail"
