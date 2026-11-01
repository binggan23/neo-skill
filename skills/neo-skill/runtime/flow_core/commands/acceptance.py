"""`acceptance`：Demo 材料、逐条 AC 人工结论与验收判定（ticket 07）。

命令边界（SRS-FR-070～074，BR-013/014/016）：

- `acceptance prepare`：`READY_FOR_ACCEPTANCE` 下生成 `07-acceptance/<module>/PRD-NNN-<slug>/`
  的 `demo.md`、`checklist.md`（受管理渲染）与 `checklist.yaml`（机器读事实来源）。
- `acceptance record`：**人工**逐条记录结论（`pass|fail|blocked`）+ 证据，必须申报团队成员身份。
  AI 不得调用它代替人确认 —— 命令只校验身份存在，不做职责假设（验收人可以是任何有职责的人）。
- `acceptance suggest`：**AI 建议**，只写 `checklist.yaml` 的 `suggestion` 字段，绝不写 `conclusion`。
- `acceptance approve`：全部 AC 为 `pass` 且职责为需求负责人 + 显式确认 → `READY_FOR_ACCEPTANCE →
  ACCEPTED`（**不**把主需求置 DONE，收尾是 ticket 08 的事）。
- `acceptance reject`：不通过 → `DEVELOPING`（记录理由与遗留问题）。
- `acceptance conditional`：有条件通过 → 状态保持 `READY_FOR_ACCEPTANCE`，记录遗留问题并
  拒绝进入 `ACCEPTED`（下次必须 `record` 补齐结论或 `reject` 退回）。
- `acceptance status`：只读投影，判定逻辑在 `flow_core/acceptance.py`（ticket 08/09 复用）。

状态值与门禁全部来自 `rules/flow.yaml`；本模块只负责参数、锁、状态跳转与结果信封。
"""

from __future__ import annotations

import argparse
from typing import Any

from .. import acceptance, fsutil, locks, metadata, state, verification
from ..output import FlowError, Result
from ..rules import Rules
from ..workspace import Workspace

NAME = "acceptance"
HELP = "人工验收：Demo 材料、逐条 AC 结论、建议与确认分离、验收报告（收尾同步见后续命令）"

FLOW_CORE = "flow-core"
ACTOR_PLACEHOLDER = "<验收人>"
DECISION_ROLE = "requirement_owner"
APPROVE_GATE = "acceptance.approve"
REJECT_GATE = "acceptance.reject"


# --------------------------------------------------------------------------
# 参数
# --------------------------------------------------------------------------
def add_arguments(parser: argparse.ArgumentParser) -> None:
    from ..cli import global_options_parser

    parents = [global_options_parser()]
    subparsers = parser.add_subparsers(dest="acceptance_command", metavar="<子命令>",
                                       required=True)

    prepare = subparsers.add_parser("prepare", parents=parents,
                                    help="生成 Demo 材料与逐条 AC checklist（只读 PRD/验证证据）")
    prepare.add_argument("--prd", default=None, help="PRD 编号（必须显式绑定）")

    record = subparsers.add_parser("record", parents=parents,
                                   help="人工逐条记录 AC 结论与证据（pass/fail/blocked）")
    record.add_argument("--prd", default=None, help="PRD 编号（必须显式绑定）")
    record.add_argument("--ac", required=True, help="AC 编号，如 AC-RES-01-01")
    record.add_argument("--result", required=True, choices=list(acceptance.CONCLUSIONS),
                        help="人工结论：pass / fail / blocked")
    record.add_argument("--evidence", default=None,
                        help="人工或技术证据（截图路径、原始输出、现场记录等）")
    record.add_argument("--note", default=None, help="备注（可空）")

    suggest = subparsers.add_parser("suggest", parents=parents,
                                    help="AI 提出建议结论（只写 suggestion，不写人工结论）")
    suggest.add_argument("--prd", default=None, help="PRD 编号（必须显式绑定）")
    suggest.add_argument("--ac", required=True, help="AC 编号")
    suggest.add_argument("--result", required=True, choices=list(acceptance.CONCLUSIONS),
                         help="建议结论：pass / fail / blocked")
    suggest.add_argument("--reason", default=None, help="建议依据（引用证据，不臆造）")

    approve = subparsers.add_parser("approve", parents=parents,
                                    help="全部 AC 通过后确认验收通过（READY_FOR_ACCEPTANCE → ACCEPTED）")
    approve.add_argument("--prd", default=None, help="PRD 编号（必须显式绑定）")
    approve.add_argument("--note", default=None, help="验收备注")
    approve.add_argument("--leftover", action="append", default=None, metavar="L",
                         help="遗留问题（可重复；写入验收报告与 metadata）")

    reject = subparsers.add_parser("reject", parents=parents,
                                   help="验收不通过（READY_FOR_ACCEPTANCE → DEVELOPING）")
    reject.add_argument("--prd", default=None, help="PRD 编号（必须显式绑定）")
    reject.add_argument("--reason", default=None, help="不通过的原因（必填）")
    reject.add_argument("--leftover", action="append", default=None, metavar="L",
                        help="遗留问题（可重复）")

    conditional = subparsers.add_parser(
        "conditional", parents=parents,
        help="有条件通过（不进入 ACCEPTED，记录遗留问题并给出退回/重验路径）")
    conditional.add_argument("--prd", default=None, help="PRD 编号（必须显式绑定）")
    conditional.add_argument("--reason", default=None, help="有条件通过的理由（必填）")
    conditional.add_argument("--leftover", action="append", default=None, metavar="L",
                             help="遗留问题（至少一条）")

    status = subparsers.add_parser("status", parents=parents,
                                   help="报告逐条 AC 结论、是否全部通过、验收结论是否失效")
    status.add_argument("--prd", default=None, help="PRD 编号")


