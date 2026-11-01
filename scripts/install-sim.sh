#!/usr/bin/env bash
# 安装模拟：把「随包分发的 Skill」复制到隔离项目的 .claude/skills/，
# 行为对齐 `npx skills@latest add binggan23/neo-skill -a claude`（skills.sh 只复制 skill 目录）。
#
# 为什么需要它：`main` 分支目前没有 `skills/` 目录，远端 `npx skills add` 返回
# `No skills found`（见 docs/pilot-report.md 的「已知阻塞」）。安装模拟用**文件系统路径**
# 复现同一套复制语义，从而在没有远端的情况下验证：
#   1. 六个 SKILL.md（五个阶段 + 路由层）都被识别（有 name 与 description）；
#   2. `runtime/`（flow-core + 声明式规则 + 模板）随 skill 目录一起走；
#   3. 安装后的 flow-core 只靠**相对位置**解析资源，不依赖源码仓库路径。
#
# 用法：
#   scripts/install-sim.sh --target /tmp/proj          # 装到 /tmp/proj/.claude/skills/
#   scripts/install-sim.sh --target DIR --into skills  # 自定义子目录（默认 .claude/skills）
#   scripts/install-sim.sh --target DIR --verify-only   # 只验证已有安装
set -euo pipefail

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
TARGET=""
INTO=".claude/skills"
VERIFY_ONLY=0

while [ $# -gt 0 ]; do
  case "$1" in
    --target) TARGET="${2:?--target 需要目录}"; shift 2 ;;
    --into) INTO="${2:?--into 需要子目录}"; shift 2 ;;
    --verify-only) VERIFY_ONLY=1; shift ;;
    -h|--help) sed -n '2,20p' "${BASH_SOURCE[0]}" | sed 's/^# \{0,1\}//'; exit 0 ;;
    *) echo "未知参数：$1" >&2; exit 2 ;;
  esac
done

[ -n "$TARGET" ] || { echo "必须给 --target" >&2; exit 2; }
mkdir -p "$TARGET"
TARGET="$(cd "$TARGET" && pwd)"
SKILLS_DIR="$TARGET/$INTO"

SKILL_NAMES="neo-skill project requirements prd dev acceptance"

if [ "$VERIFY_ONLY" = "0" ]; then
  rm -rf "$SKILLS_DIR"
  mkdir -p "$SKILLS_DIR"
  for name in $SKILL_NAMES; do
    src="$REPO_ROOT/skills/$name"
    [ -d "$src" ] || { echo "缺少 skill 目录：$src" >&2; exit 1; }
    mkdir -p "$SKILLS_DIR/$name"
    cp "$src/SKILL.md" "$SKILLS_DIR/$name/SKILL.md"
    # runtime 只跟 neo-skill 走（CLI、规则、模板和它同一个包）
    if [ -d "$src/runtime" ]; then
      cp -R "$src/runtime" "$SKILLS_DIR/$name/runtime"
    fi
  done
fi

# ---- 资源定位：四级相对路径规则（与各 SKILL.md 的「定位 flow-core」一致）----
#   1. $NEO_SKILL_HOME/runtime/flow-core
#   2. <本 skill 目录>/../neo-skill/runtime/flow-core  （安装后兄弟目录）
#   3. <本 skill 目录>/runtime/flow-core              （neo-skill 自己）
#   4. <repo>/skills/neo-skill/runtime/flow-core      （仓库布局）
resolve_flow_core() {
  local skill_dir="$1"
  if [ -n "${NEO_SKILL_HOME:-}" ] && [ -f "$NEO_SKILL_HOME/runtime/flow-core" ]; then
    echo "$NEO_SKILL_HOME/runtime/flow-core"; return 0
  fi
  if [ -f "$skill_dir/../neo-skill/runtime/flow-core" ]; then
    echo "$skill_dir/../neo-skill/runtime/flow-core"; return 0
  fi
  if [ -f "$skill_dir/runtime/flow-core" ]; then
    echo "$skill_dir/runtime/flow-core"; return 0
  fi
  if [ -f "$REPO_ROOT/skills/neo-skill/runtime/flow-core" ]; then
    echo "$REPO_ROOT/skills/neo-skill/runtime/flow-core"; return 0
  fi
  return 1
}

status=0

echo "安装目标：$SKILLS_DIR"
echo "--- 1. Skill 识别（SKILL.md 必须有 name 与 description）---"
for name in $SKILL_NAMES; do
  f="$SKILLS_DIR/$name/SKILL.md"
  if [ ! -f "$f" ]; then
    echo "MISSING $name"; status=1; continue
  fi
  has_name=$(awk '/^name:[[:space:]]*\S/{print "yes"; exit}' "$f")
  has_desc=$(awk '/^description:[[:space:]]*\S/{print "yes"; exit}' "$f")
  if [ "$has_name" = "yes" ] && [ "$has_desc" = "yes" ]; then
    echo "OK $name"
  else
    echo "INVALID $name（缺 name 或 description，skills.sh 会忽略它）"; status=1
  fi
done

echo "--- 2. 随包资源（flow-core / 规则 / 模板）---"
for rel in runtime/flow-core runtime/rules/flow.yaml; do
  if [ -f "$SKILLS_DIR/neo-skill/$rel" ]; then
    echo "OK neo-skill/$rel"
  else
    echo "MISSING neo-skill/$rel"; status=1
  fi
done
tpl_count=$(find "$SKILLS_DIR/neo-skill/runtime/templates" -maxdepth 1 -name '*.md' 2>/dev/null | wc -l | tr -d ' ')
if [ "$tpl_count" -ge 10 ]; then
  echo "OK neo-skill/runtime/templates（$tpl_count 个模板）"
else
  echo "MISSING neo-skill/runtime/templates（只有 $tpl_count 个）"; status=1
fi

echo "--- 3. 相对位置解析（从 requirements skill 的角度找 flow-core）---"
if core=$(resolve_flow_core "$SKILLS_DIR/requirements"); then
  core="$(cd "$(dirname "$core")" && pwd)/$(basename "$core")"
  echo "RESOLVED $core"
  if [ -n "${NEO_SKILL_HOME:-}" ]; then
    # 显式环境变量是人工覆盖，允许指向安装目录之外
    echo "OK 解析自 $NEO_SKILL_HOME（显式覆盖）"
  else
    case "$core" in
      "$SKILLS_DIR"/*) echo "OK 解析结果在安装目录内" ;;
      *) echo "FAIL 解析逃逸出安装目录：$core"; status=1 ;;
    esac
  fi
else
  echo "FAIL 四级规则都找不到 flow-core"; status=1
fi

echo "--- 4. flow-core 可执行性（不依赖仓库路径）---"
if [ -n "${core:-}" ]; then
  chmod +x "$core" 2>/dev/null || true
  if out=$(env -u PYTHONPATH -u NEO_SKILL_HOME python3 "$core" --version 2>&1); then
    echo "OK $out"
  else
    echo "FAIL 无法运行 flow-core：$out"; status=1
  fi
fi

if [ "$status" = "0" ]; then
  echo "INSTALL-SIM OK"
else
  echo "INSTALL-SIM FAILED" >&2
fi
exit "$status"
