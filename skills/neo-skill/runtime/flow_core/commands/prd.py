"""`prd`：PRD 草稿、结构自审、提交评审与人工批准（ticket 03）。

命令分三类：

- **草稿类**（`create`/`update`/`show`/`list`/`validate`/`self-review`）：只有 `create`
  写文件；其余只读（`update` 仅在 `DRAFT`/`CHANGES_REQUESTED` 可写，其他状态 `PRD_LOCKED`）。
- **人工决策类**（`submit-review`/`approve`/`reject`）：严格按共享设计 §2.3 的固定顺序——
  解析对象 → `CONFIRMATION_REQUIRED` → 团队/身份/职责 → 状态跳转 → 取锁 →
  追加 history/confirmations → 原子写 → 释放锁。第 1–4 步都在写入之前，失败零副作用。
- **基线校验**（`verify-hash`）：只读比对 `content_hash`（门禁拦截由 ticket 05 建立）。

状态、跳转与职责全部读 `rules/flow.yaml`（`prd` 状态机 + `gates`），本模块不硬编码状态值。
PRD 的路径、metadata 投影与结构自审在 `flow_core/metadata.py`，ticket 04/05/07/08 复用它。
"""

from __future__ import annotations

import argparse
from pathlib import Path
from typing import Any

from .. import fsutil, ids, locks, metadata, requirements as reqs, state
from ..output import FlowError, Result
from ..rules import Rules
from ..workspace import Workspace

NAME = "prd"
HELP = "PRD 草稿、结构自审、提交评审与需求负责人人工批准"

FLOW_CORE = "flow-core"
ACTOR_PLACEHOLDER = "<需求负责人>"
CR_HINT = "flow-core prd cr --id {prd_id} --reason \"<变更原因>\""

# `prd update --set` 允许的字段（id/slug/module/source_reqs 是创建后不变的标识与范围）
SETTABLE_FIELDS: tuple[str, ...] = ("title", "feature", "release", "scenarios", "related_reqs")


def add_arguments(parser: argparse.ArgumentParser) -> None:
    from ..cli import global_options_parser

    parents = [global_options_parser()]
    subparsers = parser.add_subparsers(dest="prd_command", metavar="<子命令>", required=True)

    create = subparsers.add_parser("create", parents=parents,
                                   help="从 PLANNED 主需求创建 DRAFT PRD")
    create.add_argument("--req", action="append", default=None, metavar="REQ-ID",
                        help="主需求编号（恰好一个；可重复以触发 MULTIPLE_PRIMARY_REQS 校验）")
    create.add_argument("--slug", default=None, help="稳定 slug（默认取主需求的 slug）")
    create.add_argument("--module", default=None, help="所属模块（默认取主需求的模块）")
    create.add_argument("--feature", default=None, help="覆盖的功能标识（默认取 slug）")
    create.add_argument("--scenarios", action="append", default=None, metavar="SC-A-01,...",
                        help="场景编号（逗号分隔或重复；上限读规则 max_scenarios_per_prd）")
    create.add_argument("--release", default=None, help="目标版本（默认取主需求已确认的版本）")
    create.add_argument("--related", action="append", default=None, metavar="REQ-ID,...",
                        help="相关需求（只作引用，不能成为主需求）")

    update = subparsers.add_parser("update", parents=parents,
                                   help="保存草稿正文/场景（仅 DRAFT/CHANGES_REQUESTED）")
    update.add_argument("--id", required=True, help="PRD 编号，如 PRD-001")
    update.add_argument("--body-file", default=None, help="用该 Markdown 文件替换 prd.md")
    update.add_argument("--set", action="append", default=None, metavar="FIELD=VALUE",
                        help="更新字段：title/feature/release/scenarios/related_reqs")
    update.add_argument("--scenarios", action="append", default=None, metavar="SC-A-01,...",
                        help="替换场景编号列表")

    show = subparsers.add_parser("show", parents=parents, help="显示 PRD 投影与正文")
    show.add_argument("--id", required=True)

    listing = subparsers.add_parser("list", parents=parents, help="列出 PRD（可按状态/模块过滤）")
    listing.add_argument("--status", default=None, help="按状态过滤，如 DRAFT、APPROVED")
    listing.add_argument("--module", default=None, help="按模块过滤")

    validate = subparsers.add_parser("validate", parents=parents,
                                     help="结构校验（只读，不修改正文）")
    validate.add_argument("--id", required=True)

    review = subparsers.add_parser("self-review", parents=parents,
                                   help="只读自审清单（复用结构校验）")
    review.add_argument("--id", required=True)

    submit = subparsers.add_parser("submit-review", parents=parents,
                                   help="提交评审（DRAFT/CHANGES_REQUESTED → IN_REVIEW）")
    submit.add_argument("--id", required=True)
    submit.add_argument("--note", default=None, help="提交说明（写进评审记录）")

    approve = subparsers.add_parser("approve", parents=parents,
                                    help="需求负责人批准（IN_REVIEW → APPROVED，锁定 hash）")
    approve.add_argument("--id", required=True)

    reject = subparsers.add_parser("reject", parents=parents, help="拒绝 PRD（→ REJECTED）")
    reject.add_argument("--id", required=True)
    reject.add_argument("--reason", default=None, help="拒绝理由（必填）")

    verify = subparsers.add_parser("verify-hash", parents=parents,
                                   help="校验 content_hash 是否与当前内容一致")
    verify.add_argument("--id", required=True)


