"""`index`：生成受管理索引区块（共享设计 §9、SRS-FR-085、HLD §9）。

只写三个文件：`03-requirements/backlog.md`、`02-definition/feature-map.md`、
`04-prds/index.md`。区块外的人工说明原样保留；区块内被人工改过
（`fingerprint` 与 body 的 sha256 不一致）→ `MANAGED_BLOCK_MODIFIED`，
需要 `--confirm`（并申报 `--actor`）才覆盖。

索引失败不改任何业务状态：本模块不写 REQ/PRD metadata，也不改验收结论。
"""

from __future__ import annotations

import argparse

from .. import index, state
from ..output import FlowError, Result
from ..rules import Rules
from ..workspace import Workspace

NAME = "index"
HELP = "生成 backlog / feature-map / PRD 索引的受管理区块（保留区块外内容）"

FLOW_CORE = "flow-core"
ACTOR_PLACEHOLDER = "<成员 id>"


def add_arguments(parser: argparse.ArgumentParser) -> None:
    from ..cli import global_options_parser

    parents = [global_options_parser()]
    subparsers = parser.add_subparsers(dest="index_command", metavar="<子命令>",
                                       required=False)
    generate = subparsers.add_parser("generate", parents=parents,
                                    help="生成并写入三个受管理区块（幂等；不带子命令时默认执行）")
    generate.add_argument("--path", action="append", default=None, metavar="REL",
                          help="只生成指定文件（可重复；默认三个都生成）")
    subparsers.add_parser("status", parents=parents,
                          help="报告索引区块是否存在、是否被人工修改、是否过期（只读）")


def run(workspace: Workspace, rules: Rules, args: argparse.Namespace) -> Result:
    # `flow-core index` 不带子命令时等同于 `index generate`（SRS-FR-085 的形状）
    sub = getattr(args, "index_command", None) or "generate"
    handler = _HANDLERS.get(sub)
    if handler is None:
        raise FlowError(
            "USAGE",
            "缺少 index 子命令",
            suggestion="可用子命令：" + "、".join(sorted(_HANDLERS)),
        )
    try:
        return handler(workspace, rules, args)
    except FlowError as exc:
        if not exc.command:
            exc.command = f"index.{sub}"
        raise


def _generate(workspace: Workspace, rules: Rules, args: argparse.Namespace) -> Result:
    confirm = bool(getattr(args, "confirm", False))
    actor = str(getattr(args, "actor", None) or "").strip() or None
    if confirm:
        # 覆盖受管理区块是人工决定：申报的身份必须在团队配置里（未配置团队时只记录）
        team = state.load_team(workspace)
        if team is not None and team.members and actor is None:
            raise FlowError(
                "CONFIRMATION_REQUIRED",
                "覆盖被人工修改过的受管理区块必须申报身份（缺少 --actor）",
                suggestion=f"补 --actor {ACTOR_PLACEHOLDER} 后重试",
            )
        if team is not None and team.members and actor is not None:
            state.require_member(team, actor)

    paths = list(getattr(args, "path", None) or [])
    planned = index.plan(workspace, rules, paths=paths or None)
    index.require_confirm(planned, confirm=confirm)

    result = Result(command="index.generate")
    for item in planned.plans:
        if item.changed:
            from .. import fsutil

            fsutil.atomic_write_text(workspace.path(item.path), item.text)
            result.add(item.path, "index", "updated" if item.existed else "created")
        else:
            result.add(item.path, "index", "unchanged")
    result.data["index"] = planned.to_dict()
    result.data["by"] = actor
    result.data["confirmed"] = confirm
    if planned.modified:
        result.notes.append("已按 --confirm 覆盖被人工修改过的受管理区块："
                            + "、".join(planned.modified))
    result.notes.append(
        f"受管理区块已生成（新写入 {len(planned.changed)} 个文件），区块外内容保持不变")
    result.next_actions.append(f"{FLOW_CORE} index status")
    return result


def _status(workspace: Workspace, rules: Rules, args: argparse.Namespace) -> Result:
    data = index.evaluate(workspace, rules)
    result = Result(command="index.status")
    result.data.update(data)
    for item in data["files"]:
        label = {"ok": "一致", "stale": "过期", "missing": "缺失",
                 "no_block": "无受管理区块", "modified": "被人工修改"}.get(
                     item["status"], item["status"])
        result.notes.append(f"{item['path']}：{label}")
    if not data["ok"]:
        result.notes.append("索引与当前事实不一致：" + "、".join(data["problems"]))
        result.next_actions.append(
            f"{FLOW_CORE} index generate --confirm --actor {ACTOR_PLACEHOLDER}"
            if any(problem.startswith("MANAGED_BLOCK_MODIFIED") for problem in data["problems"])
            else f"{FLOW_CORE} index generate")
    return result


_HANDLERS = {
    "generate": _generate,
    "status": _status,
}
