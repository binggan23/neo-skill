"""验证域：证据采集、防伪造检查与提交验收基线（ticket 06）。

为什么单独一个模块：
- `dev verify` 系列命令、`dev submit-acceptance`、`dev verification-status` 共享同一份
  `verification.md` / `verification.json` 读写与判定逻辑；
- ticket 09（结构 lint）需要「验证证据是否仍然有效」的只读判定，所以这里提供
  `evaluate(workspace, rules, record) -> VerificationStatus`，命令层只做薄壳；
- ticket 07（人工验收）需要读 AC 清单与验证结论，入口是 `ac_entries()` / `evaluate()`。

目录约定（共享设计 §4.3、ticket 05 报告 §4）：

    06-specs/<module>/PRD-NNN-<slug>/
        spec.md  plan.md  tasks.md  .baseline.yaml      （ticket 05）
        verification.md                                 结构化报告（人读）
        verification.json                               机器读（lint / acceptance）
        verification/raw-<suite>-<timestamp>.log        原始输出（NFR-008）

本模块只做读写与判定：**不取锁**（由 `commands/dev.py` 取），不改 PRD 状态。
所有状态值来自 `rules/flow.yaml`；修复轮次上限读 `limits.max_repair_rounds`，
可由 `.specify/constitution.md` 的 `max_repair_rounds` 覆盖（SRS-FR-065）。
"""

from __future__ import annotations

import json
import platform
import re
import subprocess
import sys
import time
from dataclasses import dataclass, field
from typing import Any, Iterable

from . import development, fsutil, hashing, metadata
from .output import FlowError
from .rules import Rules
from .workspace import Workspace

# --------------------------------------------------------------------------
# 常量
# --------------------------------------------------------------------------
VERIFICATION_DIR = "verification"
REPORT_FILE = "verification.md"
STATE_FILE = "verification.json"

RESULT_PASS = "pass"
RESULT_FAIL = "fail"
RESULT_BLOCKED = "blocked"
RESULT_NOT_RUN = "not-run"
RESULT_NOT_APPLICABLE = "not-applicable"
RESULTS = (RESULT_PASS, RESULT_FAIL, RESULT_BLOCKED, RESULT_NOT_RUN, RESULT_NOT_APPLICABLE)

# 只有这两类结论算「已覆盖」；其余一律不能提交验收（SRS-FR-066）
COVERED = (RESULT_PASS, RESULT_NOT_APPLICABLE)

# 必须处于开发中才能执行验证（ticket 05 的 dev.start_states 之外再加 DEVELOPING）
DEFAULT_VERIFY_STATES: tuple[str, ...] = ("DEVELOPING",)
ACCEPTANCE_SUBMIT_STATE = "READY_FOR_ACCEPTANCE"

DEFAULT_REPAIR_ROUNDS = 3
DEFAULT_TIMEOUT = 600

CODE_ROOTS: tuple[str, ...] = ("src", "tests")

FLOW_CORE = "flow-core"

_AC_ID_RE = re.compile(r"\bAC-[A-Za-z0-9]+-\d{1,4}(?:-\d{1,4})?\b")
_SCENARIO_ID_RE = re.compile(r"\bSC-[A-Za-z0-9]+-\d{1,4}\b")
_TEST_DEF_RE = re.compile(r"^[ \t]*(?:async[ \t]+)?def[ \t]+(test_[A-Za-z0-9_]+)[ \t]*\(", re.MULTILINE)
_ASSERT_RE = re.compile(r"^[ \t]*assert\b", re.MULTILINE)
_SKIP_RE = re.compile(
    r"@pytest\.mark\.(?:skip|skipif|xfail)\b|@unittest\.skip\b|pytest\.skip\(")
_SKIP_FLAG_RE = re.compile(r"(?:^|\s)(?:-k|--deselect|--ignore|--ignore-glob)(?:\s|=)")
_TS_RE = re.compile(r"(\d{4})-?(\d{2})-?(\d{2})[T ](\d{2}):?(\d{2}):?(\d{2})")
_SANITIZE_RE = re.compile(r"[^a-z0-9]+")


# --------------------------------------------------------------------------
# 路径
# --------------------------------------------------------------------------
def verification_dir(workspace: Workspace, record: metadata.PrdRecord) -> str:
    return f"{development.spec_dir(workspace, record)}/{VERIFICATION_DIR}"


def report_path(workspace: Workspace, record: metadata.PrdRecord) -> str:
    return development.spec_path(workspace, record, REPORT_FILE)


def state_path(workspace: Workspace, record: metadata.PrdRecord) -> str:
    return development.spec_path(workspace, record, STATE_FILE)


