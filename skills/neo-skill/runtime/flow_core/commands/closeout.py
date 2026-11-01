"""`acceptance closeout` / `acceptance closeout-status`：可恢复的验收收尾（ticket 08）。

命令边界（SRS-FR-075～077、BR-017、HLD §10.4）：

- `acceptance closeout --prd P [--actor A] [--confirm] [--retry]`：
  前置「PRD 已 `ACCEPTED` 且 `acceptance.approved_at` 存在」（否则 `ACCEPTANCE_NOT_CONFIRMED`）；
  用**一个多文件事务**同步功能现状、场景状态、受管理索引，最后才把主需求 `IN_PRD → DONE`。
  全部成功 → PRD 保持 `ACCEPTED`、主需求 `DONE`、metadata 写
  `closeout: {state: COMMITTED, tx, at, by}`。
  任一步失败 → 保留人工验收结论，metadata 写 `closeout: {state: PREPARED|FAILED, tx, error,
  pending[]}`，返回 `success=false` + `CLOSEOUT_INCOMPLETE` +「已确认通过，收尾未完成」+ 恢复建议。
- `acceptance closeout-status --prd P`：只读投影（`closeout.evaluate_closeout()`），
  ticket 09 的 lint 直接 import 该函数，不要自己重算。

`--confirm` 只在受管理区块被人工修改（`MANAGED_BLOCK_MODIFIED`）时才需要。
状态、锁、事务与信封都在本模块；计划与渲染在 `flow_core/closeout.py`。
"""

from __future__ import annotations

import argparse
from typing import Any

from .. import acceptance, closeout, fsutil, index, locks, metadata, requirements, state, transactions
from ..output import FlowError, Result
from ..rules import Rules
from ..workspace import Workspace

FLOW_CORE = "flow-core"
ACTOR_PLACEHOLDER = "<成员 id>"
COMMANDS = ("closeout", "closeout-status")

CONFIRMED_HINT = ("人工验收结论必须先在需求负责人确认下产生："
                  f"{FLOW_CORE} acceptance approve --prd {{prd}} --actor {ACTOR_PLACEHOLDER} --confirm")


# --------------------------------------------------------------------------
# 参数
# --------------------------------------------------------------------------
def add_arguments(subparsers: Any, parents: list[Any]) -> None:
    """在 `acceptance` 的子命令解析器上追加两个子命令（ticket 07 的注册保持原样）。"""
    run = subparsers.add_parser(
        "closeout", parents=parents,
        help="验收确认后的收尾同步：功能现状、主需求 DONE、场景状态与索引（可恢复事务）")
    run.add_argument("--prd", default=None, help="PRD 编号（必须显式绑定）")
    run.add_argument("--retry", action="store_true",
                     help="重试该 PRD 上一次未完成的收尾事务（等价于 recover run）")

    status = subparsers.add_parser(
        "closeout-status", parents=parents,
        help="报告收尾进度：是否已确认、事务状态、待办目标、主需求/场景/索引是否一致（只读）")
    status.add_argument("--prd", default=None, help="PRD 编号")


def run(workspace: Workspace, rules: Rules, args: argparse.Namespace) -> Result:
    sub = getattr(args, "acceptance_command", None)
    handler = _HANDLERS.get(sub)
    if handler is None:  # pragma: no cover - 注册表保证不会走到
        raise FlowError("USAGE", f"未知的收尾子命令 {sub}")
    try:
        return handler(workspace, rules, args)
    except FlowError as exc:
        if not exc.command:
            exc.command = f"acceptance.{sub}"
        raise


