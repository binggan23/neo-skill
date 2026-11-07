"""命令行入口：argparse 路由、全局参数、命令组注册、退出码。

**后续 ticket 如何加命令**（只需要两步，不要改本文件的已有逻辑）：

1. 新建 `flow_core/commands/<group>.py`，模块里提供四个属性：
   `NAME`（子命令名，如 `"req"`）、`HELP`（中文一行说明）、
   `add_arguments(parser)`（注册本组参数）、
   `run(workspace, rules, args) -> output.Result`。
   组内子命令自己再建 `add_subparsers`，`Result.command` 用点号命名（如 `req.create`）。
2. 把该模块追加到本文件下方的 `COMMAND_MODULES`。

约定：
- 命令只返回 `Result`；业务失败抛 `output.FlowError`，不要 `sys.exit`。
- 全局参数 `--root/--json/--actor/--confirm` 每个命令都可用，可写在子命令前后。
- 退出码：0 成功（含幂等无变化）、1 业务失败、2 参数用法错误。
"""

from __future__ import annotations

import argparse
import json
import sys
import traceback
from typing import Sequence

from . import __version__, redact
from .commands import acceptance as acceptance_command
from .commands import dev as dev_command
from .commands import doctor as doctor_command
from .commands import index_cmd as index_command
from .commands import init as init_command
from .commands import lint_cmd as lint_command
from .commands import prd as prd_command
from .commands import recover as recover_command
from .commands import req as req_command
from .commands import upstream as upstream_command
from .output import EXIT_FAIL, EXIT_OK, EXIT_USAGE, FlowError, Result, render_human
from .rules import load_rules
from .workspace import Workspace, resolve_root

# 命令组注册表：后续 ticket 在此追加自己的 commands 模块。
COMMAND_MODULES = (
    init_command,
    doctor_command,
    req_command,
    prd_command,
    dev_command,
    acceptance_command,
    index_command,
    recover_command,
    lint_command,
    upstream_command,
)

PROG = "flow-core"


def _global_options() -> argparse.ArgumentParser:
    """全局参数。默认值用 SUPPRESS，避免子命令覆盖写在子命令之前的同名参数。"""
    parser = argparse.ArgumentParser(add_help=False)
    parser.add_argument(
        "--root",
        metavar="PATH",
        default=argparse.SUPPRESS,
        help="项目根目录（缺省时从当前目录向上找含 .neo-skill/ 或 .specify/ 的目录）",
    )
    parser.add_argument(
        "--json",
        action="store_true",
        default=argparse.SUPPRESS,
        help="输出机器可读的 JSON 结果信封",
    )
    parser.add_argument(
        "--actor",
        metavar="ID",
        default=argparse.SUPPRESS,
        help="申报身份（人工决策命令必需，须与 00-management/team.md 的成员 id 一致）",
    )
    parser.add_argument(
        "--confirm",
        action="store_true",
        default=argparse.SUPPRESS,
        help="显式确认（人工决策命令必需）",
    )
    return parser


def global_options_parser() -> argparse.ArgumentParser:
    """给需要自己的子命令解析器的命令组复用全局参数（只追加，不改已有逻辑）。"""
    return _global_options()


def build_parser() -> argparse.ArgumentParser:
    globals_parser = _global_options()
    parser = argparse.ArgumentParser(
        prog=PROG,
        description="Neo Skill P0 确定性流程内核：项目骨架、状态机、基线、锁与结构化结果。",
        parents=[globals_parser],
    )
    parser.add_argument("--version", action="version", version=f"{PROG} {__version__}")
    subparsers = parser.add_subparsers(dest="command", metavar="<命令>")
    for module in COMMAND_MODULES:
        sub = subparsers.add_parser(
            module.NAME,
            help=module.HELP,
            description=module.HELP,
            parents=[globals_parser],
        )
        module.add_arguments(sub)
        sub.set_defaults(_handler=module.run, _command=module.NAME)
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(list(argv) if argv is not None else None)

    handler = getattr(args, "_handler", None)
    if handler is None:
        parser.print_help(sys.stderr)
        return EXIT_USAGE

    want_json = bool(getattr(args, "json", False))
    command = getattr(args, "_command", "flow-core")
    try:
        workspace = Workspace(resolve_root(getattr(args, "root", None)))
        rules = load_rules(workspace)
        result = handler(workspace, rules, args)
    except FlowError as exc:
        result = exc.to_result(command)
    except KeyboardInterrupt:  # pragma: no cover - 交互中断
        print("ERROR: 已中断", file=sys.stderr)
        return EXIT_FAIL
    except Exception as exc:  # 未预期异常也必须走信封，且不改业务状态
        print(redact.redact_text(traceback.format_exc()), end="", file=sys.stderr)
        result = Result(command=command)
        result.fail(
            "INTERNAL_ERROR",
            f"命令执行失败：{exc}",
            suggestion="这是内部错误，请保留输出并报告问题",
        )
    return _emit(result, want_json)


def _emit(result: Result, want_json: bool) -> int:
    if want_json:
        # SRS-NFR-007：打印前统一脱敏（凭据形态不得回显）。只改输出，不改文件与业务状态。
        payload = redact.redact_payload(result.to_dict())
        print(json.dumps(payload, ensure_ascii=False, indent=2))
    else:
        text = render_human(result)
        if text:
            print(redact.redact_text(text))
    return EXIT_OK if result.success else EXIT_FAIL