def ac_ids(record: metadata.PrdRecord) -> list[str]:
    """PRD 声明的 AC 清单（正文顺序，去重）。

    正文没有编号 AC 时，按场景编号推导（`SC-RES-01` → `AC-RES-01-01`），
    保证「PRD 有场景就必须有可验证的 AC」这一约定不落空。
    """
    found: list[str] = []
    for token in _AC_ID_RE.findall(record.body or ""):
        upper = token.upper()
        if upper not in found:
            found.append(upper)
    if found:
        return found
    for scenario in record.scenarios:
        derived = scenario.replace("SC-", "AC-", 1) + "-01"
        if derived not in found:
            found.append(derived)
    return found


def ac_scenario(ac_id: str) -> str | None:
    """`AC-RES-01-02` → `SC-RES-01`（与 `development.ac_scenario` 同一规则）。"""
    return development.ac_scenario(ac_id)


# --------------------------------------------------------------------------
# 结果对象
# --------------------------------------------------------------------------
@dataclass
class Finding:
    """防伪造检查的一条命中。"""

    code: str
    object: str
    message: str
    suggestion: str = ""
    waived: bool = False

    def to_dict(self) -> dict[str, Any]:
        data: dict[str, Any] = {
            "code": self.code,
            "object": self.object,
            "message": self.message,
            "suggestion": self.suggestion,
        }
        if self.waived:
            data["waived"] = True
        return data


@dataclass
class VerificationStatus:
    """`dev verification-status` 与 ticket 09 lint 共用的只读投影。"""

    prd: str
    acs: list[dict[str, Any]] = field(default_factory=list)
    summary: dict[str, int] = field(default_factory=dict)
    runs: int = 0
    complete: bool = False
    stale: bool = False
    stale_reasons: list[str] = field(default_factory=list)
    submitted: bool = False
    prd_hash: str | None = None
    code_version: str | None = None
    verification_digest: str | None = None
    repair_rounds: int = 0
    repair_limit: int = DEFAULT_REPAIR_ROUNDS
    submission: dict[str, Any] | None = None
    # 已提交过、但提交时的代码版本/PRD hash/证据摘要与当前不一致（信息性，供 ticket 07 判定）
    submission_outdated: bool = False

    @property
    def pending(self) -> list[dict[str, Any]]:
        """未覆盖的 AC（fail / blocked / not-run），供提交验收列出阻断项。"""
        return [item for item in self.acs if item.get("result") not in COVERED]

    def to_dict(self) -> dict[str, Any]:
        return {
            "prd": self.prd,
            "acs": list(self.acs),
            "summary": dict(self.summary),
            "runs": self.runs,
            "complete": self.complete,
            "stale": self.stale,
            "stale_reasons": list(self.stale_reasons),
            "submitted": self.submitted,
            "prd_hash": self.prd_hash,
            "code_version": self.code_version,
            "verification_digest": self.verification_digest,
            "repair_rounds": self.repair_rounds,
            "repair_limit": self.repair_limit,
            "submission": self.submission,
            "submission_outdated": self.submission_outdated,
        }


# --------------------------------------------------------------------------
# 状态文件读写
# --------------------------------------------------------------------------
def _blank(record: metadata.PrdRecord) -> dict[str, Any]:
    return {
        "prd": record.id,
        "module": record.module,
        "prd_dir": record.rel,
        "updated_at": None,
        "prd_hash": None,
        "code_version": None,
        "repair_rounds": 0,
        "repair_limit": DEFAULT_REPAIR_ROUNDS,
        "repairs": [],
        "runs": [],
        "acs": [],
        "snapshot": None,
        "guard": None,
        "submission": None,
        "verification_summary": None,
    }


def load_state(workspace: Workspace, record: metadata.PrdRecord) -> dict[str, Any]:
    """读取 `verification.json`；不存在时返回空骨架（不写文件）。"""
    path = workspace.path(state_path(workspace, record))
    if not path.is_file():
        return _blank(record)
    try:
        data = json.loads(fsutil.read_text(path))
    except (json.JSONDecodeError, OSError):
        return _blank(record)
    if not isinstance(data, dict):
        return _blank(record)
    base = _blank(record)
    base.update(data)
    return base


def save_state(workspace: Workspace, record: metadata.PrdRecord, data: dict[str, Any]) -> str:
    data["updated_at"] = fsutil.now_iso()
    path = workspace.path(state_path(workspace, record))
    fsutil.atomic_write_text(
        path, json.dumps(data, ensure_ascii=False, indent=2, sort_keys=False) + "\n")
    return state_path(workspace, record)


