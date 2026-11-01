"""`req`：需求草稿、grilling 记录、去重提示、共识确认与人工排期（ticket 02）。

命令分两类：

- **草稿类**（`create`/`update`/`show`/`list`/`candidates`/`align`/`classify`/
  `set-priority`/`triage-prompt`）：不改状态，只在草稿期（`rules.data.req.draft_states`）
  写正文与 metadata 字段；其他状态 → `REQ_LOCKED`。
- **人工决策类**（`confirm`/`plan`/`unplan`/`defer`/`reject`）：严格按共享设计 §2.3 的
  固定顺序——解析对象 → `CONFIRMATION_REQUIRED` → 团队/身份/职责 → 状态跳转 → 取锁 →
  追加 history/confirmations → 原子写 → 释放锁。第 1–4 步都在写入之前，失败零副作用。

状态、跳转与职责全部读 `rules/flow.yaml`（`req` 状态机 + `gates`），本模块不硬编码状态值。
"""

from __future__ import annotations

import argparse
from pathlib import Path
from typing import Any

from .. import fsutil, ids, locks, requirements as reqs, state
from ..output import FlowError, Result
from ..rules import Rules
from ..workspace import Workspace

NAME = "req"
HELP = "需求草稿、grilling 共识确认、去重提示与人工排期"

SCHEDULE_QUESTION = "是否设置 {title} 需求排期"
FLOW_CORE = "flow-core"
ACTOR_PLACEHOLDER = "<需求负责人>"

# `req update --set` 允许的字段（title/slug 创建后不可改，状态必须走人工决策命令）
SETTABLE_FIELDS = ("module", "category", "source", "related")


def add_arguments(parser: argparse.ArgumentParser) -> None:
    # 组内子命令也要接受全局参数：run_cli 与文档都把 `--json`/`--actor` 写在子命令之后。
    from ..cli import global_options_parser

    parents = [global_options_parser()]
    subparsers = parser.add_subparsers(dest="req_command", metavar="<子命令>")

    create = subparsers.add_parser("create", parents=parents,
                                   help="创建 NEW 需求草稿并分配编号")
    create.add_argument("--title", required=True, help="需求标题（创建后不可修改）")
    create.add_argument("--module", required=True, help="所属模块（小写英文与连字符）")
    create.add_argument("--category", default=None,
                        help="分类：new-feature | optimization | defect | tech-debt")
    create.add_argument("--source", default=None, help="需求来源（访谈、反馈、数据、内部决策）")
    create.add_argument("--slug", default=None, help="稳定 slug（默认由标题推导）")

    update = subparsers.add_parser("update", parents=parents,
                                   help="保存/恢复草稿（仅 NEW/TRIAGED）")
    update.add_argument("--id", required=True, help="需求编号，如 REQ-001")
    update.add_argument("--set", action="append", default=None, metavar="FIELD=VALUE",
                        help="更新字段：module/category/source/related")
    update.add_argument("--body-file", default=None, help="用该 Markdown 文件替换需求正文")

    show = subparsers.add_parser("show", parents=parents, help="显示需求投影与正文")
    show.add_argument("--id", required=True)

    listing = subparsers.add_parser("list", parents=parents,
                                    help="列出需求（可按状态/模块过滤）")
    listing.add_argument("--status", default=None, help="按状态过滤，如 NEW、TRIAGED")
    listing.add_argument("--module", default=None, help="按模块过滤")

    candidates = subparsers.add_parser("candidates", parents=parents,
                                       help="提示重复/关联需求与功能现状（只读）")
    candidates.add_argument("--title", required=True, help="待比较的需求标题")
    candidates.add_argument("--module", default=None, help="限定模块")
    candidates.add_argument("--body-file", default=None, help="可选：用草稿正文参与比较")

    align = subparsers.add_parser("align", parents=parents,
                                  help="记录 grilling 设计树、假设、未决问题与共识摘要")
    align.add_argument("--id", required=True)
    align.add_argument("--frontier", action="append", default=None,
                       help="本轮 frontier 决策问题（可重复）")
    align.add_argument("--fact", action="append", default=None,
                       help="已查证的事实（可重复；只写从代码/文档/数据查到的东西）")
    align.add_argument("--assumption", action="append", default=None,
                       help="已记录的假设（可重复）")
    align.add_argument("--open-question", action="append", default=None,
                       help="未决问题（可重复）")
    align.add_argument("--decision", action="append", default=None,
                       help="本轮已由人确认的决策（可重复）")
    align.add_argument("--design-tree", default=None, metavar="FILE",
                       help="把设计树（Markdown 文件）记入 grilling，便于下一轮定位 frontier")
    align.add_argument("--consensus", default=None, help="共识摘要（一句话）")
    align.add_argument("--consensus-file", default=None, help="从文件读取共识摘要")

    confirm = subparsers.add_parser("confirm", parents=parents,
                                    help="人工确认 grilling 共识（NEW → TRIAGED）")
    confirm.add_argument("--id", required=True)

    prompt = subparsers.add_parser("triage-prompt", parents=parents,
                                   help="只读：给出「是否设置需求排期」的下一步")
    prompt.add_argument("--id", required=True)

    plan = subparsers.add_parser("plan", parents=parents,
                                 help="需求负责人确认排期（TRIAGED → PLANNED）")
    plan.add_argument("--id", required=True)
    plan.add_argument("--release", default=None, help="目标版本，形如 v0.1.0")
    plan.add_argument("--decline", action="store_true",
                      help="明确拒绝本次排期（保持 TRIAGED，只记录决定）")
    plan.add_argument("--reason", default=None, help="决定理由（拒绝排期、取消排期时记录）")

    unplan = subparsers.add_parser("unplan", parents=parents,
                                   help="取消排期（PLANNED → TRIAGED，仅 PRD 之前）")
    unplan.add_argument("--id", required=True)
    unplan.add_argument("--reason", default=None, help="取消排期理由")

    defer = subparsers.add_parser("defer", parents=parents, help="暂缓需求（→ DEFERRED）")
    defer.add_argument("--id", required=True)
    defer.add_argument("--reason", default=None)

    reject = subparsers.add_parser("reject", parents=parents, help="拒绝需求（→ REJECTED）")
    reject.add_argument("--id", required=True)
    reject.add_argument("--reason", default=None)

    priority = subparsers.add_parser("set-priority", parents=parents,
                                     help="记录优先级方案、取值与理由")
    priority.add_argument("--id", required=True)
    priority.add_argument("--scheme", required=True, help="RICE 或 MoSCoW")
    priority.add_argument("--score", required=True,
                          help="RICE 用整数；MoSCoW 用 must/should/could/wont")
    priority.add_argument("--reason", required=True, help="为什么是这个优先级")

    classify = subparsers.add_parser("classify", parents=parents, help="设置需求分类")
    classify.add_argument("--id", required=True)
    classify.add_argument("--category", required=True,
                          help="new-feature | optimization | defect | tech-debt")