# --------------------------------------------------------------------------
# acceptance closeout
# --------------------------------------------------------------------------
def _closeout(workspace: Workspace, rules: Rules, args: argparse.Namespace) -> Result:
    record = metadata.load(workspace, _require_prd(args), rules=rules)
    actor = _resolve_actor(workspace, args, record.id)
    _require_confirmed(workspace, rules, record)

    if _is_finished(record):
        return _already_done(workspace, rules, record, actor)

    if bool(getattr(args, "retry", False)):
        return _retry(workspace, rules, record, actor, allow_confirm=bool(getattr(args, "confirm", False)))

    plan = closeout.build(workspace, rules, record)
    if plan.problems:
        raise FlowError(
            "CLOSEOUT_TARGET_MISSING",
            f"{record.id} 的收尾目标不完整（{'、'.join(plan.problems)}）",
            object=record.id,
            suggestion="PRD 必须显式绑定一个主需求（source_reqs），否则无法判定「需求是否完成」",
        )
    closeout.require_confirm(plan, confirm=bool(getattr(args, "confirm", False)))

    pending = closeout.pending_ops(workspace, plan)
    if not pending and plan.main_req_status == "DONE":
        # 已经一致：只补 closeout 记录（幂等）
        entry = _write_closeout(workspace, record, actor, tx=None, state=transactions.STATE_COMMITTED)
        return _success(workspace, rules, record, entry, notes=[
            f"{record.id} 的收尾目标已经全部一致，未新建事务"])

    result = Result(command="acceptance.closeout")
    tx = None
    try:
        with locks.object_locks(workspace, [record.id] + ([plan.main_req] if plan.main_req else []),
                                command="acceptance.closeout", rules_version=rules.version):
            tx = transactions.begin(workspace, record.id, plan.ops,
                                    command="acceptance.closeout", actor=actor)
            transactions.commit(workspace, tx)
    except FlowError as exc:
        if tx is None:
            raise
        return _incomplete(workspace, rules, record, tx, actor, error=exc)

    entry = _write_closeout(workspace, record, actor, tx=tx,
                            state=transactions.STATE_COMMITTED)
    for op in tx.ops:
        result.add(op.path, _kind_of(op.path), "created" if op.action == "create" else "updated")
    result.add(f"{record.rel}/{metadata.METADATA_FILE}", "prd", "updated")
    return _success(workspace, rules, record, entry, result=result)


def _retry(workspace: Workspace, rules: Rules, record: metadata.PrdRecord, actor: str,
           *, allow_confirm: bool) -> Result:
    """`acceptance closeout --retry`：续跑该 PRD 上一次未完成的事务。"""
    tx = transactions.find_for_object(workspace, record.id)
    if tx is None:
        raise FlowError(
            "TX_NOT_FOUND",
            f"{record.id} 没有未完成的收尾事务",
            object=record.id,
            suggestion=f"直接执行 {FLOW_CORE} acceptance closeout --prd {record.id} --actor {actor}",
            next_actions=[f"{FLOW_CORE} acceptance closeout --prd {record.id} --actor {actor}"],
        )
    if not allow_confirm and _tx_touches_modified_block(workspace, rules, tx):
        raise FlowError(
            "MANAGED_BLOCK_MODIFIED",
            "未完成事务的目标文件里有被人工改过的受管理区块",
            object=record.id,
            suggestion="确认这些修改可以被覆盖后加 --confirm 重新执行",
        )
    objects = [record.id]
    if record.source_reqs:
        objects.append(record.source_reqs[0])
    with locks.object_locks(workspace, objects, command="acceptance.closeout",
                            rules_version=rules.version):
        transactions.recover_one(workspace, tx)
    entry = _write_closeout(workspace, record, actor, tx=tx,
                            state=transactions.STATE_COMMITTED)
    result = Result(command="acceptance.closeout")
    for op in tx.ops:
        result.add(op.path, _kind_of(op.path), "updated")
    result.add(f"{record.rel}/{metadata.METADATA_FILE}", "prd", "updated")
    return _success(workspace, rules, record, entry, result=result,
                    notes=[f"已续跑事务 {tx.id}"])


def _incomplete(workspace: Workspace, rules: Rules, record: metadata.PrdRecord,
                tx: transactions.Transaction, actor: str, *, error: FlowError) -> Result:
    """收尾未完成：保留人工验收结论，记录错误与待办，返回可恢复的失败信封。"""
    pending = transactions.pending_ops(workspace, tx)
    entry = {
        "state": tx.state if tx.state in (transactions.STATE_PREPARED,
                                          transactions.STATE_FAILED) else transactions.STATE_FAILED,
        "tx": tx.id,
        "at": fsutil.now_iso(),
        "by": actor,
        "error": error.message,
        "code": error.code,
        "pending": pending,
        "main_req": (record.source_reqs or [None])[0],
    }
    record.meta["closeout"] = entry
    metadata.save(workspace, record)

    result = Result(command="acceptance.closeout")
    result.fail(
        "CLOSEOUT_INCOMPLETE",
        f"{record.id} 已确认通过，收尾未完成：{error.message}",
        object=record.id,
        suggestion="人工验收结论已保留（PRD 仍是 ACCEPTED），但主需求尚未 DONE。"
                   "按下面的步骤恢复，不要重复验收",
    )
    result.data["prd"] = record.to_dict()
    result.data["closeout"] = entry
    result.data["tx"] = tx.id
    result.data["closeout_state"] = entry["state"]
    result.data["pending"] = pending
    result.data["main_req"] = entry["main_req"]
    result.data["main_req_status"] = _main_req_status(workspace, rules, entry["main_req"])
    result.notes.append("已确认通过，收尾未完成")
    result.next_actions.extend([
        f"{FLOW_CORE} acceptance closeout --prd {record.id} --retry --actor {actor}",
        f"{FLOW_CORE} recover run --tx {tx.id} --actor {actor} --confirm",
        f"{FLOW_CORE} acceptance closeout-status --prd {record.id}",
    ])
    return result


