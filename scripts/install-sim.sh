#!/usr/bin/env bash
# 安装模拟：把「随包分发的 Skill」复制到隔离项目的 .claude/skills/，
# 行为对齐 `npx skills@latest add binggan23/neo-skill -a claude`（skills.sh 只复制 skill 目录）。
#
# 为什么需要它：`main` 分支目前没有 `skills/` 目录，远端 `npx skills add` 返回
# `No skills found`（见 docs/pilot-report.md 的「已知阻塞」）。安装模拟用**文件系统路径**
# 复现同一套复制语义，从而在没有远端的情况下验证：
#   1. 完整的独立 Skill 集合都被识别（路由层 + 五个阶段 + 只读进度查询），
#      集合从 `skills/` **自动派生**（只算含 `SKILL.md` 的目录），不维护第二份清单；
#      另有一条必需底线：catalog（Skill 清单的唯一导航入口）列出的每个 Skill 都必须在源里。
#      名单**只在 catalog 一处**，本脚本是它的投影，不维护自己的清单——新增/删除/改名
#      Skill 都不用改脚本，改 catalog 即可。目标集合与源集合不一致、或源里缺 catalog
#      列出的 Skill，都非零退出；
#   2. `runtime/`（flow-core + 声明式规则 + 模板）随 skill 目录一起走；
#   3. 安装后的 flow-core 只靠**相对位置**解析资源，不依赖源码仓库路径。
#
# 用法：
#   scripts/install-sim.sh --target /tmp/proj          # 装到 /tmp/proj/.claude/skills/
#   scripts/install-sim.sh --target DIR --into skills  # 自定义子目录（默认 .claude/skills）
#   scripts/install-sim.sh --target DIR --verify-only   # 只验证已有安装
#   scripts/install-sim.sh --target DIR --skills-root DIR  # 换一个 skills 源（测缺陷注入用）
set -euo pipefail

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
TARGET=""
INTO=".claude/skills"
VERIFY_ONLY=0
SKILLS_ROOT="$REPO_ROOT/skills"

# 必需 Skill 的底线集合**不在这里写死**：它从 catalog 表（Skill 清单的唯一导航入口）派生。
# 这样仓库里只有**一份** Skill 名单（catalog），安装侧只是它的投影：
#   - 新增 Skill：加目录 + 加 catalog 一行，安装侧自动跟上；
#   - 删除/改名：删目录 + 删 catalog 行，安装侧自动跟上，不会留下陈旧清单。
# 删了目录却没改 catalog → 本条报 MISSING-REQUIRED；反过来 catalog 有、目录没有同样报错；
# 目录有、catalog 没列由 tests/test_12_docs.py 报。
SKILL_CATALOG="$REPO_ROOT/docs/agents/p0-workflow/07-skill-catalog.md"

# 从 catalog 的 `skills/<name>/SKILL.md` 链接里取出全部 Skill 名。
required_skills() {
  [ -f "$SKILL_CATALOG" ] || return 0
  grep -oE '\]\(\.\./\.\./\.\./skills/[a-z0-9-]+/SKILL\.md\)' "$SKILL_CATALOG" \
    | sed 's|.*skills/||; s|/SKILL\.md)||' \
    | LC_ALL=C sort -u
}

usage() {
  awk 'NR > 1 && /^#/ { sub(/^# ?/, ""); print; next } NR > 1 { exit }' "${BASH_SOURCE[0]}"
}

while [ $# -gt 0 ]; do
  case "$1" in
    --target) TARGET="${2:?--target 需要目录}"; shift 2 ;;
    --into) INTO="${2:?--into 需要子目录}"; shift 2 ;;
    --skills-root) SKILLS_ROOT="${2:?--skills-root 需要目录}"; shift 2 ;;
    --verify-only) VERIFY_ONLY=1; shift ;;
    -h|--help) usage; exit 0 ;;
    *) echo "未知参数：$1" >&2; exit 2 ;;
  esac
done

[ -n "$TARGET" ] || { echo "必须给 --target" >&2; exit 2; }
mkdir -p "$TARGET"
TARGET="$(cd "$TARGET" && pwd)"
SKILLS_DIR="$TARGET/$INTO"
SKILLS_ROOT="$(cd "$SKILLS_ROOT" 2>/dev/null && pwd)" || {
  echo "skills 根目录不存在：$SKILLS_ROOT" >&2; exit 1
}

