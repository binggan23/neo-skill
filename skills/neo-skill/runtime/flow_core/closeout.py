"""验收收尾（ticket 08）：把「已确认通过」同步成一致的业务事实。

SRS-FR-075～077 / BR-017 / HLD §10.4。收尾要一次性同步四类事实：

1. **功能现状** `02-definition/modules/<module>/features/<feature>.md`
   —— 首次验收创建，已有则由「本次变化」追加（正文不被覆盖）；
2. **场景状态** `02-definition/modules/<module>/README.md` 的受管理区块
   —— P0 取值 `待规划 → 已规划 → 开发中 → 已验收`（SRS §6.4）；
3. **受管理索引** `backlog.md` / `feature-map.md` / `04-prds/index.md`
   —— 复用 `index.plan()/merge()`，与 `index` 命令同一套约定；
4. **主需求** `IN_PRD → DONE` —— **必须最后写**：其它同步没落地就不许标完成（FR-076）。

这些写入装在 `transactions.py` 的一个多文件事务里：任一步失败都保留人工验收结论，
报告「已确认通过，收尾未完成」，并留下可 `recover` 的审计记录（FR-077）。

本模块只做**计划与渲染**（`build()` 返回待写入内容，不碰磁盘），
锁、事务、metadata 写入与结果信封都在 `commands/closeout.py`。
"""

from __future__ import annotations

import copy
from dataclasses import dataclass, field
from typing import Any

from . import acceptance, fsutil, index, metadata, requirements, state
from .output import FlowError
from .rules import Rules
from .workspace import Workspace

FLOW_CORE = "flow-core"

# 场景状态（SRS §6.4）：P0 只有这四级，「已上线/已下线」不在 P0 范围
SCENARIO_PENDING = "待规划"
SCENARIO_PLANNED = "已规划"
SCENARIO_DEVELOPING = "开发中"
SCENARIO_ACCEPTED = "已验收"
SCENARIO_STATES: tuple[str, ...] = (
    SCENARIO_PENDING, SCENARIO_PLANNED, SCENARIO_DEVELOPING, SCENARIO_ACCEPTED,
)
_SCENARIO_RANK = {name: rank for rank, name in enumerate(SCENARIO_STATES)}

README_FILE = "README.md"
SCENARIO_SECTION = "## 场景状态"
CHANGE_SECTION = "## 本次变化"

# PRD 状态 → 场景状态（除本次收尾的 PRD 之外，其它场景按此推导，保证确定性）
_PRD_STATE_TO_SCENARIO = {
    "DRAFT": SCENARIO_PENDING,
    "IN_REVIEW": SCENARIO_PENDING,
    "CHANGES_REQUESTED": SCENARIO_PENDING,
    "REJECTED": SCENARIO_PENDING,
    "SUPERSEDED": SCENARIO_PENDING,
    "APPROVED": SCENARIO_PLANNED,
    "DEVELOPING": SCENARIO_DEVELOPING,
    "READY_FOR_ACCEPTANCE": SCENARIO_DEVELOPING,
    "ACCEPTED": SCENARIO_ACCEPTED,
}


# --------------------------------------------------------------------------
# 计划对象
# --------------------------------------------------------------------------
@dataclass
class CloseoutPlan:
    """收尾的一次性计划：待写入内容全部在这里，`build()` 不写任何文件。"""

    prd: str
    main_req: str | None
    main_req_rel: str | None
    main_req_status: str
    ops: list[dict[str, Any]] = field(default_factory=list)
    index_plans: list[index.BlockPlan] = field(default_factory=list)
    scenarios: dict[str, str] = field(default_factory=dict)
    feature_rel: str = ""
    readme_rel: str = ""
    problems: list[str] = field(default_factory=list)
    # 受管理区块被人工改过的目标（含模块 README 的场景区块）：覆盖前需要 --confirm
    modified_blocks: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return {
            "prd": self.prd,
            "main_req": self.main_req,
            "main_req_status": self.main_req_status,
            "ops": [{"order": op["order"], "path": op["path"]} for op in self.ops],
            "scenarios": dict(self.scenarios),
            "feature_doc": self.feature_rel,
            "scenario_block": self.readme_rel,
            "index_files": [plan.path for plan in self.index_plans],
            "modified_blocks": list(self.modified_blocks),
            "problems": list(self.problems),
        }