def _already_done(workspace: Workspace, rules: Rules, record: metadata.PrdRecord,
                  actor: str) -> Result:
    entry = record.meta.get("closeout") or {}
    filled = _success(workspace, rules, record, dict(entry),
                      notes=[f"{record.id} 已在 {entry.get('at')} 完成收尾（{entry.get('by')}），"
                             "本次无变化"])
    filled.data["already_done"] = True
    return filled


def _success(workspace: Workspace, rules: Rules, record: metadata.PrdRecord,
             entry: dict[str, Any], *, result: Result | None = None,
             notes: list[str] | None = None) -> Result:
    result = result or Result(command="acceptance.closeout")
    data = result.data
    data["prd"] = record.to_dict()
    data["closeout"] = entry
    data["tx"] = entry.get("tx")
    data["closeout_state"] = entry.get("state")
    data["pending"] = []
    data["main_req"] = entry.get("main_req") or (record.source_reqs or [None])[0]
    data["main_req_status"] = _main_req_status(workspace, rules, data["main_req"])
    data["scenarios"] = closeout.scenario_statuses(workspace, rules, record)
    data["index_ok"] = index.evaluate(workspace, rules)["ok"]
    for note in notes or []:
        result.notes.append(note)
    result.notes.append(
        f"{record.id} 收尾完成：功能现状已同步，主需求 {data['main_req']} "
        f"{data['main_req_status']}，场景与索引一致（PRD 保持 ACCEPTED）")
    result.next_actions.append(
        f"{FLOW_CORE} acceptance closeout-status --prd {record.id}")
    return result


def _write_closeout(workspace: Workspace, record: metadata.PrdRecord, actor: str, *,
                    tx: transactions.Transaction | None, state: str) -> dict[str, Any]:
    """写 PRD metadata 的 `closeout`（事务提交后的完成记录）与 `acceptance.closeout`。"""
    previous = record.meta.get("closeout") or {}
    entry = {
        "state": state,
        "tx": tx.id if tx is not None else previous.get("tx"),
        "at": fsutil.now_iso(),
        "by": actor,
        "main_req": (record.source_reqs or [None])[0],
        "ops": [op.path for op in tx.ops] if tx is not None else [],
    }
    record.meta["closeout"] = entry
    acceptance_data = record.meta.get("acceptance")
    if isinstance(acceptance_data, dict):
        acceptance_data["closeout"] = entry
        record.meta["acceptance"] = acceptance_data
    metadata.save(workspace, record)
    return entry


# --------------------------------------------------------------------------
# acceptance closeout-status（只读）
# --------------------------------------------------------------------------
def _closeout_status(workspace: Workspace, rules: Rules, args: argparse.Namespace) -> Result:
    record = metadata.load(workspace, _require_prd(args), rules=rules)
    data = closeout.evaluate_closeout(workspace, rules, record)

    result = Result(command="acceptance.closeout-status")
    result.data.update(data)
    result.data["prd"] = record.to_dict()

    if not data["confirmed"]:
        if "ACCEPTANCE_STALE" in data["acceptance_problems"]:
            result.fail(
                "ACCEPTANCE_STALE",
                f"{record.id} 的验收结论已失效（{'、'.join(data['stale_reasons'])}），不能收尾",
                object=record.id,
                suggestion="PRD/代码/证据/清单变化后旧结论不得沿用：重新 prepare 并逐条重新记录",
                next_action=f"{FLOW_CORE} acceptance prepare --prd {record.id}",
            )
            return result
        # 未确认不是错误：这只是「还不能收尾」的状态事实，如实报告即可
        result.notes.append(
            f"{record.id} 尚未人工确认通过（当前状态 {record.status}），还不能收尾")
        result.next_actions.extend([
            f"{FLOW_CORE} acceptance status --prd {record.id}",
            f"{FLOW_CORE} acceptance approve --prd {record.id} --actor {ACTOR_PLACEHOLDER} --confirm",
        ])
        return result

    if data["closeout_state"] == "COMMITTED" and data["main_req_status"] == "DONE":
        result.notes.append(
            f"{record.id} 收尾已完成：主需求 {data['main_req']} DONE，索引一致")
        return result

    result.notes.append(
        f"{record.id} 已确认通过，收尾未完成（"
        f"事务 {data['tx'] or '未创建'}，状态 {data['closeout_state'] or '未开始'}）")
    if data["pending_ops"]:
        result.notes.append(f"待同步 {len(data['pending_ops'])} 项："
                            + "、".join(data["pending_ops"]))
    if not data["index_ok"]:
        result.notes.append("索引与当前事实不一致：" + "、".join(data["index_problems"]))
    result.next_actions.extend([
        f"{FLOW_CORE} acceptance closeout --prd {record.id} --actor {ACTOR_PLACEHOLDER}",
        f"{FLOW_CORE} recover list",
    ])
    return result