def run(workspace: Workspace, rules: Rules, args: argparse.Namespace) -> Result:
    sub = getattr(args, "prd_command", None)
    handler = _HANDLERS.get(sub)
    if handler is None:
        raise FlowError(
            "USAGE",
            "缺少 prd 子命令",
            suggestion="可用子命令：" + "、".join(sorted(_HANDLERS)),
        )
    try:
        return handler(workspace, rules, args)
    except FlowError as exc:
        exc.command = f"prd.{sub}"
        raise


# --------------------------------------------------------------------------
# 创建
# --------------------------------------------------------------------------
def _create(workspace: Workspace, rules: Rules, args: argparse.Namespace) -> Result:
    primary = _primary_reqs(args.req, rules)
    record = reqs.require_planned(workspace, rules, primary[0])

    module = reqs.validate_module(args.module or record.module)
    scenarios = metadata.parse_scenarios(args.scenarios)
    limit = metadata.max_scenarios(rules)
    if len(scenarios) > limit:
        raise FlowError(
            "TOO_MANY_SCENARIOS",
            f"场景数 {len(scenarios)} 超过规则上限 {limit}",
            object=record.id,
            suggestion=f"按功能边界拆分成多个 PRD（每份不超过 {limit} 个场景），或先只登记本期场景",
        )
    slug = metadata.validate_slug(args.slug or record.slug)
    _ensure_slug_free(workspace, module=module, slug=slug)
    feature = metadata.validate_slug(args.feature or slug, field="功能标识")
    release = record.release
    if args.release:
        release = metadata.validate_release(args.release)
    related = _related_reqs(workspace, rules, args.related, primary=record.id)

    actor = _actor(args) or "flow-core"
    prd_id = ids.allocate(workspace, "PRD", width=rules.id_width,
                          prefix=rules.id_prefixes.get("prd", "PRD"))
    directory = metadata.directory_path(workspace, module=module, prd_id=prd_id, slug=slug)

    meta = metadata.initial_metadata(
        prd_id=prd_id, slug=slug, title=record.title, module=module, feature=feature,
        scenarios=scenarios, source_reqs=[record.id], related_reqs=related,
        release=release, actor=actor, rules=rules,
    )
    _append_create_history(meta, actor=actor, rules=rules)
    body = metadata.render_body(workspace, prd_id=prd_id, title=record.title, module=module,
                                feature=feature, scenarios=scenarios,
                                source_reqs=[record.id], release=release)

    with locks.object_locks(workspace, [prd_id, record.id], command="prd.create",
                            rules_version=rules.version):
        (directory / metadata.REVIEWS_DIR).mkdir(parents=True, exist_ok=True)
        (directory / metadata.CHANGES_DIR).mkdir(parents=True, exist_ok=True)
        fsutil.atomic_write_text(directory / metadata.BODY_FILE, body)
        fsutil.atomic_write_yaml(directory / metadata.METADATA_FILE, meta)

        # 主需求转 IN_PRD：机械状态，只追加 history（不追 confirmations）
        state.apply_transition(record.meta, machine="req", to_state=_in_prd_state(rules),
                               actor=actor, action="prd.create", rules=rules)
        record.meta["prd"] = prd_id
        reqs.save(workspace, record)

    saved = metadata.PrdRecord(directory=directory, rel=workspace.rel(directory),
                               meta=meta, body=body)
    result = Result(command="prd.create")
    result.add(saved.rel + "/" + metadata.METADATA_FILE, "prd", "created")
    result.add(saved.rel + "/" + metadata.BODY_FILE, "prd", "created")
    result.add(record.rel, "req", "updated")
    result.data["prd"] = saved.to_dict()
    result.data["requirement"] = record.to_dict()
    result.notes.append(f"{prd_id} {record.title}（DRAFT，模块 {module}"
                        f"，主需求 {record.id}" + (f"，版本 {release}" if release else "") + "）")
    result.notes.append(f"主需求 {record.id} 已转 " + _in_prd_state(rules))
    result.next_actions.extend([
        f"{FLOW_CORE} prd validate --id {prd_id}",
        f"{FLOW_CORE} prd submit-review --id {prd_id} --actor <提交人> --confirm",
    ])
    return result