def run(workspace: Workspace, rules: Rules, args: argparse.Namespace) -> Result:
    sub = getattr(args, "acceptance_command", None)
    handler = _HANDLERS.get(sub)
    if handler is None:
        raise FlowError(
            "USAGE",
            "缺少 acceptance 子命令",
            suggestion="可用子命令：" + "、".join(sorted(_HANDLERS)),
        )
    try:
        return handler(workspace, rules, args)
    except FlowError as exc:
        if not exc.command:
            exc.command = f"acceptance.{sub}"
        raise


# --------------------------------------------------------------------------
# acceptance prepare
# --------------------------------------------------------------------------
def _prepare(workspace: Workspace, rules: Rules, args: argparse.Namespace) -> Result:
    command = "acceptance.prepare"
    record = metadata.load(workspace, _require_prd(args), rules=rules)
    acceptance.require_state(record, acceptance.ACCEPTANCE_STATES, command=command)

    vstatus = verification.evaluate(workspace, rules, record)
    entries = verification.checklist(workspace, rules, record, vstatus)
    if not entries:
        raise FlowError(
            "AC_NOT_FOUND",
            f"{record.id} 正文没有可验收的 AC，无法生成 checklist",
            object=record.id,
            suggestion="先在 PRD 的场景下补编号 AC（`AC-<模块>-<场景>-<序号>`），再重新提交评审",
        )
    submitted_at = (vstatus.submission or {}).get("at")
    data = acceptance.load_checklist(workspace, record) or acceptance.blank_checklist(record)
    prepared_at = data.get("prepared_at") or fsutil.now_iso()
    current = {
        "prd_hash": record.compute_content_hash(),
        "code_version": vstatus.code_version,
        "verification_digest": vstatus.verification_digest,
    }
    action = "created" if not data.get("items") else "updated"
    changed = acceptance.baseline_mismatch(data, **current) if data.get("items") else []
    if changed:
        # 基线变了：旧的人工结论不得沿用（BR-016），移到 superseded 保留审计
        acceptance.archive_conclusions(data)

    data.update({
        "prd": record.id,
        "module": record.module,
        "prd_dir": record.rel,
        "prepared_at": prepared_at,
        "updated_at": fsutil.now_iso(),
        "status": record.status,
        "submission_at": submitted_at,
        # 提交基线已过期时清单照旧生成（供人核对），但 approve 仍会拦下
        "submission_stale": bool(vstatus.submission_outdated or vstatus.stale),
        "submission_stale_reasons": list(vstatus.stale_reasons),
        **current,
    })
    acceptance.sync_items(data, record, entries)

    result = Result(command=command)
    with locks.object_lock(workspace, record.id, command=command, rules_version=rules.version):
        _write_acceptance(workspace, record, data, action, result)

    result.data["prd"] = record.to_dict()
    result.data["checklist"] = _checklist_summary(workspace, rules, record)
    if changed:
        result.notes.append(
            f"验收基线已变化（{'、'.join(changed)}），旧的人工结论已归档并重置为 pending，"
            "必须逐条重新记录")
    result.notes.append(
        f"{record.id} 已生成 Demo 材料与 {len(entries)} 条 AC 的验收清单"
        f"（PRD 基线 {data['prd_hash']}，代码版本 {data['code_version']}）")
    if data["submission_stale"]:
        result.notes.append(
            "注意：提交验收基线已失效（"
            f"{'、'.join(vstatus.stale_reasons) or '提交基线过期'}）；"
            "旧证据不得沿用，验收通过会被拦下，需重新验证并再次提交验收（BR-016）")
    result.notes.append(
        "AI 只能写建议（acceptance suggest）；结论必须由人工 "
        f"{FLOW_CORE} acceptance record --actor <成员> 逐条记录")
    result.next_actions.append(
        f"{FLOW_CORE} acceptance record --prd {record.id} --ac {entries[0]['ac']} "
        f"--result pass --evidence \"<人工核对到的证据>\" --actor {ACTOR_PLACEHOLDER}")
    result.next_actions.append(
        f"{FLOW_CORE} acceptance status --prd {record.id}")
    return result


