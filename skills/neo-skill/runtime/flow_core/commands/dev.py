"""`dev`：开发门禁、Spec/Plan/Tasks 生成、开发状态与范围检查（ticket 05）。

三条命令的边界：

- `dev gate`：只检查、不写。九项门禁全部通过才返回 `success=true`；任一失败给出结构化
  错误码与下一步建议，且不创建任何文件、不改 PRD 状态（SRS-FR-060、BR-011）。
- `dev start`：门禁通过后取 PRD 对象锁 → 建 `06-specs/<module>/PRD-NNN-<slug>/` 下的
  Spec/Plan/Tasks 与 `.baseline.yaml` → `APPROVED → DEVELOPING`（机械状态，但仍要求
  `--actor` 供审计）。门禁失败时**零副作用**：第 1–9 步全部在写入之前完成。
- `dev status` / `dev scope-check`：只读报告，不改任何状态。

判定逻辑在 `flow_core/development.py`（ticket 06 复用同一套门禁）；本模块只负责参数、
锁、状态跳转与结果信封。状态值与允许集合全部来自 `rules/flow.yaml`。
"""

from __future__ import annotations

import argparse
from typing import Any

from .. import development, fsutil, locks, metadata, state
from ..output import FlowError, Result
from ..rules import Rules
from ..workspace import Workspace

NAME = "dev"
HELP = "开发门禁、从有效 PRD 建立 Spec/Plan/Tasks、开发状态与范围检查"

FLOW_CORE = "flow-core"
ACTOR_PLACEHOLDER = "<开发人员>"


def add_arguments(parser: argparse.ArgumentParser) -> None:
    from ..cli import global_options_parser

    parents = [global_options_parser()]
    subparsers = parser.add_subparsers(dest="dev_command", metavar="<子命令>", required=True)

    gate = subparsers.add_parser("gate", parents=parents,
                                 help="只读检查开发门禁（不写任何文件）")
    gate.add_argument("--prd", default=None,
                      help="要开发的 PRD 编号（必须显式绑定，如 PRD-001）")

    start = subparsers.add_parser("start", parents=parents,
                                  help="门禁通过后建立 Spec/Plan/Tasks 并转 DEVELOPING")
    start.add_argument("--prd", default=None, help="要开发的 PRD 编号（必须显式绑定）")

    status = subparsers.add_parser("status", parents=parents,
                                   help="报告 Spec/Plan/Tasks 与基线是否仍然有效")
    status.add_argument("--prd", default=None, help="PRD 编号")

    scope = subparsers.add_parser("scope-check", parents=parents,
                                  help="检查 Spec/Tasks 与变更路径是否越出 PRD 范围")
    scope.add_argument("--prd", default=None, help="PRD 编号")
    scope.add_argument("--paths", action="append", default=None, metavar="PATH,...",
                       help="本次要改动的路径（逗号分隔或重复；只允许 src/tests/06-specs）")


def run(workspace: Workspace, rules: Rules, args: argparse.Namespace) -> Result:
    sub = getattr(args, "dev_command", None)
    handler = _HANDLERS.get(sub)
    if handler is None:
        raise FlowError(
            "USAGE",
            "缺少 dev 子命令",
            suggestion="可用子命令：" + "、".join(sorted(_HANDLERS)),
        )
    try:
        return handler(workspace, rules, args)
    except FlowError as exc:
        exc.command = f"dev.{sub}"
        raise


# --------------------------------------------------------------------------
# dev gate
# --------------------------------------------------------------------------
def _gate(workspace: Workspace, rules: Rules, args: argparse.Namespace) -> Result:
    report = development.evaluate_gate(workspace, rules, _prd_arg(args))
    result = Result(command="dev.gate")
    result.data["checks"] = [item.to_dict() for item in report.checks]
    result.data["binding"] = report.binding
    result.checks = [item.to_dict() for item in report.checks]
    _emit_failures(result, report)
    if report.ok and report.record is not None:
        result.notes.append(_binding_line(report.record))
        result.next_actions.append(
            f"{FLOW_CORE} dev start --prd {report.record.id} --actor {_suggest_actor(workspace, rules)}"
            " --confirm"
        )
    return result