def run(workspace: Workspace, rules: Rules, args: argparse.Namespace) -> Result:
    sub = getattr(args, "req_command", None)
    handler = _HANDLERS.get(sub)
    if handler is None:
        raise FlowError(
            "USAGE",
            "缺少 req 子命令",
            suggestion="可用子命令：" + "、".join(sorted(_HANDLERS)),
        )
    try:
        return handler(workspace, rules, args)
    except FlowError as exc:
        # 失败信封的 command 必须是 `req.<子命令>`，不是笼统的 `req`
        exc.command = f"req.{sub}"
        raise


# --------------------------------------------------------------------------
# 草稿类命令
# --------------------------------------------------------------------------
def _create(workspace: Workspace, rules: Rules, args: argparse.Namespace) -> Result:
    title = str(args.title or "").strip()
    if not title:
        raise FlowError("TITLE_REQUIRED", "需求标题不能为空（--title）")
    module = reqs.validate_module(args.module)
    category = reqs.validate_category(args.category)
    source = str(args.source or "").strip() or None
    if args.slug:
        slug = reqs.validate_slug(args.slug)
        _ensure_slug_free(workspace, slug)
    else:
        # 纯中文标题 slugify 后为空：回退到模块名（模块名本身就是合法 slug）
        slug = _unique_slug(workspace, reqs.slugify(title) or module)

    # 候选在分配编号/落盘之前算完：结果里不会出现自己，也不会因候选失败留下计数器副作用
    found = reqs.candidates(workspace, title=title, module=module)
    req_id = ids.allocate(workspace, "REQ", width=rules.id_width,
                          prefix=rules.id_prefixes.get("req", "REQ"))
    path = reqs.item_path(workspace, req_id, slug)
    meta = reqs.initial_metadata(req_id=req_id, slug=slug, title=title, module=module,
                                 category=category, source=source, rules=rules)
    body = reqs.render_body(workspace, req_id, title)
    fsutil.atomic_write_text(path, fsutil.render_front_matter(meta, body))

    record = reqs.ReqRecord(path=path, rel=workspace.rel(path), meta=meta, body=body)
    result = Result(command="req.create")
    result.add(record.rel, "req", "created")
    result.data["req"] = record.to_dict()
    result.data["candidates"] = found
    result.notes.append(f"{req_id} {title}（{record.status}，模块 {module}）")
    if found:
        result.notes.append("发现可能的重复/关联对象：" + "、".join(
            f"{item['kind']} {item['id']}" for item in found))
    result.next_actions.append(
        f"{FLOW_CORE} req align --id {req_id} --frontier \"<本轮只问一个 frontier 决策>\""
    )
    result.next_actions.append(f"{FLOW_CORE} req show --id {req_id}")
    return result