# --------------------------------------------------------------------------
# acceptance record（人工结论）
# --------------------------------------------------------------------------
def _record(workspace: Workspace, rules: Rules, args: argparse.Namespace) -> Result:
    command = "acceptance.record"
    record = metadata.load(workspace, _require_prd(args), rules=rules)
    acceptance.require_state(record, acceptance.RECORD_STATES, command=command)
    data = acceptance.require_prepared(workspace, record)

    gate = _gate_rule(rules, APPROVE_GATE)
    team = state.require_team(workspace, object=record.id)
    actor = _actor(args)
    if not actor:
        raise FlowError(
            "CONFIRMATION_REQUIRED",
            "记录验收结论必须申报身份（缺少 --actor）",
            object=record.id,
            suggestion="补 --actor <团队成员 id>；AI 不得代替人记录验收结论（BR-013）",
        )
    # 验收人不必是需求负责人：任何已登记成员都可以记录，只要留下身份
    state.require_member(team, actor, object=record.id)

    ac = _require_ac(args)
    result_value = str(getattr(args, "result", "") or "").strip()
    if result_value not in acceptance.CONCLUSIONS:
        raise FlowError("INVALID_RESULT", f"不支持的验收结论 {result_value}",
                        suggestion="只允许 pass / fail / blocked")
    evidence = _clean(getattr(args, "evidence", None))
    note = _clean(getattr(args, "note", None))
    if result_value in (acceptance.CONCLUSION_FAIL, acceptance.CONCLUSION_BLOCKED) and not evidence:
        raise FlowError(
            "EVIDENCE_REQUIRED",
            f"结论为 {result_value} 时必须给出证据或现场记录（--evidence）",
            object=ac,
            suggestion="写清观察到的现象或阻塞原因；只写结论无法复核",
        )

    item = acceptance.find_item(data, ac)
    conflict = bool(item.get("suggestion") and item["suggestion"] != result_value)
    item.update({
        "conclusion": result_value,
        "evidence": evidence,
        "note": note,
        "recorded_by": actor,
        "recorded_at": fsutil.now_iso(),
        "suggestion_conflict": conflict,
        "stale": False,
    })
    data["updated_at"] = fsutil.now_iso()
    if result_value != acceptance.CONCLUSION_PASS:
        data["conditional"] = False

    result = Result(command=command)
    with locks.object_lock(workspace, record.id, command=command, rules_version=rules.version):
        _write_acceptance(workspace, record, data, "updated", result)

    summary = _checklist_summary(workspace, rules, record)
    result.data["prd"] = record.to_dict()
    result.data["item"] = dict(item)
    result.data["checklist"] = summary
    result.data["all_pass"] = summary["all_pass"]
    result.data["pending"] = summary["pending"]
    result.notes.append(
        f"{record.id} {ac} 人工结论记为 {result_value}（{actor}）"
        f"；仍未通过：{'、'.join(summary['pending']) or '无'}")
    if conflict:
        result.notes.append(
            f"注意：AI 曾建议 {item['suggestion']}，与本次人工结论 {result_value} 不一致，"
            "请人工确认哪个正确（建议不覆盖人工结论）")
    if summary["all_pass"]:
        result.next_actions.append(
            f"{FLOW_CORE} acceptance approve --prd {record.id} --actor "
            f"{_suggest_actor(workspace, rules)} --confirm")
    else:
        result.next_actions.append(
            f"{FLOW_CORE} acceptance status --prd {record.id}")
    return result


# --------------------------------------------------------------------------
# acceptance suggest（AI 建议）
# --------------------------------------------------------------------------
def _suggest(workspace: Workspace, rules: Rules, args: argparse.Namespace) -> Result:
    command = "acceptance.suggest"
    record = metadata.load(workspace, _require_prd(args), rules=rules)
    acceptance.require_state(record, acceptance.SUGGEST_STATES, command=command)
    data = acceptance.require_prepared(workspace, record)
    actor = _actor(args)

    ac = _require_ac(args)
    result_value = str(getattr(args, "result", "") or "").strip()
    if result_value not in acceptance.CONCLUSIONS:
        raise FlowError("INVALID_RESULT", f"不支持的建议结论 {result_value}",
                        suggestion="只允许 pass / fail / blocked")

    item = acceptance.find_item(data, ac)
    conflict = item.get("conclusion") in acceptance.CONCLUSIONS \
        and item.get("conclusion") != result_value
    item.update({
        "suggestion": result_value,
        "suggestion_reason": _clean(getattr(args, "reason", None)),
        "suggestion_conflict": conflict,
        "suggested_by": actor or FLOW_CORE,
        "suggested_at": fsutil.now_iso(),
    })
    data["updated_at"] = fsutil.now_iso()

    result = Result(command=command)
    with locks.object_lock(workspace, record.id, command=command, rules_version=rules.version):
        _write_acceptance(workspace, record, data, "updated", result)

    result.data["prd"] = record.to_dict()
    result.data["ac"] = item.get("ac")
    result.data["suggestion"] = result_value
    result.data["conclusion"] = item.get("conclusion")
    result.data["suggestion_conflict"] = conflict
    result.notes.append(
        f"{record.id} {ac} 已记录 AI 建议 {result_value}"
        f"；人工结论仍为 {item.get('conclusion')}（建议不改变结论）")
    if conflict:
        result.notes.append(
            f"建议 {result_value} 与人工结论 {item.get('conclusion')} 冲突："
            "以人工结论为准，请人工复核")
    result.next_actions.append(
        f"{FLOW_CORE} acceptance record --prd {record.id} --ac {ac} "
        f"--result <pass|fail|blocked> --evidence \"<证据>\" --actor {ACTOR_PLACEHOLDER}")
    return result