# --------------------------------------------------------------------------
# dev start
# --------------------------------------------------------------------------
def _start(workspace: Workspace, rules: Rules, args: argparse.Namespace) -> Result:
    command = "dev.start"
    actor = state.require_confirmation(_actor(args), _confirm(args), command=command)
    report = development.evaluate_gate(workspace, rules, _prd_arg(args))
    result = Result(command=command)
    result.data["checks"] = [item.to_dict() for item in report.checks]
    result.data["binding"] = report.binding
    if not report.ok or report.record is None:
        _emit_failures(result, report)
        return result

    record = report.record
    gate = _gate_rule(rules, command)
    team = state.require_team(workspace, object=record.id)
    state.require_member(team, actor, role=str(gate["role"]), object=record.id)

    already_developing = record.status == str(gate.get("to") or "DEVELOPING")
    if not already_developing:
        state.check_transition(rules, "prd", record.status, str(gate.get("to") or "DEVELOPING"),
                               object=record.id)

    directory = workspace.path(development.spec_dir(workspace, record))
    existing_baseline = development.read_baseline(workspace, record)
    with locks.object_lock(workspace, record.id, command=command, rules_version=rules.version):
        directory.mkdir(parents=True, exist_ok=True)
        created: list[str] = []
        updated: list[str] = []
        kept: list[str] = []
        for name in development.spec_files(rules):
            path = directory / name
            rel = workspace.rel(path)
            if path.is_file():
                kept.append(rel)
                continue
            fsutil.atomic_write_text(
                path, development.render_spec(workspace, record, name=name, rules=rules))
            created.append(rel)

        baseline_rel = development.baseline_path(workspace, record)
        stale = development.baseline_is_stale(existing_baseline, record)
        if stale and existing_baseline is not None:
            for name in development.spec_files(rules):
                path = directory / name
                if path.is_file() and workspace.rel(path) not in created:
                    fsutil.atomic_write_text(
                        path, development.refresh_front_matter(path, record))
                    updated.append(workspace.rel(path))
            development.write_baseline(workspace, record)
            updated.append(baseline_rel)
        elif existing_baseline is not None:
            kept.append(baseline_rel)
        else:
            development.write_baseline(workspace, record)
            created.append(baseline_rel)

        if not already_developing:
            state.apply_transition(record.meta, machine="prd",
                                   to_state=str(gate.get("to") or "DEVELOPING"),
                                   actor=actor, action=command, rules=rules)
            metadata.save(workspace, record)

    for rel in created:
        result.add(rel, "dev", "created")
    for rel in updated:
        result.add(rel, "dev", "updated")
    for rel in kept:
        result.add(rel, "dev", "unchanged")
    result.data["prd"] = record.to_dict()
    result.data["baseline"] = development.read_baseline(workspace, record)
    result.notes.append(_binding_line(record))
    if already_developing:
        result.notes.append(f"{record.id} 已是 {record.status}，本次为幂等重启，未重建已有文件")
    else:
        result.notes.append(f"{record.id} 已转 {gate.get('to') or 'DEVELOPING'}（{actor}）")
    result.next_actions.extend([
        f"{FLOW_CORE} dev scope-check --prd {record.id} --paths <本次改动的路径>",
        f"{FLOW_CORE} dev status --prd {record.id}",
    ])
    return result


# --------------------------------------------------------------------------
# dev status
# --------------------------------------------------------------------------
def _status(workspace: Workspace, rules: Rules, args: argparse.Namespace) -> Result:
    record = metadata.load(workspace, _require_prd(args), rules=rules)
    baseline = development.read_baseline(workspace, record)
    files = development.existing_spec_files(workspace, record, rules)
    actual = record.compute_content_hash()
    hash_matches = bool(record.content_hash) and record.content_hash == actual
    baseline_valid = hash_matches and development.baseline_is_valid(baseline)

    result = Result(command="dev.status")
    result.data["prd"] = record.to_dict()
    result.data["spec_dir"] = development.spec_dir(workspace, record)
    result.data["files"] = files
    result.data["baseline"] = baseline
    result.data["baseline_hash"] = (baseline or {}).get("baseline_hash")
    result.data["current_hash"] = actual
    result.data["hash_matches"] = hash_matches
    result.data["baseline_valid"] = baseline_valid
    result.data["started"] = all(files.values()) and baseline is not None

    result.notes.append(
        f"{record.id}（{record.status}）Spec {_mark(files.get('spec'))}"
        f"、Plan {_mark(files.get('plan'))}、Tasks {_mark(files.get('tasks'))}"
    )
    result.notes.append("基线有效" if baseline_valid else "基线无效或已失效")
    if not hash_matches:
        result.next_actions.append(
            f"{FLOW_CORE} prd cr --id {record.id} --reason \"<变更原因>\"")
    elif not baseline:
        result.next_actions.append(
            f"{FLOW_CORE} dev start --prd {record.id} --actor {ACTOR_PLACEHOLDER} --confirm")
    else:
        result.next_actions.append(f"{FLOW_CORE} dev scope-check --prd {record.id}")
    return result


