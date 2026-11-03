"""`prd cr`：PRD 变更请求（CR）与重新批准（ticket 04）。

覆盖 SRS-FR-049/050/051、§6.3、BR-012/016/019：

- **创建 CR**（`cr create`）：记录内容/原因/影响面/版本变化；若 PRD 处于 `cr_states`
  （`APPROVED`/`DEVELOPING`/`READY_FOR_ACCEPTANCE`/`ACCEPTED`/`SUPERSEDED`），
  自动退回 `IN_REVIEW` 并在 metadata 写 `baseline_invalidated`，同时给已存在的 Spec /
  验收报告打失效标记（不存在就跳过，绝不新建目录）。
- **重新批准**（`cr approve`）：必须 `prd validate` 无 error；`version + 1`、重算
  `content_hash`、写 `approved_by/approved_at`；CR 置 `APPROVED` + `version_after`；
  旧批准与旧验收结论**保留**（只加 `superseded_by_*` 标记）。
- **拒绝**（`cr reject`）：CR 置 `REJECTED`；若正文与锁定字段没变（hash 一致），PRD 回到
  CR 前的状态并解除失效标记；若内容已改，PRD 留在 `IN_REVIEW`，失效标记保持有效。
- **查询**（`cr list`/`cr show`、`baseline-status`）：只读。

人工决策顺序与 `commands/prd.py` 一致（共享设计 §2.3）：解析对象 → `CONFIRMATION_REQUIRED`
→ 团队/身份/职责 → 状态跳转 → 取锁 → 追加 history/confirmations → 原子写 → 释放锁。
"""

from __future__ import annotations

import argparse
from pathlib import Path

from .. import baseline, changerequest as crs, fsutil, locks, metadata, state
from ..output import FlowError, Result
from ..rules import Rules
from ..workspace import Workspace

FLOW_CORE = "flow-core"
ACTOR_PLACEHOLDER = "<需求负责人>"

# PRD 状态机的机器名（rules/flow.yaml 的 `prd:` 段）
PRD_MACHINE = "prd"


# --------------------------------------------------------------------------
# 参数
# --------------------------------------------------------------------------
def add_arguments(parser: argparse.ArgumentParser) -> None:
    """把 `cr` 子组挂到 `prd` 命令组上（父 parser 已注册全局参数）。"""
    from ..cli import global_options_parser

    parents = [global_options_parser()]
    subparsers = parser.add_subparsers(dest="prd_cr_command", metavar="<子命令>",
                                       required=True)

    create = subparsers.add_parser("create", parents=parents,
                                   help="为 PRD 创建 CR（已建立基线的 PRD 会退回 IN_REVIEW）")
    create.add_argument("--id", required=True, help="PRD 编号，如 PRD-001")
    create.add_argument("--reason", default=None, help="变更原因（必填）")
    create.add_argument("--title", default=None, help="变更标题（默认取原因首句）")
    create.add_argument("--slug", default=None, help="CR 文件名 slug（默认从原因推导）")
    create.add_argument("--body-file", default=None,
                        help="变更内容正文（该 Markdown 文件写进 CR 的「变更内容」）")
    create.add_argument("--impact", action="append", default=None, metavar="KEY=VALUE,...",
                        help="影响面：spec/tasks/tests/design（true|false）与 version（目标版本）")

    listing = subparsers.add_parser("list", parents=parents, help="列出 PRD 的 CR")
    listing.add_argument("--id", required=True)
    listing.add_argument("--status", default=None, help="按 CR 状态过滤：OPEN/APPROVED/REJECTED")

    show = subparsers.add_parser("show", parents=parents, help="显示单个 CR")
    show.add_argument("--id", required=True)
    show.add_argument("--cr", required=True, metavar="CR-ID", help="CR 编号，如 CR-001")

    approve = subparsers.add_parser("approve", parents=parents,
                                    help="重新批准（版本 +1、生成新 hash、恢复基线）")
    approve.add_argument("--id", required=True)
    approve.add_argument("--cr", required=True, metavar="CR-ID")

    reject = subparsers.add_parser("reject", parents=parents, help="拒绝 CR")
    reject.add_argument("--id", required=True)
    reject.add_argument("--cr", required=True, metavar="CR-ID")
    reject.add_argument("--reason", default=None, help="拒绝理由（必填）")