def sync_acs(data: dict[str, Any], record: metadata.PrdRecord) -> list[dict[str, Any]]:
    """把 PRD 当前 AC 清单与已有证据对齐（保留历史结论，新 AC 记 not-run）。"""
    existing = {str(item.get("ac")): dict(item) for item in data.get("acs") or []
                if isinstance(item, dict) and item.get("ac")}
    ordered: list[dict[str, Any]] = []
    for ac in ac_ids(record):
        entry = existing.pop(ac, None) or {}
        entry["ac"] = ac
        entry.setdefault("scenario", ac_scenario(ac))
        entry.setdefault("result", RESULT_NOT_RUN)
        entry.setdefault("run_id", None)
        entry.setdefault("suite", None)
        entry.setdefault("reason", None)
        entry.setdefault("at", None)
        entry.setdefault("evidence", None)
        ordered.append(entry)
    for ac in sorted(existing):  # PRD 已移除的 AC 保留历史，标 not-run
        entry = existing[ac]
        entry["stale"] = True
        ordered.append(entry)
    data["acs"] = ordered
    return ordered


def summarize(entries: Iterable[dict[str, Any]]) -> dict[str, int]:
    summary = {name: 0 for name in RESULTS}
    for item in entries:
        result = str(item.get("result") or RESULT_NOT_RUN)
        summary[result] = summary.get(result, 0) + 1
    return summary


def repair_limit(workspace: Workspace, rules: Rules) -> int:
    """修复轮次上限：constitution 覆盖 > rules.limits.max_repair_rounds > 默认 3。"""
    constitution = development.read_constitution(workspace) or {}
    override = constitution.get("max_repair_rounds")
    if isinstance(override, int) and override >= 0:
        return override
    if isinstance(override, str) and override.strip().isdigit():
        return int(override.strip())
    return rules.limit("max_repair_rounds", DEFAULT_REPAIR_ROUNDS)


def code_roots(rules: Rules) -> tuple[str, ...]:
    """代码版本摘要与防伪造检查的源码根目录（`rules.dev.code_roots`，默认 src/tests）。"""
    declared = (rules.data.get("dev") or {}).get("code_roots")
    if isinstance(declared, list) and declared:
        return tuple(str(item) for item in declared)
    return CODE_ROOTS


def verify_states(rules: Rules) -> tuple[str, ...]:
    declared = (rules.data.get("dev") or {}).get("verify_states")
    if isinstance(declared, list) and declared:
        return tuple(str(item) for item in declared)
    return DEFAULT_VERIFY_STATES


# --------------------------------------------------------------------------
# 执行环境、代码版本与证据摘要
# --------------------------------------------------------------------------
def environment(workspace: Workspace, command: str | None = None) -> dict[str, Any]:
    data: dict[str, Any] = {
        "python": platform.python_version(),
        "implementation": platform.python_implementation(),
        "platform": platform.platform(),
        "executable": sys.executable,
        "cwd": str(workspace.root),
    }
    if command is not None:
        data["command"] = command
    return data


def _iter_code_files(workspace: Workspace,
                     roots: Iterable[str] = CODE_ROOTS) -> list[tuple[str, str]]:
    parts: list[tuple[str, str]] = []
    for root in roots:
        directory = workspace.path(root)
        if not directory.is_dir():
            continue
        for path in sorted(directory.rglob("*")):
            if not path.is_file():
                continue
            rel = workspace.rel(path)
            parts.append((rel, hashing.sha256_file(path)))
    return parts


def code_version(workspace: Workspace, roots: Iterable[str] = CODE_ROOTS) -> str:
    """Git 项目记 `git:<sha>`；非 Git 项目记 `src/`+`tests/` 内容摘要（SRS-FR-068）。"""
    try:
        proc = subprocess.run(
            ["git", "rev-parse", "HEAD"],
            cwd=str(workspace.root),
            capture_output=True,
            text=True,
            timeout=30,
        )
    except (OSError, subprocess.SubprocessError):
        proc = None
    if proc is not None and proc.returncode == 0:
        sha = (proc.stdout or "").strip()
        if sha:
            return f"git:{sha}"
    return hashing.digest_of(_iter_code_files(workspace, roots))


def verification_digest(workspace: Workspace, data: dict[str, Any]) -> str:
    """证据摘要：所有原始输出文件的（相对路径, 内容 sha256）。"""
    parts: list[tuple[str, str]] = []
    for run in data.get("runs") or []:
        rel = str(run.get("raw_log") or "")
        if not rel:
            continue
        parts.append((rel, str(run.get("raw_log_hash") or "")))
    return hashing.digest_of(parts) if parts else hashing.sha256_text("")


