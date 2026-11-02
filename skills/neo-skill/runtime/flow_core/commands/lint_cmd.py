"""`lint`：结构检查（ticket 09；SRS-FR-084、SRS §9）。

只读命令：**不写任何文件、不取锁、不改状态**。存在 error → 退出码 1 且信封
`success=false`；只有 warning → 退出码 0。JSON 的 `data` 里给 `findings`（每条
`{code, severity, topic, object, message, suggestion, path, next_action}`）与
`counts`（`error/warning/total`），人类输出给「对象 + 错误码 + 建议」。

**这是流程治理，不是安全边界**：lint 只能发现已经写坏的项目事实，不能阻止有人
直接改文件（SRS §9「lint/CI 尽可能发现，但 P0 不承诺实时阻止」）。
"""

from __future__ import annotations

import argparse

from .. import lint as lint_core
from ..output import Result
from ..rules import Rules
from ..workspace import Workspace

NAME = "lint"
HELP = "结构检查：必需产物、ID/目录、孤儿 Spec、引用、状态、基线与验收后同步（只读）"

FLOW_CORE = "flow-core"


def add_arguments(parser: argparse.ArgumentParser) -> None:
    from ..cli import global_options_parser

    parser.add_argument("--only", default=None, metavar="CODE,CODE",
                        help="只报告这些检查类（逗号分隔；默认全部）")
    parents = [global_options_parser()]
    subparsers = parser.add_subparsers(dest="lint_command", metavar="<子命令>", required=False)
    # `flow-core lint`（不带子命令）= 检查整个工作区
    subparsers.add_parser("check", parents=parents,
                          help="检查整个工作区（不带子命令时默认执行）")
    rules_parser = subparsers.add_parser(
        "rules", parents=parents,
        help="比对项目规则基线 .specify/rules/flow.yaml 与随包规则（版本与字段一致性）")
    rules_parser.add_argument("--only", default=None, metavar="CODE,CODE",
                              help="只报告这些检查类（逗号分隔；默认全部）")


def run(workspace: Workspace, rules: Rules, args: argparse.Namespace) -> Result:
    sub = getattr(args, "lint_command", None) or "check"
    only = lint_core.parse_only(getattr(args, "only", None))
    if sub == "rules":
        report = lint_core.run(workspace, rules, only=only or ["RULES_MISMATCH"])
        return _emit(report, command="lint.rules")
    report = lint_core.run(workspace, rules, only=only)
    return _emit(report, command="lint")


def _emit(report: lint_core.LintReport, *, command: str) -> Result:
    result = Result(command=command)
    result.data.update(report.to_dict())
    result.notes.append(
        f"检查完成：{report.counts.get('error', 0)} 个阻断问题、"
        f"{report.counts.get('warning', 0)} 个提示项（检查类：{'、'.join(report.checked) or '无'}）"
    )
    for item in report.findings:
        prefix = "ERROR" if item["severity"] == lint_core.SEVERITY_ERROR else "WARN"
        result.notes.append(
            f"[{prefix}] {item['code']} {item['object']}：{item['message']}")
        if item["suggestion"]:
            result.notes.append(f"    建议：{item['suggestion']}")
    if report.errors:
        for item in report.errors:
            result.fail(
                item["code"],
                item["message"],
                object=item["object"] or None,
                suggestion=item["suggestion"] or None,
                next_action=item["next_action"] or None,
            )
    else:
        result.next_actions.append(f"{FLOW_CORE} index status")
    return result
