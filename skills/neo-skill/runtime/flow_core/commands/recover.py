"""`recover`：残留锁与中断事务的显式恢复（共享设计 §7、SRS-FR-088、HLD §8.1/§8.3）。

三个子命令：

- `recover list`：列出事务（id / object / state / created_at / 待完成目标）。
- `recover run [--tx TX-...]`：逐（或指定）事务重做——重新取对象锁、**重新比对基线**，
  不一致 → `BASELINE_CONFLICT` 且**不写任何文件**（禁止静默覆盖），并给出恢复建议。
- `recover release-lock --object OBJ`：只在 `locks.pid_alive()` 为假时删除残留锁；
  进程仍存活 → `LOCK_HELD`（不删除）。`doctor` 已按这个形状给建议文案。

`run`/`release-lock` 都是危险动作，必须 `--actor` + `--confirm`（`CONFIRMATION_REQUIRED`）。
"""

from __future__ import annotations

import argparse
import sys
from typing import Any

from .. import closeout, locks, metadata, state, transactions
from ..output import EXIT_USAGE, FlowError, Result
from ..rules import Rules
from ..workspace import Workspace

NAME = "recover"
HELP = "恢复中断的多文件事务、清理确认已结束进程的残留锁"

FLOW_CORE = "flow-core"
ACTOR_PLACEHOLDER = "<成员 id>"


# --------------------------------------------------------------------------
# 参数
# --------------------------------------------------------------------------
def add_arguments(parser: argparse.ArgumentParser) -> None:
    from ..cli import global_options_parser

    parents = [global_options_parser()]
    # `doctor` 与 `locks` 的建议文案是 `recover --release-lock <对象> --actor <id> --confirm`，
    # 因此这里也支持「顶层开关」写法，与 `recover release-lock --object <对象>` 等价。
    parser.add_argument("--release-lock", nargs="?", const="", default=None, metavar="OBJ",
                        help="清理指定对象的残留锁（等价于 recover release-lock --object OBJ）；"
                             "对象也可用 --object 给出")
    parser.add_argument("--object", dest="object", default=None, metavar="OBJ",
                        help="锁对象编号（配合 --release-lock 使用）")

    subparsers = parser.add_subparsers(dest="recover_command", metavar="<子命令>",
                                       required=False)

    listing = subparsers.add_parser("list", parents=parents,
                                    help="列出事务（未完成的排在最前）")
    listing.add_argument("--all", action="store_true", help="同时列出已完成的事务")

    run = subparsers.add_parser("run", parents=parents,
                                help="按基线重新执行未完成的事务（基线冲突则拒绝）")
    run.add_argument("--tx", default=None, help="只恢复指定事务，如 TX-20260301-0001")

    release = subparsers.add_parser("release-lock", parents=parents,
                                    help="清理残留锁（只在持有进程已结束时删除）")
    release.add_argument("--object", required=True, metavar="OBJ",
                         help="锁对象编号，如 REQ-001 / PRD-001")


def run(workspace: Workspace, rules: Rules, args: argparse.Namespace) -> Result:
    sub = getattr(args, "recover_command", None)
    flag = getattr(args, "release_lock", None)
    if flag is not None:
        # 兼容 `recover --release-lock [<对象>] [--object <对象>]` 写法
        # （doctor 与 LOCK_CONFLICT 的建议文案就是这个形状）
        args.object = str(flag).strip() or str(getattr(args, "object", None) or "").strip()
        if not args.object:
            print("ERROR: --release-lock 需要给出对象编号（--release-lock PRD-001 或 "
                  "--release-lock --object PRD-001）", file=sys.stderr)
            raise SystemExit(EXIT_USAGE)
        sub = "release-lock"
    if sub is None:
        # 与 argparse「缺少必需子命令」一致：用法错误 → 退出码 2
        print("ERROR: 缺少 recover 子命令；可用：" + "、".join(sorted(_HANDLERS)),
              file=sys.stderr)
        raise SystemExit(EXIT_USAGE)
    handler = _HANDLERS.get(sub)
    try:
        return handler(workspace, rules, args)
    except FlowError as exc:
        if not exc.command:
            exc.command = f"recover.{sub}"
        raise


# --------------------------------------------------------------------------
# recover list
# --------------------------------------------------------------------------
def _list(workspace: Workspace, rules: Rules, args: argparse.Namespace) -> Result:
    include_all = bool(getattr(args, "all", False))
    found = transactions.list_transactions(workspace)
    entries: list[dict[str, Any]] = []
    for tx in found:
        pending = transactions.pending_ops(workspace, tx)
        entries.append({
            "id": tx.id,
            "object": tx.object,
            "state": tx.state,
            "created_at": tx.created_at,
            "updated_at": tx.updated_at,
            "command": tx.command,
            "actor": tx.actor,
            "error": tx.error,
            "pending": pending,
            "ops": tx.paths(),
            "finished": tx.state == transactions.STATE_COMMITTED,
        })
    entries.sort(key=lambda item: (item["finished"], item["created_at"], item["id"]))
    if not include_all:
        entries = [item for item in entries if not item["finished"]]

    result = Result(command="recover.list")
    result.data["transactions"] = entries
    result.data["unfinished"] = sum(1 for item in entries if not item["finished"])
    if entries:
        for item in entries:
            result.notes.append(
                f"{item['id']} {item['object']} {item['state']}"
                f"（{item['created_at']}，待完成 {len(item['pending'])} 项）")
        if result.data["unfinished"]:
            result.next_actions.append(
                f"{FLOW_CORE} recover run --tx {entries[0]['id']} --actor {ACTOR_PLACEHOLDER} --confirm")
    else:
        result.notes.append("没有未完成的事务")
    return result


