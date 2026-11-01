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

from .. import development, fsutil, locks, metadata, state, verification
from ..output import FlowError, Result
from ..rules import Rules
from ..workspace import Workspace

NAME = "dev"
HELP = "开发门禁、从有效 PRD 建立 Spec/Plan/Tasks、开发状态与范围检查、验证与提交验收"

FLOW_CORE = "flow-core"
ACTOR_PLACEHOLDER = "<开发人员>"
# 提交验收读 `gates.acceptance.submit`（ticket 01 就在 rules 里声明），
# 但实际执行的命令是 `dev submit-acceptance`，history 的 action 记命令名。
ACCEPTANCE_SUBMIT_GATE = "acceptance.submit"


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

    _add_verification_commands(subparsers, parents)


# --------------------------------------------------------------------------
# 参数：ticket 06（验证与提交验收）
# --------------------------------------------------------------------------
def _verify_common() -> "argparse.ArgumentParser":
    """`dev verify` 与其子命令共用的参数（写在子命令前后都行）。"""
    parser = argparse.ArgumentParser(add_help=False)
    parser.add_argument("--prd", default=None, help="PRD 编号（必须显式绑定）")
    parser.add_argument("--suite", default="unit",
                        help="验证套件：unit / integration / static / e2e 或项目自定义")
    parser.add_argument("--ac", action="append", default=None, metavar="AC-...",
                        help="关联的 AC 编号（逗号分隔或重复；缺省为 PRD 的全部 AC）")
    return parser


def _add_verification_commands(subparsers, parents) -> None:
    verify = subparsers.add_parser(
        "verify", parents=[*parents, _verify_common()],
        help="执行项目声明的验证命令，保存原始输出与逐条 AC 结果")
    verify.add_argument("--command", default=None,
                        help="要执行的项目验证命令（原样记录，不做改写）")
    verify.add_argument("--timeout", type=int, default=verification.DEFAULT_TIMEOUT,
                        help=f"超时秒数（默认 {verification.DEFAULT_TIMEOUT}）")
    verify.add_argument("--not-applicable", action="store_true",
                        help="本环境确实不适用该验证（必须同时给 --reason）")
    verify.add_argument("--reason", default=None, help="不适用/阻塞的理由（必填项）")

    vsub = verify.add_subparsers(dest="verify_command", metavar="<子命令>")

    record = vsub.add_parser("record", parents=[*parents, _verify_common()],
                             help="记录已在外部执行过的验证结果")
    record.add_argument("--result", required=True,
                        choices=[verification.RESULT_PASS, verification.RESULT_FAIL,
                                 verification.RESULT_BLOCKED],
                        help="外部执行得到的结论")
    record.add_argument("--exit-code", type=int, default=0, help="外部命令的退出码")
    record.add_argument("--output-file", default=None, required=True,
                        help="外部命令的原始输出文件（内容原样保存为 raw log）")
    record.add_argument("--reason", default=None, help="阻塞/未通过的理由")

    repair = vsub.add_parser("repair", parents=[*parents, _verify_common()],
                             help="记录一次自动修复轮次（受 max_repair_rounds 限制）")
    repair.add_argument("--note", required=True, help="本轮修复做了什么")

    guard = vsub.add_parser("guard", parents=[*parents, _verify_common()],
                            help="防伪造检查：删用例/弱化断言/跳过测试/改 AC")
    guard.add_argument("--allow-skip", action="store_true",
                       help="显式豁免「跳过测试」类命中（必须同时给 --reason）")
    guard.add_argument("--reason", default=None, help="豁免理由")

    submit = subparsers.add_parser(
        "submit-acceptance", parents=parents,
        help="验证全部覆盖后自动提交人工验收（DEVELOPING → READY_FOR_ACCEPTANCE）")
    submit.add_argument("--prd", default=None, help="PRD 编号（必须显式绑定）")

    status = subparsers.add_parser(
        "verification-status", parents=parents,
        help="报告逐条 AC 结论、代码版本与验证基线是否仍有效")
    status.add_argument("--prd", default=None, help="PRD 编号")


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
    gate = _gate_rule(rules, "dev.start")
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