def _unique_slug(workspace: Workspace, base: str) -> str:
    """自动推导的 slug 冲突时追加序号；显式 `--slug` 冲突则报错（见 `_ensure_slug_free`）。"""
    if not _slug_taken(workspace, base):
        return base
    number = ids.peek_next(workspace, "REQ")
    candidate = f"{base}-{number:02d}"
    while _slug_taken(workspace, candidate):
        number += 1
        candidate = f"{base}-{number:02d}"
    return candidate


def _slug_taken(workspace: Workspace, slug: str) -> bool:
    directory = reqs.items_dir(workspace)
    if not directory.is_dir():
        return False
    return any(True for _ in directory.glob(f"*-{slug}.md"))


def _ensure_slug_free(workspace: Workspace, slug: str) -> None:
    directory = reqs.items_dir(workspace)
    if not directory.is_dir():
        return
    for path in sorted(directory.glob(f"*-{slug}.md")):
        raise FlowError(
            "SLUG_CONFLICT",
            f"slug `{slug}` 已被 {path.stem.rsplit('-', 1)[0]} 使用",
            object=slug,
            suggestion="slug 创建后不改、也不允许重复；换一个能区分的标题或显式 --slug",
        )


def _update(workspace: Workspace, rules: Rules, args: argparse.Namespace) -> Result:
    record = reqs.load(workspace, args.id, rules=rules)
    _require_draft(rules, record)

    assignments = _parse_sets(args.set, rules)
    body_file = args.body_file
    if not assignments and not body_file:
        raise FlowError(
            "NOTHING_TO_UPDATE",
            "没有要更新的内容：至少给一个 --set 或 --body-file",
            object=record.id,
            suggestion="例如 --set source=客户访谈，或 --body-file draft.md",
        )

    new_body = record.body
    if body_file:
        path = Path(body_file).expanduser()
        if not path.is_file():
            raise FlowError(
                "BODY_FILE_NOT_FOUND",
                f"正文文件不存在：{body_file}",
                object=record.id,
                suggestion="确认路径后重试；草稿未被修改",
            )
        new_body = fsutil.read_text(path)

    for key, value in assignments.items():
        record.meta[key] = value
    record.body = new_body
    record.meta["updated_at"] = fsutil.now_iso()
    reqs.save(workspace, record)

    result = Result(command="req.update")
    result.add(record.rel, "req", "updated")
    result.data["req"] = record.to_dict()
    result.next_actions.extend(_draft_next_actions(record))
    return result


def _parse_sets(raw: list[str] | None, rules: Rules) -> dict[str, Any]:
    assignments: dict[str, Any] = {}
    for item in raw or []:
        text = str(item)
        if "=" not in text:
            raise FlowError(
                "INVALID_SET",
                f"`--set {text}` 不是 key=value 形式",
                suggestion="例如 --set category=optimization",
            )
        key, value = (part.strip() for part in text.split("=", 1))
        if key not in SETTABLE_FIELDS:
            raise FlowError(
                "IMMUTABLE_FIELD",
                f"字段 `{key}` 不能在草稿里直接改",
                object=key,
                suggestion="可改字段：" + "、".join(SETTABLE_FIELDS)
                            + "；状态与排期必须走 req confirm / req plan 等人工决策命令",
            )
        if key == "module":
            assignments[key] = reqs.validate_module(value)
        elif key == "category":
            assignments[key] = reqs.validate_category(value)
        elif key == "related":
            assignments[key] = reqs.validate_related(value, rules)
        else:
            assignments[key] = value or None
    return assignments