# --------------------------------------------------------------------------
# 快照与防伪造
# --------------------------------------------------------------------------
def _parse_tests(workspace: Workspace) -> tuple[dict[str, int], int]:
    """返回 (测试用例名 → 断言数, 跳过标记数)。"""
    cases: dict[str, int] = {}
    skip_markers = 0
    tests_dir = workspace.path("tests")
    if not tests_dir.is_dir():
        return cases, skip_markers
    for path in sorted(tests_dir.rglob("*.py")):
        if not path.is_file():
            continue
        text = fsutil.read_text(path)
        skip_markers += len(_SKIP_RE.findall(text))
        current: str | None = None
        for line in text.splitlines():
            match = _TEST_DEF_RE.match(line)
            if match:
                current = match.group(1)
                cases.setdefault(current, 0)
                continue
            if current is not None and _ASSERT_RE.match(line):
                cases[current] = cases.get(current, 0) + 1
            elif current is not None and line.strip() and not line.startswith((" ", "\t")):
                current = None
    return cases, skip_markers


def take_snapshot(workspace: Workspace, record: metadata.PrdRecord, data: dict[str, Any],
                  *, code: str, roots: Iterable[str] = CODE_ROOTS) -> dict[str, Any]:
    cases, skip_markers = _parse_tests(workspace)
    return {
        "at": fsutil.now_iso(),
        "prd_hash": record.compute_content_hash(),
        "code_version": code,
        "acs": ac_ids(record),
        "test_cases": cases,
        "skip_markers": skip_markers,
        "files": dict(_iter_code_files(workspace, roots)),
    }


def guard_findings(workspace: Workspace, record: metadata.PrdRecord,
                   data: dict[str, Any]) -> list[Finding]:
    """防伪造检查（SRS-BR-014/015，SRS-FR-065）。

    与「上一次验证记录」的快照对比：删除用例、断言减少、新增跳过标记、
    PRD 的 AC 清单被修改，任何一项命中即视为不可信证据。
    """
    snapshot = data.get("snapshot") or {}
    if not snapshot:
        raise FlowError(
            "VERIFICATION_MISSING",
            f"{record.id} 还没有任何验证记录，无法做防伪造检查",
            object=record.id,
            suggestion=f"先执行 {FLOW_CORE} dev verify --prd {record.id} --suite unit "
                       "--command \"<项目验证命令>\" --ac <AC 编号>",
        )

    findings: list[Finding] = []
    baseline_cases = {str(k): int(v) for k, v in (snapshot.get("test_cases") or {}).items()}
    current_cases, current_skips = _parse_tests(workspace)

    for name, count in sorted(baseline_cases.items()):
        if name not in current_cases:
            findings.append(Finding(
                "TEST_CASE_REMOVED",
                name,
                f"验证之后测试用例 {name} 被删除（原 {count} 条断言）",
                "不得删除用例制造通过：恢复该用例，或先说明需求变化并走 CR（BR-014/015）",
            ))
            continue
        if current_cases[name] < count:
            findings.append(Finding(
                "ASSERTION_WEAKENED",
                name,
                f"测试用例 {name} 的断言由 {count} 条减少到 {current_cases[name]} 条",
                "断言被削弱不能算通过：恢复原有断言，或用更严格的断言替代",
            ))

    baseline_skips = int(snapshot.get("skip_markers") or 0)
    if current_skips > baseline_skips:
        findings.append(Finding(
            "TEST_SKIPPED",
            "tests",
            f"验证之后新增 {current_skips - baseline_skips} 处跳过标记"
            "（@skip / skipif / xfail）",
            "不得跳过适用验证：移除跳过标记；确实不适用时用 "
            "dev verify --not-applicable --reason 显式记录，或 --allow-skip --reason 豁免",
        ))

    for run in data.get("runs") or []:
        command = str(run.get("command") or "")
        if _SKIP_FLAG_RE.search(command):
            findings.append(Finding(
                "TEST_SKIPPED",
                str(run.get("id") or "run"),
                f"验证命令含排除参数：{command}",
                "验证命令不得用 -k/--deselect/--ignore 排除用例；请显式记录不适用项",
            ))

    baseline_acs = [str(item) for item in (snapshot.get("acs") or [])]
    current_acs = ac_ids(record)
    if baseline_acs and current_acs != baseline_acs:
        added = [item for item in current_acs if item not in baseline_acs]
        removed = [item for item in baseline_acs if item not in current_acs]
        detail = []
        if removed:
            detail.append("删除 " + "、".join(removed))
        if added:
            detail.append("新增 " + "、".join(added))
        findings.append(Finding(
            "AC_MODIFIED",
            record.id,
            f"PRD 的验收标准在验证之后被修改（{'；'.join(detail)}）",
            "已批准 PRD 的 AC 不得直接修改：必须走 "
            + development.CR_HINT.format(prd_id=record.id),
        ))
    return findings


