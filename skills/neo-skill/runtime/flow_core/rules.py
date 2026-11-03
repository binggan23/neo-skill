"""规则加载：项目基线优先，回退随包资源（共享设计 §5、§8）。

`runtime/rules/flow.yaml` 是声明式规则的发布源；`init` 把它复制成项目基线
`.specify/rules/flow.yaml`。运行时先读项目基线，缺失才用随包规则，
因此升级只补齐缺失内容，不会静默改写项目已确认的规则。
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any

from . import fsutil
from .output import FlowError
from .workspace import Workspace

RUNTIME_DIR = Path(__file__).resolve().parents[1]
PACKAGED_RULES_FILE = RUNTIME_DIR / "rules" / "flow.yaml"
PACKAGED_TEMPLATES_DIR = RUNTIME_DIR / "templates"


def packaged_rules_file() -> Path:
    return PACKAGED_RULES_FILE


def packaged_templates_dir() -> Path:
    return PACKAGED_TEMPLATES_DIR


def runtime_dir() -> Path:
    """`runtime/` 目录：doctor 用它报告资源位置。"""
    return RUNTIME_DIR


@dataclass(frozen=True)
class Rules:
    """规则视图。所有状态机/裁剪/编号判定都从这里取值，代码里不硬编码状态。"""

    data: dict[str, Any]
    source: Path
    origin: str  # "project" | "packaged"

    @property
    def version(self) -> int:
        return int(self.data.get("version", 1))

    # ---- 状态机 ----
    def machine(self, name: str) -> dict[str, Any]:
        section = self.data.get(name)
        if not isinstance(section, dict):
            raise FlowError("RULES_INVALID", f"规则缺少 `{name}` 状态机定义", object=name)
        return section

    def initial_state(self, name: str) -> str:
        return str(self.machine(name)["initial"])

    def states(self, name: str) -> list[str]:
        return list(self.machine(name)["states"])

    def transitions(self, name: str) -> dict[str, list[str]]:
        raw = self.machine(name)["transitions"]
        return {str(key): list(value or []) for key, value in raw.items()}

    def can_transition(self, name: str, from_state: str, to_state: str) -> bool:
        return to_state in self.transitions(name).get(from_state, [])

    def is_state(self, name: str, state: str) -> bool:
        return state in self.states(name)

    # ---- 编号 ----
    @property
    def id_prefixes(self) -> dict[str, str]:
        return dict(self.data.get("ids", {}).get("prefixes", {}))

    @property
    def id_width(self) -> int:
        return int(self.data.get("ids", {}).get("width", 3))

    # ---- 角色与限制 ----
    @property
    def roles(self) -> list[str]:
        return list(self.data.get("roles", []))

    def limit(self, name: str, default: int) -> int:
        return int(self.data.get("limits", {}).get(name, default))

    # ---- 人工决策门禁 ----
    def gate(self, command: str) -> dict[str, Any]:
        """取一条人工决策门禁定义（`gates.<command>`）。

        缺失或形状不合法直接报 `RULES_INVALID`，避免把「规则没声明」
        误当成「无需校验」而静默放行人工决策。
        """
        gates = self.data.get("gates")
        gate = gates.get(command) if isinstance(gates, dict) else None
        if not isinstance(gate, dict) or "role" not in gate:
            raise FlowError(
                "RULES_INVALID",
                f"规则缺少门禁定义 `{command}`",
                object=command,
                suggestion="在 .specify/rules/flow.yaml 的 gates 中声明 role（以及需要时的 from/to）",
            )
        return gate

    # ---- 裁剪 ----
    @property
    def prunable_groups(self) -> list[str]:
        directories = self.data.get("directories", {})
        return [str(group) for group in (directories.get("prunable") or [])]

    @property
    def not_prunable(self) -> list[str]:
        directories = self.data.get("directories", {})
        return list(directories.get("not_prunable") or [])


def load_rules(workspace: Workspace | None = None) -> Rules:
    """项目基线优先；缺失时回退随包规则。"""
    if workspace is not None and workspace.project_rules_file.is_file():
        return Rules(data=_read(workspace.project_rules_file),
                     source=workspace.project_rules_file, origin="project")
    if PACKAGED_RULES_FILE.is_file():
        return Rules(data=_read(PACKAGED_RULES_FILE), source=PACKAGED_RULES_FILE,
                     origin="packaged")
    raise FlowError(
        "RULES_MISSING",
        "找不到规则文件（.specify/rules/flow.yaml 与随包 rules/flow.yaml 均缺失）",
        suggestion="重新安装 Neo Skill，或运行 flow-core init 生成项目规则基线",
    )


def _read(path: Path) -> dict[str, Any]:
    data = fsutil.read_yaml(path)
    if not isinstance(data, dict) or "version" not in data:
        raise FlowError("RULES_INVALID", f"规则文件格式不合法：{path}", object=str(path))
    return data