# --------------------------------------------------------------------------
# dev scope-check
# --------------------------------------------------------------------------
def _scope_check(workspace: Workspace, rules: Rules, args: argparse.Namespace) -> Result:
    record = metadata.load(workspace, _require_prd(args), rules=rules)
    report = development.check_scope(workspace, rules, record, paths=args.paths)

    result = Result(command="dev.scope-check")
    result.data["prd"] = record.to_dict()
    result.data["scope"] = report.to_dict()
    result.data["spec_dir"] = development.spec_dir(workspace, record)

    for token in report.unknown_scenarios:
        result.fail(
            "SCOPE_EXCEEDED",
            f"Spec/Tasks 引用了 PRD 未定义的场景 {token}",
            object=token,
            suggestion=_stop_suggestion(record),
        )
    for token in report.unknown_acs:
        result.fail(
            "SCOPE_EXCEEDED",
            f"Spec/Tasks 引用了 PRD 场景之外的验收标准 {token}",
            object=token,
            suggestion=_stop_suggestion(record),
        )
    for item in report.path_violations:
        reason = development.path_violation(item, development.allowed_path_prefixes(rules))
        result.fail(
            "SCOPE_EXCEEDED",
            f"变更路径 {item} 越出允许范围（{reason}）",
            object=item,
            suggestion=_stop_suggestion(record),
        )

    if not result.errors:
        result.notes.append(
            f"{record.id} 范围检查通过：场景 {'、'.join(report.referenced_scenarios) or '未引用'}"
            f"；路径 {len(report.paths)} 个"
        )
        result.next_actions.append(f"{FLOW_CORE} dev status --prd {record.id}")
    return result


def _stop_suggestion(record: metadata.PrdRecord) -> str:
    return ("需求不明确或范围疑似扩大时停止：要求 CR 或人工澄清，不得自动修改 PRD；"
            f"需要变更时执行 {development.CR_HINT.format(prd_id=record.id)}")


# --------------------------------------------------------------------------
# 辅助
# --------------------------------------------------------------------------
def _prd_arg(args: argparse.Namespace) -> str | None:
    value = str(getattr(args, "prd", None) or "").strip()
    return value or None


def _require_prd(args: argparse.Namespace) -> str:
    """dev 命令必须显式绑定 PRD（不允许隐式推断要开发哪一份）。"""
    value = _prd_arg(args)
    if not value:
        raise FlowError(
            "PRD_NOT_BOUND",
            "开发入口必须显式绑定一个 PRD（缺少 --prd）",
            suggestion="用 --prd PRD-001 显式声明要开发哪一份 PRD",
        )
    return value


def _actor(args: argparse.Namespace) -> str | None:
    return str(getattr(args, "actor", None) or "").strip() or None


def _confirm(args: argparse.Namespace) -> bool:
    return bool(getattr(args, "confirm", False))


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


def _emit_failures(result: Result, report: development.GateReport) -> None:
    """把门禁失败项写成 `errors[]`，并把建议命令放进 `next_actions`。"""
    for item in report.failures:
        result.fail(item.code or "GATE_FAILED", item.detail, object=report.prd_id,
                    suggestion=item.suggestion)
    if report.failures:
        result.notes.append(f"门禁未通过：{len(report.failures)} 项失败，未创建任何开发产物")
        if any(item.code == "PRD_STATE_NOT_ALLOWED" for item in report.failures):
            result.next_actions.append(
                f"{FLOW_CORE} prd approve --id {report.prd_id} --actor "
                f"{ACTOR_PLACEHOLDER} --confirm")
        if any(item.code == "HASH_MISMATCH" for item in report.failures):
            result.next_actions.append(
                development.CR_HINT.format(prd_id=report.prd_id))
        if any(item.code == "CONSTITUTION_NOT_CONFIRMED" or item.code == "CONSTITUTION_MISSING"
               for item in report.failures):
            result.next_actions.append("补齐并确认 .specify/constitution.md："
                                       f"{FLOW_CORE} dev gate --prd {report.prd_id}")


def _suggest_actor(workspace: Workspace, rules: Rules) -> str:
    gate = (rules.data.get("gates") or {}).get("dev.start")
    role = str(gate.get("role")) if isinstance(gate, dict) else state.ROLE_DEVELOPER
    team = state.load_team(workspace)
    if team is not None:
        for member in team.members:
            if member.has_role(role):
                return member.id
    return ACTOR_PLACEHOLDER


def _binding_line(record: metadata.PrdRecord) -> str:
    return (f"{record.id} {record.title}（{record.status}，模块 {record.module}"
            f"，版本 {record.version}，基线 {record.content_hash or '未锁定'}）")


def _mark(value: bool | None) -> str:
    return "有" if value else "缺"


_HANDLERS = {
    "gate": _gate,
    "start": _start,
    "status": _status,
    "scope-check": _scope_check,
}
