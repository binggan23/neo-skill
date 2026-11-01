"""`init`：创建 P0 项目骨架、项目规则基线、模板与运行时元数据。

设计要点（共享设计 §3）：
- 骨架清单只在 `workspace.SKELETON` 声明一次；本模块只负责生成内容与写入。
- 只补齐缺失内容，绝不静默覆盖已有文件：内容不同 → `action: planned`，需要
  `--apply-conflicts` 才写。
- `AGENTS.md` 只维护 `<!-- neo-skill:begin -->` / `<!-- neo-skill:end -->` 之间。
- 全部校验（裁剪组合法性等）在第一次写入之前完成，失败不留副作用。
"""

from __future__ import annotations

import argparse
from typing import Any

from .. import fsutil, hashing, locks
from ..output import FlowError, Result
from ..rules import Rules, packaged_rules_file, packaged_templates_dir
from ..workspace import (
    CORE_ALIASES,
    COUNTERS_FILE,
    COUNTER_KEYS,
    INIT_STATE_FILE,
    PROJECT_LOCK_OBJECT,
    SKELETON,
    SkeletonEntry,
    TEMPLATE_NAMES,
    Workspace,
    is_not_prunable,
)

NAME = "init"
HELP = "初始化项目骨架、项目规则基线与模板（幂等，不覆盖已有内容）"

AGENTS_BEGIN = "<!-- neo-skill:begin -->"
AGENTS_END = "<!-- neo-skill:end -->"

_AGENTS_BLOCK = f"""{AGENTS_BEGIN}
## Neo Skill 工作流

- 确定性操作统一走 `flow-core`（`init`、`doctor` 以及后续命令组），不要手工维护 metadata。
- 需求、PRD、Spec、验证与验收产物按目录约定存放；ID 与 slug 创建后不再修改。
- 人工决策（排期、PRD 批准、验收通过）必须由本人显式申报身份并确认，AI 不得代确认。
- `03-requirements/backlog.md`、`02-definition/feature-map.md`、`04-prds/index.md`
  的受管理区块由 `flow-core index` 生成，区块外内容可自由编辑。
{AGENTS_END}
"""


def add_arguments(parser: argparse.ArgumentParser) -> None:
    parser.add_argument(
        "--prune",
        metavar="GROUPS",
        action="append",
        default=None,
        help="裁剪可省略的非核心目录组，逗号分隔（discovery,design,growth,releases,meetings,reports）",
    )
    parser.add_argument(
        "--apply-conflicts",
        action="store_true",
        help="按上一次变更计划覆盖内容不同的已有文件（默认只报告不写）",
    )


def run(workspace: Workspace, rules: Rules, args: argparse.Namespace) -> Result:
    result = Result(command=NAME)
    pruned = _resolve_prune(args.prune, rules, workspace)
    existing_state = _load_state(workspace)

    root = workspace.root
    if not root.exists():
        root.mkdir(parents=True, exist_ok=True)

    # 项目级写入串行化：并发 init 直接 LOCK_CONFLICT，而不是互相覆盖。
    with locks.object_lock(workspace, PROJECT_LOCK_OBJECT, command=NAME,
                           rules_version=rules.version):
        return _apply(workspace, rules, args, pruned, existing_state, result)