# --------------------------------------------------------------------------
# acceptance approve
# --------------------------------------------------------------------------
def _approve(workspace: Workspace, rules: Rules, args: argparse.Namespace) -> Result:
    command = "acceptance.approve"
    record = metadata.load(workspace, _require_prd(args), rules=rules)
    actor = state.require_confirmation(_actor(args), _confirm(args), command=command)
    acceptance.require_state(record, acceptance.RECORD_STATES, command=command)
    data = acceptance.require_prepared(workspace, record)

    gate = _gate_rule(rules, APPROVE_GATE)
    team = state.require_team(workspace, object=record.id)
    state.require_member(team, actor, role=str(gate.get("role") or DECISION_ROLE),
                         object=record.id)
    target = str(gate.get("to") or "ACCEPTED")
    state.check_transition(rules, "prd", record.status, target, object=record.id)

    vstatus = verification.evaluate(workspace, rules, record)
    if vstatus.stale or vstatus.submission_outdated:
        raise FlowError(
            "BASELINE_STALE",
            f"{record.id} 的验证/提交基线已失效"
            f"（{'、'.join(vstatus.stale_reasons) or '提交基线过期'}），不能验收通过",
            object=record.id,
            suggestion="PRD、代码或验证证据变化后旧结论不得沿用：重新验证并再次提交验收（BR-016）",
            next_actions=[f"{FLOW_CORE} dev verification-status --prd {record.id}"],
        )
    changed = acceptance.baseline_mismatch(
        data, prd_hash=record.compute_content_hash(), code_version=vstatus.code_version,
        verification_digest=vstatus.verification_digest)
    if changed:
        raise FlowError(
            "BASELINE_STALE",
            f"{record.id} 的验收清单基线已变化（{'、'.join(changed)}），旧结论不得沿用",
            object=record.id,
            suggestion="重新生成清单并逐条重新记录结论",
            next_actions=[f"{FLOW_CORE} acceptance prepare --prd {record.id}"],
        )

    pending = [item for item in data.get("items") or []
               if item.get("conclusion") != acceptance.CONCLUSION_PASS]
    if data.get("conditional"):
        # 有条件通过：遗留问题未处理前不得进入 ACCEPTED（SRS-FR-073）
        return _not_all_passed(command, workspace, rules, record, data, pending)
    if pending:
        return _not_all_passed(command, workspace, rules, record, data, pending)

    leftovers = _clean_list(getattr(args, "leftover", None))
    note = _clean(getattr(args, "note", None))
    if leftovers:
        data["leftovers"] = leftovers
    decision = {
        "decision": acceptance.DECISION_APPROVED,
        "by": actor,
        "role": str(gate.get("role") or DECISION_ROLE),
        "at": fsutil.now_iso(),
        "note": note,
        "reason": None,
    }
    data["status"] = target
    data["conditional"] = False
    _append_decision(data, decision)
    acceptance_record = _acceptance_metadata(workspace, record, data, decision, vstatus)

    result = Result(command=command)
    with locks.object_lock(workspace, record.id, command=command, rules_version=rules.version):
        _write_acceptance(workspace, record, data, "updated", result)
        _write_report(workspace, record, data, decision, result)
        state.apply_transition(record.meta, machine="prd", to_state=target, actor=actor,
                               action=command, rules=rules, role=str(decision["role"]),
                               content_hash=acceptance_record["prd_hash"],
                               version=record.version)
        record.meta["acceptance"] = acceptance_record
        record.meta["closeout"] = record.meta.get("closeout")
        metadata.save(workspace, record)

    result.add(record.rel + "/" + metadata.METADATA_FILE, "prd", "updated")
    result.data["prd"] = record.to_dict()
    result.data["acceptance"] = acceptance_record
    result.data["accepted"] = True
    result.data["conditional"] = False
    result.data["leftovers"] = list(data.get("leftovers") or [])
    result.data["closeout"] = record.meta.get("closeout")
    result.data["next_step"] = "closeout"
    result.data["checklist"] = _checklist_summary(workspace, rules, record)
    result.notes.append(
        f"{record.id} 验收通过（{actor}），状态 READY_FOR_ACCEPTANCE → {target}")
    result.notes.append(
        "验收通过不等于需求完成：主需求仍未 DONE，"
        "还需收尾同步（功能现状、主需求、场景、索引）")
    result.next_actions.extend([
        "下一步：收尾同步（ticket 08 的 acceptance closeout）—— 同步功能现状、主需求、场景与索引；"
        "收尾未完成前主需求尚未 DONE",
        f"{FLOW_CORE} acceptance status --prd {record.id}",
    ])
    return result