def _primary_reqs(raw: list[str] | None, rules: Rules) -> list[str]:
    values: list[str] = []
    for item in raw or []:
        for part in str(item).split(","):
            name = part.strip()
            if not name:
                continue
            value = reqs.normalize_id(name, rules)
            if value not in values:
                values.append(value)
    if not values:
        raise FlowError(
            "REQ_REQUIRED",
            "创建 PRD 必须指定主需求（--req）",
            suggestion="一个 PRD 只能有一个主需求：flow-core prd create --req REQ-001",
        )
    if len(values) > 1:
        raise FlowError(
            "MULTIPLE_PRIMARY_REQS",
            f"一个 PRD 只能有一个主需求，收到了 {len(values)} 个：{'、'.join(values)}",
            object="、".join(values),
            suggestion="只保留一个主需求；其他需求请用 --related 作为相关需求引用",
        )
    return values


def _related_reqs(workspace: Workspace, rules: Rules, raw: list[str] | None, *,
                  primary: str) -> list[str]:
    values = reqs.validate_related(raw, rules)
    for value in values:
        if value == primary:
            raise FlowError(
                "INVALID_RELATED_REQ",
                f"{value} 已经是本 PRD 的主需求，不能再作为相关需求",
                object=value,
                suggestion="从 --related 中移除该需求",
            )
        reqs.load(workspace, value, rules=rules)  # 不存在 → REQ_NOT_FOUND
    return values


def _ensure_slug_free(workspace: Workspace, *, module: str, slug: str) -> None:
    directory = workspace.prds_dir / module
    if not directory.is_dir():
        return
    for path in sorted(directory.glob(f"PRD-*-{slug}")):
        owner = path.name.split("-")[0] + "-" + path.name.split("-")[1]
        raise FlowError(
            "SLUG_CONFLICT",
            f"模块 {module} 下已有 {owner} 使用 slug `{slug}`",
            object=slug,
            suggestion="slug 创建后不改也不重复；换一个能区分的标题或显式 --slug",
        )


def _append_create_history(meta: dict[str, Any], *, actor: str, rules: Rules) -> None:
    state.append_history(meta, {
        "at": fsutil.now_iso(),
        "from": None,
        "to": rules.initial_state("prd"),
        "by": actor,
        "action": "prd.create",
        "rules_version": rules.version,
    })