# --------------------------------------------------------------------------
# 辅助
# --------------------------------------------------------------------------
def _require_confirmed(workspace: Workspace, rules: Rules,
                       record: metadata.PrdRecord) -> dict[str, Any]:
    """前置：PRD 已 `ACCEPTED` 且存在 `acceptance.approved_at`；结论不得失效。"""
    accepted = record.meta.get("acceptance")
    approved_at = accepted.get("approved_at") if isinstance(accepted, dict) else None
    if record.status != "ACCEPTED" or not approved_at:
        raise FlowError(
            "ACCEPTANCE_NOT_CONFIRMED",
            f"{record.id} 尚未人工确认通过（当前状态 {record.status}），不能收尾",
            object=record.id,
            suggestion=CONFIRMED_HINT.format(prd=record.id),
            next_actions=[
                f"{FLOW_CORE} acceptance status --prd {record.id}",
                f"{FLOW_CORE} acceptance approve --prd {record.id} --actor {ACTOR_PLACEHOLDER} --confirm",
            ],
        )
    inputs = acceptance.closeout_inputs(workspace, rules, record)
    if "ACCEPTANCE_STALE" in inputs["problems"]:
        raise FlowError(
            "ACCEPTANCE_STALE",
            f"{record.id} 的验收结论已失效（{'、'.join(inputs['stale_reasons'])}），不能收尾",
            object=record.id,
            suggestion="PRD/代码/证据/清单变化后旧结论不得沿用：重新 prepare 并逐条重新记录",
            next_actions=[f"{FLOW_CORE} acceptance prepare --prd {record.id}"],
        )
    return inputs


def _is_finished(record: metadata.PrdRecord) -> bool:
    entry = record.meta.get("closeout")
    return (isinstance(entry, dict) and entry.get("state") == transactions.STATE_COMMITTED
            and bool(entry.get("tx")))


def _main_req_status(workspace: Workspace, rules: Rules, req_id: str | None) -> str:
    if not req_id:
        return ""
    try:
        return requirements.load(workspace, req_id, rules=rules).status
    except FlowError:
        return ""


def _tx_touches_modified_block(workspace: Workspace, rules: Rules,
                               tx: transactions.Transaction) -> bool:
    """待写入的目标里有受管理区块被人工改过（覆盖前需要 --confirm）。"""
    return any(closeout.has_modified_block(workspace, op.path) for op in tx.ops)


def _resolve_actor(workspace: Workspace, args: argparse.Namespace, prd_id: str) -> str:
    """收尾是机械同步，不要求人工二次确认；给了 `--actor` 就校验身份。"""
    actor = str(getattr(args, "actor", None) or "").strip()
    if not actor:
        return FLOW_CORE
    team = state.load_team(workspace)
    if team is not None and team.members:
        state.require_member(team, actor, object=prd_id)
    return actor


def _kind_of(path: str) -> str:
    if path.startswith("03-requirements"):
        return "req"
    if path.startswith("04-prds"):
        return "prd"
    if path.startswith("02-definition"):
        return "feature"
    if path.startswith(".neo-skill"):
        return "transaction"
    return "doc"


def _prd_arg(args: argparse.Namespace) -> str | None:
    return str(getattr(args, "prd", None) or "").strip() or None


def _require_prd(args: argparse.Namespace) -> str:
    value = _prd_arg(args)
    if not value:
        raise FlowError(
            "PRD_NOT_BOUND",
            "收尾命令必须显式绑定一个 PRD（缺少 --prd）",
            suggestion="用 --prd PRD-001 显式声明要收尾哪一份 PRD",
        )
    return value


_HANDLERS = {
    "closeout": _closeout,
    "closeout-status": _closeout_status,
}