def apply_waiver(workspace: Workspace, record: metadata.PrdRecord, data: dict[str, Any],
                 findings: list[Finding], reason: str) -> None:
    """把 TEST_SKIPPED 类命中标记为已豁免，并登记理由（显式、可审计）。"""
    for item in findings:
        if item.code == "TEST_SKIPPED":
            item.waived = True
    guard = data.get("guard")
    if not isinstance(guard, dict):
        guard = {}
    waivers = list(guard.get("waivers") or [])
    waivers.append({"at": fsutil.now_iso(), "reason": reason, "codes": ["TEST_SKIPPED"]})
    guard["waivers"] = waivers
    guard["waived_at"] = fsutil.now_iso()
    data["guard"] = guard


def unwaived(findings: Iterable[Finding]) -> list[Finding]:
    return [item for item in findings if not item.waived]


def changed_files(workspace: Workspace, data: dict[str, Any]) -> list[dict[str, str]]:
    """上次验证之后 `src/`、`tests/` 下内容发生变化的文件（供 guard 报告）。

    Git 可用且上次验证记录到 `git:<sha>` 时优先 `git diff --name-only <sha> -- src tests`；
    否则用上次验证留下的文件摘要对比（非 Git 项目）。只做报告，不产生错误码——
    真正的判定在 `guard_findings`。
    """
    snapshot = data.get("snapshot") or {}
    previous = {str(k): str(v) for k, v in (snapshot.get("files") or {}).items()}
    base = str(snapshot.get("code_version") or "")
    if base.startswith("git:"):
        diffed = _git_diff(workspace, base.removeprefix("git:"))
        if diffed is not None:
            return diffed
    current = dict(_iter_code_files(workspace))
    changed: list[dict[str, str]] = []
    for rel in sorted(set(previous) | set(current)):
        if previous.get(rel) == current.get(rel):
            continue
        change = ("added" if rel not in previous
                  else "removed" if rel not in current else "modified")
        changed.append({"path": rel, "change": change})
    return changed


def _git_diff(workspace: Workspace, sha: str) -> list[dict[str, str]] | None:
    """Git 可用时列出既有变更；失败返回 None（调用方回退到摘要对比）。"""
    try:
        proc = subprocess.run(
            ["git", "diff", "--name-status", sha, "--", *CODE_ROOTS],
            cwd=str(workspace.root), capture_output=True, text=True, timeout=30,
        )
    except (OSError, subprocess.SubprocessError):
        return None
    if proc.returncode != 0:
        return None
    labels = {"A": "added", "M": "modified", "D": "removed", "R": "renamed", "C": "copied"}
    changed: list[dict[str, str]] = []
    for line in (proc.stdout or "").splitlines():
        parts = line.split("\t")
        if len(parts) < 2:
            continue
        change = labels.get(parts[0][:1], "modified")
        rel = parts[-1].replace("\\", "/")
        changed.append({"path": rel, "change": change})
    return changed


# --------------------------------------------------------------------------
# 追加一条验证记录
# --------------------------------------------------------------------------
def write_raw_log(workspace: Workspace, record: metadata.PrdRecord, suite: str,
                  text: str) -> str:
    """把**原始输出**写到 `verification/raw-<suite>-<timestamp>.log`（不改写内容）。"""
    directory = workspace.path(verification_dir(workspace, record))
    directory.mkdir(parents=True, exist_ok=True)
    stamp = _timestamp()
    base = directory / f"raw-{_sanitize(suite)}-{stamp}.log"
    path = base
    index = 2
    while path.exists():
        path = directory / f"raw-{_sanitize(suite)}-{stamp}-{index:02d}.log"
        index += 1
    fsutil.atomic_write_text(path, text)
    return workspace.rel(path)


def _timestamp() -> str:
    now = fsutil.now_iso()
    match = _TS_RE.search(now)
    if not match:
        return re.sub(r"[^0-9T]", "", now)[:15] or "00000000T000000"
    return "".join(match.groups()[:3]) + "T" + "".join(match.groups()[3:])


def _sanitize(suite: str) -> str:
    text = _SANITIZE_RE.sub("-", str(suite or "").strip().lower()).strip("-")
    return text or "suite"