def run(workspace: Workspace, rules: Rules, args: argparse.Namespace) -> Result:
    sub = str(getattr(args, "prd_cr_command", "") or "")
    handler = _HANDLERS.get(sub)
    if handler is None:
        raise FlowError(
            "USAGE",
            "缺少 prd cr 子命令",
            suggestion="可用子命令：" + "、".join(sorted(_HANDLERS)),
        )
    try:
        return handler(workspace, rules, args)
    except FlowError as exc:
        exc.command = f"prd.cr.{sub}"
        raise


# --------------------------------------------------------------------------
# 创建
# --------------------------------------------------------------------------
def _create(workspace: Workspace, rules: Rules, args: argparse.Namespace) -> Result:
    record = metadata.load(workspace, args.id, rules=rules)
    command = "prd.cr.create"
    actor = state.require_confirmation(_actor(args), _confirm(args), command=command)
    gate = _gate(rules, command)
    team = state.require_team(workspace, object=record.id)
    state.require_member(team, actor, role=str(gate["role"]), object=record.id)

    reason = str(args.reason or "").strip()
    if not reason:
        raise FlowError(
            "REASON_REQUIRED",
            "创建 CR 必须写明变更原因（--reason）",
            object=record.id,
            suggestion='例如 --reason "客户要求增加 CSV 导出，需扩展范围"',
        )

    impact = crs.parse_impact(args.impact)
    detail = _read_body_file(args.body_file, record.id)
    title = str(args.title or "").strip() or crs.summarize(detail, fallback=reason)
    content = crs.summarize(detail, fallback=title)
    slug = crs.validate_slug(args.slug, reason=reason)

    # 触发失效的状态集合来自规则 `prd.cr_states`（SRS-FR-050）。
    # 状态机里没有 `<status> → IN_REVIEW` 出边时（如终态 `ACCEPTED`/`SUPERSEDED`）只记失效、
    # 不改状态：规则表是状态的唯一事实来源，代码不越权造跳转。
    invalidating = record.status in metadata.cr_states(rules)
    to_state = _trigger_state(rules, record.status) if invalidating else None
    triggers = to_state is not None

    at = fsutil.now_iso()
    previous_status = record.status
    previous_hash = record.content_hash
    with locks.object_lock(workspace, record.id, command=command,
                           rules_version=rules.version):
        cr_id = crs.format_id(crs.next_number(record), rules)
        cr = crs.write(workspace, record, cr_id=cr_id, slug=slug, title=title,
                       reason=reason, impact=impact, content=content, actor=actor,
                       detail=detail)

        try:
            if invalidating:
                if triggers and to_state is not None:
                    state.apply_transition(record.meta, machine=PRD_MACHINE, to_state=to_state,
                                           actor=actor, action=command, rules=rules)
                    _annotate_history(record.meta, f"{cr_id}: {title}")
                _append_decision(record.meta, actor=actor, role=str(gate["role"]),
                                 decision="changes_requested", object_id=record.id,
                                 note=reason, extra={"cr": cr_id})
                record.meta["baseline_invalidated"] = baseline.mark_invalidated(
                    cr_id=cr_id, previous_status=previous_status,
                    previous_hash=previous_hash, actor=actor, at=at,
                )
                _mark_acceptance_superseded(record, cr_id=cr_id, at=at)
                metadata.save(workspace, record)
                marked = baseline.mark_downstream(workspace, record, cr_id=cr_id,
                                                  prd_hash=previous_hash, at=at, actor=actor)
            else:
                _append_decision(record.meta, actor=actor, role=str(gate["role"]),
                                 decision="changes_requested", object_id=record.id,
                                 note=reason, extra={"cr": cr_id})
                metadata.save(workspace, record)
                marked = []
        except BaseException:
            # 失败零副作用：metadata 未能落地时删掉刚写的 CR 文件（本次刚创建，不是用户资产）
            try:
                cr.path.unlink()
            except OSError:
                pass
            raise

    result = Result(command=command)
    result.add(cr.rel, "cr", "created")
    result.add(record.rel + "/" + metadata.METADATA_FILE, "prd", "updated")
    for path in marked:
        result.add(path, "baseline-marker", "updated")
    result.data["prd"] = record.to_dict()
    result.data["cr"] = cr.to_dict()
    result.data["invalidated"] = bool(invalidating)
    result.data["status_changed"] = bool(triggers)
    result.notes.append(f"{record.id} 已登记变更 {cr_id}（{actor}）：{title}")
    if invalidating:
        if triggers:
            result.notes.append(f"PRD 状态 {previous_status} → {to_state}，"
                                "旧基线失效：重新批准前不得继续正式开发")
        else:
            result.notes.append(f"PRD 处于终态 {previous_status}（规则表无回到 IN_REVIEW 的出边），"
                                "只登记变更并标记旧基线失效，状态保持不变")
        if marked:
            result.notes.append("已标记基线失效：" + "、".join(marked))
    else:
        result.notes.append(f"PRD 处于 {record.status}，本次 CR 只登记，不触发基线失效")
    result.next_actions.append(
        f"{FLOW_CORE} prd cr approve --id {record.id} --cr {cr_id} "
        f"--actor {_suggest_actor(workspace, rules)} --confirm"
    )
    return result


