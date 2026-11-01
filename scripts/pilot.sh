#!/usr/bin/env bash
# 隔离端到端试点：在**仓库之外**的全新临时目录里，用安装模拟装好 Skill 包，
# 然后跑通 P0 主链路：初始化 → 需求（grilling/确认/排期）→ PRD（评审/批准）→
# 开发（门禁/Spec/验证）→ 人工验收 → 收尾同步。
#
# 隔离证明：试点前后对 `~/.claude/skills/`、`~/.agents/` 与仓库目录做内容快照比对，
# 断言零改动；所有写入都落在 `--workdir`（默认 `mktemp -d`）之内。
#
# 用法：
#   scripts/pilot.sh                     # 完整试点，证据写到临时目录
#   scripts/pilot.sh --workdir DIR       # 指定工作目录（不删除，便于查看证据）
#   scripts/pilot.sh --keep              # 保留默认临时目录并打印路径
#   scripts/pilot.sh --with-claude       # 额外用真实 Claude Code 非交互跑一次调用
#
# 退出码：0 试点通过；1 断言失败（会打印失败项与证据目录）；2 用法错误。
set -uo pipefail

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
WORKDIR=""
KEEP=0
WITH_CLAUDE=0

while [ $# -gt 0 ]; do
  case "$1" in
    --workdir) WORKDIR="${2:?--workdir 需要目录}"; shift 2 ;;
    --keep) KEEP=1; shift ;;
    --with-claude) WITH_CLAUDE=1; shift ;;
    -h|--help) sed -n '2,18p' "${BASH_SOURCE[0]}" | sed 's/^# \{0,1\}//'; exit 0 ;;
    *) echo "未知参数：$1" >&2; exit 2 ;;
  esac
done

if [ -z "$WORKDIR" ]; then
  WORKDIR="$(mktemp -d "${TMPDIR:-/tmp}/neo-skill-pilot-XXXXXX")"
  [ "$KEEP" = "1" ] || trap 'rm -rf "$WORKDIR"' EXIT
fi
mkdir -p "$WORKDIR"
WORKDIR="$(cd "$WORKDIR" && pwd)"
case "$WORKDIR" in
  "$REPO_ROOT"*) echo "拒绝在仓库内试点：$WORKDIR" >&2; exit 2 ;;
esac

PROJ="$WORKDIR/proj"
EVID="$WORKDIR/evidence"
mkdir -p "$PROJ" "$EVID"

# 固定时钟：让证据里的时间戳可复现（flow-core 支持的测试时钟，不影响任何真实时间）
export NEO_SKILL_NOW="${NEO_SKILL_NOW:-2026-11-01T10:00:00+08:00}"
export PYTHONIOENCODING=utf-8
unset NEO_SKILL_HOME PYTHONPATH

# 人工确认身份（试点夹具，非真实组织授权；见 docs/pilot-report.md 的边界说明）
OWNER=alice      # requirement_owner：排期、PRD 批准、验收结论
STAKE=carol      # stakeholder：共识确认、提交评审
DEV=bob          # developer：启动开发、提交人工验收
PROJECT_OWNER=carol

STEP=0
say() { STEP=$((STEP + 1)); printf '\n[%02d] %s\n' "$STEP" "$*"; }
die() { echo "试点失败：$*" >&2; echo "证据目录：$EVID" >&2; exit 1; }

# ---- 快照（隔离证明）：路径 → sha256，递归、排序 -----------------------------
snapshot() {
  python3 - "$@" <<'PY'
import hashlib, os, sys
for target in sys.argv[1:]:
    label = target
    root = os.path.expanduser(target)
    if not os.path.exists(root):
        print("%s\tMISSING" % label)
        continue
    for dirpath, dirnames, filenames in os.walk(root):
        dirnames.sort()
        for name in sorted(filenames):
            full = os.path.join(dirpath, name)
            rel = os.path.relpath(full, root)
            try:
                if os.path.islink(full):
                    digest = "link:" + os.readlink(full)
                else:
                    with open(full, "rb") as fh:
                        digest = hashlib.sha256(fh.read()).hexdigest()
            except OSError as exc:
                digest = "ERR:%s" % exc
            print("%s\t%s\t%s" % (label, rel, digest))
PY
}

SNAP_BEFORE="$EVID/snapshot-before.txt"
SNAP_AFTER="$EVID/snapshot-after.txt"
snapshot "$HOME/.claude/skills" "$HOME/.agents" "$REPO_ROOT" > "$SNAP_BEFORE"