# --------------------------------------------------------------------------
# 路径
# --------------------------------------------------------------------------
def feature_doc_rel(workspace: Workspace, record: metadata.PrdRecord) -> str:
    return workspace.rel(metadata.feature_doc_path(workspace, record.module, record.feature))


def module_readme_rel(workspace: Workspace, record: metadata.PrdRecord) -> str:
    return workspace.rel(workspace.modules_dir / (record.module or "_") / README_FILE)


def main_req_rel(workspace: Workspace, req_id: str) -> str | None:
    directory = workspace.req_items_dir
    if not directory.is_dir():
        return None
    matches = sorted(directory.glob(f"{req_id}-*.md"))
    if matches:
        return workspace.rel(matches[0])
    exact = directory / f"{req_id}.md"
    return workspace.rel(exact) if exact.is_file() else None


# --------------------------------------------------------------------------
# 渲染：功能现状
# --------------------------------------------------------------------------
def change_marker(record: metadata.PrdRecord) -> str:
    """本次验收的幂等标记行：已存在同样的行 → 说明这次收尾已经写过（不重复追加）。"""
    data = record.meta.get("acceptance") or {}
    return (f"- 验收通过：{record.id}（{data.get('approved_at')}，"
            f"{data.get('approved_by')}）")


def _leftover_lines(record: metadata.PrdRecord) -> list[str]:
    data = record.meta.get("acceptance") or {}
    leftovers = [str(item) for item in (data.get("leftovers") or [])]
    if not leftovers:
        return ["- 遗留问题：无"]
    return ["- 遗留问题："] + [f"  - {item}" for item in leftovers]


def _change_lines(workspace: Workspace, record: metadata.PrdRecord,
                  scenarios: list[str]) -> list[str]:
    titles = acceptance.scenario_titles(record)
    lines = [change_marker(record)]
    if scenarios:
        lines.append("- 场景置为「" + SCENARIO_ACCEPTED + "」："
                     + "、".join(scenarios)
                     + ("（" + "；".join(
                         f"{sc} {titles[sc]}" for sc in scenarios if titles.get(sc)) + "）"
                        if any(titles.get(sc) for sc in scenarios) else ""))
    else:
        lines.append("- 本次 PRD 未登记场景")
    lines.extend(_leftover_lines(record))
    return lines


def render_feature_doc(workspace: Workspace, record: metadata.PrdRecord,
                       scenarios: list[str]) -> str:
    """首次验收的功能现状文档。只写结构与本次事实，不伪造功能描述。"""
    data = record.meta.get("acceptance") or {}
    titles = acceptance.scenario_titles(record)
    body = [
        f"# {record.title or record.feature or record.slug} 功能现状",
        "",
        "> 本文件由 `flow-core acceptance closeout` 维护：首次验收创建，"
        "之后每次验收只在「本次变化」追加，正文由人维护。",
        "",
        f"- 模块：{record.module or '—'}",
        f"- 功能标识：{record.feature or '—'}",
        f"- 当前状态：{SCENARIO_ACCEPTED}",
        f"- 首次验收：{record.id}（{data.get('approved_at') or '—'}，"
        f"{data.get('approved_by') or '—'}）",
        "",
        "## 概览",
        "",
        "<!-- TODO: 由人补充这个功能解决什么问题、边界与已知限制；"
        "收尾不会覆盖这里的人工内容。 -->",
        "",
        "## 场景",
        "",
        "| 场景 | 状态 | 说明 |",
        "| --- | --- | --- |",
    ]
    for scenario in scenarios:
        title = titles.get(scenario) or "—"
        body.append(f"| {scenario} | {SCENARIO_ACCEPTED} | {title} |")
    body += ["", CHANGE_SECTION, ""]
    body.extend(_change_lines(workspace, record, scenarios))
    return "\n".join(body) + "\n"


def render_feature_update(workspace: Workspace, record: metadata.PrdRecord,
                          existing: str, scenarios: list[str]) -> str:
    """已有功能现状：只在末尾追加「本次变化」，既有正文原样保留。"""
    lines = [CHANGE_SECTION, ""]
    lines.extend(_change_lines(workspace, record, scenarios))
    return existing.rstrip("\n") + "\n\n" + "\n".join(lines) + "\n"