# --------------------------------------------------------------------------
# ticket 06：dev verify / submit-acceptance / verification-status
# --------------------------------------------------------------------------
def _verify(workspace: Workspace, rules: Rules, args: argparse.Namespace) -> Result:
    sub = getattr(args, "verify_command", None) or "run"
    handler = _VERIFY_HANDLERS.get(sub)
    if handler is None:
        raise FlowError("USAGE", "缺少 dev verify 子命令",
                        suggestion="可用子命令：" + "、".join(sorted(_VERIFY_HANDLERS)))
    try:
        return handler(workspace, rules, args)
    except FlowError as exc:
        if not exc.command:
            exc.command = f"dev.verify.{sub}"
        raise


def _login_state(workspace: Workspace, rules: Rules, args: argparse.Namespace
                 ) -> metadata.PrdRecord:
    """验证类命令的公共前置：显式绑定 PRD + 状态属 DEVELOPING（否则提示先 dev start）。"""
    record = metadata.load(workspace, _require_prd(args), rules=rules)
    allowed = verification.verify_states(rules)
    if record.status in allowed:
        return record
    suggestion = (f"先执行 {FLOW_CORE} dev start --prd {record.id} --actor {ACTOR_PLACEHOLDER}"
                  " --confirm")
    if record.status != "DEVELOPING":
        suggestion = ("只有开发中的 PRD 才能产出验证证据；先批准 PRD 并启动开发："
                      f"{FLOW_CORE} dev start --prd {record.id} --actor {ACTOR_PLACEHOLDER}"
                      " --confirm")
    raise FlowError(
        "PRD_STATE_NOT_ALLOWED",
        f"PRD {record.id} 当前状态 {record.status}，不允许执行验证（允许 {'、'.join(allowed)}）",
        object=record.id,
        suggestion=suggestion,
        next_actions=[f"{FLOW_CORE} dev start --prd {record.id} --actor {ACTOR_PLACEHOLDER}"
                      " --confirm"],
    )


def _verify_run(workspace: Workspace, rules: Rules, args: argparse.Namespace) -> Result:
    command = "dev.verify"
    record = _login_state(workspace, rules, args)
    suite = str(getattr(args, "suite", None) or "unit").strip() or "unit"
    acs = _resolve_acs(args, record)
    not_applicable = bool(getattr(args, "not_applicable", False))
    reason = _clean(getattr(args, "reason", None))
    project_command = _clean(getattr(args, "command", None))

    if not_applicable:
        if not reason:
            raise FlowError(
                "NOT_APPLICABLE_REASON_REQUIRED",
                "--not-applicable 必须同时给 --reason（不得以环境限制自动豁免）",
                object=record.id,
                suggestion="写清为何不适用（例如「本环境无外部存储，由人工验收确认」）；"
                           "理由会写入 verification.md 供人工验收核对（SRS-FR-066）",
            )
        project_command = project_command or None
    elif not project_command:
        raise FlowError(
            "COMMAND_REQUIRED",
            "缺少要执行的验证命令（--command），或改用 --not-applicable --reason",
            object=record.id,
            suggestion='例如 --command "pytest -q"；命令会在项目根目录原样执行',
        )

    result = Result(command=command)
    if not_applicable:
        data = verification.load_state(workspace, record)
        entry = verification.append_run(
            workspace, record, data, suite=suite, result=verification.RESULT_NOT_APPLICABLE,
            acs=acs, raw_text="", command=project_command, exit_code=None,
            duration_ms=0, mode="not-applicable", reason=reason)
        data["repair_limit"] = verification.repair_limit(workspace, rules)
    else:
        exit_code, stdout, stderr, duration_ms, timed_out = verification.execute(
            workspace, project_command, int(getattr(args, "timeout", verification.DEFAULT_TIMEOUT)))
        outcome = verification.RESULT_PASS if exit_code == 0 and not timed_out \
            else verification.RESULT_FAIL
        raw_text = _raw_text(project_command, exit_code, stdout, stderr)
        data = verification.load_state(workspace, record)
        entry = verification.append_run(
            workspace, record, data, suite=suite, result=outcome, acs=acs,
            raw_text=raw_text, command=project_command, exit_code=exit_code,
            duration_ms=duration_ms, mode="executed")
        data["repair_limit"] = verification.repair_limit(workspace, rules)

    paths = _persist(workspace, rules, record, data, result)
    result.data["prd"] = record.to_dict()
    result.data["run"] = entry
    result.data["result"] = entry["result"]
    result.data["exit_code"] = entry["exit_code"]
    result.data["acs"] = entry["acs"]
    result.data["raw_log"] = entry["raw_log"]
    result.data["verification_summary"] = data.get("verification_summary")
    result.notes.append(
        f"{record.id} {suite} 验证：{entry['result']}"
        f"（退出码 {entry['exit_code']}，证据 {entry['raw_log']}）")

    if entry["result"] == verification.RESULT_FAIL:
        result.fail(
            "VERIFICATION_FAILED",
            f"{suite} 验证未通过（退出码 {entry['exit_code']}），原始输出已保存到 "
            f"{entry['raw_log']}",
            object=record.id,
            suggestion="先修复失败原因再重新验证；不得删除用例、降低断言或跳过测试制造通过"
                       "（BR-014/015）。修复轮次受 max_repair_rounds 限制",
        )
        result.next_actions.extend([
            f'{FLOW_CORE} dev verify repair --prd {record.id} --note "<本轮修复内容>"',
            f"{FLOW_CORE} dev verify --prd {record.id} --suite {suite} "
            f'--command "{project_command}" --ac {",".join(acs)}',
        ])
    else:
        status = verification.evaluate(workspace, rules, record)
        if status.complete:
            result.next_actions.append(
                f"{FLOW_CORE} dev submit-acceptance --prd {record.id} --actor {ACTOR_PLACEHOLDER}")
        else:
            missing = "、".join(str(item.get("ac")) for item in status.pending)
            result.notes.append(f"仍有未覆盖的验证项：{missing}")
            result.next_actions.append(
                f"{FLOW_CORE} dev verification-status --prd {record.id}")
    return result