def _show(workspace: Workspace, rules: Rules, args: argparse.Namespace) -> Result:
    record = reqs.load(workspace, args.id, rules=rules)
    result = Result(command="req.show")
    result.data["req"] = record.to_dict(include_body=True)
    readiness = reqs.prd_readiness(workspace, rules, record)
    result.data["prd_readiness"] = readiness
    result.notes.append(_summary_line(record))
    for reason in readiness["reasons"]:
        result.notes.append(f"未就绪：{reason}")
    result.next_actions.extend(_draft_next_actions(record))
    return result


def _list(workspace: Workspace, rules: Rules, args: argparse.Namespace) -> Result:
    status = str(args.status or "").strip().upper() or None
    module = str(args.module or "").strip() or None
    records = reqs.list_requirements(workspace)
    if status:
        if not rules.is_state("req", status):
            raise FlowError(
                "INVALID_STATE",
                f"req 不存在状态 {status}",
                object=status,
                suggestion="可用状态：" + "、".join(rules.states("req")),
            )
        records = [item for item in records if item.status == status]
    if module:
        records = [item for item in records if item.module == module]

    result = Result(command="req.list")
    result.data["requirements"] = [item.to_dict() for item in records]
    result.data["count"] = len(records)
    result.notes.extend(_summary_line(item) for item in records)
    if not records:
        result.next_actions.append(
            f"{FLOW_CORE} req create --title \"<标题>\" --module <模块>"
        )
    return result


def _candidates(workspace: Workspace, rules: Rules, args: argparse.Namespace) -> Result:
    title = str(args.title or "").strip()
    if not title:
        raise FlowError("TITLE_REQUIRED", "需要 --title 才能比对重复/关联关系")
    module = reqs.validate_module(args.module) if args.module else None
    if args.body_file:
        path = Path(args.body_file).expanduser()
        if not path.is_file():
            raise FlowError("BODY_FILE_NOT_FOUND", f"正文文件不存在：{args.body_file}")
        title = f"{title} {fsutil.read_text(path)}"

    found = reqs.candidates(workspace, title=title, module=module)
    result = Result(command="req.candidates")
    result.data["candidates"] = found
    result.data["count"] = len(found)
    for item in found:
        result.notes.append(f"{item['kind']} {item['id']}（{item['title']}，相似度 {item['score']}）")
    result.next_actions.append(
        "先与人工确认重复/关联关系再继续；flow-core 不会自动删除或合并需求"
    )
    return result


def _align(workspace: Workspace, rules: Rules, args: argparse.Namespace) -> Result:
    record = reqs.load(workspace, args.id, rules=rules)
    _require_draft(rules, record)

    frontier = _clean(args.frontier)
    facts = _clean(getattr(args, "fact", None))
    decisions = _clean(args.decision)
    assumptions = _clean(args.assumption)
    open_questions = _clean(args.open_question)
    design_tree = _read_optional(args.design_tree, record.id)
    consensus = str(args.consensus or "").strip() or None
    if args.consensus_file:
        path = Path(args.consensus_file).expanduser()
        if not path.is_file():
            raise FlowError(
                "BODY_FILE_NOT_FOUND",
                f"共识文件不存在：{args.consensus_file}",
                object=record.id,
                suggestion="确认路径后重试；需求未被修改",
            )
        consensus = fsutil.read_text(path).strip() or consensus
    if not (frontier or facts or decisions or assumptions or open_questions or consensus
            or design_tree):
        raise FlowError(
            "NOTHING_TO_UPDATE",
            "没有要记录的 grilling 内容",
            object=record.id,
            suggestion="至少给一个 --frontier / --fact / --decision / --assumption / "
                       "--open-question / --consensus / --design-tree",
        )

    grilling = reqs.grilling(record)
    grilling["frontier"] = grilling["frontier"] + frontier
    grilling["facts"] = grilling["facts"] + facts
    grilling["decisions"] = grilling["decisions"] + decisions
    grilling["assumptions"] = grilling["assumptions"] + assumptions
    grilling["open_questions"] = grilling["open_questions"] + open_questions
    if design_tree:
        grilling["design_tree"] = design_tree
    if consensus:
        grilling["consensus"] = consensus
    record.meta["grilling"] = grilling
    record.meta["updated_at"] = fsutil.now_iso()
    reqs.save(workspace, record)

    result = Result(command="req.align")
    result.add(record.rel, "req", "updated")
    result.data["req"] = record.to_dict()
    result.data["grilling"] = grilling
    result.next_actions.extend(_draft_next_actions(record))
    return result