def _read_body_file(raw: str | None, prd_id: str) -> str | None:
    if not raw:
        return None
    path = Path(raw).expanduser()
    if not path.is_file():
        raise FlowError(
            "BODY_FILE_NOT_FOUND",
            f"变更内容文件不存在：{raw}",
            object=prd_id,
            suggestion="确认路径后重试；没有写入任何 CR",
        )
    return fsutil.read_text(path)


# --------------------------------------------------------------------------
# 重新批准
# --------------------------------------------------------------------------
def _approve(workspace: Workspace, rules: Rules, args: argparse.Namespace) -> Result:
    record = metadata.load(workspace, args.id, rules=rules)
    command = "prd.cr.approve"
    actor = state.require_confirmation(_actor(args), _confirm(args), command=command)
    gate = _gate(rules, command)
    team = state.require_team(workspace, object=record.id)
    state.require_member(team, actor, role=str(gate["role"]), object=record.id)

    cr = crs.load(workspace, record, args.cr, rules)
    _require_open(cr)
    to_state = str(gate.get("to") or "APPROVED")
    state.check_transition(rules, PRD_MACHINE, record.status, to_state, object=record.id)

    issues = metadata.review(workspace, record, rules)
    blocking = [item for item in issues if item["severity"] == metadata.SEVERITY_ERROR]
    if blocking:
        result = Result(command=command)
        result.data["prd"] = record.to_dict()
        result.data["cr"] = cr.to_dict()
        result.data["issues"] = issues
        codes = "、".join(item["code"] for item in blocking[:5])
        result.fail(
            "VALIDATION_FAILED",
            f"PRD {record.id} 还有 {len(blocking)} 个阻断问题（{codes}），不能重新批准",
            object=record.id,
            suggestion="先修正问题（prd update）再重新批准；失败不改任何状态与版本",
            next_action=f"{FLOW_CORE} prd validate --id {record.id}",
        )
        return result

    at = fsutil.now_iso()
    with locks.object_lock(workspace, record.id, command=command,
                           rules_version=rules.version):
        version_before = record.version
        record.meta["version"] = version_before + 1
        content_hash = record.compute_content_hash()
        state.apply_transition(record.meta, machine=PRD_MACHINE, to_state=to_state,
                               actor=actor, action=command, rules=rules)
        _annotate_history(record.meta, f"{cr.id}: 版本 {version_before} → {version_before + 1}")
        _append_decision(record.meta, actor=actor, role=str(gate["role"]),
                         decision="approved", object_id=record.id,
                         content_hash=content_hash, version=record.version,
                         extra={"cr": cr.id})
        record.meta["content_hash"] = content_hash
        record.meta["approved_by"] = actor
        record.meta["approved_at"] = at
        _resolve_invalidation(record, cr_id=cr.id, at=at)
        _mark_acceptance_superseded(record, cr_id=cr.id, at=at)
        metadata.save(workspace, record)

        cr.meta.update({
            "status": crs.STATUS_APPROVED,
            "version_after": record.version,
            "approved_by": actor,
            "approved_at": at,
        })
        crs.save(workspace, cr)

    result = Result(command=command)
    result.add(record.rel + "/" + metadata.METADATA_FILE, "prd", "updated")
    result.add(cr.rel, "cr", "updated")
    result.data["prd"] = record.to_dict()
    result.data["cr"] = cr.to_dict()
    result.data["content_hash"] = content_hash
    result.notes.append(f"{cr.id} 已批准，PRD {record.id} 版本 {version_before} → "
                        f"{record.version}，新基线已锁定")
    result.notes.append("旧批准与旧验收结论保留为历史，但对当前内容不再有效")
    result.next_actions.append(f"{FLOW_CORE} prd baseline-status --id {record.id}")
    return result