def _verify_record(workspace: Workspace, rules: Rules, args: argparse.Namespace) -> Result:
    command = "dev.verify.record"
    record = _login_state(workspace, rules, args)
    suite = str(getattr(args, "suite", None) or "unit").strip() or "unit"
    outcome = str(getattr(args, "result", "") or "").strip()
    if outcome not in (verification.RESULT_PASS, verification.RESULT_FAIL,
                       verification.RESULT_BLOCKED):
        raise FlowError("INVALID_RESULT", f"不支持的验证结论 {outcome}",
                        suggestion="只允许 pass / fail / blocked") 
    acs = _resolve_acs(args, record)
    source = _clean(getattr(args, "output_file", None))
    if not source:
        raise FlowError("OUTPUT_FILE_REQUIRED", "缺少 --output-file（外部验证的原始输出）")
    source_path = workspace.path(source)
    if not source_path.is_file():
        raise FlowError(
            "OUTPUT_FILE_NOT_FOUND",
            f"找不到外部验证的输出文件 {source}（不得只保存摘要）",
            object=source,
            suggestion="先把外部命令的完整输出重定向到文件，再用 --output-file 指向它"
                       "（NFR-008）",
        )
    raw_text = fsutil.read_text(source_path)
    exit_code = int(getattr(args, "exit_code", 0) or 0)
    reason = _clean(getattr(args, "reason", None))
    if outcome != verification.RESULT_PASS and not reason:
        reason = f"外部验证结论为 {outcome}（退出码 {exit_code}）"

    data = verification.load_state(workspace, record)
    entry = verification.append_run(
        workspace, record, data, suite=suite, result=outcome, acs=acs,
        raw_text=raw_text, command=_clean(getattr(args, "command", None)),
        exit_code=exit_code, duration_ms=0, mode="recorded", reason=reason)
    data["repair_limit"] = verification.repair_limit(workspace, rules)

    result = Result(command=command)
    _persist(workspace, rules, record, data, result)
    result.data["prd"] = record.to_dict()
    result.data["run"] = entry
    result.data["result"] = entry["result"]
    result.data["exit_code"] = entry["exit_code"]
    result.data["raw_log"] = entry["raw_log"]
    result.data["verification_summary"] = data.get("verification_summary")
    result.notes.append(
        f"{record.id} {suite} 外部验证已记录为 {entry['result']}"
        f"（退出码 {entry['exit_code']}，证据 {entry['raw_log']}）")
    if outcome != verification.RESULT_PASS:
        result.next_actions.append(
            f'{FLOW_CORE} dev verify repair --prd {record.id} --note "<本轮修复内容>"')
    return result