def _classify(workspace: Workspace, rules: Rules, args: argparse.Namespace) -> Result:
    record = reqs.load(workspace, args.id, rules=rules)
    _require_draft(rules, record)
    category = reqs.validate_category(args.category)
    if record.category == category:
        result = Result(command="req.classify")
        result.add(record.rel, "req", "unchanged")
        result.data["req"] = record.to_dict()
        return result
    record.meta["category"] = category
    record.meta["updated_at"] = fsutil.now_iso()
    reqs.save(workspace, record)

    result = Result(command="req.classify")
    result.add(record.rel, "req", "updated")
    result.data["req"] = record.to_dict()
    return result


def _set_priority(workspace: Workspace, rules: Rules, args: argparse.Namespace) -> Result:
    record = reqs.load(workspace, args.id, rules=rules)
    _require_draft(rules, record)
    scheme = str(args.scheme or "").strip()
    if scheme not in reqs.PRIORITY_SCHEMES:
        raise FlowError(
            "INVALID_PRIORITY_SCHEME",
            f"未知的优先级方案 `{scheme}`",
            object=scheme,
            suggestion="可用方案：" + "、".join(reqs.PRIORITY_SCHEMES),
        )
    score = _priority_score(scheme, args.score)
    reason = str(args.reason or "").strip()
    if not reason:
        raise FlowError(
            "PRIORITY_REASON_REQUIRED",
            "必须记录优先级理由（--reason）",
            object=record.id,
            suggestion="说明影响范围、阻塞程度或成本依据",
        )
    record.meta["priority"] = {"scheme": scheme, "score": score, "reason": reason}
    record.meta["updated_at"] = fsutil.now_iso()
    reqs.save(workspace, record)

    result = Result(command="req.set-priority")
    result.add(record.rel, "req", "updated")
    result.data["req"] = record.to_dict()
    return result


def _priority_score(scheme: str, raw: Any) -> Any:
    text = str(raw or "").strip()
    if scheme == "RICE":
        try:
            value = int(text)
        except ValueError:
            raise FlowError(
                "INVALID_PRIORITY_SCORE",
                f"RICE 分数必须是整数，收到 `{text}`",
                object=text,
                suggestion="RICE = Reach × Impact × Confidence ÷ Effort，用整数表示",
            ) from None
        if value < 0:
            raise FlowError("INVALID_PRIORITY_SCORE", f"RICE 分数不能为负：{value}", object=text)
        return value
    lowered = text.lower()
    if lowered not in reqs.MOSCOW_VALUES:
        raise FlowError(
            "INVALID_PRIORITY_SCORE",
            f"MoSCoW 取值必须是 {'/'.join(reqs.MOSCOW_VALUES)}，收到 `{text}`",
            object=text,
            suggestion="例如 --score must",
        )
    return lowered


def _triage_prompt(workspace: Workspace, rules: Rules, args: argparse.Namespace) -> Result:
    record = reqs.load(workspace, args.id, rules=rules)
    result = Result(command="req.triage-prompt")
    result.data["req"] = record.to_dict()
    result.data["status"] = record.status
    prompt, actions = _schedule_question(workspace, rules, record)
    result.data["prompt"] = prompt
    result.notes.append(_summary_line(record))
    if prompt:
        result.notes.append(prompt)
        result.next_actions.append(prompt)
    result.next_actions.extend(actions)
    return result