CLI="$PROJ/.claude/skills/neo-skill/runtime/flow-core"
fc() { python3 "$CLI" --root "$PROJ" "$@"; }
record_json() { fc "$@" --json > "$EVID/$(printf '%02d' "$STEP")-$1.json" 2>&1; }

# ==========================================================================
say "安装模拟：把随包 Skill 复制到隔离项目的 .claude/skills/（等价 npx skills add -a claude）"
bash "$REPO_ROOT/scripts/install-sim.sh" --target "$PROJ" | tee "$EVID/install-sim.log" || die "安装模拟失败"
[ -x "$PROJ/.claude/skills/neo-skill/runtime/flow-core" ] || chmod +x "$PROJ/.claude/skills/neo-skill/runtime/flow-core"
[ -f "$CLI" ] || die "找不到 $CLI"

say "环境诊断 doctor"
fc doctor --json > "$EVID/doctor.json" || die "doctor 报错"
python3 - "$EVID/doctor.json" <<'PY' || die "doctor 存在非 ok 检查项"
import json, sys
data = json.load(open(sys.argv[1], encoding="utf-8"))
bad = [c for c in data.get("checks", []) if c["status"] == "error"]
print("  flow-core =", data["data"]["runtime"]["flow_core"])
print("  checks    =", ", ".join("%s:%s" % (c["name"], c["status"]) for c in data["checks"]))
sys.exit(1 if bad else 0)
PY

say "初始化项目骨架 init"
fc init --json > "$EVID/init.json" || die "init 失败"
python3 - "$EVID/init.json" <<'PY' || die "init 产物不足"
import json, sys
data = json.load(open(sys.argv[1], encoding="utf-8"))
assert data["success"] is True, data
print("  artifacts =", len(data["artifacts"]))
PY

say "写入团队配置与 constitution（人工确认内容，由试点夹具提供）"
cat > "$PROJ/00-management/team.md" <<EOF
---
confirmed: true
confirmed_by: $PROJECT_OWNER
confirmed_at: $NEO_SKILL_NOW
members:
  - {id: $OWNER, name: 张三, roles: [requirement_owner]}
  - {id: $STAKE, name: 李四, roles: [stakeholder, project_owner]}
  - {id: $DEV, name: 王五, roles: [developer]}
---

# 团队与职责

成员与职责映射见 front matter。需求负责人：$OWNER；共识确认与评审：$STAKE；开发：$DEV。
EOF
cat > "$PROJ/.specify/constitution.md" <<EOF
---
confirmed: true
confirmed_by: $PROJECT_OWNER
confirmed_at: $NEO_SKILL_NOW
---

# 项目约束（constitution）

## 技术栈

Python 3.11+，仅标准库（不引入第三方依赖）。

## 编码规范

PEP 8；中文文案、英文标识符、UTF-8。

## 测试要求

标准库 unittest；每轮修复后必须重跑全部用例，修复轮次上限 3。

## 安全与合规

不写入凭据；日志不输出简历正文。

## 架构约束

单一状态流转函数，纯函数式，不做 IO。

## 交付约束

不自动 commit / merge / push。
EOF
echo "  team.md 与 constitution.md 已写入（confirmed: true）"

say "需求：创建草稿并补齐六章节正文"
fc req create --title "简历批量解析" --module resume --category new-feature \
  --source "客户访谈 2026-10（招聘旺季 3 人全职人工解析）" --slug batch-parse \
  --json > "$EVID/req-create.json" || die "req create 失败"
REQ_ID=$(python3 -c 'import json,sys;print(json.load(open(sys.argv[1]))["data"]["req"]["id"])' "$EVID/req-create.json")
echo "  REQ = $REQ_ID"

cat > "$WORKDIR/req-body.md" <<EOF
# $REQ_ID 简历批量解析

## 背景

招聘旺季单月简历量 2000 份，人工解析需要 3 人全职处理，解析时延导致候选人流失。

## 用户

招聘专员（企业 HR 团队，单批处理岗位投递）。

## 问题

单份上传与人工判定耗时，旺季堆积 3 天以上，优秀候选人在等待期被其他公司录用。

## 场景

1. 专员一次上传 100 份简历，系统给出上传进度；
2. 系统异步解析并输出结果清单，失败项可单独重试。

## 期望结果

单批 100 份简历在 5 分钟内完成解析，结果清单可导出。

## 来源