def _verify_repair(workspace: Workspace, rules: Rules, args: argparse.Namespace) -> Result:
    command = "dev.verify.repair"
    record = _login_state(workspace, rules, args)
    note = _clean(getattr(args, "note", None))
    if not note:
        raise FlowError("NOTE_REQUIRED", "缺少 --note（要记录本轮修复做了什么）")
    data = verification.add_repair(workspace, rules, record, note)

    result = Result(command=command)
    _persist(workspace, rules, record, data, result)
    rounds = int(data["repair_rounds"])
    limit = int(data["repair_limit"])
    result.data["prd"] = record.to_dict()
    result.data["repair_rounds"] = rounds
    result.data["repair_limit"] = limit
    result.notes.append(f"已记录第 {rounds} 轮修复（上限 {limit} 轮）")
    if rounds >= limit:
        result.notes.append("已达修复上限：下一轮必须请人工介入")
    result.next_actions.append(
        f"{FLOW_CORE} dev verify --prd {record.id} --suite unit --command \"<项目验证命令>\"")
    return result


def _verify_guard(workspace: Workspace, rules: Rules, args: argparse.Namespace) -> Result:
    command = "dev.verify.guard"
    record = _login_state(workspace, rules, args)
    data = verification.load_state(workspace, record)
    findings = verification.guard_findings(workspace, record, data)

    allow_skip = bool(getattr(args, "allow_skip", False))
    reason = _clean(getattr(args, "reason", None))
    if allow_skip:
        if not reason:
            raise FlowError(
                "REASON_REQUIRED",
                "--allow-skip 必须同时给 --reason（豁免要留可审计理由）",
                object=record.id,
                suggestion='例如 --reason "该用例依赖外部设备，已由项目负责人登记豁免"',
            )
        verification.apply_waiver(workspace, record, data, findings, reason)

    remaining = verification.unwaived(findings)
    guard = data.get("guard") if isinstance(data.get("guard"), dict) else {}
    guard.update({
        "checked_at": fsutil.now_iso(),
        "ok": not remaining,
        "findings": [item.to_dict() for item in findings],
    })
    data["guard"] = guard

    result = Result(command=command)
    _persist(workspace, rules, record, data, result)
    result.data["prd"] = record.to_dict()
    result.data["ok"] = not remaining
    result.data["findings"] = [item.to_dict() for item in findings]
    result.data["waived"] = [item.to_dict() for item in findings if item.waived]

    for item in remaining:
        result.fail(item.code, item.message, object=item.object, suggestion=item.suggestion)
    if remaining:
        result.notes.append(f"防伪造检查命中 {len(remaining)} 项：验证证据不可信，不得提交验收")
        result.next_actions.extend([
            "恢复被删除/弱化的用例与断言，或先通过 PRD 变更（CR）说明范围变化",
            f"{FLOW_CORE} dev verify guard --prd {record.id}",
        ])
    else:
        waived = [item for item in findings if item.waived]
        result.notes.append("防伪造检查通过" + (f"（{len(waived)} 项跳过已显式豁免）" if waived else ""))
        result.next_actions.append(f"{FLOW_CORE} dev verification-status --prd {record.id}")
    return result