# --------------------------------------------------------------------------
# 人工决策类命令
# --------------------------------------------------------------------------
def _confirm(workspace: Workspace, rules: Rules, args: argparse.Namespace) -> Result:
    record = reqs.load(workspace, args.id, rules=rules)
    actor = state.require_confirmation(getattr(args, "actor", None),
                                       bool(getattr(args, "confirm", False)),
                                       command="req.confirm")
    gate = _gate(rules, "req.confirm")
    team = state.require_team(workspace, object=record.id)
    state.require_member(team, actor, role=gate["role"], object=record.id)
    to_state = str(gate.get("to") or "TRIAGED")
    state.check_transition(rules, "req", record.status, to_state, object=record.id)

    prompt, next_actions = _schedule_question(workspace, rules, record, assume_status=to_state)

    with locks.object_lock(workspace, record.id, command="req.confirm",
                           rules_version=rules.version):
        meta = record.meta
        state.apply_transition(meta, machine="req", to_state=to_state, actor=actor,
                               action="req.confirm", rules=rules)
        grilling = reqs.grilling(record)
        grilling["consensus_confirmed"] = True
        meta["grilling"] = grilling
        meta["confirmed_at"] = fsutil.now_iso()
        _append_decision(meta, actor=actor, role=str(gate["role"]), decision="approved",
                         object_id=record.id)
        reqs.save(workspace, record)

    result = Result(command="req.confirm")
    result.add(record.rel, "req", "updated")
    result.data["req"] = record.to_dict()
    result.data["prompt"] = prompt
    if prompt:
        # 人类输出也把这句话读出来：下一步先问排期，再给确切命令
        result.notes.append(prompt)
        result.next_actions.append(prompt)
    result.next_actions.extend(next_actions)
    return result


def _plan(workspace: Workspace, rules: Rules, args: argparse.Namespace) -> Result:
    record = reqs.load(workspace, args.id, rules=rules)
    actor = state.require_confirmation(getattr(args, "actor", None),
                                       bool(getattr(args, "confirm", False)),
                                       command="req.plan")
    gate = _gate(rules, "req.plan")
    team = state.require_team(workspace, object=record.id)
    state.require_member(team, actor, role=gate["role"], object=record.id)

    if args.decline:
        return _decline(workspace, rules, record, args, actor, gate)

    to_state = str(gate.get("to") or "PLANNED")
    # 状态校验先于「缺版本」：状态不对时不该继续追问版本
    state.check_transition(rules, "req", record.status, to_state, object=record.id)
    if not args.release:
        raise FlowError(
            "RELEASE_REQUIRED",
            f"需求 {record.id} 排期还需要确认目标版本（--release）",
            object=record.id,
            suggestion="询问需求负责人目标版本（形如 v0.1.0）后重新执行；状态保持不变",
            next_actions=[_plan_command(record.id, actor, "<版本，如 v0.1.0>")],
        )
    release = reqs.validate_release(args.release)

    missing = reqs.missing_sections(record.body)
    if missing:
        raise FlowError(
            "INCOMPLETE_REQUIREMENT",
            f"需求 {record.id} 关键信息不完整：{'、'.join(missing)}",
            object=record.id,
            suggestion="先补齐正文小节（或用 req update --body-file 恢复草稿），再排期",
            next_actions=[f"{FLOW_CORE} req update --id {record.id} --body-file <草稿文件>"],
        )

    reason = str(args.reason or "").strip() or None
    with locks.object_lock(workspace, record.id, command="req.plan",
                           rules_version=rules.version):
        meta = record.meta
        state.apply_transition(meta, machine="req", to_state=to_state, actor=actor,
                               action="req.plan", rules=rules)
        _annotate_last_history(meta, reason)
        meta["release"] = release
        meta["planned_by"] = actor
        meta["planned_at"] = fsutil.now_iso()
        _append_decision(meta, actor=actor, role=str(gate["role"]), decision="approved",
                         object_id=record.id, release=release, reason=reason)
        reqs.save(workspace, record)

    result = Result(command="req.plan")
    result.add(record.rel, "req", "updated")
    result.data["req"] = record.to_dict()
    result.data["release"] = release
    result.next_actions.append(
        f"{FLOW_CORE} prd create --req {record.id} --release {release}"
        "  # 需求进入 PRD 阶段（prd 命令组在 ticket 03 提供）"
    )
    return result


