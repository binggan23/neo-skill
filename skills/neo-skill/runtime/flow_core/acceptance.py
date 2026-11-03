"""验收域：Demo 材料、逐条 AC checklist、人工结论与验收判定（ticket 07）。

为什么单独一个模块：
- `acceptance prepare/record/suggest/approve/reject/conditional/status` 共享同一份
  `checklist.yaml` 读写与判定逻辑；
- ticket 08（收尾事务）需要只读判定「是否已人工确认通过、遗留问题有哪些」，
  入口是 `evaluate(workspace, rules, record) -> AcceptanceStatus`；
- ticket 09（结构 lint）需要「验收结论是否失效」，同样用 `evaluate()`。

目录约定（共享设计 §4.3）：

    07-acceptance/<module>/PRD-NNN-<slug>/
        demo.md          Demo 访问与操作材料（人工补齐环境信息，不伪造）
        checklist.md     人读清单（受管理渲染，逐条 AC 一行）
        checklist.yaml   机器读事实来源（逐条结论/证据/建议/遗留问题/决策）
        report.md        验收报告（验收人、时间、hash、代码版本、逐项结论、遗留问题）

**AI 只能写 `suggestion`，`conclusion` 只能由人工 `acceptance record` 写**（SRS-BR-013）。
本模块只做读写与判定：**不取锁**（由 `commands/acceptance.py` 取），不改 PRD 状态。
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Iterable

from . import fsutil, hashing, metadata, verification
from .output import FlowError
from .rules import Rules
from .workspace import Workspace

# --------------------------------------------------------------------------
# 常量
# --------------------------------------------------------------------------
DEMO_FILE = "demo.md"
CHECKLIST_FILE = "checklist.md"
CHECKLIST_STATE_FILE = "checklist.yaml"
REPORT_FILE = "report.md"
FILES = (DEMO_FILE, CHECKLIST_FILE, CHECKLIST_STATE_FILE, REPORT_FILE)

CONCLUSION_PENDING = "pending"
CONCLUSION_PASS = "pass"
CONCLUSION_FAIL = "fail"
CONCLUSION_BLOCKED = "blocked"
CONCLUSIONS = (CONCLUSION_PASS, CONCLUSION_FAIL, CONCLUSION_BLOCKED)

ACCEPTANCE_STATES = ("READY_FOR_ACCEPTANCE", "ACCEPTED")
RECORD_STATES = ("READY_FOR_ACCEPTANCE",)
SUGGEST_STATES = ("READY_FOR_ACCEPTANCE", "ACCEPTED")

DECISION_APPROVED = "approved"
DECISION_REJECTED = "rejected"
DECISION_CONDITIONAL = "conditional"

FLOW_CORE = "flow-core"

_SCENARIO_HEADING_RE = re.compile(
    r"^###\s+(SC-[A-Za-z0-9]+-\d{1,4})\s*(.*)$", re.MULTILINE)


# --------------------------------------------------------------------------
# 路径
# --------------------------------------------------------------------------
def acceptance_dir(workspace: Workspace, record: metadata.PrdRecord) -> str:
    """`07-acceptance/<module>/PRD-NNN-<slug>`（与 PRD/Spec 目录同构）。"""
    return f"{workspace.rel(workspace.acceptance_dir)}/{record.module}/{record.directory.name}"


def acceptance_path(workspace: Workspace, record: metadata.PrdRecord, name: str) -> str:
    return f"{acceptance_dir(workspace, record)}/{name}"


# --------------------------------------------------------------------------
# 结果对象
# --------------------------------------------------------------------------
@dataclass
class AcceptanceStatus:
    """`acceptance status` 与 ticket 08/09 共用的只读投影。"""

    prd: str
    prepared: bool = False
    items: list[dict[str, Any]] = field(default_factory=list)
    all_pass: bool = False
    conditional: bool = False
    leftovers: list[str] = field(default_factory=list)
    acceptance: dict[str, Any] | None = None
    stale: bool = False
    stale_reasons: list[str] = field(default_factory=list)
    # 清单本身（prepare 时记录的基线）与当前基线是否一致（未 approve 时的失效判定）
    baseline_stale: bool = False
    baseline_reasons: list[str] = field(default_factory=list)
    prd_hash: str | None = None
    code_version: str | None = None
    verification_digest: str | None = None
    checklist_digest: str | None = None
    decision: str | None = None

    @property
    def pending(self) -> list[dict[str, Any]]:
        """结论不是 `pass` 的 AC（含未记录）——这些是阻断「验收通过」的项。"""
        return [item for item in self.items
                if item.get("conclusion") != CONCLUSION_PASS]

    @property
    def approved(self) -> bool:
        return self.decision == DECISION_APPROVED

    @property
    def next_step(self) -> str | None:
        """给 ticket 08/09 与人类输出用的下一步标识。"""
        if self.accepted_live:
            return "closeout"
        if self.conditional:
            return "conditional"
        if self.prepared:
            return "record"
        return None

    @property
    def accepted_live(self) -> bool:
        """已人工确认通过且结论未失效 —— ticket 08 收尾事务的唯一入口条件。"""
        return bool(self.acceptance) and not self.stale

    @property
    def baseline_ok(self) -> bool:
        """清单基线与当前 PRD/代码/证据一致（prepare 之后没人改过东西）。"""
        return self.prepared and not self.baseline_stale

    def to_dict(self) -> dict[str, Any]:
        return {
            "prd": self.prd,
            "prepared": self.prepared,
            "items": list(self.items),
            "all_pass": self.all_pass,
            "conditional": self.conditional,
            "leftovers": list(self.leftovers),
            "acceptance": self.acceptance,
            "decision": self.decision,
            "stale": self.stale,
            "stale_reasons": list(self.stale_reasons),
            "baseline_stale": self.baseline_stale,
            "baseline_reasons": list(self.baseline_reasons),
            "prd_hash": self.prd_hash,
            "code_version": self.code_version,
            "verification_digest": self.verification_digest,
            "checklist_digest": self.checklist_digest,
            "next_step": self.next_step,
        }


# --------------------------------------------------------------------------
# checklist.yaml 读写
# --------------------------------------------------------------------------
def blank_checklist(record: metadata.PrdRecord) -> dict[str, Any]:
    return {
        "prd": record.id,
        "module": record.module,
        "prd_dir": record.rel,
        "prepared_at": None,
        "updated_at": None,
        "prd_hash": None,
        "code_version": None,
        "verification_digest": None,
        "status": record.status,
        "conditional": False,
        "leftovers": [],
        "items": [],
        "decisions": [],
    }


def checklist_path(workspace: Workspace, record: metadata.PrdRecord) -> Path:
    return workspace.path(acceptance_path(workspace, record, CHECKLIST_STATE_FILE))


def load_checklist(workspace: Workspace, record: metadata.PrdRecord) -> dict[str, Any] | None:
    """读 `checklist.yaml`；不存在返回 None（不写文件）。"""
    path = checklist_path(workspace, record)
    if not path.is_file():
        return None
    try:
        data = fsutil.read_yaml(path)
    except Exception:  # 损坏文件按「未准备」处理，由 lint 报告
        return None
    if not isinstance(data, dict):
        return None
    base = blank_checklist(record)
    base.update(data)
    if not isinstance(base.get("items"), list):
        base["items"] = []
    return base


def save_checklist(workspace: Workspace, record: metadata.PrdRecord,
                   data: dict[str, Any]) -> str:
    fsutil.atomic_write_yaml(checklist_path(workspace, record), data)
    return acceptance_path(workspace, record, CHECKLIST_STATE_FILE)


def checklist_digest(data: dict[str, Any]) -> str:
    """人工结论摘要：只覆盖逐条 AC 的结论/证据/记录人/时间（改结论即失效）。"""
    parts: list[tuple[str, str]] = []
    for item in data.get("items") or []:
        if not isinstance(item, dict):
            continue
        payload = json.dumps(
            [item.get("conclusion"), item.get("evidence"),
             item.get("recorded_by"), item.get("recorded_at")],
            ensure_ascii=False, sort_keys=True,
        )
        parts.append((str(item.get("ac")), hashing.sha256_text(payload)))
    return hashing.digest_of(parts) if parts else hashing.sha256_text("")


def sync_items(data: dict[str, Any], record: metadata.PrdRecord,
               entries: Iterable[dict[str, Any]]) -> list[dict[str, Any]]:
    """把 PRD 当前 AC 清单与验证证据对齐到 checklist（保留人工结论）。"""
    existing = {str(item.get("ac")): dict(item) for item in data.get("items") or []
                if isinstance(item, dict) and item.get("ac")}
    ordered: list[dict[str, Any]] = []
    for entry in entries:
        ac = str(entry.get("ac"))
        item = existing.pop(ac, None) or _blank_item(ac)
        item["ac"] = ac
        item["scenario"] = entry.get("scenario") or item.get("scenario")
        item["verification"] = entry.get("result")
        item["verification_evidence"] = entry.get("evidence") or entry.get("reason")
        item["verification_suite"] = entry.get("suite")
        ordered.append(item)
    for ac in sorted(existing):  # PRD 已移除的 AC：保留历史，标 stale
        item = existing[ac]
        item["stale"] = True
        ordered.append(item)
    data["items"] = ordered
    return ordered


def _blank_item(ac: str) -> dict[str, Any]:
    return {
        "ac": ac,
        "scenario": verification.ac_scenario(ac),
        "conclusion": CONCLUSION_PENDING,
        "evidence": None,
        "note": None,
        "recorded_by": None,
        "recorded_at": None,
        "verification": None,
        "verification_evidence": None,
        "verification_suite": None,
        "suggestion": None,
        "suggestion_reason": None,
        "suggestion_conflict": False,
        "suggested_by": None,
        "suggested_at": None,
        "superseded": [],
    }


def find_item(data: dict[str, Any], ac: str) -> dict[str, Any]:
    for item in data.get("items") or []:
        if str(item.get("ac")).upper() == ac.upper():
            return item
    known = "、".join(str(item.get("ac")) for item in data.get("items") or []) or "（无）"
    raise FlowError(
        "UNKNOWN_AC",
        f"验收清单里没有 {ac}",
        object=ac,
        suggestion=f"{data.get('prd')} 的 AC：{known}；先跑 "
                   f"{FLOW_CORE} acceptance prepare --prd {data.get('prd')}",
    )


# --------------------------------------------------------------------------
# 渲染：demo.md
# --------------------------------------------------------------------------
def scenario_titles(record: metadata.PrdRecord) -> dict[str, str]:
    """PRD 正文里 `### SC-RES-01 批量上传简历` 的编号 → 场景名（不臆造名字）。"""
    titles: dict[str, str] = {}
    for token, title in _SCENARIO_HEADING_RE.findall(record.body or ""):
        text = title.strip()
        titles[token.upper()] = text or ""
    return titles


def render_demo(workspace: Workspace, record: metadata.PrdRecord,
                data: dict[str, Any]) -> str:
    """Demo 材料：访问方式/启动命令/操作步骤全部是待填占位 + 已记录的验证证据。

    生成器只拼接「PRD 里已有的场景名」与「verification.json 里已有的结论」，
    **不伪造**环境地址、账号或操作步骤（SRS-FR-070、共享设计 §3）。
    """
    titles = scenario_titles(record)
    entries = {str(item.get("ac")): item for item in data.get("items") or []}
    front = {
        "prd": record.id,
        "module": record.module,
        "prepared_at": data.get("prepared_at"),
        "prd_hash": data.get("prd_hash"),
        "code_version": data.get("code_version"),
        "verification_digest": data.get("verification_digest"),
    }
    lines: list[str] = [
        f"# Demo：{record.id} {record.title}",
        "",
        "> 本文件由 `flow-core acceptance prepare` 依据 PRD 场景与 `verification.json` 生成。",
        "> 环境地址、账号、启动命令与具体操作步骤需要人工补齐 —— 生成器不伪造环境信息。",
        "",
        "## 访问方式",
        "",
        "<!-- TODO: 环境地址、访问入口、账号来源（不要写真实凭据）。 -->",
        "",
        f"- PRD：`{workspace.rel(record.directory)}/prd.md`（版本 {record.version}）",
        f"- PRD 基线：{data.get('prd_hash') or '未记录'}",
        f"- 代码版本：{data.get('code_version') or '未记录'}",
        "",
        "## 启动命令",
        "",
        "<!-- TODO: 写下本项目的启动命令（例如 python3 -m app），并说明依赖与数据准备。 -->",
        "",
        "## 操作步骤",
        "",
    ]
    grouped: dict[str, list[dict[str, Any]]] = {}
    for _ac, item in entries.items():
        scenario = str(item.get("scenario") or "未归属场景")
        grouped.setdefault(scenario, []).append(item)
    if not grouped:
        lines.append("1. <!-- TODO: 场景、操作、期望观察结果 -->")
    for scenario in sorted(grouped):
        title = titles.get(scenario) or ""
        heading = f"### {scenario} {title}".rstrip()
        lines += [heading, ""]
        if not title:
            lines.append("<!-- TODO: 场景描述（PRD 正文未写场景名，请补齐）。 -->")
            lines.append("")
        for item in grouped[scenario]:
            ac = item.get("ac")
            lines.append(f"- [ ] **{ac}** 人工核对：操作 → 期望结果（验证结论 "
                         f"{item.get('verification') or 'not-run'}）")
            lines.append("  - <!-- TODO: 具体操作步骤与期望观察结果 -->")
            evidence = item.get("verification_evidence") or "无验证证据"
            lines.append(f"  - 已有验证证据：{evidence}")
        lines.append("")
    lines += [
        "## 已知限制",
        "",
        "<!-- TODO: 演示环境中不覆盖的部分（例如外部依赖、真实数据、性能规模）。 -->",
    ]
    return fsutil.render_front_matter(front, "\n".join(lines) + "\n")


# --------------------------------------------------------------------------
# 渲染：checklist.md
# --------------------------------------------------------------------------
def render_checklist_md(workspace: Workspace, record: metadata.PrdRecord,
                        data: dict[str, Any]) -> str:
    """人读清单：每条 AC 一行（`AC ID | 场景 | 结论 | 证据 | 记录人`）。"""
    front = {
        "prd": record.id,
        "module": record.module,
        "prepared_at": data.get("prepared_at"),
        "prd_hash": data.get("prd_hash"),
        "code_version": data.get("code_version"),
        "verification_digest": data.get("verification_digest"),
        "conditional": bool(data.get("conditional")),
    }
    lines: list[str] = [
        f"# 验收清单：{record.id} {record.title}",
        "",
        f"- PRD 基线：{data.get('prd_hash') or '未记录'}",
        f"- 代码版本：{data.get('code_version') or '未记录'}",
        f"- 验证证据摘要：{data.get('verification_digest') or '未记录'}",
        "",
        "结论取值：`pass`（通过）/ `fail`（不通过）/ `blocked`（阻塞）/ `pending`（未记录）。",
        "AI 只能写 `checklist.yaml` 的 `suggestion` 字段，**不得**填写 `conclusion`：",
        "人工结论只能由 `flow-core acceptance record --actor <成员> ...` 写入（BR-013）。",
        "",
        "| AC | 场景 | 结论 | 证据 | 记录人 |",
        "| --- | --- | --- | --- | --- |",
    ]
    for item in data.get("items") or []:
        evidence = item.get("evidence") or item.get("verification_evidence") or "—"
        recorder = item.get("recorded_by") or "—"
        conclusion = item.get("conclusion") or CONCLUSION_PENDING
        if item.get("suggestion"):
            conclusion += f"（AI 建议 {item['suggestion']}）"
        lines.append(f"| {item.get('ac')} | {item.get('scenario') or '—'} | {conclusion} "
                     f"| {_cell(evidence)} | {recorder} |")
    if not data.get("items"):
        lines.append("| <!-- TODO: 先执行 acceptance prepare --> | — | pending | — | — |")
    if data.get("leftovers"):
        lines += ["", "## 遗留问题", ""]
        for leftover in data.get("leftovers") or []:
            lines.append(f"- {leftover}")
    return fsutil.render_front_matter(front, "\n".join(lines) + "\n")


def _cell(value: Any) -> str:
    text = str(value or "—").replace("|", "\\|").replace("\n", " ")
    return text


# --------------------------------------------------------------------------
# 渲染：report.md
# --------------------------------------------------------------------------
def render_report(workspace: Workspace, record: metadata.PrdRecord, data: dict[str, Any],
                  decision: dict[str, Any]) -> str:
    """验收报告：验收人、时间、PRD hash、代码版本、逐项结论、遗留问题（SRS-FR-072）。"""
    kind = str(decision.get("decision") or DECISION_APPROVED)
    label = {
        DECISION_APPROVED: "验收通过（ACCEPTED）",
        DECISION_REJECTED: "不通过（退回 DEVELOPING）",
        DECISION_CONDITIONAL: "有条件通过（不进入 ACCEPTED，保留遗留问题）",
    }.get(kind, kind)
    front = {
        "prd": record.id,
        "module": record.module,
        "decision": kind,
        "accepted": kind == DECISION_APPROVED,
        "approved_by": decision.get("by"),
        "approved_at": decision.get("at"),
        "role": decision.get("role"),
        "prd_hash": data.get("prd_hash"),
        "code_version": data.get("code_version"),
        "checklist_digest": checklist_digest(data),
        "verification_digest": data.get("verification_digest"),
    }
    lines: list[str] = [
        f"# 验收报告：{record.id} {record.title}",
        "",
        f"- 结论：{label}",
        f"- 验收人：{decision.get('by') or '—'}（{decision.get('role') or '—'}）",
        f"- 验收时间：{decision.get('at') or '—'}",
        f"- PRD 基线：{data.get('prd_hash') or '—'}",
        f"- PRD 版本：{record.version}",
        f"- 代码版本：{data.get('code_version') or '—'}",
        f"- 验证证据摘要：{data.get('verification_digest') or '—'}",
        f"- 验收清单摘要：{front['checklist_digest']}",
    ]
    if decision.get("reason"):
        lines.append(f"- 理由：{decision['reason']}")
    lines += ["", "## 逐条结论", "",
              "| AC | 场景 | 结论 | 证据 | 记录人 | 记录时间 |",
              "| --- | --- | --- | --- | --- | --- |"]
    for item in data.get("items") or []:
        evidence = item.get("evidence") or item.get("verification_evidence") or "—"
        lines.append(
            f"| {item.get('ac')} | {item.get('scenario') or '—'} "
            f"| {item.get('conclusion') or CONCLUSION_PENDING} | {_cell(evidence)} "
            f"| {item.get('recorded_by') or '—'} | {item.get('recorded_at') or '—'} |")
    if not data.get("items"):
        lines.append("| <!-- TODO --> | — | pending | — | — | — |")

    leftovers = list(data.get("leftovers") or [])
    lines += ["", "## 遗留问题", ""]
    if leftovers:
        for leftover in leftovers:
            lines.append(f"- {leftover}")
    else:
        lines.append("无。")

    lines += ["", "## 收尾状态", ""]
    if kind == DECISION_APPROVED:
        lines.append("当前状态：已确认通过，收尾未完成；主需求尚未 DONE。")
    else:
        lines.append("当前状态：本次验收未通过，尚未开始任何收尾同步。")
    lines += [
        "",
        "<!-- TODO: 收尾同步（功能现状、主需求、场景、索引）由 "
        "`flow-core acceptance closeout` 完成（ticket 08）。 -->",
    ]
    return fsutil.render_front_matter(front, "\n".join(lines) + "\n")


# --------------------------------------------------------------------------
# 判定：acceptance status / ticket 08 收尾 / ticket 09 lint 共用
# --------------------------------------------------------------------------
def evaluate(workspace: Workspace, rules: Rules,
             record: metadata.PrdRecord) -> AcceptanceStatus:
    """只读判定：清单是否就绪、结论是否全通过、验收结论是否失效（BR-016）。"""
    status = AcceptanceStatus(prd=record.id)
    verification_status = verification.evaluate(workspace, rules, record)
    status.prd_hash = record.compute_content_hash()
    status.code_version = verification_status.code_version
    status.verification_digest = verification_status.verification_digest

    acceptance = record.meta.get("acceptance")
    if isinstance(acceptance, dict) and acceptance:
        status.acceptance = dict(acceptance)
        status.decision = str(acceptance.get("decision") or DECISION_APPROVED)

    data = load_checklist(workspace, record)
    if data is None:
        return status

    status.prepared = True
    status.items = list(data.get("items") or [])
    status.all_pass = bool(status.items) and all(
        item.get("conclusion") == CONCLUSION_PASS for item in status.items)
    status.conditional = bool(data.get("conditional"))
    status.leftovers = [str(item) for item in (data.get("leftovers") or [])]
    status.checklist_digest = checklist_digest(data)
    status.baseline_reasons = baseline_mismatch(
        data, prd_hash=status.prd_hash, code_version=status.code_version,
        verification_digest=status.verification_digest,
    ) if data.get("items") else []
    status.baseline_stale = bool(status.baseline_reasons)

    if status.acceptance:
        reasons: list[str] = []
        if status.acceptance.get("prd_hash") != status.prd_hash:
            reasons.append("PRD_CHANGED")
        if status.acceptance.get("code_version") != status.code_version:
            reasons.append("CODE_CHANGED")
        if status.acceptance.get("verification_digest") != status.verification_digest:
            reasons.append("EVIDENCE_CHANGED")
        if status.acceptance.get("checklist_digest") != status.checklist_digest:
            reasons.append("CHECKLIST_CHANGED")
        # 原始输出被替换只有逐文件比对能发现（verification_digest 读的是记录里的 hash）
        for reason in verification_status.stale_reasons:
            if reason not in reasons:
                reasons.append(reason)
        status.stale_reasons = reasons
        status.stale = bool(reasons)
    return status


def require_state(record: metadata.PrdRecord, allowed: Iterable[str],
                  *, command: str) -> None:
    """验收类命令的状态前置；不满足 → `PRD_STATE_NOT_ALLOWED`（不写任何文件）。"""
    allowed = tuple(allowed)
    if record.status in allowed:
        return
    hint = (f"人工验收只能对已提交验收的 PRD 执行：先补齐验证并执行 "
            f"{FLOW_CORE} dev submit-acceptance --prd {record.id}")
    if record.status == "ACCEPTED":
        # ACCEPTED 是终态：不能再改验收结论；收尾走 closeout，需要重验则先走 CR
        hint = (f"{record.id} 已验收通过（结束状态，不能再改验收结论）：收尾请执行 "
                f"{FLOW_CORE} acceptance status --prd {record.id}；"
                f"若验收结论已失效（PRD/代码/证据变化），必须走 "
                f"{FLOW_CORE} prd cr --id {record.id} --reason \"<变更原因>\" "
                "重新评审批准后重新开发与验收（BR-016）")
    raise FlowError(
        "PRD_STATE_NOT_ALLOWED",
        f"PRD {record.id} 当前状态 {record.status}，不能执行 {command}（允许 {'、'.join(allowed)}）",
        object=record.id,
        suggestion=hint,
        next_actions=[f"{FLOW_CORE} dev verification-status --prd {record.id}"],
    )


def require_prepared(workspace: Workspace, record: metadata.PrdRecord) -> dict[str, Any]:
    """`checklist.yaml` 必须已由 `acceptance prepare` 生成。"""
    data = load_checklist(workspace, record)
    if data is None:
        raise FlowError(
            "ACCEPTANCE_NOT_PREPARED",
            f"{record.id} 还没有验收清单（07-acceptance 下缺少 checklist.yaml）",
            object=record.id,
            suggestion=f"先执行 {FLOW_CORE} acceptance prepare --prd {record.id} "
                       "生成 Demo 材料与逐条 AC 清单",
            next_actions=[f"{FLOW_CORE} acceptance prepare --prd {record.id}"],
        )
    return data


def baseline_mismatch(data: dict[str, Any], *, prd_hash: str,
                      code_version: str | None, verification_digest: str | None) -> list[str]:
    """清单基线 vs 当前基线的不一致项（失败前置，供 approve 使用）。"""
    reasons: list[str] = []
    if data.get("prd_hash") != prd_hash:
        reasons.append("PRD_CHANGED")
    if data.get("code_version") != code_version:
        reasons.append("CODE_CHANGED")
    if data.get("verification_digest") != verification_digest:
        reasons.append("EVIDENCE_CHANGED")
    return reasons


def archive_conclusions(data: dict[str, Any]) -> None:
    """基线变化后作废旧的人工结论：移到 `superseded`，`conclusion` 回到 pending。"""
    for item in data.get("items") or []:
        if not isinstance(item, dict):
            continue
        if item.get("conclusion") in (None, CONCLUSION_PENDING) and item.get("suggestion") is None:
            continue
        item.setdefault("superseded", []).append({
            "conclusion": item.get("conclusion"),
            "evidence": item.get("evidence"),
            "note": item.get("note"),
            "recorded_by": item.get("recorded_by"),
            "recorded_at": item.get("recorded_at"),
            "suggestion": item.get("suggestion"),
            "at": fsutil.now_iso(),
        })
        item["conclusion"] = CONCLUSION_PENDING
        item["evidence"] = None
        item["note"] = None
        item["recorded_by"] = None
        item["recorded_at"] = None
        item["suggestion"] = None
        item["suggestion_reason"] = None
        item["suggestion_conflict"] = False
    data["conditional"] = False
    data["leftovers"] = []


# --------------------------------------------------------------------------
# 给 ticket 08 收尾事务的入口
# --------------------------------------------------------------------------
def closeout_inputs(workspace: Workspace, rules: Rules,
                    record: metadata.PrdRecord) -> dict[str, Any]:
    """收尾事务需要的事实：已确认通过的验收记录 + 遗留问题（只读）。

    只有 `accepted_live` 为真（已人工确认通过且结论未失效）才允许开始收尾；
    否则返回 `ok=false` 与原因，调用方必须停止，不得把主需求置 DONE。
    """
    status = evaluate(workspace, rules, record)
    problems: list[str] = []
    if status.acceptance is None:
        problems.append("ACCEPTANCE_MISSING")
    if status.stale:
        problems.append("ACCEPTANCE_STALE")
    if record.status != "ACCEPTED":
        problems.append("PRD_STATE_NOT_ALLOWED")
    return {
        "ok": not problems,
        "problems": problems,
        "prd": record.id,
        "acceptance": status.acceptance,
        "leftovers": list(status.leftovers),
        "stale_reasons": list(status.stale_reasons),
        "prd_hash": status.prd_hash,
        "code_version": status.code_version,
        "checklist_digest": status.checklist_digest,
        "verification_digest": status.verification_digest,
    }