def _submit_acceptance(workspace: Workspace, rules: Rules, args: argparse.Namespace
                       ) -> Result:
    command = "dev.submit-acceptance"
    record = metadata.load(workspace, _require_prd(args), rules=rules)
    actor = _actor(args)
    if record.status != "DEVELOPING":
        raise FlowError(
            "PRD_STATE_NOT_ALLOWED",
            f"只有 DEVELOPING 的 PRD 能提交验收，{record.id} 当前是 {record.status}",
            object=record.id,
            suggestion=f"先执行 {FLOW_CORE} dev start --prd {record.id} --actor "
                       f"{ACTOR_PLACEHOLDER} --confirm 并完成验证",
        )

    status = verification.evaluate(workspace, rules, record)
    if status.stale:
        raise FlowError(
            "BASELINE_STALE",
            f"{record.id} 的验证证据已失效（{'、'.join(status.stale_reasons)}）",
            object=record.id,
            suggestion="PRD、代码或原始输出变化后旧证据不能沿用，请重新执行适用验证",
            next_actions=[f"{FLOW_CORE} dev verification-status --prd {record.id}"],
        )

    pending = status.pending
    if pending:
        detail = "、".join(
            f"{item.get('ac')}（{item.get('result')}）" for item in pending)
        return _incomplete(workspace, record, pending, detail, status)

    gate = _gate_rule(rules, ACCEPTANCE_SUBMIT_GATE)
    team = state.require_team(workspace, object=record.id)
    # 提交验收是机械状态（BR-013 只限制「判定验收通过」），但仍要登记职责供审计
    if actor:
        state.require_member(team, actor, role=str(gate.get("role")), object=record.id)
    target = str(gate.get("to") or verification.ACCEPTANCE_SUBMIT_STATE)
    state.check_transition(rules, "prd", record.status, target, object=record.id)

    data = verification.load_state(workspace, record)
    verification.sync_acs(data, record)
    data["repair_limit"] = verification.repair_limit(workspace, rules)
    submission = verification.build_submission(workspace, record, data, actor)

    result = Result(command=command)
    with locks.object_lock(workspace, record.id, command=command,
                           rules_version=rules.version):
        _persist(workspace, rules, record, data, result)
        state.apply_transition(record.meta, machine="prd", to_state=target,
                               actor=actor or FLOW_CORE, action=command, rules=rules)
        metadata.save(workspace, record)

    result.data["prd"] = record.to_dict()
    result.data["submission"] = {
        "code_version": submission["code_version"],
        "prd_hash": submission["prd_hash"],
        "verification_digest": submission["verification_digest"],
        "checklist_preview": submission["checklist"],
    }
    result.data["verification_summary"] = submission["verification_summary"]
    result.notes.append(
        f"{record.id} 已验证完成并提交人工验收（{target}）；"
        f"代码版本 {submission['code_version']}")
    result.next_actions.extend([
        "请人工验收：逐条核对 verification.md 的 AC 结论与原始输出，不要只信摘要",
        f"{FLOW_CORE} dev verification-status --prd {record.id}",
    ])
    return result


def _incomplete(workspace: Workspace, record: metadata.PrdRecord,
                pending: list[dict[str, Any]], detail: str,
                status: verification.VerificationStatus) -> Result:
    result = Result(command="dev.submit-acceptance")
    result.fail(
        "VERIFICATION_INCOMPLETE",
        f"{record.id} 还有未通过的验证项：{detail}；未执行/阻塞/失败不得记为通过",
        object=record.id,
        suggestion="逐条补齐证据：执行适用验证命令，或用 --not-applicable --reason 显式记录"
                   "不适用理由（SRS-FR-066）",
    )
    for item in pending:
        result.next_actions.append(
            f"{FLOW_CORE} dev verify --prd {record.id} --suite <套件> "
            f"--command \"<项目验证命令>\" --ac {item.get('ac')}")
    result.next_actions.append(
        f"{FLOW_CORE} dev verification-status --prd {record.id}")
    result.data["prd"] = record.to_dict()
    result.data["pending"] = pending
    result.data["summary"] = status.summary
    return result