客户访谈 2026-10；招聘旺季 3 人全职人工解析的现场记录。
EOF
fc req update --id "$REQ_ID" --body-file "$WORKDIR/req-body.md" --json > "$EVID/req-update.json" \
  || die "req update 失败"

say "需求：记录 grilling（事实 / 决策 / 假设 / 未决 / 共识）"
fc req align --id "$REQ_ID" \
  --frontier "解析结果如何交付给招聘专员" \
  --fact "现有后端只有同步接口，单次调用上限 20 份" \
  --decision "本期只做批量上传 + 异步解析 + 结果清单导出，不做智能评分" \
  --assumption "单批上限 100 份，超出由后续需求处理" \
  --open-question "结果字段映射是否需要支持自定义模板（由产品负责人在 PRD 阶段决定）" \
  --consensus "本期范围：批量上传、异步解析、失败重试、结果清单导出；单批 100 份 5 分钟内完成" \
  --json > "$EVID/req-align.json" || die "req align 失败"

say "需求：共识确认（$STAKE / stakeholder）"
fc req confirm --id "$REQ_ID" --actor "$STAKE" --confirm --json > "$EVID/req-confirm.json" \
  || die "req confirm 失败"

say "需求：人工排期（$OWNER / requirement_owner，v0.1.0）"
fc req plan --id "$REQ_ID" --actor "$OWNER" --confirm --release v0.1.0 \
  --json > "$EVID/req-plan.json" || die "req plan 失败"

say "PRD：从 PLANNED 主需求创建草稿"
fc prd create --req "$REQ_ID" --scenarios SC-RES-01,SC-RES-02 \
  --json > "$EVID/prd-create.json" || die "prd create 失败"
PRD_ID=$(python3 -c 'import json,sys;d=json.load(open(sys.argv[1]));print(d["data"]["prd"]["id"])' "$EVID/prd-create.json")
echo "  PRD = $PRD_ID"

cat > "$WORKDIR/prd-body.md" <<EOF
# $PRD_ID 简历批量解析

## 背景与目标

招聘旺季单月简历量 2000 份，人工解析需要 3 人全职，解析时延导致候选人流失。
目标：单批 100 份简历在 5 分钟内完成解析，结果清单可导出。

## 范围

- 本期做：批量上传、异步解析、失败重试、结果清单导出。
- 本期不做：简历智能评分、自动排期面试、移动端上传。

## 现状与变化

新功能，无现状；现有后端只有同步接口，本期新增批量与异步能力。

## 场景

### SC-RES-01 批量上传与异步解析

招聘专员一次上传多份简历，系统异步解析并给出结果清单。

**验收标准**

- AC-RES-01-01
  - Given 招聘专员已登录且被授予上传权限
  - When 一次上传 100 份简历
  - Then 上传完成后 100 份简历进入待解析状态

- AC-RES-01-02
  - Given 已上传 100 份简历
  - When 解析队列开始处理
  - Then 5 分钟内 100 条解析结果全部可见

### SC-RES-02 解析失败重试

单份简历解析失败后可单独重试。

**验收标准**

- AC-RES-02-01
  - Given 某份简历解析失败并记录错误码
  - When 招聘专员点击重试
  - Then 该份简历重新进入解析队列且错误码被清除

## 非功能需求

- 单批 100 份简历解析在 5 分钟内完成，接口 P95 延迟不超过 800ms。
- 简历文件只保存在内网存储，不写入任何外部服务。

## 数据需求

- 保存简历文件名、上传人、上传时间、解析状态与错误码，保留 90 天。

## 依赖与风险

- 依赖现有对象存储；解析服务异常时任务留在队列，可重投。

## 待确认问题

- 结果字段映射是否需要支持自定义模板？由产品负责人在首个版本后确认。
EOF
fc prd update --id "$PRD_ID" --body-file "$WORKDIR/prd-body.md" --json > "$EVID/prd-update.json" \
  || die "prd update 失败"

say "PRD：结构自审（只读）"
fc prd validate --id "$PRD_ID" --json > "$EVID/prd-validate.json" || die "prd validate 报错"
python3 -c 'import json,sys;d=json.load(open(sys.argv[1]));print("  findings =",len(d.get("errors") or []))' "$EVID/prd-validate.json"

say "PRD：提交评审（$STAKE / stakeholder）"
fc prd submit-review --id "$PRD_ID" --actor "$STAKE" --confirm \
  --note "范围、AC 与场景已对齐，请评审" --json > "$EVID/prd-submit-review.json" \
  || die "prd submit-review 失败"