def _in_prd_state(rules: Rules) -> str:
    """主需求在 PRD 创建后进入的状态（读 `req` 状态机，不硬编码）。"""
    transitions = rules.transitions("req")
    candidates = transitions.get("PLANNED") or []
    for name in candidates:
        if name.startswith("IN_"):
            return name
    return "IN_PRD"


# --------------------------------------------------------------------------
# 草稿类命令
# --------------------------------------------------------------------------
def _update(workspace: Workspace, rules: Rules, args: argparse.Namespace) -> Result:
    record = metadata.load(workspace, args.id, rules=rules)
    _require_editable(rules, record)

    assignments = _parse_sets(args.set, rules)
    if args.scenarios:
        assignments["scenarios"] = metadata.parse_scenarios(args.scenarios)

    new_body = record.body
    body_file = args.body_file
    if body_file:
        path = Path(body_file).expanduser()
        if not path.is_file():
            raise FlowError(
                "BODY_FILE_NOT_FOUND",
                f"正文文件不存在：{body_file}",
                object=record.id,
                suggestion="确认路径后重试；PRD 未被修改",
            )
        new_body = fsutil.read_text(path)

    if not assignments and not body_file:
        raise FlowError(
            "NOTHING_TO_UPDATE",
            "没有要更新的内容：至少给一个 --set 或 --body-file 或 --scenarios",
            object=record.id,
            suggestion="例如 --set feature=resume-parse，或 --body-file prd-draft.md",
        )

    limit = metadata.max_scenarios(rules)
    scenarios = assignments.get("scenarios")
    if scenarios is not None and len(scenarios) > limit:
        raise FlowError(
            "TOO_MANY_SCENARIOS",
            f"场景数 {len(scenarios)} 超过规则上限 {limit}",
            object=record.id,
            suggestion=f"按功能边界拆分成多个 PRD（每份不超过 {limit} 个场景）",
        )

    for key, value in assignments.items():
        record.meta[key] = value
    record.meta["updated_at"] = fsutil.now_iso()
    if body_file:
        metadata.save_body(workspace, record, new_body)
    metadata.save(workspace, record)

    result = Result(command="prd.update")
    result.add(record.rel + "/" + metadata.METADATA_FILE, "prd", "updated")
    if body_file:
        result.add(record.rel + "/" + metadata.BODY_FILE, "prd", "updated")
    result.data["prd"] = record.to_dict()
    result.next_actions.append(f"{FLOW_CORE} prd validate --id {record.id}")
    return result


def _parse_sets(raw: list[str] | None, rules: Rules) -> dict[str, Any]:
    assignments: dict[str, Any] = {}
    for item in raw or []:
        text = str(item)
        if "=" not in text:
            raise FlowError(
                "INVALID_SET",
                f"`--set {text}` 不是 key=value 形式",
                suggestion="例如 --set feature=resume-parse",
            )
        key, value = (part.strip() for part in text.split("=", 1))
        if key not in SETTABLE_FIELDS:
            raise FlowError(
                "IMMUTABLE_FIELD",
                f"字段 `{key}` 不能在草稿里直接改",
                object=key,
                suggestion="可改字段：" + "、".join(SETTABLE_FIELDS)
                            + "；状态必须走 submit-review / approve 等人工决策命令，"
                              "已批准内容必须走 CR",
            )
        if key in ("feature", "title"):
            assignments[key] = (metadata.validate_slug(value, field="功能标识")
                                if key == "feature" else value)
        elif key == "release":
            assignments[key] = metadata.validate_release(value) if value else None
        elif key == "scenarios":
            assignments[key] = metadata.parse_scenarios(value)
        elif key == "related_reqs":
            assignments[key] = reqs.validate_related(value, rules)
        else:  # pragma: no cover - 防御：新增字段必须在此分支
            assignments[key] = value or None
    return assignments


