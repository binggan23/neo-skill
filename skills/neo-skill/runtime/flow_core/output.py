"""结果信封、人类可读输出与退出码（共享设计 §2.1、§2.2）。

所有命令只返回 `Result`；`cli.py` 负责按 `--json` 决定渲染方式与退出码。
命令内部遇到业务失败时抛 `FlowError`，由 `cli.py` 统一翻译成信封，
因此「失败无副作用」只需保证抛错前没有写入。
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from typing import Any

EXIT_OK = 0
EXIT_FAIL = 1
EXIT_USAGE = 2

ACTIONS = ("created", "updated", "unchanged", "planned", "staged")


@dataclass(frozen=True)
class Artifact:
    path: str
    kind: str
    action: str = "created"

    def __post_init__(self) -> None:
        if self.action not in ACTIONS:
            raise ValueError(f"未知 artifact action: {self.action}")

    def to_dict(self) -> dict[str, str]:
        return {"path": self.path, "kind": self.kind, "action": self.action}


@dataclass(frozen=True)
class Error:
    code: str
    message: str
    object: str | None = None
    suggestion: str | None = None

    def to_dict(self) -> dict[str, Any]:
        data: dict[str, Any] = {"code": self.code, "message": self.message}
        if self.object is not None:
            data["object"] = self.object
        if self.suggestion is not None:
            data["suggestion"] = self.suggestion
        return data


@dataclass
class Result:
    """命令结果。`checks` 供 `doctor` 等诊断命令附加结构化明细。"""

    command: str
    artifacts: list[Artifact] = field(default_factory=list)
    errors: list[Error] = field(default_factory=list)
    next_actions: list[str] = field(default_factory=list)
    checks: list[dict[str, Any]] = field(default_factory=list)
    data: dict[str, Any] = field(default_factory=dict)

    @property
    def success(self) -> bool:
        return not self.errors

    def add(self, path: str, kind: str, action: str = "created") -> None:
        self.artifacts.append(Artifact(path=path, kind=kind, action=action))

    def fail(self, code: str, message: str, *, object: str | None = None,
             suggestion: str | None = None, next_action: str | None = None) -> None:
        self.errors.append(Error(code=code, message=message, object=object, suggestion=suggestion))
        if next_action:
            self.next_actions.append(next_action)

    def to_dict(self) -> dict[str, Any]:
        payload: dict[str, Any] = {
            "success": self.success,
            "command": self.command,
            "artifacts": [item.to_dict() for item in self.artifacts],
            "errors": [item.to_dict() for item in self.errors],
            "next_actions": list(self.next_actions),
        }
        if self.checks:
            payload["checks"] = list(self.checks)
        if self.data:
            payload["data"] = dict(self.data)
        return payload

    def to_json(self) -> str:
        return json.dumps(self.to_dict(), ensure_ascii=False, indent=2)


class FlowError(Exception):
    """业务/校验失败。命令抛出它即表示失败且不得再写入。"""

    def __init__(
        self,
        code: str,
        message: str,
        *,
        object: str | None = None,
        suggestion: str | None = None,
        next_actions: list[str] | None = None,
        command: str | None = None,
    ) -> None:
        super().__init__(message)
        self.code = code
        self.message = message
        self.object = object
        self.suggestion = suggestion
        self.next_actions = list(next_actions or [])
        self.command = command

    def to_error(self) -> Error:
        return Error(code=self.code, message=self.message, object=self.object,
                     suggestion=self.suggestion)

    def to_result(self, command: str) -> Result:
        result = Result(command=self.command or command)
        result.errors.append(self.to_error())
        result.next_actions.extend(self.next_actions)
        return result


_ACTION_LABELS = {
    "created": "新建",
    "updated": "更新",
    "unchanged": "无变化",
    "planned": "待确认",
    "staged": "已暂存",
}


def render_human(result: Result) -> str:
    """人类可读输出：中文短句 + `OK:`/`ERROR:` 前缀，不打印结构化结构体。"""
    lines: list[str] = []
    if result.success:
        created = [a for a in result.artifacts if a.action == "created"]
        updated = [a for a in result.artifacts if a.action == "updated"]
        unchanged = [a for a in result.artifacts if a.action == "unchanged"]
        planned = [a for a in result.artifacts if a.action == "planned"]
        summary = f"OK: {result.command} 完成"
        details: list[str] = []
        if created:
            details.append(f"新建 {len(created)} 项")
        if updated:
            details.append(f"更新 {len(updated)} 项")
        if unchanged:
            details.append(f"无变化 {len(unchanged)} 项")
        if planned:
            details.append(f"待确认 {len(planned)} 项")
        if details:
            summary += "（" + "，".join(details) + "）"
        lines.append(summary)
        for artifact in created + updated:
            lines.append(f"  - {_ACTION_LABELS[artifact.action]} {artifact.path}")
        if planned:
            lines.append("  以下文件已存在且内容不同，未覆盖：")
            for artifact in planned:
                lines.append(f"  - {artifact.path}")
        for check in result.checks:
            status = check.get("status", "")
            label = {"ok": "正常", "warn": "注意", "fail": "缺失"}.get(status, status)
            lines.append(f"  - [{label}] {check.get('name')}：{check.get('detail', '')}")
            advice = check.get("suggestion")
            if advice and status != "ok":
                lines.append(f"      建议：{advice}")
    else:
        for error in result.errors:
            lines.append(f"ERROR: {error.code} {error.message}")
            if error.object:
                lines.append(f"  对象：{error.object}")
            if error.suggestion:
                lines.append(f"  建议：{error.suggestion}")
    if result.next_actions:
        lines.append("下一步：")
        for action in result.next_actions:
            lines.append(f"  - {action}")
    return "\n".join(lines)