def _not_all_passed(command: str, workspace: Workspace, rules: Rules,
                    record: metadata.PrdRecord, data: dict[str, Any],
                    pending: list[dict[str, Any]]) -> Result:
    """还有 AC 不是 pass：不得进入 ACCEPTED，并给出退回/继续记录两条路径。"""
    result = Result(command=command)
    detail = "、".join(
        f"{item.get('ac')}（{item.get('conclusion') or acceptance.CONCLUSION_PENDING}）"
        for item in pending)
    blocked = [item for item in pending
               if item.get("conclusion") == acceptance.CONCLUSION_BLOCKED]
    failed = [item for item in pending
              if item.get("conclusion") == acceptance.CONCLUSION_FAIL]
    result.fail(
        "AC_NOT_ALL_PASSED",
        f"{record.id} 还有 {len(pending)} 条 AC 未通过：{detail}；只有全部通过才允许验收通过",
        object=record.id,
        suggestion="逐条补齐结论与证据；有 fail/blocked 时需要退回开发或标记有条件通过，"
                   "不得直接验收通过（BR-013）",
    )
    for item in pending:
        result.next_actions.append(
            f"{FLOW_CORE} acceptance record --prd {record.id} --ac {item.get('ac')} "
            f"--result <pass|fail|blocked> --evidence \"<证据>\" --actor {ACTOR_PLACEHOLDER}")
    if failed or blocked:
        result.next_actions.append(
            f"{FLOW_CORE} acceptance reject --prd {record.id} --actor {ACTOR_PLACEHOLDER} "
            "--confirm --reason \"<不通过原因>\" --leftover \"<遗留问题>\"")
        result.next_actions.append(
            f"{FLOW_CORE} acceptance conditional --prd {record.id} --actor "
            f"{ACTOR_PLACEHOLDER} --confirm --reason \"<理由>\" --leftover \"<遗留问题>\"")
    if data.get("conditional"):
        result.fail(
            "ACCEPTANCE_CONDITIONAL",
            f"{record.id} 本次验收为「有条件通过」，遗留问题未处理前不能进入 ACCEPTED",
            object=record.id,
            suggestion="处理遗留问题后重新记录结论并再次请求确认；或退回开发",
        )
        result.next_actions.append(
            f"{FLOW_CORE} acceptance prepare --prd {record.id}")
    result.data["prd"] = record.to_dict()
    result.data["pending"] = [
        {"ac": item.get("ac"), "conclusion": item.get("conclusion"),
         "evidence": item.get("evidence"), "suggestion": item.get("suggestion")}
        for item in pending
    ]
    result.data["conditional"] = bool(data.get("conditional"))
    result.data["leftovers"] = list(data.get("leftovers") or [])
    result.data["all_pass"] = False
    result.notes.append("本次未写入任何验收结论，PRD 状态保持不变")
    return result