def _show(workspace: Workspace, rules: Rules, args: argparse.Namespace) -> Result:
    record = metadata.load(workspace, args.id, rules=rules)
    result = Result(command="prd.show")
    result.data["prd"] = record.to_dict(include_body=True)
    result.data["files"] = record.files()
    result.notes.append(_summary_line(record))
    result.next_actions.extend(_next_actions(record, rules))
    return result


def _list(workspace: Workspace, rules: Rules, args: argparse.Namespace) -> Result:
    status = str(args.status or "").strip().upper() or None
    module = str(args.module or "").strip() or None
    records = metadata.list_prds(workspace)
    if status:
        if not rules.is_state("prd", status):
            raise FlowError(
                "INVALID_STATE",
                f"prd 不存在状态 {status}",
                object=status,
                suggestion="可用状态：" + "、".join(rules.states("prd")),
            )
        records = [item for item in records if item.status == status]
    if module:
        records = [item for item in records if item.module == module]

    result = Result(command="prd.list")
    result.data["prds"] = [item.to_dict() for item in records]
    result.data["count"] = len(records)
    result.notes.extend(_summary_line(item) for item in records)
    if not records:
        result.next_actions.append(
            f"{FLOW_CORE} prd create --req <PLANNED 主需求> --scenarios SC-<模块>-01"
        )
    return result


def _validate(workspace: Workspace, rules: Rules, args: argparse.Namespace) -> Result:
    record = metadata.load(workspace, args.id, rules=rules)
    issues = metadata.review(workspace, record, rules)
    errors = [item for item in issues if item["severity"] == metadata.SEVERITY_ERROR]
    warnings = [item for item in issues if item["severity"] == metadata.SEVERITY_WARNING]

    result = Result(command="prd.validate")
    result.data["prd"] = record.to_dict()
    result.data["issues"] = issues
    result.data["counts"] = {"error": len(errors), "warning": len(warnings)}
    for item in errors:
        result.fail(item["code"], item["message"], object=item["object"],
                    suggestion=item["suggestion"] or None)
    result.notes.append(
        f"{record.id} 结构校验：{len(errors)} 个阻断问题，{len(warnings)} 个提示"
    )
    for item in errors + warnings:
        result.notes.append(f"[{item['severity']}] {item['code']} {item['object']}：{item['message']}")
    if errors:
        result.next_actions.append(
            f"先修正上述问题（flow-core prd update --id {record.id} --body-file <草稿文件>）再提交评审"
        )
    else:
        result.next_actions.append(f"{FLOW_CORE} prd self-review --id {record.id}")
    return result


def _self_review(workspace: Workspace, rules: Rules, args: argparse.Namespace) -> Result:
    record = metadata.load(workspace, args.id, rules=rules)
    issues = metadata.review(workspace, record, rules)
    result = Result(command="prd.self-review")
    result.data["prd"] = record.to_dict()
    result.data["issues"] = issues
    result.data["checklist"] = metadata.checklist(record, issues)
    result.data["counts"] = {
        "error": sum(1 for item in issues if item["severity"] == metadata.SEVERITY_ERROR),
        "warning": sum(1 for item in issues if item["severity"] == metadata.SEVERITY_WARNING),
    }
    for item in result.data["checklist"]:
        result.notes.append(f"[{item['status']}] {item['item']}：{item['detail']}")
    result.next_actions.append(
        f"{FLOW_CORE} prd submit-review --id {record.id} --actor <提交人> --confirm"
    )
    return result