def execute(workspace: Workspace, command: str,
            timeout: int) -> tuple[int | None, str, str, int, bool]:
    """同步执行项目声明的验证命令：`shell=True`、`cwd=<项目根>`。

    返回 (exit_code, stdout, stderr, duration_ms, timed_out)。
    """
    started = time.monotonic()
    timed_out = False
    try:
        proc = subprocess.run(
            command,
            shell=True,
            cwd=str(workspace.root),
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=max(1, int(timeout)),
        )
        exit_code: int | None = proc.returncode
        stdout, stderr = proc.stdout or "", proc.stderr or ""
    except subprocess.TimeoutExpired as exc:
        timed_out = True
        exit_code = None
        stdout = _decode(exc.stdout)
        stderr = _decode(exc.stderr)
        stderr += f"\n[flow-core] 命令超时（{timeout}s），已终止\n"
    duration_ms = int((time.monotonic() - started) * 1000)
    return exit_code, stdout, stderr, duration_ms, timed_out


def _decode(raw: Any) -> str:
    if raw is None:
        return ""
    if isinstance(raw, bytes):
        return raw.decode("utf-8", errors="replace")
    return str(raw)


def append_run(workspace: Workspace, record: metadata.PrdRecord, data: dict[str, Any], *,
               suite: str, result: str, acs: list[str], raw_text: str, command: str | None,
               exit_code: int | None, duration_ms: int, mode: str,
               reason: str | None = None, started_at: str | None = None,
               roots: Iterable[str] = CODE_ROOTS) -> dict[str, Any]:
    """追加一条验证记录（含原始输出落盘与 AC 结论更新）。返回该记录。"""
    if mode == "not-applicable":
        raw_rel = None   # 不适用项没有可执行输出，不伪造空证据文件
    else:
        raw_rel = write_raw_log(workspace, record, suite, raw_text)
    run_id = f"RUN-{len(data.get('runs') or []) + 1:04d}"
    at = fsutil.now_iso()
    entry: dict[str, Any] = {
        "id": run_id,
        "suite": suite,
        "mode": mode,
        "result": result,
        "command": command,
        "exit_code": exit_code,
        "acs": list(acs),
        "raw_log": raw_rel,
        "raw_log_hash": hashing.sha256_file(workspace.path(raw_rel)) if raw_rel else None,
        "started_at": started_at or at,
        "finished_at": at,
        "duration_ms": duration_ms,
        "environment": environment(workspace, command),
        "reason": reason,
    }
    data.setdefault("runs", []).append(entry)

    entries = sync_acs(data, record)
    stamp = raw_rel or "无原始输出（不适用项已记录理由）"
    evidence = f"{suite} 退出码 {exit_code if exit_code is not None else '未执行'} → {stamp}"
    for item in entries:
        if item.get("ac") in acs:
            item["result"] = result
            item["run_id"] = run_id
            item["suite"] = suite
            item["reason"] = reason
            item["at"] = at
            item["evidence"] = evidence
            item.pop("stale", None)

    if result == RESULT_PASS:
        data["repair_rounds"] = 0  # 通过即结束本轮修复循环

    data["prd_hash"] = record.compute_content_hash()
    data["code_version"] = code_version(workspace, roots)
    data["verification_summary"] = summarize(entries)
    data["snapshot"] = take_snapshot(workspace, record, data,
                                     code=str(data["code_version"]), roots=roots)
    return entry


# --------------------------------------------------------------------------
# 判定：verification-status / submit-acceptance / lint 共用
# --------------------------------------------------------------------------
def evaluate(workspace: Workspace, rules: Rules, record: metadata.PrdRecord) -> VerificationStatus:
    """只读判定：AC 覆盖、代码版本、PRD hash、证据是否失效（SRS-FR-066/068，BR-016）。"""
    data = load_state(workspace, record)
    entries = sync_acs(data, record)
    status = VerificationStatus(prd=record.id)
    status.acs = entries
    status.summary = summarize(entries)
    status.runs = len(data.get("runs") or [])
    status.complete = bool(entries) and all(item.get("result") in COVERED for item in entries)
    status.repair_rounds = int(data.get("repair_rounds") or 0)
    status.repair_limit = repair_limit(workspace, rules)
    status.submitted = bool(data.get("submission"))
    status.submission = data.get("submission")
    status.prd_hash = str(data.get("prd_hash") or "") or None
    status.code_version = code_version(workspace, code_roots(rules))
    status.verification_digest = verification_digest(workspace, data)

    reasons: list[str] = []
    snapshot = data.get("snapshot") or {}
    if snapshot:
        if snapshot.get("prd_hash") and snapshot["prd_hash"] != record.compute_content_hash():
            reasons.append("PRD_CHANGED")
        if snapshot.get("code_version") and snapshot["code_version"] != status.code_version:
            reasons.append("CODE_CHANGED")
        for run in data.get("runs") or []:
            rel = str(run.get("raw_log") or "")
            if not rel:
                continue
            path = workspace.path(rel)
            if not path.is_file() or hashing.sha256_file(path) != run.get("raw_log_hash"):
                reasons.append("EVIDENCE_CHANGED")
                break
    status.stale_reasons = reasons
    status.stale = bool(reasons and (status.submitted or status.runs))
    submission = data.get("submission") or {}
    if submission:
        status.submission_outdated = bool(
            submission.get("prd_hash") != record.compute_content_hash()
            or submission.get("code_version") != status.code_version
            or submission.get("verification_digest") != status.verification_digest
        )
    return status