def _decline(workspace: Workspace, rules: Rules, record: reqs.ReqRecord,
             args: argparse.Namespace, actor: str, gate: dict[str, Any]) -> Result:
    from_state = str(gate.get("from") or "")
    if not from_state or record.status != from_state:
        raise FlowError(
            "INVALID_TRANSITION",
            f"需求 {record.id} 当前是 {record.status}，不在可询问排期的 {from_state or 'TRIAGED'} 状态",
            object=record.id,
            suggestion="只有等待排期询问的需求才会被问「是否设置需求排期」",
        )
    reason = str(args.reason or "").strip() or None
    with locks.object_lock(workspace, record.id, command="req.plan",
                           rules_version=rules.version):
        meta = record.meta
        _append_decision(meta, actor=actor, role=str(gate["role"]), decision="declined",
                         object_id=record.id, reason=reason)
        reqs.save(workspace, record)

    result = Result(command="req.plan")
    result.add(record.rel, "req", "updated")
    result.data["req"] = record.to_dict()
    result.data["declined"] = True
    result.next_actions.append(
        f"需求 {record.id} 保持 TRIAGED，未进入排期；需要时可再次询问是否设置需求排期"
    )
    return result


def _unplan(workspace: Workspace, rules: Rules, args: argparse.Namespace) -> Result:
    record = reqs.load(workspace, args.id, rules=rules)
    actor = state.require_confirmation(getattr(args, "actor", None),
                                       bool(getattr(args, "confirm", False)),
                                       command="req.unplan")
    gate = _gate(rules, "req.unplan")
    team = state.require_team(workspace, object=record.id)
    state.require_member(team, actor, role=gate["role"], object=record.id)
    if record.status in reqs.prd_states(rules):
        raise FlowError(
            "REQ_LOCKED",
            f"需求 {record.id} 已进入 {record.status}，不能再取消或调整排期",
            object=record.id,
            suggestion="进入 PRD 后必须通过 PRD 变更（CR）或重新评审处理，不得无记录改期",
            next_actions=[f"{FLOW_CORE} prd cr --prd {record.meta.get('prd') or 'PRD-xxx'} "
                          "--reason \"<改期原因>\""],
        )
    to_state = str(gate.get("to") or "TRIAGED")
    state.check_transition(rules, "req", record.status, to_state, object=record.id)

    reason = str(args.reason or "").strip() or None
    with locks.object_lock(workspace, record.id, command="req.unplan",
                           rules_version=rules.version):
        meta = record.meta
        state.apply_transition(meta, machine="req", to_state=to_state, actor=actor,
                               action="req.unplan", rules=rules)
        _annotate_last_history(meta, reason)
        meta["release"] = None
        meta["planned_by"] = None
        meta["planned_at"] = None
        _append_decision(meta, actor=actor, role=str(gate["role"]), decision="approved",
                         object_id=record.id, reason=reason)
        reqs.save(workspace, record)

    result = Result(command="req.unplan")
    result.add(record.rel, "req", "updated")
    result.data["req"] = record.to_dict()
    result.next_actions.append(_plan_command(record.id, actor, "<版本，如 v0.1.0>"))
    return result


def _side_transition(workspace: Workspace, rules: Rules, args: argparse.Namespace,
                     command: str) -> Result:
    """`req.defer` / `req.reject` 共用：人工决策 + 规则驱动的旁支跳转 + 理由记录。"""
    record = reqs.load(workspace, args.id, rules=rules)
    actor = state.require_confirmation(getattr(args, "actor", None),
                                       bool(getattr(args, "confirm", False)),
                                       command=command)
    gate = _gate(rules, command)
    team = state.require_team(workspace, object=record.id)
    state.require_member(team, actor, role=gate["role"], object=record.id)
    to_state = str(gate.get("to") or "")
    state.check_transition(rules, "req", record.status, to_state, object=record.id)

    reason = str(args.reason or "").strip() or None
    with locks.object_lock(workspace, record.id, command=command,
                           rules_version=rules.version):
        meta = record.meta
        state.apply_transition(meta, machine="req", to_state=to_state, actor=actor,
                               action=command, rules=rules)
        _annotate_last_history(meta, reason)
        meta["status_reason"] = reason
        _append_decision(meta, actor=actor, role=str(gate["role"]), decision="approved",
                         object_id=record.id, reason=reason)
        reqs.save(workspace, record)

    result = Result(command=command)
    result.add(record.rel, "req", "updated")
    result.data["req"] = record.to_dict()
    return result


def _defer(workspace: Workspace, rules: Rules, args: argparse.Namespace) -> Result:
    return _side_transition(workspace, rules, args, "req.defer")


def _reject(workspace: Workspace, rules: Rules, args: argparse.Namespace) -> Result:
    return _side_transition(workspace, rules, args, "req.reject")