# --------------------------------------------------------------------------
# acceptance reject
# --------------------------------------------------------------------------
def _reject(workspace: Workspace, rules: Rules, args: argparse.Namespace) -> Result:
    command = "acceptance.reject"
    record = metadata.load(workspace, _require_prd(args), rules=rules)
    actor = state.require_confirmation(_actor(args), _confirm(args), command=command)
    acceptance.require_state(record, acceptance.RECORD_STATES, command=command)
    data = acceptance.require_prepared(workspace, record)

    gate = _gate_rule(rules, REJECT_GATE)
    team = state.require_team(workspace, object=record.id)
    state.require_member(team, actor, role=str(gate.get("role") or DECISION_ROLE),
                         object=record.id)
    target = str(gate.get("to") or "DEVELOPING")
    state.check_transition(rules, "prd", record.status, target, object=record.id)

    reason = _clean(getattr(args, "reason", None))
    if not reason:
        raise FlowError(
            "REASON_REQUIRED",
            "验收不通过必须写明原因（--reason）",
            object=record.id,
            suggestion="记录哪条 AC 未通过、观察到什么现象，便于开发整改",
        )
    leftovers = _clean_list(getattr(args, "leftover", None))
    data["leftovers"] = leftovers or data.get("leftovers") or []
    data["conditional"] = False
    data["status"] = target
    decision = {
        "decision": acceptance.DECISION_REJECTED,
        "by": actor,
        "role": str(gate.get("role") or DECISION_ROLE),
        "at": fsutil.now_iso(),
        "reason": reason,
        "note": None,
    }
    _append_decision(data, decision)

    result = Result(command=command)
    with locks.object_lock(workspace, record.id, command=command, rules_version=rules.version):
        _write_acceptance(workspace, record, data, "updated", result)
        _write_report(workspace, record, data, decision, result)
        state.apply_transition(record.meta, machine="prd", to_state=target, actor=actor,
                               action=command, rules=rules, role=str(decision["role"]),
                               content_hash=record.compute_content_hash(),
                               version=record.version)
        record.meta["acceptance"] = None
        metadata.save(workspace, record)

    result.add(record.rel + "/" + metadata.METADATA_FILE, "prd", "updated")
    result.data["prd"] = record.to_dict()
    result.data["decision"] = acceptance.DECISION_REJECTED
    result.data["reason"] = reason
    result.data["leftovers"] = list(data.get("leftovers") or [])
    result.data["accepted"] = False
    result.notes.append(f"{record.id} 验收不通过，已退回 {target}（{actor}）")
    result.next_actions.extend([
        f"{FLOW_CORE} dev verify --prd {record.id} --suite unit --command \"<项目验证命令>\"",
        f"{FLOW_CORE} dev submit-acceptance --prd {record.id} --actor {ACTOR_PLACEHOLDER}",
    ])
    return result


# --------------------------------------------------------------------------
# acceptance conditional
# --------------------------------------------------------------------------
def _conditional(workspace: Workspace, rules: Rules, args: argparse.Namespace) -> Result:
    command = "acceptance.conditional"
    record = metadata.load(workspace, _require_prd(args), rules=rules)
    actor = state.require_confirmation(_actor(args), _confirm(args), command=command)
    acceptance.require_state(record, acceptance.RECORD_STATES, command=command)
    data = acceptance.require_prepared(workspace, record)

    gate = _gate_rule(rules, APPROVE_GATE)
    team = state.require_team(workspace, object=record.id)
    state.require_member(team, actor, role=str(gate.get("role") or DECISION_ROLE),
                         object=record.id)

    reason = _clean(getattr(args, "reason", None))
    if not reason:
        raise FlowError(
            "REASON_REQUIRED",
            "有条件通过必须写明理由（--reason）",
            object=record.id,
            suggestion="说明哪些部分满足、哪些需要后续处理",
        )
    leftovers = _clean_list(getattr(args, "leftover", None))
    if not leftovers:
        raise FlowError(
            "LEFTOVER_REQUIRED",
            "有条件通过必须至少登记一条遗留问题（--leftover）",
            object=record.id,
            suggestion="遗留问题会写入验收报告并要求后续处理；无遗留问题请走 "
                       f"{FLOW_CORE} acceptance approve",
        )

    data["conditional"] = True
    data["leftovers"] = leftovers
    data["status"] = record.status  # 保持 READY_FOR_ACCEPTANCE：不进 ACCEPTED
    decision = {
        "decision": acceptance.DECISION_CONDITIONAL,
        "by": actor,
        "role": str(gate.get("role") or DECISION_ROLE),
        "at": fsutil.now_iso(),
        "reason": reason,
        "note": None,
    }
    _append_decision(data, decision)

    result = Result(command=command)
    with locks.object_lock(workspace, record.id, command=command, rules_version=rules.version):
        _write_acceptance(workspace, record, data, "updated", result)
        _write_report(workspace, record, data, decision, result)
        record.meta["acceptance"] = None
        record.meta["closeout"] = record.meta.get("closeout")
        metadata.save(workspace, record)

    result.add(record.rel + "/" + metadata.METADATA_FILE, "prd", "updated")
    result.data["prd"] = record.to_dict()
    result.data["decision"] = acceptance.DECISION_CONDITIONAL
    result.data["conditional"] = True
    result.data["accepted"] = False
    result.data["reason"] = reason
    result.data["leftovers"] = leftovers
    result.data["next_step"] = "conditional"
    result.notes.append(
        f"{record.id} 记为「有条件通过」：状态保持 {record.status}，不进入 ACCEPTED")
    result.notes.append("遗留问题必须先处理；处理完再记录结论并重新请求确认，或退回开发")
    result.next_actions.extend([
        f"{FLOW_CORE} acceptance record --prd {record.id} --ac <AC 编号> "
        f"--result <pass|fail|blocked> --evidence \"<证据>\" --actor {ACTOR_PLACEHOLDER}",
        f"{FLOW_CORE} acceptance reject --prd {record.id} --actor {ACTOR_PLACEHOLDER} "
        f"--confirm --reason \"<不通过原因>\" --leftover \"<遗留问题>\"",
        f"{FLOW_CORE} acceptance status --prd {record.id}",
    ])
    return result