def feature_doc_content(workspace: Workspace, record: metadata.PrdRecord,
                        scenarios: list[str]) -> str:
    path = workspace.path(feature_doc_rel(workspace, record))
    if not path.is_file():
        return render_feature_doc(workspace, record, scenarios)
    existing = fsutil.read_text(path)
    if change_marker(record) in existing:
        # 这次收尾已经写过（幂等）：不加重复的「本次变化」
        return existing
    return render_feature_update(workspace, record, existing, scenarios)


# --------------------------------------------------------------------------
# 渲染：场景状态表
# --------------------------------------------------------------------------
def _scenario_labels(workspace: Workspace, rules: Rules) -> dict[str, str]:
    """场景编号 → 场景名（取自各 PRD 正文的 `### SC-… 名称`）。"""
    labels: dict[str, str] = {}
    for record in metadata.list_prds(workspace):
        for scenario, title in acceptance.scenario_titles(record).items():
            if title and scenario not in labels:
                labels[scenario] = title
    return labels


def _scenario_existing(workspace: Workspace, readme_rel: str) -> dict[str, dict[str, str]]:
    """现有受管理区块里的场景行（状态可能已被人工调过）。"""
    path = workspace.path(readme_rel)
    if not path.is_file():
        return {}
    body = index.read_body(fsutil.read_text(path)) or ""
    rows: dict[str, dict[str, str]] = {}
    for line in body.splitlines():
        if not line.startswith("|"):
            continue
        cells = [cell.strip() for cell in line.strip().strip("|").split("|")]
        if len(cells) < 2 or not cells[0].startswith("SC-"):
            continue
        rows[cells[0]] = {
            "status": cells[1] if len(cells) > 1 else SCENARIO_PENDING,
            "prd": cells[2] if len(cells) > 2 else "—",
            "title": cells[3] if len(cells) > 3 else "",
        }
    return rows


def scenario_statuses(workspace: Workspace, rules: Rules,
                      record: metadata.PrdRecord) -> dict[str, str]:
    """收尾后应有的场景状态（本次 PRD 的场景固定为「已验收」）。"""
    return {scenario: row["status"]
            for scenario, row in _scenario_table(workspace, rules, record).items()}


def on_disk_scenarios(workspace: Workspace, record: metadata.PrdRecord) -> dict[str, str]:
    """**磁盘上当前**的场景状态（模块 README 受管理区块里写的，可能是空的）。

    `closeout-status` 报告这个（事实），收尾目标另见 `target_scenarios`。
    """
    existing = _scenario_existing(workspace, module_readme_rel(workspace, record))
    return {scenario: row["status"] for scenario, row in sorted(existing.items())}


def _scenario_table(workspace: Workspace, rules: Rules,
                    record: metadata.PrdRecord) -> dict[str, dict[str, str]]:
    """本模块的场景状态表（模块 README 只列本模块的场景）。"""
    readme_rel = module_readme_rel(workspace, record)
    existing = _scenario_existing(workspace, readme_rel)
    labels = _scenario_labels(workspace, rules)
    current = set(record.scenarios)

    table: dict[str, dict[str, str]] = {}
    for prd in metadata.list_prds(workspace):
        if record.module and prd.module != record.module:
            continue
        derived = _PRD_STATE_TO_SCENARIO.get(prd.status, SCENARIO_PENDING)
        for scenario in prd.scenarios:
            row = table.setdefault(scenario, {
                "status": SCENARIO_PENDING, "prd": "—", "title": labels.get(scenario, ""),
            })
            # 只前进不后退：取「推导值」与「现有值」中更靠后的一个
            prior = existing.get(scenario, {}).get("status")
            candidates = [derived] + ([prior] if prior in _SCENARIO_RANK else [])
            row["status"] = max(candidates, key=lambda name: _SCENARIO_RANK[name])
            if row["prd"] == "—" or prd.status == "ACCEPTED":
                row["prd"] = prd.id
    for scenario in sorted(current):
        row = table.setdefault(scenario, {
            "status": SCENARIO_ACCEPTED, "prd": record.id,
            "title": labels.get(scenario, ""),
        })
        row["status"] = SCENARIO_ACCEPTED
        row["prd"] = record.id
    return dict(sorted(table.items()))