def _apply(workspace: Workspace, rules: Rules, args: argparse.Namespace, pruned: list[str],
           existing_state: dict[str, Any], result: Result) -> Result:
    plan: list[tuple[SkeletonEntry, str, str]] = []  # (entry, action, content)
    for entry in _skeleton_entries(pruned):
        action, content = _plan_entry(workspace, entry, args.apply_conflicts)
        plan.append((entry, action, content))

    template_plan = _plan_templates(workspace, args.apply_conflicts)
    meta_plan = _plan_meta(workspace)

    # ---- 写入（全部校验已完成）----
    recorded: dict[str, str] = {}
    for entry, action, content in plan:
        target = workspace.path(entry.path)
        if entry.kind == "dir":
            target.mkdir(parents=True, exist_ok=True)
            result.add(entry.path, "dir", "created" if action == "created" else "unchanged")
            continue
        if action == "unchanged":
            result.add(entry.path, entry.kind, "unchanged")
            recorded[entry.path] = hashing.sha256_text(fsutil.read_text(target))
            continue
        if action == "planned":
            result.add(entry.path, entry.kind, "planned")
            continue
        fsutil.atomic_write_text(target, content)
        result.add(entry.path, entry.kind, action)
        recorded[entry.path] = hashing.sha256_text(content)

    for rel, action, content in template_plan:
        target = workspace.path(rel)
        if action == "unchanged":
            result.add(rel, "template", "unchanged")
            recorded[rel] = hashing.sha256_text(fsutil.read_text(target))
            continue
        if action == "planned":
            result.add(rel, "template", "planned")
            continue
        fsutil.atomic_write_text(target, content)
        result.add(rel, "template", action)
        recorded[rel] = hashing.sha256_text(content)

    for rel, action, content in meta_plan:
        target = workspace.path(rel)
        if action == "unchanged":
            result.add(rel, "meta", "unchanged")
            continue
        fsutil.atomic_write_text(target, content)
        result.add(rel, "meta", action)

    # init-state 最后写：它记录本轮写入结果
    state_rel = INIT_STATE_FILE
    state_text = fsutil.dump_yaml(
        {
            "pruned": pruned,
            "rules_version": rules.version,
            "created_at": str(existing_state.get("created_at") or fsutil.now_iso()),
            "files": dict(sorted(recorded.items())),
        }
    )
    state_path = workspace.path(state_rel)
    existed = state_path.is_file()
    if existed and fsutil.read_text(state_path) == state_text:
        result.add(state_rel, "meta", "unchanged")
    else:
        fsutil.atomic_write_text(state_path, state_text)
        result.add(state_rel, "meta", "updated" if existed else "created")

    if any(a.action == "planned" for a in result.artifacts):
        result.next_actions.append("flow-core init --apply-conflicts")
        result.data["pending_conflicts"] = [
            a.path for a in result.artifacts if a.action == "planned"
        ]
    result.next_actions.append("flow-core doctor")
    return result


# --------------------------------------------------------------------------
# 校验与计划
# --------------------------------------------------------------------------
def _resolve_prune(raw: list[str] | None, rules: Rules, workspace: Workspace) -> list[str]:
    """解析并校验裁剪组；任何不合法都在写入前失败。"""
    requested: list[str] = []
    for item in raw or []:
        for part in str(item).split(","):
            name = part.strip()
            if name:
                requested.append(name)

    allowed = set(rules.prunable_groups)
    for name in requested:
        if name in allowed:
            continue
        if name in CORE_ALIASES or _is_core_path(name):
            raise FlowError(
                "NOT_PRUNABLE",
                f"`{name}` 属于不可裁剪的核心能力，必须保留",
                object=name,
                suggestion="核心项目管理、需求池、PRD 门禁、人工验收与 .specify/、.neo-skill/ 不可裁剪",
            )
        raise FlowError(
            "UNKNOWN_PRUNE_GROUP",
            f"未知的裁剪组 `{name}`",
            object=name,
            suggestion=f"可用裁剪组：{', '.join(sorted(allowed))}",
        )

    existing = _load_state(workspace)
    previous = [str(item) for item in (existing.get("pruned") or [])]
    merged = list(dict.fromkeys([*previous, *requested]))
    return [name for name in merged if name in allowed]


def _is_core_path(name: str) -> bool:
    return is_not_prunable(name)


def _skeleton_entries(pruned: list[str]) -> list[SkeletonEntry]:
    skip = set(pruned)
    return [entry for entry in _all_entries() if not entry.group or entry.group not in skip]


def _all_entries() -> tuple[SkeletonEntry, ...]:
    return SKELETON


def _plan_entry(workspace: Workspace, entry: SkeletonEntry,
                apply_conflicts: bool) -> tuple[str, str]:
    target = workspace.path(entry.path)
    if entry.kind == "dir":
        return ("unchanged" if target.is_dir() else "created", "")
    content = _render(workspace, entry)
    if not target.exists():
        return ("created", content)
    if not target.is_file():
        return ("planned", content)
    current = fsutil.read_text(target)
    if entry.kind == "agents":
        merged = _merge_agents_block(current)
        if merged == current:
            return ("unchanged", merged)
        return ("updated", merged)
    if current == content:
        return ("unchanged", content)
    if apply_conflicts:
        return ("updated", content)
    return ("planned", content)


def _plan_templates(workspace: Workspace, apply_conflicts: bool) -> list[tuple[str, str, str]]:
    plan: list[tuple[str, str, str]] = []
    source_dir = packaged_templates_dir()
    for name in TEMPLATE_NAMES:
        rel = f".specify/templates/{name}"
        target = workspace.path(rel)
        source = source_dir / name
        if not source.is_file():
            raise FlowError(
                "RESOURCE_MISSING",
                f"随包模板缺失：{source}",
                object=name,
                suggestion="重新安装 Neo Skill",
            )
        content = fsutil.read_text(source)
        if not target.exists():
            plan.append((rel, "created", content))
        elif target.is_file() and fsutil.read_text(target) == content:
            plan.append((rel, "unchanged", content))
        elif apply_conflicts:
            plan.append((rel, "updated", content))
        else:
            plan.append((rel, "planned", content))
    return plan