def checklist(workspace: Workspace, rules: Rules, record: metadata.PrdRecord,
              status: VerificationStatus | None = None) -> list[dict[str, Any]]:
    """AC 清单投影（供 `dev submit-acceptance` 与 ticket 07 的 checklist.md 读取）。"""
    status = status or evaluate(workspace, rules, record)
    items: list[dict[str, Any]] = []
    for item in status.acs:
        items.append({
            "ac": item.get("ac"),
            "scenario": item.get("scenario"),
            "result": item.get("result"),
            "reason": item.get("reason"),
            "suite": item.get("suite"),
            "evidence": item.get("evidence"),
            "run_id": item.get("run_id"),
        })
    return items


def load_rules_for(workspace: Workspace) -> Rules:
    from .rules import load_rules

    return load_rules(workspace)


# --------------------------------------------------------------------------
# 提交验收基线
# --------------------------------------------------------------------------
def build_submission(workspace: Workspace, record: metadata.PrdRecord,
                     data: dict[str, Any], actor: str | None = None,
                     *, roots: Iterable[str] = CODE_ROOTS) -> dict[str, Any]:
    """写入验收提交基线：代码版本 + PRD hash + 验证证据摘要 + AC 清单。

    这三者是 ticket 07/09 判定「旧提交是否失效」的依据（HLD §6.3，BR-016）。
    """
    code = code_version(workspace, roots)
    prd_hash = record.compute_content_hash()
    entries = data.get("acs") or []
    submission = {
        "at": fsutil.now_iso(),
        "by": actor,
        "code_version": code,
        "prd_hash": prd_hash,
        "prd_version": record.version,
        "verification_digest": verification_digest(workspace, data),
        "verification_summary": summarize(entries),
        "checklist": [_checklist_item(item) for item in entries],
        "raw_logs": [str(run.get("raw_log")) for run in (data.get("runs") or [])],
    }
    data["submission"] = submission
    data["prd_hash"] = prd_hash
    data["code_version"] = code
    data["verification_summary"] = submission["verification_summary"]
    return submission


def _checklist_item(item: dict[str, Any]) -> dict[str, Any]:
    return {
        "ac": item.get("ac"),
        "scenario": item.get("scenario"),
        "result": item.get("result"),
        "reason": item.get("reason"),
        "evidence": item.get("evidence"),
        "suite": item.get("suite"),
    }