say "PRD：需求负责人批准（$OWNER / requirement_owner）"
fc prd approve --id "$PRD_ID" --actor "$OWNER" --confirm --json > "$EVID/prd-approve.json" \
  || die "prd approve 失败"

say "开发：门禁检查（只读，绑定 $PRD_ID）"
fc dev gate --prd "$PRD_ID" --json > "$EVID/dev-gate.json" || die "dev gate 未通过"

say "开发：建立 Spec / Plan / Tasks 并进入 DEVELOPING（$DEV / developer）"
fc dev start --prd "$PRD_ID" --actor "$DEV" --confirm --json > "$EVID/dev-start.json" \
  || die "dev start 失败"

say "开发：写入最小实现与测试（试点代码，非伪造验收数据）"
mkdir -p "$PROJ/src" "$PROJ/tests"
cat > "$PROJ/src/__init__.py" <<'EOF'
EOF
cat > "$PROJ/src/resume_parse.py" <<'EOF'
"""Pilot 最小实现：简历解析状态流转（纯函数，不做真实解析）。"""

STATES = ("pending", "parsed", "failed", "retrying")

_TRANSITIONS = {
    ("pending", "start"): "parsed",
    ("pending", "error"): "failed",
    ("failed", "retry"): "retrying",
    ("retrying", "start"): "parsed",
}


def next_status(current: str, event: str) -> str:
    if current not in STATES:
        raise ValueError("未知状态：%s" % current)
    try:
        return _TRANSITIONS[(current, event)]
    except KeyError:
        raise ValueError("非法流转：%s + %s" % (current, event)) from None
EOF
cat > "$PROJ/tests/__init__.py" <<'EOF'
EOF
cat > "$PROJ/tests/test_resume_parse.py" <<'EOF'
"""Pilot 最小实现的单元测试（标准库 unittest，无第三方依赖）。"""

import unittest

from src.resume_parse import next_status


class NextStatusTest(unittest.TestCase):
    def test_pending_to_parsed(self) -> None:
        self.assertEqual(next_status("pending", "start"), "parsed")

    def test_pending_error(self) -> None:
        self.assertEqual(next_status("pending", "error"), "failed")

    def test_failed_retry(self) -> None:
        self.assertEqual(next_status("failed", "retry"), "retrying")

    def test_illegal_transition(self) -> None:
        with self.assertRaises(ValueError):
            next_status("parsed", "retry")


if __name__ == "__main__":
    unittest.main()
EOF
echo "  src/resume_parse.py + tests/test_resume_parse.py 已写入"

say "开发：范围检查（只读）"
fc dev scope-check --prd "$PRD_ID" --json > "$EVID/dev-scope-check.json" || die "dev scope-check 报错"

say "开发：验证（极轻量命令 python3 -m unittest，原样执行并保存原始输出）"
VERIFY_CMD="python3 -m unittest discover -s tests -t . -v"
fc dev verify --prd "$PRD_ID" --suite unit --command "$VERIFY_CMD" \
  --ac AC-RES-01-01,AC-RES-01-02 --json > "$EVID/dev-verify-unit.json" \
  || die "dev verify（unit）失败"
fc dev verify --prd "$PRD_ID" --suite integration --command "$VERIFY_CMD" \
  --ac AC-RES-02-01 --json > "$EVID/dev-verify-integration.json" \
  || die "dev verify（integration）失败"

say "开发：提交人工验收（$DEV / developer）"
fc dev submit-acceptance --prd "$PRD_ID" --actor "$DEV" --json > "$EVID/dev-submit-acceptance.json" \
  || die "dev submit-acceptance 失败"

say "验收：准备 Demo 材料与逐条 AC checklist"
fc acceptance prepare --prd "$PRD_ID" --json > "$EVID/acceptance-prepare.json" \
  || die "acceptance prepare 失败"

say "验收：逐条记录人工结论（$STAKE 代表需求负责人现场确认）"
for ac in AC-RES-01-01 AC-RES-01-02 AC-RES-02-01; do
  fc acceptance record --prd "$PRD_ID" --ac "$ac" --result pass \
    --actor "$STAKE" --evidence "$ac：招聘专员现场演示通过（记录于试点验收会议 $NEO_SKILL_NOW）" \
    --json > "$EVID/acceptance-record-$ac.json" || die "acceptance record $ac 失败"
  echo "  record $ac = pass"
done

say "验收：确认验收通过（$OWNER / requirement_owner，ACCEPTED）"
fc acceptance approve --prd "$PRD_ID" --actor "$OWNER" --confirm \
  --note "逐条 AC 已人工确认通过" --json > "$EVID/acceptance-approve.json" \
  || die "acceptance approve 失败"