# --------------------------------------------------------------------------
# 拒绝
# --------------------------------------------------------------------------
def _reject(workspace: Workspace, rules: Rules, args: argparse.Namespace) -> Result:
    record = metadata.load(workspace, args.id, rules=rules)
    command = "prd.cr.reject"
    actor = state.require_confirmation(_actor(args), _confirm(args), command=command)
    gate = _gate(rules, command)
    team = state.require_team(workspace, object=record.id)
    state.require_member(team, actor, role=str(gate["role"]), object=record.id)

    cr = crs.load(workspace, record, args.cr, rules)
    _require_open(cr)
    reason = str(args.reason or "").strip()
    if not reason:
        raise FlowError(
            "REASON_REQUIRED",
            "拒绝 CR 必须写明理由（--reason）",
            object=cr.id,
            suggestion="例如 --reason \"本期范围已冻结，放到 v0.2.0\"",
        )

    marker = _own_invalidation(record, cr.id)
    intact = _baseline_intact(record)
    restore_to: str | None = None
    if intact and isinstance(marker, dict):
        candidate = str(marker.get("previous_status") or "")
        if candidate and rules.can_transition(PRD_MACHINE, record.status, candidate):
            restore_to = candidate

    at = fsutil.now_iso()
    with locks.object_lock(workspace, record.id, command=command,
                           rules_version=rules.version):
        if restore_to:
            state.apply_transition(record.meta, machine=PRD_MACHINE, to_state=restore_to,
                                   actor=actor, action=command, rules=rules)
        _annotate_history(record.meta, f"{cr.id}: {reason}"
                          + ("" if restore_to else "（内容已改，保持 IN_REVIEW）"))
        _append_decision(record.meta, actor=actor, role=str(gate["role"]),
                         decision="rejected", object_id=record.id, note=reason,
                         extra={"cr": cr.id})
        if isinstance(marker, dict):
            if intact and restore_to:
                baseline.mark_resolved(marker, cr_id=cr.id, at=at)
                record.meta["baseline_invalidated"] = marker
        metadata.save(workspace, record)

        cr.meta.update({
            "status": crs.STATUS_REJECTED,
            "rejected_by": actor,
            "rejected_at": at,
            "reject_reason": reason,
        })
        crs.save(workspace, cr)

    result = Result(command=command)
    result.add(record.rel + "/" + metadata.METADATA_FILE, "prd", "updated")
    result.add(cr.rel, "cr", "updated")
    result.data["prd"] = record.to_dict()
    result.data["cr"] = cr.to_dict()
    result.data["restored_to"] = restore_to
    result.notes.append(f"{cr.id} 已拒绝：{reason}")
    if restore_to:
        result.notes.append(f"正文与锁定字段未变，PRD 已回到 {restore_to}，基线恢复有效")
    else:
        result.notes.append(f"内容已改动，PRD 留在 {record.status}，基线仍失效")
        result.next_actions.append(
            f"{FLOW_CORE} prd cr create --id {record.id} --actor {_suggest_actor(workspace, rules)} "
            f'--confirm --reason "<本次改动的真实原因>"'
        )
    return result