# --------------------------------------------------------------------------
# 报告渲染
# --------------------------------------------------------------------------
def render_markdown(workspace: Workspace, rules: Rules, record: metadata.PrdRecord,
                    data: dict[str, Any], status: VerificationStatus | None = None) -> str:
    """结构化 Markdown 报告：front matter + 逐条 AC 表格 + 原始输出 + 修复轮次 + 结论。"""
    entries = data.get("acs") or sync_acs(data, record)
    summary = data.get("verification_summary") or summarize(entries)
    stamp = str(data.get("updated_at") or fsutil.now_iso())
    front = {
        "prd": record.id,
        "module": record.module,
        "baseline_version": record.version,
        "baseline_hash": record.content_hash,
        "prd_hash": data.get("prd_hash"),
        "code_version": data.get("code_version"),
        "updated_at": stamp,
        "verification_summary": summary,
        "repair_rounds": int(data.get("repair_rounds") or 0),
        "repair_limit": int(data.get("repair_limit") or DEFAULT_REPAIR_ROUNDS),
    }
    if data.get("submission"):
        front["submitted_at"] = data["submission"].get("at")

    lines: list[str] = [
        f"# 验证报告：{record.id} {record.title}",
        "",
        f"- PRD 基线: {record.content_hash or '未锁定'}",
        f"- 代码版本: {data.get('code_version') or '未记录'}",
        f"- 执行时间: {stamp}",
        f"- 环境: {_env_line(data)}",
        "",
        "## 逐条验收标准结果",
        "",
        "| AC | 结果 | 证据 |",
        "| --- | --- | --- |",
    ]
    for item in entries:
        evidence = str(item.get("evidence") or item.get("reason") or "—")
        lines.append(f"| {item.get('ac')} | {item.get('result')} | {evidence} |")

    lines += ["", "## 原始输出", ""]
    runs = data.get("runs") or []
    if not runs:
        lines.append("- 尚无验证记录。")
    for run in runs:
        lines.append(
            f"- {run.get('id')} suite={run.get('suite')} result={run.get('result')} "
            f"exit_code={run.get('exit_code')} command=`{run.get('command') or '—'}` "
            f"→ {run.get('raw_log')}"
            + (f"（{run.get('reason')}）" if run.get("reason") else "")
        )

    lines += ["", "## 修复轮次", ""]
    rounds = int(data.get("repair_rounds") or 0)
    limit = int(data.get("repair_limit") or repair_limit(workspace, rules))
    lines.append(f"本轮修复 {rounds} 次，上限 {limit} 次（在 constitution 可调整）。")
    for repair in data.get("repairs") or []:
        lines.append(f"- 第 {repair.get('round')} 轮：{repair.get('note')}（{repair.get('at')}）")
    if rounds >= limit:
        lines.append("- 已达修复上限：请人工介入，不要继续自动修复。")

    lines += ["", "## 结论", ""]
    if status is None:
        status = evaluate(workspace, rules, record)
    if status.complete:
        lines.append("全部适用验证项已覆盖（pass 或带理由的 not-applicable），可以提交人工验收。")
    else:
        pending = "、".join(
            f"{item.get('ac')}({item.get('result')})" for item in status.pending) or "无"
        lines.append(f"尚有未覆盖的验证项：{pending}。未执行/阻塞/失败都不得写成通过。")
    if status.stale:
        lines.append(f"注意：验证基线已失效（{'、'.join(status.stale_reasons)}），需重新验证。")
    if data.get("submission"):
        submission = data["submission"]
        lines += [
            "",
            "## 提交验收基线",
            "",
            f"- 代码版本: {submission.get('code_version')}",
            f"- PRD hash: {submission.get('prd_hash')}",
            f"- 证据摘要: {submission.get('verification_digest')}",
            f"- 提交时间: {submission.get('at')}",
            f"- 提交人: {submission.get('by')}",
        ]
    return fsutil.render_front_matter(front, "\n".join(lines) + "\n")


def _env_line(data: dict[str, Any]) -> str:
    runs = data.get("runs") or []
    if not runs:
        return "尚无执行环境记录"
    env = runs[-1].get("environment") or {}
    return f"{env.get('platform') or 'unknown'} / Python {env.get('python') or 'unknown'}"


def save_report(workspace: Workspace, rules: Rules, record: metadata.PrdRecord,
                data: dict[str, Any], *,
                status: VerificationStatus | None = None) -> str:
    path = workspace.path(report_path(workspace, record))
    fsutil.atomic_write_text(path, render_markdown(workspace, rules, record, data, status))
    return report_path(workspace, record)


# --------------------------------------------------------------------------
# 修复轮次
# --------------------------------------------------------------------------
def add_repair(workspace: Workspace, rules: Rules, record: metadata.PrdRecord,
               note: str) -> dict[str, Any]:
    """记一轮自动修复；达到上限抛 `REPAIR_LIMIT_EXCEEDED`（SRS-FR-065，BR-015）。"""
    data = load_state(workspace, record)
    limit = repair_limit(workspace, rules)
    rounds = int(data.get("repair_rounds") or 0)
    data["repair_limit"] = limit
    if rounds >= limit:
        raise FlowError(
            "REPAIR_LIMIT_EXCEEDED",
            f"{record.id} 已达自动修复上限（连续 {rounds} 轮，上限 {limit} 轮）",
            object=record.id,
            suggestion="已到上限：请人工介入定位问题（需求不明确或实现方向有误时不得继续自动修复）；"
                       "如确需放宽，由项目负责人在 .specify/constitution.md 调整 "
                       "max_repair_rounds",
            next_actions=[f"{FLOW_CORE} dev verification-status --prd {record.id}"],
        )
    rounds += 1
    data["repair_rounds"] = rounds
    repairs = list(data.get("repairs") or [])
    repairs.append({"at": fsutil.now_iso(), "round": rounds, "note": note})
    data["repairs"] = repairs
    return data