# --------------------------------------------------------------------------
# 人工决策类命令
# --------------------------------------------------------------------------
def _submit_review(workspace: Workspace, rules: Rules, args: argparse.Namespace) -> Result:
    record = metadata.load(workspace, args.id, rules=rules)
    command = "prd.submit-review"
    actor = state.require_confirmation(_actor(args), _confirm(args), command=command)
    gate = _gate(rules, command)
    team = state.require_team(workspace, object=record.id)
    state.require_member(team, actor, role=str(gate["role"]), object=record.id)
    if record.status in metadata.cr_states(rules):
        raise FlowError(
            "PRD_LOCKED",
            f"PRD {record.id} 已是 {record.status}，不能直接重新提交评审",
            object=record.id,
            suggestion="已批准内容的变化必须走 PRD 变更（CR）：先提变更再重新评审",
            next_actions=[CR_HINT.format(prd_id=record.id)],
        )
    to_state = str(gate.get("to") or "IN_REVIEW")
    state.check_transition(rules, "prd", record.status, to_state, object=record.id)

    note = str(args.note or "").strip() or None
    issues = metadata.review(workspace, record, rules)
    blocking = [item for item in issues if item["severity"] == metadata.SEVERITY_ERROR]

    with locks.object_lock(workspace, record.id, command=command,
                           rules_version=rules.version):
        state.apply_transition(record.meta, machine="prd", to_state=to_state, actor=actor,
                               action=command, rules=rules)
        _annotate_last_history(record.meta, note, key="note")
        _append_decision(record.meta, actor=actor, role=str(gate["role"]),
                         decision="submitted", object_id=record.id, note=note)
        record.meta["submitted_by"] = actor
        record.meta["submitted_at"] = fsutil.now_iso()
        metadata.save(workspace, record)
        review_file = metadata.review_path(record)
        fsutil.atomic_write_text(review_file, _render_review(record, actor=actor, note=note,
                                                             issues=issues))

    result = Result(command=command)
    result.add(record.rel + "/" + metadata.METADATA_FILE, "prd", "updated")
    result.add(workspace.rel(review_file), "review", "created")
    result.data["prd"] = record.to_dict()
    result.data["issues"] = issues
    result.notes.append(f"{record.id} 已提交评审（{actor}），评审记录：{workspace.rel(review_file)}")
    if blocking:
        result.notes.append(f"仍有 {len(blocking)} 个阻断问题，批准前必须修正")
    result.next_actions.append(
        f"{FLOW_CORE} prd approve --id {record.id} --actor {_suggest_actor(workspace, rules)}"
        " --confirm"
    )
    return result


def _approve(workspace: Workspace, rules: Rules, args: argparse.Namespace) -> Result:
    record = metadata.load(workspace, args.id, rules=rules)
    command = "prd.approve"
    actor = state.require_confirmation(_actor(args), _confirm(args), command=command)
    gate = _gate(rules, command)
    team = state.require_team(workspace, object=record.id)
    state.require_member(team, actor, role=str(gate["role"]), object=record.id)
    to_state = str(gate.get("to") or "APPROVED")
    state.check_transition(rules, "prd", record.status, to_state, object=record.id)

    issues = metadata.review(workspace, record, rules)
    blocking = [item for item in issues if item["severity"] == metadata.SEVERITY_ERROR]
    if blocking:
        result = Result(command=command)
        result.data["prd"] = record.to_dict()
        result.data["issues"] = issues
        codes = "、".join(item["code"] for item in blocking[:5])
        result.fail(
            "VALIDATION_FAILED",
            f"PRD {record.id} 还有 {len(blocking)} 个结构校验阻断问题（{codes}），不能批准",
            object=record.id,
            suggestion="先修正问题并重新提交评审；批准前不修改任何内容",
            next_action=f"{FLOW_CORE} prd validate --id {record.id}",
        )
        return result

    with locks.object_lock(workspace, record.id, command=command,
                           rules_version=rules.version):
        content_hash = record.compute_content_hash()
        version = record.version
        state.apply_transition(record.meta, machine="prd", to_state=to_state, actor=actor,
                               action=command, rules=rules)
        _append_decision(record.meta, actor=actor, role=str(gate["role"]),
                         decision="approved", object_id=record.id,
                         content_hash=content_hash, version=version)
        record.meta["content_hash"] = content_hash
        record.meta["approved_by"] = actor
        record.meta["approved_at"] = fsutil.now_iso()
        metadata.save(workspace, record)

    result = Result(command=command)
    result.add(record.rel + "/" + metadata.METADATA_FILE, "prd", "updated")
    result.data["prd"] = record.to_dict()
    result.data["content_hash"] = content_hash
    result.notes.append(f"{record.id} 已批准（{actor}），版本 {version}，内容基线已锁定")
    result.next_actions.append(f"{FLOW_CORE} prd verify-hash --id {record.id}")
    return result