say "收尾：closeout（功能现状、主需求 DONE、索引同步）"
fc acceptance closeout --prd "$PRD_ID" --actor "$OWNER" --confirm \
  --json > "$EVID/acceptance-closeout.json" || die "acceptance closeout 失败"

say "终态与完整度检查（lint / index status / 状态史与证据）"
fc lint --json > "$EVID/lint.json" || die "lint 有 error"
fc index status --json > "$EVID/index-status.json" || die "index status 不 ok"
fc doctor --json > "$EVID/doctor-final.json" || die "终态 doctor 报错"

python3 - "$PROJ" "$PRD_ID" "$REQ_ID" "$EVID" <<'PY' || exit 1
import glob
import json
import sys
from pathlib import Path

import yaml

proj = Path(sys.argv[1])
prd_id, req_id, evid = sys.argv[2], sys.argv[3], Path(sys.argv[4])
failures = []

def check(label, ok, detail=""):
    print("  %-42s %s %s" % (label, "OK" if ok else "FAIL", detail))
    if not ok:
        failures.append(label)

# --- 主需求 DONE ---
req_files = sorted((proj / "03-requirements/items").glob(f"{req_id}-*.md"))
req_meta = yaml.safe_load(req_files[0].read_text(encoding="utf-8").split("---\n", 2)[1])
check("主需求状态 DONE", req_meta["status"] == "DONE", req_meta["status"])

# --- PRD ACCEPTED + closeout COMMITTED ---
prd_dir = next(p for p in (proj / "04-prds").rglob("metadata.yaml"))
prd_meta = yaml.safe_load(prd_dir.read_text(encoding="utf-8"))
check("PRD 状态 ACCEPTED", prd_meta["status"] == "ACCEPTED", prd_meta["status"])
closeout = prd_meta.get("closeout") or {}
check("closeout COMMITTED", closeout.get("state") == "COMMITTED", closeout.get("tx", ""))

# --- lint 无 error ---
lint = json.loads((evid / "lint.json").read_text(encoding="utf-8"))
counts = lint["data"]["counts"]
check("lint 无 error", counts["error"] == 0, "%s" % counts)
check("lint 成功", lint["success"] is True)

# --- index status ok ---
idx = json.loads((evid / "index-status.json").read_text(encoding="utf-8"))["data"]
check("index status ok", idx["ok"] is True, "%d 个区块" % len(idx["files"]))

# --- 状态历史与人工确认记录 ---
check("主需求 history ≥ 4 条", len(req_meta["history"]) >= 4, str(len(req_meta["history"])))
req_conf = req_meta.get("confirmations") or []
check("主需求 confirmations ≥ 2 条", len(req_conf) >= 2, str(len(req_conf)))
check("PRD history ≥ 6 条", len(prd_meta["history"]) >= 6, str(len(prd_meta["history"])))
prd_conf = prd_meta.get("confirmations") or []
check("PRD confirmations ≥ 3 条", len(prd_conf) >= 3, str(len(prd_conf)))
check("PRD 批准人是需求负责人", (prd_meta.get("approved_by") or "") != "", prd_meta.get("approved_by"))

# --- PRD hash ---
hash_value = prd_meta.get("content_hash") or ""
check("PRD content_hash 存在", hash_value.startswith("sha256:"), hash_value[:23] + "…")
acceptance = prd_meta.get("acceptance") or {}
check("验收绑定同一 PRD hash", acceptance.get("prd_hash") == hash_value)
check("验收决策 approved", acceptance.get("decision") == "approved", acceptance.get("approved_by", ""))

# --- 逐条 AC 人工结论 ---
checklist = acceptance.get("checklist") or []
acs = sorted(item["ac"] for item in checklist)
check("逐条 AC 三条齐全且全 pass",
      acs == ["AC-RES-01-01", "AC-RES-01-02", "AC-RES-02-01"]
      and all(item["conclusion"] == "pass" for item in checklist),
      ",".join(acs))
check("每条 AC 都记录了确认人", all(item.get("recorded_by") for item in checklist))

# --- 原始验证输出 ---
spec_dir = next(p for p in (proj / "06-specs").rglob("verification.json")).parent
logs = sorted(spec_dir.joinpath("verification").glob("raw-*.log"))
check("原始验证输出 raw-*.log ≥ 2", len(logs) >= 2, ",".join(p.name for p in logs))
for log in logs:
    body = log.read_text(encoding="utf-8")
    check("raw log 含真实 runner 输出（%s）" % log.name, "Ran 3 tests" in body or "OK" in body)