# --------------------------------------------------------------------------
# 辅助
# --------------------------------------------------------------------------
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


def _require_draft(rules: Rules, record: reqs.ReqRecord) -> None:
    if reqs.is_draft(rules, record.status):
        return
    raise FlowError(
        "REQ_LOCKED",
        f"需求 {record.id} 已是 {record.status}，草稿内容不可直接修改",
        object=record.id,
        suggestion="NEW/TRIAGED 才可直接改；排期后必须走 req unplan 或 PRD 变更（CR）",
    )


def _read_optional(raw: str | None, object_id: str) -> str | None:
    if not raw:
        return None
    path = Path(raw).expanduser()
    if not path.is_file():
        raise FlowError(
            "BODY_FILE_NOT_FOUND",
            f"文件不存在：{raw}",
            object=object_id,
            suggestion="确认路径后重试；需求未被修改",
        )
    return fsutil.read_text(path).strip() or None


def _clean(values: list[str] | None) -> list[str]:
    return [str(item).strip() for item in (values or []) if str(item).strip()]


def _append_decision(meta: dict[str, Any], *, actor: str, role: str, decision: str,
                     object_id: str, release: str | None = None,
                     reason: str | None = None) -> None:
    """追加人工确认记录。`release`/`reason` 只在有值时出现，保持记录精简可读。"""
    entry = state.confirmation_entry(actor=actor, role=role, decision=decision,
                                     object=object_id)
    if release:
        entry["release"] = release
    if reason:
        entry["reason"] = reason
    state.append_confirmation(meta, entry)


def _annotate_last_history(meta: dict[str, Any], reason: str | None) -> None:
    if not reason:
        return
    history = meta.get("history")
    if isinstance(history, list) and history and isinstance(history[-1], dict):
        history[-1]["reason"] = reason


def _schedule_question(workspace: Workspace, rules: Rules, record: reqs.ReqRecord,
                       *, assume_status: str | None = None) -> tuple[str | None, list[str]]:
    """「是否设置 xxx 需求排期」：只有等待排期询问的状态才给文案与确切命令。"""
    status = assume_status or record.status
    gates = rules.data.get("gates") or {}
    gate = gates.get("req.plan") if isinstance(gates.get("req.plan"), dict) else {}
    from_state = str(gate.get("from") or "TRIAGED")
    if status != from_state:
        return None, []

    actor = _suggest_actor(workspace, rules, str(gate.get("role") or state.ROLE_REQUIREMENT_OWNER))
    prompt = SCHEDULE_QUESTION.format(title=record.title)
    actions = [
        _plan_command(record.id, actor, "<版本，如 v0.1.0>"),
        f"{FLOW_CORE} req plan --id {record.id} --actor {actor} --confirm --decline"
        " --reason \"<暂不排期的原因>\"",
    ]
    return prompt, actions


def _plan_command(req_id: str, actor: str, release: str) -> str:
    return f"{FLOW_CORE} req plan --id {req_id} --actor {actor} --confirm --release {release}"


def _suggest_actor(workspace: Workspace, rules: Rules, role: str) -> str:
    team = state.load_team(workspace)
    if team is not None:
        for member in team.members:
            if member.has_role(role):
                return member.id
    return ACTOR_PLACEHOLDER


def _summary_line(record: reqs.ReqRecord) -> str:
    return (f"{record.id} {record.title}（{record.status}，模块 {record.module}"
            f"，分类 {reqs.CATEGORY_LABELS.get(record.category, record.category)}"
            + (f"，版本 {record.release}" if record.release else "") + "）")


def _draft_next_actions(record: reqs.ReqRecord) -> list[str]:
    if record.status == "NEW":
        return [
            f"{FLOW_CORE} req align --id {record.id} --frontier \"<本轮只问一个 frontier 决策>\"",
            f"{FLOW_CORE} req confirm --id {record.id} --actor <确认人> --confirm",
        ]
    return [f"{FLOW_CORE} req triage-prompt --id {record.id}"]


_HANDLERS = {
    "create": _create,
    "update": _update,
    "show": _show,
    "list": _list,
    "candidates": _candidates,
    "align": _align,
    "classify": _classify,
    "set-priority": _set_priority,
    "triage-prompt": _triage_prompt,
    "confirm": _confirm,
    "plan": _plan,
    "unplan": _unplan,
    "defer": _defer,
    "reject": _reject,
}