def _verification_status(workspace: Workspace, rules: Rules, args: argparse.Namespace
                         ) -> Result:
    record = metadata.load(workspace, _require_prd(args), rules=rules)
    status = verification.evaluate(workspace, rules, record)

    result = Result(command="dev.verification-status")
    result.data.update(status.to_dict())
    result.data["prd"] = record.to_dict()
    result.data["repair_limit"] = status.repair_limit

    summary = status.summary
    result.notes.append(
        f"{record.id}（{record.status}）：pass {summary.get('pass', 0)}、"
        f"fail {summary.get('fail', 0)}、blocked {summary.get('blocked', 0)}、"
        f"not-run {summary.get('not-run', 0)}、not-applicable {summary.get('not-applicable', 0)}")
    result.notes.append(f"代码版本 {status.code_version}")

    if status.stale:
        result.fail(
            "BASELINE_STALE",
            f"{record.id} 的验证/提交基线已失效（{'、'.join(status.stale_reasons)}）",
            object=record.id,
            suggestion="PRD、代码或原始输出变化后必须重新验证，旧结论不得沿用（BR-016）",
        )
        result.next_actions.extend([
            f'{FLOW_CORE} dev verify --prd {record.id} --suite unit --command "<项目验证命令>"',
            f"{FLOW_CORE} dev verify guard --prd {record.id}",
        ])
    elif not status.complete:
        missing = "、".join(str(item.get("ac")) for item in status.pending)
        result.notes.append(f"未覆盖：{missing}")
        result.next_actions.append(
            f'{FLOW_CORE} dev verify --prd {record.id} --suite unit --command "<项目验证命令>"')
    else:
        target = verification.ACCEPTANCE_SUBMIT_STATE
        if record.status == "DEVELOPING":
            result.next_actions.append(
                f"{FLOW_CORE} dev submit-acceptance --prd {record.id} --actor {ACTOR_PLACEHOLDER}")
        elif record.status == target:
            result.notes.append("已提交人工验收，等待验收结论")
    return result


def _persist(workspace: Workspace, rules: Rules, record: metadata.PrdRecord,
             data: dict[str, Any], result: Result) -> list[str]:
    """写 `verification.json` + `verification.md`，并把产物登记到信封。"""
    state_rel = verification.state_path(workspace, record)
    report_rel = verification.report_path(workspace, record)
    existed_state = workspace.path(state_rel).is_file()
    existed_report = workspace.path(report_rel).is_file()
    verification.save_state(workspace, record, data)
    verification.save_report(workspace, rules, record, data)
    result.add(state_rel, "verification", "updated" if existed_state else "created")
    result.add(report_rel, "verification", "updated" if existed_report else "created")
    return [state_rel, report_rel]


def _resolve_acs(args: argparse.Namespace, record: metadata.PrdRecord) -> list[str]:
    """解析 `--ac`（逗号分隔或重复）；缺省为本 PRD 的全部 AC，并校验编号存在。"""
    raw = getattr(args, "ac", None)
    items = [raw] if isinstance(raw, str) else list(raw or [])
    requested: list[str] = []
    for item in items:
        for part in str(item).split(","):
            token = part.strip().upper()
            if token and token not in requested:
                requested.append(token)
    known = verification.ac_ids(record)
    if not requested:
        if not known:
            raise FlowError(
                "AC_REQUIRED",
                f"{record.id} 正文没有可验证的 AC，也没有给 --ac",
                object=record.id,
                suggestion="先在 PRD 的场景下补编号 AC，或用 --ac 显式指定验收标准编号",
            )
        return known
    unknown = [item for item in requested if item not in known]
    if unknown:
        raise FlowError(
            "UNKNOWN_AC",
            f"{record.id} 没有验收标准 {'、'.join(unknown)}",
            object=unknown[0],
            suggestion=f"{record.id} 的 AC：{'、'.join(known) or '（无）'}；"
                       "PRD 里不存在的 AC 不能挂验证结论",
        )
    return requested


def _raw_text(command: str, exit_code: int | None, stdout: str, stderr: str) -> str:
    """原始输出文件内容：命令原样 + 未改写的 stdout/stderr + 退出码（NFR-008）。"""
    parts = [
        f"$ {command}",
        "",
        "--- stdout ---",
        stdout.rstrip("\n"),
        "",
        "--- stderr ---",
        stderr.rstrip("\n"),
        "",
        f"--- exit code: {exit_code if exit_code is not None else 'timeout'} ---",
        "",
    ]
    return "\n".join(parts)


def _clean(value: Any) -> str | None:
    text = str(value or "").strip()
    return text or None


_VERIFY_HANDLERS = {
    "run": _verify_run,
    "record": _verify_record,
    "repair": _verify_repair,
    "guard": _verify_guard,
}


_HANDLERS = {
    "gate": _gate,
    "start": _start,
    "status": _status,
    "scope-check": _scope_check,
    "verify": _verify,
    "submit-acceptance": _submit_acceptance,
    "verification-status": _verification_status,
}