def _reject(workspace: Workspace, rules: Rules, args: argparse.Namespace) -> Result:
    record = metadata.load(workspace, args.id, rules=rules)
    command = "prd.reject"
    actor = state.require_confirmation(_actor(args), _confirm(args), command=command)
    gate = _gate(rules, command)
    team = state.require_team(workspace, object=record.id)
    state.require_member(team, actor, role=str(gate["role"]), object=record.id)
    reason = str(args.reason or "").strip()
    if not reason:
        raise FlowError(
            "REASON_REQUIRED",
            "拒绝 PRD 必须写明理由（--reason）",
            object=record.id,
            suggestion="例如 --reason \"范围过大，需要拆分后再做\"",
        )
    to_state = str(gate.get("to") or "REJECTED")
    state.check_transition(rules, "prd", record.status, to_state, object=record.id)

    with locks.object_lock(workspace, record.id, command=command,
                           rules_version=rules.version):
        state.apply_transition(record.meta, machine="prd", to_state=to_state, actor=actor,
                               action=command, rules=rules)
        _annotate_last_history(record.meta, reason, key="reason")
        _append_decision(record.meta, actor=actor, role=str(gate["role"]),
                         decision="rejected", object_id=record.id, note=reason)
        record.meta["status_reason"] = reason
        metadata.save(workspace, record)

    result = Result(command=command)
    result.add(record.rel + "/" + metadata.METADATA_FILE, "prd", "updated")
    result.data["prd"] = record.to_dict()
    result.notes.append(f"{record.id} 已拒绝，理由：{reason}")
    return result


# --------------------------------------------------------------------------
# 基线校验
# --------------------------------------------------------------------------
def _verify_hash(workspace: Workspace, rules: Rules, args: argparse.Namespace) -> Result:
    record = metadata.load(workspace, args.id, rules=rules)
    expected = record.content_hash
    actual = record.compute_content_hash()
    result = Result(command="prd.verify-hash")
    result.data["prd"] = record.to_dict()
    result.data["files"] = record.files()
    result.data["expected"] = expected
    result.data["actual"] = actual
    result.data["match"] = bool(expected) and expected == actual
    result.data["baseline_valid"] = result.data["match"]

    if not expected:
        result.fail(
            "BASELINE_MISSING",
            f"PRD {record.id} 还没有批准基线（content_hash 为空）",
            object=record.id,
            suggestion="先走评审与人工批准：prd submit-review 后由需求负责人 prd approve",
            next_action=f"{FLOW_CORE} prd show --id {record.id}",
        )
        return result
    if expected != actual:
        result.fail(
            "HASH_MISMATCH",
            f"PRD {record.id} 的内容与批准基线不一致（正文或锁定字段被修改）",
            object=record.id,
            suggestion="已批准内容的变化必须走 CR 并重新批准；不要直接改 prd.md 或锁定字段",
            next_action=CR_HINT.format(prd_id=record.id),
        )
        return result

    result.notes.append(f"{record.id} 内容与批准基线一致（版本 {record.version}）")
    return result


# --------------------------------------------------------------------------
# 辅助
# --------------------------------------------------------------------------
def _actor(args: argparse.Namespace) -> str | None:
    return str(getattr(args, "actor", None) or "").strip() or None


def _confirm(args: argparse.Namespace) -> bool:
    return bool(getattr(args, "confirm", False))


def _gate(rules: Rules, command: str) -> dict[str, Any]:
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


def _require_editable(rules: Rules, record: metadata.PrdRecord) -> None:
    if metadata.is_editable(rules, record.status):
        return
    raise FlowError(
        "PRD_LOCKED",
        f"PRD {record.id} 已是 {record.status}，正文与锁定字段不可直接修改",
        object=record.id,
        suggestion="已批准内容必须走 CR 并重新批准（BR-012）；不要直接改 prd.md 或 metadata",
        next_actions=[CR_HINT.format(prd_id=record.id)],
    )