status=0

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

# 自动派生：skills 根下每个含 SKILL.md 的目录就是一个 Skill（按名字排序，输出稳定）。
discover_skills() {
  local root="$1" dir
  [ -d "$root" ] || return 0
  for dir in "$root"/*/; do
    [ -d "$dir" ] || continue
    [ -f "$dir/SKILL.md" ] || continue
    basename "$dir"
  done | LC_ALL=C sort
}

DISCOVERED="$(discover_skills "$SKILLS_ROOT")"
if [ -z "$DISCOVERED" ]; then
  echo "skills 根目录里一个 SKILL.md 都没找到：$SKILLS_ROOT" >&2
  exit 1
fi
SKILL_COUNT="$(printf '%s\n' "$DISCOVERED" | wc -l | tr -d ' ')"

if [ "$VERIFY_ONLY" = "0" ]; then
  rm -rf "$SKILLS_DIR"
  mkdir -p "$SKILLS_DIR"
  for name in $DISCOVERED; do
    src="$SKILLS_ROOT/$name"
    mkdir -p "$SKILLS_DIR/$name"
    cp "$src/SKILL.md" "$SKILLS_DIR/$name/SKILL.md"
    # runtime 只跟 neo-skill 走（CLI、规则、模板和它同一个包）
    if [ -d "$src/runtime" ]; then
      cp -R "$src/runtime" "$SKILLS_DIR/$name/runtime"
    fi
  done
fi

echo "安装目标：$SKILLS_DIR"
echo "skills 源：$SKILLS_ROOT"

echo "--- 0. 必需 Skill 底线（catalog 列出的每个 Skill 都必须在源里）---"
CATALOG_REQUIRED="$(required_skills)"
if [ -z "$CATALOG_REQUIRED" ]; then
  echo "FAIL 无法从 catalog 派生必需集合（$SKILL_CATALOG）"; status=1
fi
for name in $CATALOG_REQUIRED; do
  if printf '%s\n' "$DISCOVERED" | grep -qx "$name"; then
    echo "OK-REQUIRED $name"
  else
    echo "MISSING-REQUIRED $name（catalog 列出了它，但 skills 源里没有这个目录）"
    status=1
  fi
done

echo "--- 0b. Skill 集合一致性（源与目标必须相同，否则非零退出）---"
INSTALLED="$(discover_skills "$SKILLS_DIR")"
for name in $DISCOVERED; do
  if ! printf '%s\n' "$INSTALLED" | grep -qx "$name"; then
    echo "MISMATCH 目标缺少 Skill：$name"; status=1
  fi
done
for name in $INSTALLED; do
  if ! printf '%s\n' "$DISCOVERED" | grep -qx "$name"; then
    echo "MISMATCH 目标多出 Skill：$name"; status=1
  fi
done
if [ "$INSTALLED" = "$DISCOVERED" ]; then
  echo "OK-SET 源与目标的 Skill 集合一致（$SKILL_COUNT 个）"
fi

echo "--- 1. Skill 发现与识别（自动派生；SKILL.md 必须有 name 与 description）---"
echo "发现 $SKILL_COUNT 个 Skill：$(printf '%s ' $DISCOVERED | sed 's/ $//')"
for name in $DISCOVERED; do
  f="$SKILLS_DIR/$name/SKILL.md"
  if [ ! -f "$f" ]; then
    echo "MISSING $name"; status=1; continue
  fi
  if [ "$(head -1 "$f")" != "---" ]; then
    echo "INVALID $name（SKILL.md 没有 frontmatter，skills.sh 会忽略它）"; status=1; continue
  fi
  name_val=$(sed -n 's/^name:[[:space:]]*//p' "$f" | head -1 | sed 's/[[:space:]]*$//')
  desc_val=$(sed -n 's/^description:[[:space:]]*//p' "$f" | head -1 | sed 's/[[:space:]]*$//')
  if [ -z "$name_val" ] || [ -z "$desc_val" ]; then
    echo "INVALID $name（缺 name 或 description，skills.sh 会忽略它）"; status=1; continue
  fi
  if [ "$name_val" != "$name" ]; then
    echo "INVALID $name（name=$name_val 与目录名不一致）"; status=1; continue
  fi
  echo "OK $name"
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