# --------------------------------------------------------------------------
# 查询
# --------------------------------------------------------------------------
def _list(workspace: Workspace, rules: Rules, args: argparse.Namespace) -> Result:
    record = metadata.load(workspace, args.id, rules=rules)
    wanted = str(args.status or "").strip().upper() or None
    allowed = (crs.STATUS_OPEN, crs.STATUS_APPROVED, crs.STATUS_REJECTED)
    if wanted and wanted not in allowed:
        raise FlowError(
            "INVALID_STATE",
            f"CR 不存在状态 {wanted}",
            object=wanted,
            suggestion="可用状态：" + "、".join(allowed),
        )
    records = crs.list_crs(workspace, record)
    if wanted:
        records = [item for item in records if item.status == wanted]

    result = Result(command="prd.cr.list")
    result.data["prd"] = record.to_dict()
    result.data["crs"] = [item.to_dict() for item in records]
    result.data["count"] = len(records)
    for item in records:
        result.notes.append(f"{item.id}（{item.status}）{item.reason}")
    if not records:
        result.next_actions.append(
            f'{FLOW_CORE} prd cr create --id {record.id} --actor <需求负责人> --confirm '
            f'--reason "<变更原因>"'
        )
    return result


def _show(workspace: Workspace, rules: Rules, args: argparse.Namespace) -> Result:
    record = metadata.load(workspace, args.id, rules=rules)
    cr = crs.load(workspace, record, args.cr, rules)
    result = Result(command="prd.cr.show")
    result.data["prd"] = record.to_dict()
    result.data["cr"] = cr.to_dict()
    result.data["body"] = cr.body
    result.notes.append(f"{cr.id}（{cr.status}）{cr.reason}")
    if cr.status == crs.STATUS_OPEN:
        result.next_actions.append(
            f"{FLOW_CORE} prd cr approve --id {record.id} --cr {cr.id} "
            f"--actor {_suggest_actor(workspace, rules)} --confirm"
        )
    return result


def baseline_status(workspace: Workspace, rules: Rules, args: argparse.Namespace) -> Result:
    """`prd baseline-status`：`baseline.evaluate` 的薄壳（ticket 05/09 直接 import 函数）。"""
    record = metadata.load(workspace, args.id, rules=rules)
    status = baseline.evaluate(workspace, rules, record)
    result = Result(command="prd.baseline-status")
    result.data.update(status.to_dict())
    result.data["prd"] = record.to_dict()

    if baseline.REASON_MISSING in status.reasons:
        result.fail(
            "BASELINE_MISSING",
            f"PRD {record.id} 还没有批准基线（content_hash 为空）",
            object=record.id,
            suggestion="先走评审与人工批准：prd submit-review 后由需求负责人 prd approve",
            next_action=f"{FLOW_CORE} prd show --id {record.id}",
        )
        return result
    if baseline.REASON_MISMATCH in status.reasons:
        result.fail(
            "HASH_MISMATCH",
            f"PRD {record.id} 的内容与批准基线不一致（正文或锁定字段被直接修改）",
            object=record.id,
            suggestion="已批准内容的变化必须走 CR 并重新批准；不要直接改 prd.md 或锁定字段",
            next_action=f"{FLOW_CORE} prd cr create --id {record.id} --actor <需求负责人> "
                        f'--confirm --reason "<变更原因>"',
        )
    if baseline.REASON_INVALIDATED in status.reasons:
        marker = status.invalidated_by or {}
        result.fail(
            "BASELINE_INVALIDATED",
            f"PRD {record.id} 的基线因变更 {marker.get('cr')} 失效，重新批准前不得继续开发或沿用旧验收",
            object=record.id,
            suggestion="需求负责人批准该 CR（prd cr approve）或拒绝它（prd cr reject）后再继续",
            next_action=f"{FLOW_CORE} prd cr approve --id {record.id} "
                        f"--cr {marker.get('cr')} --actor <需求负责人> --confirm",
        )
    if status.valid:
        result.notes.append(f"{record.id} 基线有效（版本 {record.version}）")
    return result


# --------------------------------------------------------------------------
# 辅助
# --------------------------------------------------------------------------
# --------------------------------------------------------------------------
# 子命令路由
# --------------------------------------------------------------------------
_HANDLERS = {
    "create": _create,
    "list": _list,
    "show": _show,
    "approve": _approve,
    "reject": _reject,
}


def _actor(args: argparse.Namespace) -> str | None:
    return str(getattr(args, "actor", None) or "").strip() or None


def _confirm(args: argparse.Namespace) -> bool:
    return bool(getattr(args, "confirm", False))