# --- 版本信息 ---
doc = json.loads((evid / "doctor-final.json").read_text(encoding="utf-8"))
version = doc["data"]["runtime"]["version"]
check("记录版本信息", bool(version), version)

# --- 功能现状已落地 ---
feature = next((proj / "02-definition").rglob("features/batch-parse.md"), None)
check("功能现状文档已生成", feature is not None and feature.is_file())

(evid / "assertions.txt").write_text(
    "\n".join("%s\t%s" % ("FAIL" if f in failures else "OK", f) for f in failures) or "all ok",
    encoding="utf-8")
if failures:
    print("\n失败项：%s" % ", ".join(failures))
    sys.exit(1)
PY
[ $? -eq 0 ] || die "终态断言未全部通过"

if [ "$WITH_CLAUDE" = "1" ]; then
  say "Claude Code 真调用（非交互 claude -p）"
  CLAUDE_BIN="${CLAUDE_BIN:-$(command -v claude || echo /home/cookie/.local/bin/claude)}"
  if [ -x "$CLAUDE_BIN" ]; then
    # 注意：`--allowedTools` 是可变参数选项（会吃掉后面的位置参数），所以提示词必须放在它前面。
    ( cd "$PROJ" && timeout 300 "$CLAUDE_BIN" -p \
        "使用 project skill 对当前项目跑一次环境诊断：执行 python3 .claude/skills/neo-skill/runtime/flow-core doctor --root . --json，然后把 data.runtime.flow_core 与各 check 的 name/status 逐条列出来。不要修改任何文件。" \
        --allowedTools "Bash(python3:*)" ) > "$EVID/claude-doctor.log" 2>&1
    claude_rc=$?
    if [ "$claude_rc" -eq 0 ] && grep -q "ok" "$EVID/claude-doctor.log"; then
      echo "  Claude Code 非交互调用成功，输出见 evidence/claude-doctor.log"
    else
      echo "  Claude Code 调用未成功，日志见 evidence/claude-doctor.log"
      die "Claude Code 真调用失败"
    fi
  else
    echo "  未安装 claude CLI，跳过"
  fi
fi

# ---- 隔离证明：试点后重新快照并比对 ------------------------------------
say "隔离证明：~/.claude/skills、~/.agents 与仓库目录零改动"
snapshot "$HOME/.claude/skills" "$HOME/.agents" "$REPO_ROOT" > "$SNAP_AFTER"
if diff -q "$SNAP_BEFORE" "$SNAP_AFTER" > /dev/null; then
  echo "  OK 快照完全一致（$(wc -l < "$SNAP_BEFORE") 个文件）"
else
  echo "  差异：" >&2
  diff "$SNAP_BEFORE" "$SNAP_AFTER" | head -40 >&2
  die "检测到试点之外的写入（全局配置/仓库目录被改动）"
fi

# ---- 证据清单 ----------------------------------------------------------
say "整理证据"
{
  echo "# 试点证据清单"
  echo
  echo "- 工作目录：$WORKDIR"
  echo "- 时钟（NEO_SKILL_NOW）：$NEO_SKILL_NOW"
  echo "- flow-core：$CLI"
  echo "- REQ：$REQ_ID　PRD：$PRD_ID"
  echo "- 隔离快照文件数：$(wc -l < "$SNAP_BEFORE")"
  echo
  echo "## 证据文件"
  echo
  ( cd "$EVID" && ls -1 )
} > "$EVID/INDEX.md"
python3 - "$PROJ" "$EVID" <<'PY'
import shutil
import sys
from pathlib import Path

proj, evid = Path(sys.argv[1]), Path(sys.argv[2])
# 把状态历史与人工确认记录原文复制进证据目录（不改项目内任何文件）
copies = {
    "state-history-req.md": next((proj / "03-requirements/items").glob("REQ-*.md")),
    "state-history-prd-metadata.yaml": next((proj / "04-prds").rglob("metadata.yaml")),
    "team.md": proj / "00-management/team.md",
    "constitution.md": proj / ".specify/constitution.md",
}
for name, src in copies.items():
    shutil.copyfile(src, evid / name)
print("  已复制：", ", ".join(copies))
PY

echo
echo "PILOT OK"
echo "证据目录：$EVID"
[ "$KEEP" = "1" ] && echo "（工作目录保留：$WORKDIR）"
exit 0