# --------------------------------------------------------------------------
# recover run
# --------------------------------------------------------------------------
def _run(workspace: Workspace, rules: Rules, args: argparse.Namespace) -> Result:
    actor = state.require_confirmation(
        str(getattr(args, "actor", None) or "").strip() or None,
        bool(getattr(args, "confirm", False)), command="recover.run")
    team = state.load_team(workspace)
    if team is not None and team.members:
        state.require_member(team, actor)

    tx_id = str(getattr(args, "tx", None) or "").strip() or None
    if tx_id:
        targets = [transactions.load(workspace, tx_id)]
    else:
        targets = transactions.unfinished(workspace)
        if not targets:
            result = Result(command="recover.run")
            result.data["recovered"] = []
            result.notes.append("没有未完成的事务")
            return result

    result = Result(command="recover.run")
    recovered: list[dict[str, Any]] = []
    conflicts: list[dict[str, Any]] = []
    for tx in targets:
        objects = [tx.object] if tx.object else []
        if tx.state == transactions.STATE_COMMITTED:
            recovered.append({"id": tx.id, "object": tx.object, "state": tx.state,
                              "skipped": "already-committed"})
            continue
        try:
            with locks.object_locks(workspace, objects, command="recover.run",
                                    rules_version=rules.version):
                transactions.recover_one(workspace, tx)
        except FlowError as exc:
            if exc.code != "BASELINE_CONFLICT":
                raise
            conflicts.append({"id": tx.id, "object": tx.object, "conflicts": exc.message,
                              "suggestion": exc.suggestion})
            result.fail(
                "BASELINE_CONFLICT",
                f"事务 {tx.id} 的恢复被拒绝：{exc.message}",
                object=tx.object,
                suggestion=exc.suggestion,
            )
            continue
        entry = {"id": tx.id, "object": tx.object, "state": tx.state, "pending": []}
        closeout_entry = _refresh_closeout(workspace, rules, tx, actor)
        if closeout_entry is not None:
            entry["closeout"] = closeout_entry
            entry["main_req_status"] = closeout_entry.get("main_req_status")
        recovered.append(entry)

    result.data["recovered"] = recovered
    result.data["conflicts"] = conflicts
    for item in recovered:
        result.notes.append(f"{item['id']} {item['object']} → COMMITTED")
        if item.get("closeout"):
            result.data["closeout"] = item["closeout"]
            result.data["main_req_status"] = item.get("main_req_status")
    if conflicts:
        result.next_actions.extend([
            f"{FLOW_CORE} recover list",
            f"{FLOW_CORE} acceptance closeout --prd {conflicts[0]['object']} --actor {actor}",
        ])
    elif recovered:
        result.next_actions.append(f"{FLOW_CORE} recover list --all")
    return result


def _refresh_closeout(workspace: Workspace, rules: Rules, tx: transactions.Transaction,
                      actor: str) -> dict[str, Any] | None:
    """事务恢复成功后回写 PRD 的 `closeout`（PRD 仍是 ACCEPTED）。"""
    if not tx.object.startswith("PRD-"):
        return None
    try:
        record = metadata.load(workspace, tx.object, rules=rules)
    except FlowError:
        return None
    if not record.source_reqs:
        return None
    from .. import fsutil, requirements

    status = ""
    try:
        status = requirements.load(workspace, record.source_reqs[0], rules=rules).status
    except FlowError:
        status = ""
    entry = {
        "state": transactions.STATE_COMMITTED,
        "tx": tx.id,
        "at": fsutil.now_iso(),
        "by": actor,
        "main_req": record.source_reqs[0],
        "ops": [op.path for op in tx.ops],
    }
    record.meta["closeout"] = entry
    accepted = record.meta.get("acceptance")
    if isinstance(accepted, dict):
        accepted["closeout"] = entry
        record.meta["acceptance"] = accepted
    metadata.save(workspace, record)
    return {**entry, "main_req_status": status}


# --------------------------------------------------------------------------
# recover release-lock
# --------------------------------------------------------------------------
def _release_lock(workspace: Workspace, rules: Rules, args: argparse.Namespace) -> Result:
    object_id = str(getattr(args, "object", None) or "").strip()
    actor = state.require_confirmation(
        str(getattr(args, "actor", None) or "").strip() or None,
        bool(getattr(args, "confirm", False)), command="recover.release-lock")
    team = state.load_team(workspace)
    if team is not None and team.members:
        state.require_member(team, actor, object=object_id or None)

    info = locks.read_lock(workspace, object_id)
    if info is None:
        raise FlowError(
            "LOCK_NOT_FOUND",
            f"对象 {object_id} 没有锁文件（可能已经释放）",
            object=object_id,
            suggestion=f"用 {FLOW_CORE} doctor 查看当前残留锁",
            next_actions=[f"{FLOW_CORE} doctor"],
        )
    if locks.pid_alive(info.pid):
        raise FlowError(
            "LOCK_HELD",
            f"对象 {object_id} 的锁仍被进程 {info.pid} 持有（创建于 {info.created_at}，"
            f"命令 {info.command}），不能删除",
            object=object_id,
            suggestion="等该流程结束。若确认是僵死进程，先结束它再执行本命令；"
                       "系统不会抢占仍在运行的锁（FR-088）",
            next_actions=[f"{FLOW_CORE} doctor"],
        )
    locks.force_release(workspace, object_id)
    result = Result(command="recover.release-lock")
    result.data["object"] = object_id
    result.data["released"] = True
    result.data["pid"] = info.pid
    result.data["by"] = actor
    result.notes.append(f"已清理 {object_id} 的残留锁（pid={info.pid} 已结束）")
    result.next_actions.append(f"{FLOW_CORE} doctor")
    return result


_HANDLERS = {
    "list": _list,
    "run": _run,
    "release-lock": _release_lock,
}
