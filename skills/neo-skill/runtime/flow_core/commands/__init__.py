"""命令组包。

每个模块导出 `NAME` / `HELP` / `add_arguments(parser)` / `run(workspace, rules, args)`，
并在 `flow_core/cli.py` 的 `COMMAND_MODULES` 里注册一行。
"""

from __future__ import annotations

__all__: list[str] = []