def _gate(rules: Rules, command: str) -> dict:
    gates = rules.data.get("gates") or {}
    gate = gates.get(command)
    if not isinstance(gate, dict) or "role" not in gate:
        raise FlowError(
            "RULES_INVALID",
            f"规则缺少门禁定义 `{command}`",
            object=command,
            suggestion="在 .specify/rules/flow.yaml 的 gates 中声明 role（以及需要时的 from/to）",
        )
    return gate


def _trigger_state(rules: Rules, from_state: str) -> str | None:
    """CR 触发时 PRD 退回的状态：读 `prd` 状态机里 `from_state → *` 中包含 `IN_REVIEW` 的边。

    不硬编码状态值；没有这条边（终态）就返回 None，由调用方只标失效不改状态。
    """
    for target in rules.transitions(PRD_MACHINE).get(from_state, []):
        if target == "IN_REVIEW":
            return target
    return None


def _require_open(cr) -> None:
    if cr.status == crs.STATUS_OPEN:
        return
    raise FlowError(
        "CR_NOT_OPEN",
        f"CR {cr.id} 已是 {cr.status}，不能再次处理",
        object=cr.id,
        suggestion="每次变更只批准或拒绝一次；需要继续改请新建一个 CR",
    )


def _baseline_intact(record) -> bool:
    """正文与锁定字段是否仍与最后一次批准的 hash 一致（没有直接篡改）。"""
    expected = record.content_hash
    return bool(expected) and expected == record.compute_content_hash()


def _own_invalidation(record, cr_id: str):
    """只返回「由这个 CR 建立」的失效标记；别的 CR 建立的标记不由它解决。"""
    marker = baseline.invalidation(record)
    if marker is None or str(marker.get("cr") or "") != cr_id:
        return None
    return marker


def _resolve_invalidation(record, *, cr_id: str, at: str) -> None:
    """重新批准建立新基线：解除**当前任何**未决失效标记，并记下解决它的 CR。

    一个已拒绝的 CR 若因正文已改而留下了失效标记（见 `_reject`），后来的新 CR 批准
    覆盖了当前内容，这个标记就该解除——否则 PRD 会永远卡在失效状态。
    """
    marker = baseline.invalidation(record)
    if marker is None or not marker.get("active", False):
        return
    record.meta["baseline_invalidated"] = baseline.mark_resolved(marker, cr_id=cr_id, at=at)


def _mark_acceptance_superseded(record, *, cr_id: str, at: str) -> None:
    """旧验收结论保留原始字段，只加 `superseded_by_*` 标记（SRS-FR-051、BR-016）。"""
    acceptance = record.meta.get("acceptance")
    if not isinstance(acceptance, dict):
        return
    if str(acceptance.get("superseded_by_cr") or "") == cr_id:
        return
    acceptance.setdefault("superseded_by_version", acceptance.get("version"))
    acceptance["superseded_by_cr"] = cr_id
    acceptance["superseded_at"] = at
    record.meta["acceptance"] = acceptance


def _annotate_history(meta: dict, note: str | None) -> None:
    """给最后一条 history 追加说明（不新增记录，不改已有条目）。"""
    if not note:
        return
    history = meta.get("history")
    if isinstance(history, list) and history and isinstance(history[-1], dict):
        history[-1].setdefault("note", note)


def _append_decision(meta: dict, *, actor: str, role: str, decision: str, object_id: str,
                     content_hash: str | None = None, version: int | None = None,
                     note: str | None = None,
                     extra: dict | None = None) -> None:
    entry = state.confirmation_entry(actor=actor, role=role, decision=decision,
                                     object=object_id, content_hash=content_hash,
                                     version=version)
    if note:
        entry["note"] = note
    if extra:
        entry.update(extra)
    state.append_confirmation(meta, entry)


def _suggest_actor(workspace: Workspace, rules: Rules) -> str:
    gate = (rules.data.get("gates") or {}).get("prd.cr.approve")
    role = str(gate.get("role")) if isinstance(gate, dict) else state.ROLE_REQUIREMENT_OWNER
    team = state.load_team(workspace)
    if team is not None:
        for member in team.members:
            if member.has_role(role):
                return member.id
    return ACTOR_PLACEHOLDER