# --------------------------------------------------------------------------
# acceptance status（只读）
# --------------------------------------------------------------------------
def _status(workspace: Workspace, rules: Rules, args: argparse.Namespace) -> Result:
    record = metadata.load(workspace, _require_prd(args), rules=rules)
    status = acceptance.evaluate(workspace, rules, record)

    result = Result(command="acceptance.status")
    result.data.update(status.to_dict())
    result.data["prd"] = record.to_dict()
    result.data["dir"] = acceptance.acceptance_dir(workspace, record)

    pending = status.pending
    result.notes.append(
        f"{record.id}（{record.status}）：{len(status.items) - len(pending)}/{len(status.items)} "
        f"条 AC 通过" + ("，全部通过" if status.all_pass else "")
        + ("；本次为有条件通过" if status.conditional else ""))
    if status.stale:
        result.fail(
            "ACCEPTANCE_STALE",
            f"{record.id} 的验收结论已失效（{'、'.join(status.stale_reasons)}）",
            object=record.id,
            suggestion="PRD、代码、验证证据或验收清单变化后旧结论不得沿用：重新 prepare 并逐条重新记录",
            next_action=f"{FLOW_CORE} acceptance prepare --prd {record.id}",
        )
    elif not status.prepared:
        result.notes.append("尚未生成验收清单")
        if record.status in acceptance.ACCEPTANCE_STATES:
            result.next_actions.append(
                f"{FLOW_CORE} acceptance prepare --prd {record.id}")
    elif status.conditional:
        result.notes.append(f"遗留问题 {len(status.leftovers)} 条待处理")
        result.next_actions.append(
            f"{FLOW_CORE} acceptance record --prd {record.id} --ac <AC 编号> "
            f"--result <pass|fail|blocked> --evidence \"<证据>\" --actor {ACTOR_PLACEHOLDER}")
    elif status.all_pass and not status.acceptance:
        result.next_actions.append(
            f"{FLOW_CORE} acceptance approve --prd {record.id} --actor "
            f"{_suggest_actor(workspace, rules)} --confirm")
    elif status.acceptance:
        result.notes.append(
            f"已由 {status.acceptance.get('approved_by')} 于 "
            f"{status.acceptance.get('approved_at')} 确认通过")
        result.next_actions.append(
            "下一步：收尾同步（ticket 08 的 acceptance closeout）；主需求尚未 DONE")
    if pending:
        missing = "、".join(str(item.get("ac")) for item in pending)
        result.notes.append(f"未通过/未记录：{missing}")
    return result


# --------------------------------------------------------------------------
# 写入辅助
# --------------------------------------------------------------------------
def _write_acceptance(workspace: Workspace, record: metadata.PrdRecord, data: dict[str, Any],
                      action: str, result: Result) -> list[str]:
    """写 `checklist.yaml` + `checklist.md` + `demo.md`（幂等：内容相同记 unchanged）。"""
    demo_rel = acceptance.acceptance_path(workspace, record, acceptance.DEMO_FILE)
    md_rel = acceptance.acceptance_path(workspace, record, acceptance.CHECKLIST_FILE)
    state_rel = acceptance.acceptance_path(workspace, record,
                                          acceptance.CHECKLIST_STATE_FILE)
    demo_text = acceptance.render_demo(workspace, record, data)
    md_text = acceptance.render_checklist_md(workspace, record, data)

    written: list[str] = []
    for rel, text in ((demo_rel, demo_text), (md_rel, md_text)):
        path = workspace.path(rel)
        previous = fsutil.read_text(path) if path.is_file() else None
        if previous == text:
            result.add(rel, "acceptance", "unchanged")
        else:
            fsutil.atomic_write_text(path, text)
            result.add(rel, "acceptance", "updated" if previous is not None else action)
        written.append(rel)

    existed = workspace.path(state_rel).is_file()
    acceptance.save_checklist(workspace, record, data)
    result.add(state_rel, "acceptance", "updated" if existed else action)
    written.append(state_rel)
    return written


def _write_report(workspace: Workspace, record: metadata.PrdRecord, data: dict[str, Any],
                  decision: dict[str, Any], result: Result) -> str:
    rel = acceptance.acceptance_path(workspace, record, acceptance.REPORT_FILE)
    existed = workspace.path(rel).is_file()
    fsutil.atomic_write_text(workspace.path(rel),
                             acceptance.render_report(workspace, record, data, decision))
    result.add(rel, "acceptance", "updated" if existed else "created")
    return rel