def _plan_meta(workspace: Workspace) -> list[tuple[str, str, str]]:
    """运行时元数据：counters.yaml 缺失时建零基线；init-state 由 `_apply` 最后写。"""
    plan: list[tuple[str, str, str]] = []
    if workspace.counters_file.is_file():
        plan.append((COUNTERS_FILE, "unchanged", ""))
    else:
        payload = {key: 0 for key in COUNTER_KEYS}
        plan.append((COUNTERS_FILE, "created", fsutil.dump_yaml(payload)))
    return plan


# --------------------------------------------------------------------------
# 内容生成
# --------------------------------------------------------------------------
def _render(workspace: Workspace, entry: SkeletonEntry) -> str:
    if entry.kind == "rules":
        return fsutil.read_text(packaged_rules_file())
    if entry.kind == "constitution":
        return fsutil.read_text(packaged_templates_dir() / "constitution.md")
    if entry.kind == "agents":
        return _AGENTS_BLOCK
    if entry.kind == "team":
        return _render_team()
    if entry.path == "README.md":
        return _render_readme()
    return _render_doc(entry)


def _render_doc(entry: SkeletonEntry) -> str:
    return (
        f"# {entry.title}\n\n"
        f"<!-- TODO: {entry.hint} -->\n\n"
        "## 待填写\n\n"
        "本文件由 `flow-core init` 创建，尚未填写业务内容。\n"
        "在补齐之前，请不要把它当作已确认的项目事实引用。\n"
    )


def _render_readme() -> str:
    return """# 项目卡片

<!-- TODO: 用一两句话说明这个项目解决什么问题、给谁用。 -->

## 当前阶段

<!-- TODO: 记录当前阶段与最近一次评审结论。 -->

## 当前版本

<!-- TODO: 记录当前交付版本，例如 v0.1.0。 -->

## 负责人

<!-- TODO: 记录项目负责人与需求负责人的成员标识（与 00-management/team.md 一致）。 -->

## 导航

- 项目管理：`00-management/`
- 需求池：`03-requirements/backlog.md`
- PRD：`04-prds/index.md`
- 技术方案：`06-specs/`
- 验收：`07-acceptance/`
- 项目规则与模板：`.specify/`

## 待填写

本文件由 `flow-core init` 创建，尚未填写业务内容。
"""


def _render_team() -> str:
    return """---
confirmed: false
confirmed_by: null
confirmed_at: null
members: []
---

# 团队与职责

<!-- TODO: 由项目负责人补齐成员与职责映射，并在确认后把 confirmed 改为 true。 -->

## 成员与职责

<!-- TODO: 每人一行：成员标识、姓名、职责。职责取值见 .specify/rules/flow.yaml 的 roles。 -->

| 成员标识 | 姓名 | 职责 |
| --- | --- | --- |
| <!-- TODO --> | <!-- TODO --> | <!-- TODO --> |

## 说明

- 至少需要一名 `requirement_owner`（负责排期、PRD 批准、验收结论）。
- 至少需要一名 `project_owner`（负责项目约束与团队配置确认）。
- 未确认（`confirmed: false`）或缺少有效成员时，项目视为「未配置完成」，
  不得排期或批准；工具只做职责匹配与审计记录，不做真实身份认证。
"""


def _merge_agents_block(current: str) -> str:
    """只替换受管理区块；无标记则追加。区块外内容原样保留。"""
    start = current.find(AGENTS_BEGIN)
    end = current.find(AGENTS_END)
    if start != -1 and end != -1 and end > start:
        head = current[:start]
        tail = current[end + len(AGENTS_END):]
        tail = tail.lstrip("\n")
        merged = f"{head}{_AGENTS_BLOCK}"
        if tail:
            merged += ("\n" if not merged.endswith("\n\n") else "") + tail
        return merged
    base = current.rstrip("\n")
    if base:
        return f"{base}\n\n{_AGENTS_BLOCK}"
    return _AGENTS_BLOCK


def _load_state(workspace: Workspace) -> dict[str, Any]:
    data = fsutil.load_yaml(workspace.init_state_file, default={})
    return data if isinstance(data, dict) else {}