def render_scenario_block(workspace: Workspace, rules: Rules,
                          record: metadata.PrdRecord) -> str:
    lines = [
        "| 场景 | 状态 | 主 PRD | 说明 |",
        "| --- | --- | --- | --- |",
    ]
    table = _scenario_table(workspace, rules, record)
    if not table:
        lines.append("| （暂无场景） | — | — | — |")
    for scenario, row in table.items():
        lines.append(f"| {scenario} | {row['status']} | {row['prd']} "
                     f"| {row['title'] or '—'} |")
    return "\n".join(lines) + "\n"


def module_readme_content(workspace: Workspace, rules: Rules,
                          record: metadata.PrdRecord) -> str:
    """模块 README：只替换/追加受管理区块，区块外人工内容原样保留。"""
    rel = module_readme_rel(workspace, record)
    path = workspace.path(rel)
    body = render_scenario_block(workspace, rules, record)
    if not path.is_file():
        header = (
            f"# {record.module or '模块'} 模块\n\n"
            "<!-- TODO: 由人说明本模块的业务范围与边界；"
            "下面的场景状态区块由 flow-core 维护。 -->\n\n"
            f"{SCENARIO_SECTION}\n\n"
        )
        return header + index.render_block(body) + "\n"
    current = fsutil.read_text(path)
    merged = index.merge(current, body)
    if index.split(current) is None:
        # 原本没有区块：追加到末尾（`index.merge` 已做到），顺手补一个二级标题
        merged = current.rstrip("\n") + f"\n\n{SCENARIO_SECTION}\n\n" + index.render_block(body) + "\n"
    return merged


# --------------------------------------------------------------------------
# 计划
# --------------------------------------------------------------------------
def build(workspace: Workspace, rules: Rules, record: metadata.PrdRecord) -> CloseoutPlan:
    """生成收尾计划（只读）。顺序固定：功能现状 → 场景 → 索引 → 主需求。"""
    source_reqs = record.source_reqs
    main_req = source_reqs[0] if source_reqs else None
    plan = CloseoutPlan(
        prd=record.id,
        main_req=main_req,
        main_req_rel=main_req_rel(workspace, main_req) if main_req else None,
        main_req_status="",
        feature_rel=feature_doc_rel(workspace, record),
        readme_rel=module_readme_rel(workspace, record),
    )
    if not main_req or not plan.main_req_rel:
        plan.problems.append("MAIN_REQ_MISSING")
        return plan

    scenarios = list(record.scenarios)
    plan.scenarios = scenario_statuses(workspace, rules, record)

    ops: list[dict[str, Any]] = []
    order = 1

    # ①功能现状
    feature_content = feature_doc_content(workspace, record, scenarios)
    _add_op(ops, order, plan.feature_rel, feature_content)
    order += 1

    # ②场景状态（模块 README 的受管理区块，与索引同一套约定）
    if has_modified_block(workspace, plan.readme_rel):
        plan.modified_blocks.append(plan.readme_rel)
    _add_op(ops, order, plan.readme_rel, module_readme_content(workspace, rules, record))
    order += 1

    # ③受管理索引（先算主需求新内容，让索引反映「即将 DONE」）
    req_record = requirements.load(workspace, main_req, rules=rules)
    req_text, req_status = _main_req_content(rules, req_record, record)
    plan.main_req_status = req_status
    overrides = {plan.main_req_rel: req_text}
    plan.index_plans = index.plan(workspace, rules, overrides=overrides).plans
    for block in plan.index_plans:
        if block.modified:
            plan.modified_blocks.append(block.path)
        _add_op(ops, order, block.path, block.text)
        order += 1

    # ④主需求：最后写（收尾未完成时不得 DONE，FR-076）
    if req_record.status != req_status:
        _add_op(ops, order, plan.main_req_rel, req_text)
    plan.ops = ops
    return plan


def has_modified_block(workspace: Workspace, rel: str) -> bool:
    path = workspace.path(rel)
    return path.is_file() and index.is_modified(fsutil.read_text(path))


def require_confirm(plan: CloseoutPlan, *, confirm: bool) -> None:
    """受管理区块被人工改过且没有显式确认 → `MANAGED_BLOCK_MODIFIED`（不写任何文件）。"""
    if not plan.modified_blocks or confirm:
        return
    paths = "、".join(plan.modified_blocks)
    raise FlowError(
        "MANAGED_BLOCK_MODIFIED",
        f"受管理区块被人工修改过：{paths}",
        object=plan.modified_blocks[0],
        suggestion="确认这些修改可以被收尾覆盖后，加 --confirm（并申报 --actor）重新执行；"
                   "区块外的说明不会被覆盖",
        next_actions=[f"{FLOW_CORE} acceptance closeout --prd {plan.prd} "
                      f"--actor <成员 id> --confirm"],
    )