def _annotate_last_history(meta: dict[str, Any], note: str | None, *, key: str = "note") -> None:
    if not note:
        return
    history = meta.get("history")
    if isinstance(history, list) and history and isinstance(history[-1], dict):
        history[-1][key] = note


def _append_decision(meta: dict[str, Any], *, actor: str, role: str, decision: str,
                     object_id: str, content_hash: str | None = None,
                     version: int | None = None, note: str | None = None) -> None:
    """追加人工确认记录。hash/version/note 只在有值时出现，保持记录精简可读。"""
    entry = state.confirmation_entry(actor=actor, role=role, decision=decision,
                                     object=object_id, content_hash=content_hash,
                                     version=version)
    if note:
        entry["note"] = note
    state.append_confirmation(meta, entry)


def _render_review(record: metadata.PrdRecord, *, actor: str, note: str | None,
                   issues: list[dict[str, Any]]) -> str:
    blocking = [item for item in issues if item["severity"] == metadata.SEVERITY_ERROR]
    warnings = [item for item in issues if item["severity"] == metadata.SEVERITY_WARNING]
    lines = [
        f"# {record.id} 评审记录（{fsutil.now_iso()[:10]}）",
        "",
        f"- PRD：{record.id} {record.title}",
        f"- 版本：{record.version}",
        f"- 提交人：{actor}",
        f"- 提交时间：{fsutil.now_iso()}",
        f"- 目录：{record.directory.name}",
        "",
        "## 自审摘要",
        "",
        f"- 阻断问题：{len(blocking)}",
        f"- 提示项：{len(warnings)}",
    ]
    if issues:
        lines.append("")
        for item in issues:
            lines.append(f"- [{item['severity']}] {item['code']} {item['object']}：{item['message']}")
    else:
        lines.append("- 无问题")
    if note:
        lines += ["", "## 提交说明", "", note]
    lines += [
        "",
        "## 待办",
        "",
        "- 需求负责人评审后执行 `flow-core prd approve` 或提出修改意见。",
        "- 提交评审与批准是两个独立的人工确认点（SRS-FR-046 / SRS-FR-047）。",
        "",
    ]
    return "\n".join(lines)


def _suggest_actor(workspace: Workspace, rules: Rules) -> str:
    gate = (rules.data.get("gates") or {}).get("prd.approve")
    role = str(gate.get("role")) if isinstance(gate, dict) else state.ROLE_REQUIREMENT_OWNER
    team = state.load_team(workspace)
    if team is not None:
        for member in team.members:
            if member.has_role(role):
                return member.id
    return ACTOR_PLACEHOLDER


def _summary_line(record: metadata.PrdRecord) -> str:
    return (f"{record.id} {record.title}（{record.status}，模块 {record.module}"
            f"，场景 {len(record.scenarios)} 个，主需求 {'、'.join(record.source_reqs) or '未关联'}"
            + (f"，版本 {record.release}" if record.release else "") + "）")


def _next_actions(record: metadata.PrdRecord, rules: Rules) -> list[str]:
    status = record.status
    if status in metadata.draft_states(rules):
        return [
            f"{FLOW_CORE} prd validate --id {record.id}",
            f"{FLOW_CORE} prd submit-review --id {record.id} --actor <提交人> --confirm",
        ]
    if status == "IN_REVIEW":
        return [f"{FLOW_CORE} prd approve --id {record.id} --actor <需求负责人> --confirm"]
    if record.content_hash:
        return [f"{FLOW_CORE} prd verify-hash --id {record.id}"]
    return [f"{FLOW_CORE} prd show --id {record.id}"]


_HANDLERS = {
    "create": _create,
    "update": _update,
    "show": _show,
    "list": _list,
    "validate": _validate,
    "self-review": _self_review,
    "submit-review": _submit_review,
    "approve": _approve,
    "reject": _reject,
    "verify-hash": _verify_hash,
}