def _acceptance_metadata(workspace: Workspace, record: metadata.PrdRecord,
                         data: dict[str, Any], decision: dict[str, Any],
                         vstatus: verification.VerificationStatus) -> dict[str, Any]:
    """PRD metadata 的 `acceptance` 字段（ticket 08 收尾事务的读取入口）。"""
    return {
        "decision": acceptance.DECISION_APPROVED,
        "approved_by": decision.get("by"),
        "approved_at": decision.get("at"),
        "role": decision.get("role"),
        "prd_hash": record.compute_content_hash(),
        "prd_version": record.version,
        "code_version": vstatus.code_version,
        "verification_digest": vstatus.verification_digest,
        "checklist_digest": acceptance.checklist_digest(data),
        "leftovers": list(data.get("leftovers") or []),
        "checklist": [
            {"ac": item.get("ac"), "conclusion": item.get("conclusion"),
             "evidence": item.get("evidence"), "recorded_by": item.get("recorded_by"),
             "recorded_at": item.get("recorded_at")}
            for item in data.get("items") or []
        ],
        "dir": acceptance.acceptance_dir(workspace, record),
        "closeout": None,
    }


def _append_decision(data: dict[str, Any], decision: dict[str, Any]) -> None:
    """把本次验收决策追加进 checklist.yaml 的 decisions（只 append，不改历史）。"""
    decisions = data.get("decisions")
    if not isinstance(decisions, list):
        decisions = []
    entry = dict(decision)
    entry["leftovers"] = list(data.get("leftovers") or [])
    decisions.append(entry)
    data["decisions"] = decisions


def _checklist_summary(workspace: Workspace, rules: Rules,
                       record: metadata.PrdRecord) -> dict[str, Any]:
    status = acceptance.evaluate(workspace, rules, record)
    return {
        "prepared": status.prepared,
        "all_pass": status.all_pass,
        "conditional": status.conditional,
        "items": [
            {"ac": item.get("ac"), "conclusion": item.get("conclusion"),
             "suggestion": item.get("suggestion"),
             "evidence": item.get("evidence"), "recorded_by": item.get("recorded_by")}
            for item in status.items
        ],
        "pending": [str(item.get("ac")) for item in status.pending],
        "leftovers": list(status.leftovers),
        "stale": status.stale,
        "stale_reasons": list(status.stale_reasons),
        "baseline_stale": status.baseline_stale,
        "baseline_reasons": list(status.baseline_reasons),
    }


# --------------------------------------------------------------------------
# 辅助
# --------------------------------------------------------------------------
def _prd_arg(args: argparse.Namespace) -> str | None:
    return str(getattr(args, "prd", None) or "").strip() or None


def _require_prd(args: argparse.Namespace) -> str:
    value = _prd_arg(args)
    if not value:
        raise FlowError(
            "PRD_NOT_BOUND",
            "验收命令必须显式绑定一个 PRD（缺少 --prd）",
            suggestion="用 --prd PRD-001 显式声明要验收哪一份 PRD",
        )
    return value


def _require_ac(args: argparse.Namespace) -> str:
    value = str(getattr(args, "ac", None) or "").strip()
    if not value:
        raise FlowError("AC_REQUIRED", "必须指定要记录的 AC 编号（--ac）",
                        suggestion="用 --ac AC-RES-01-01 指定行号，一次只记录一条")
    _assert_single_ac(value)
    return value.upper()


def _assert_single_ac(value: str) -> None:
    if "," in value or " " in value.strip():
        raise FlowError(
            "AC_REQUIRED",
            f"一次只能记录一条 AC（收到 {value}）",
            suggestion="逐条记录：每条 AC 的结论与证据都必须单独核对，不得批量填 pass",
        )


def _actor(args: argparse.Namespace) -> str | None:
    return str(getattr(args, "actor", None) or "").strip() or None


def _confirm(args: argparse.Namespace) -> bool:
    return bool(getattr(args, "confirm", False))


def _clean(value: Any) -> str | None:
    text = str(value or "").strip()
    return text or None


def _clean_list(value: Any) -> list[str]:
    items = [value] if isinstance(value, str) else list(value or [])
    cleaned: list[str] = []
    for item in items:
        text = str(item or "").strip()
        if text and text not in cleaned:
            cleaned.append(text)
    return cleaned


def _gate_rule(rules: Rules, command: str) -> dict[str, Any]:
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


def _suggest_actor(workspace: Workspace, rules: Rules) -> str:
    role = str(_gate_rule(rules, APPROVE_GATE).get("role") or DECISION_ROLE)
    team = state.load_team(workspace)
    if team is not None:
        for member in team.members:
            if member.has_role(role):
                return member.id
    return ACTOR_PLACEHOLDER


_HANDLERS = {
    "prepare": _prepare,
    "record": _record,
    "suggest": _suggest,
    "approve": _approve,
    "reject": _reject,
    "conditional": _conditional,
    "status": _status,
}