def _add_op(ops: list[dict[str, Any]], order: int, path: str, content: str) -> None:
    """一个待写入目标。`action` 留给 `transactions.begin()` 按磁盘现状判定。"""
    ops.append({"order": order, "path": path, "content": content})


def _main_req_content(rules: Rules, req_record: requirements.ReqRecord,
                      record: metadata.PrdRecord) -> tuple[str, str]:
    """主需求的收尾后内容与状态（`IN_PRD → DONE`，幂等：已 DONE 则不动）。"""
    if req_record.status == "DONE":
        return fsutil.render_front_matter(req_record.meta, req_record.body), "DONE"
    meta = copy.deepcopy(req_record.meta)
    acceptance_data = record.meta.get("acceptance") or {}
    state.apply_transition(
        meta, machine="req", to_state="DONE",
        actor=str(acceptance_data.get("approved_by") or FLOW_CORE),
        action="acceptance.closeout", rules=rules,
    )
    return fsutil.render_front_matter(meta, req_record.body), "DONE"


def pending_ops(workspace: Workspace, plan: CloseoutPlan) -> list[str]:
    """计划里尚未落地（磁盘内容与计划不一致）的目标路径。"""
    pending: list[str] = []
    for op in plan.ops:
        path = workspace.path(op["path"])
        current = fsutil.read_text(path) if path.is_file() else None
        if current != op["content"]:
            pending.append(op["path"])
    return pending


# --------------------------------------------------------------------------
# 只读判定：acceptance closeout-status / ticket 09 lint 共用
# --------------------------------------------------------------------------
def evaluate_closeout(workspace: Workspace, rules: Rules,
                      record: metadata.PrdRecord) -> dict[str, Any]:
    """收尾进度投影（**只读**，不取锁、不写文件）。

    返回：`confirmed`、`closeout_state`、`tx`、`pending_ops`、`main_req_status`、
    `scenarios`（磁盘事实）、`target_scenarios`（收尾目标）、`index_ok`、`index_problems`、
    `resumable`、`closeout_ok`、`problems`、`acceptance_problems`、`stale_reasons`。

    ticket 09 的 lint 用它判「验收后是否未收尾」，不要自己重算同步状态。
    """
    inputs = acceptance.closeout_inputs(workspace, rules, record)
    metadata_closeout = record.meta.get("closeout")
    closeout = dict(metadata_closeout) if isinstance(metadata_closeout, dict) else None
    confirmed = bool(record.status == "ACCEPTED" and inputs.get("ok"))

    index_state = index.evaluate(workspace, rules)
    plan = build(workspace, rules, record) if confirmed else None
    pending = pending_ops(workspace, plan) if plan is not None else []
    problems = list(plan.problems) if plan is not None else list(inputs.get("problems") or [])

    main_req_status = ""
    if plan is not None and plan.main_req:
        try:
            main_req_status = requirements.load(
                workspace, plan.main_req, rules=rules).status
        except FlowError:
            main_req_status = ""
    closeout_ok = (
        confirmed
        and (closeout or {}).get("state") == "COMMITTED"
        and main_req_status == "DONE"
        and index_state["ok"]
        and not pending
    )
    return {
        "prd": record.id,
        "confirmed": confirmed,
        "closeout_state": (closeout or {}).get("state"),
        "tx": (closeout or {}).get("tx"),
        "pending_ops": pending,
        "main_req": plan.main_req if plan is not None else None,
        "main_req_status": main_req_status,
        "scenarios": on_disk_scenarios(workspace, record),
        "target_scenarios": dict(plan.scenarios) if plan is not None else {},
        "index_ok": bool(index_state["ok"]),
        "index_problems": list(index_state["problems"]),
        "resumable": bool(pending),
        "closeout_ok": closeout_ok,
        "problems": problems,
        "acceptance_problems": list(inputs.get("problems") or []),
        "stale_reasons": list(inputs.get("stale_reasons") or []),
    }
