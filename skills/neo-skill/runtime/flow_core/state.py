"""状态机校验、人工确认与 team.md 职责核对（共享设计 §2.3、§5）。

状态定义只来自 `rules/flow.yaml`；本模块只做「读规则 → 校验 → 追加记录」，
不硬编码任何状态值或跳转关系。

人工决策命令的固定处理顺序（共享设计 §2.3，ticket 02–08 必须照此顺序实现）：

1. 解析对象（不存在 → 对应的 NOT_FOUND 错误）；
2. `require_confirmation` → `require_member`/`require_role_for`（团队/身份/职责，失败不改状态）；
3. `check_transition`（`INVALID_TRANSITION`）；
4. `locks.object_lock`（`LOCK_CONFLICT`）；
5. `apply_transition` 追加 history/confirmations（**只改内存**）；
6. `fsutil.atomic_write_yaml` 原子写；
7. 退出 `with` 块释放锁。

第 1–3 步都必须在拿到锁并写入之前完成，这样「失败无副作用」只需保证抛错前没有写。
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from . import fsutil
from .output import FlowError
from .rules import Rules
from .workspace import Workspace

ROLE_REQUIREMENT_OWNER = "requirement_owner"
ROLE_PROJECT_OWNER = "project_owner"
ROLE_DEVELOPER = "developer"
ROLE_TESTER = "tester"
ROLE_STAKEHOLDER = "stakeholder"


@dataclass(frozen=True)
class Member:
    id: str
    name: str = ""
    roles: tuple[str, ...] = ()

    def has_role(self, role: str) -> bool:
        return role in self.roles

    def to_dict(self) -> dict[str, Any]:
        return {"id": self.id, "name": self.name, "roles": list(self.roles)}


@dataclass(frozen=True)
class Team:
    """`00-management/team.md` 的 front matter 投影。"""

    confirmed: bool = False
    members: tuple[Member, ...] = ()
    extra: dict[str, Any] = field(default_factory=dict)

    def member(self, actor: str) -> Member | None:
        for item in self.members:
            if item.id == actor:
                return item
        return None

    def has_role(self, role: str) -> bool:
        return any(item.has_role(role) for item in self.members)

    def to_dict(self) -> dict[str, Any]:
        return {"confirmed": self.confirmed, "members": [m.to_dict() for m in self.members]}


def load_team(workspace: Workspace) -> Team | None:
    """读取团队配置；文件不存在返回 None。"""
    path = workspace.team_file
    if not path.is_file():
        return None
    metadata, _body = fsutil.parse_front_matter(fsutil.read_text(path))
    members: list[Member] = []
    for raw in metadata.get("members") or []:
        if not isinstance(raw, dict):
            continue
        members.append(
            Member(
                id=str(raw.get("id", "")).strip(),
                name=str(raw.get("name", "") or ""),
                roles=tuple(str(role) for role in (raw.get("roles") or [])),
            )
        )
    return Team(confirmed=bool(metadata.get("confirmed", False)),
                members=tuple(members),
                extra={k: v for k, v in metadata.items() if k not in {"confirmed", "members"}})


def require_team(workspace: Workspace, *, object: str | None = None,
                 require_confirmed: bool = True) -> Team:
    """人工决策前的团队配置校验：文件存在、有成员、（默认）已由项目负责人确认。

    与 `require_member` 的区别：本函数负责「团队配置是否可用」这一层，
    `require_member` 负责「这个 actor 能不能做这个决定」。两者都不改变状态。
    """
    team = load_team(workspace)
    if team is None or not team.members:
        raise FlowError(
            "TEAM_NOT_CONFIGURED",
            "项目还没有有效的团队配置（00-management/team.md 缺失或没有成员）",
            object=object,
            suggestion="先补齐 00-management/team.md 的成员与职责映射，再由项目负责人确认",
        )
    if require_confirmed and not team.confirmed:
        raise FlowError(
            "TEAM_NOT_CONFIGURED",
            "团队配置尚未确认（00-management/team.md 的 confirmed 不是 true）",
            object=object,
            suggestion="由项目负责人核对成员与职责后把 confirmed 改为 true，再执行人工决策",
        )
    return team


def require_confirmation(actor: str | None, confirm: bool, *, command: str) -> str:
    """人工决策命令统一前置校验：缺 `--actor` 或 `--confirm` → `CONFIRMATION_REQUIRED`。"""
    if not confirm or not actor:
        missing = []
        if not actor:
            missing.append("--actor")
        if not confirm:
            missing.append("--confirm")
        raise FlowError(
            "CONFIRMATION_REQUIRED",
            f"{command} 是人工决策命令，必须显式申报身份并确认（缺少 {'、'.join(missing)}）",
            suggestion=f"补充 {' '.join(missing)} 后重试",
        )
    return actor


def require_member(team: Team | None, actor: str, *, role: str | None = None,
                   object: str | None = None) -> Member:
    """校验团队配置存在、actor 已登记、且（可选）具备所需职责。不改变任何状态。"""
    if team is None or not team.members:
        raise FlowError(
            "TEAM_NOT_CONFIGURED",
            "项目还没有有效的团队配置（00-management/team.md 缺失或没有成员）",
            object=object,
            suggestion="先补齐 00-management/team.md 的成员与职责映射，再由项目负责人确认",
        )
    member = team.member(actor)
    if member is None:
        raise FlowError(
            "ACTOR_UNKNOWN",
            f"申报身份 {actor} 不在团队配置中",
            object=object,
            suggestion="检查 00-management/team.md 中的成员 id，或使用已登记的身份申报",
        )
    if role and not member.has_role(role):
        raise FlowError(
            "ROLE_MISMATCH",
            f"申报身份 {actor} 不具备职责 {role}",
            object=object,
            suggestion=f"由具备 {role} 职责的成员执行该决定",
        )
    return member


def require_role_for(team: Team | None, role: str, *, object: str | None = None) -> Member:
    """校验团队里存在承担某职责的成员（例如 PRD 批准前要求有需求负责人）。"""
    if team is None or not team.members:
        raise FlowError(
            "TEAM_NOT_CONFIGURED",
            "项目还没有有效的团队配置，无法记录人工决定",
            object=object,
            suggestion="先补齐 00-management/team.md 的成员与职责映射",
        )
    for item in team.members:
        if item.has_role(role):
            return item
    raise FlowError(
        "ROLE_MISMATCH",
        f"团队配置里没有承担 {role} 职责的成员",
        object=object,
        suggestion=f"在 00-management/team.md 中为至少一名成员添加 {role} 职责",
    )


def check_transition(rules: Rules, machine: str, from_state: str, to_state: str,
                     *, object: str | None = None) -> None:
    """校验状态跳转；非法 → `INVALID_TRANSITION`，且调用方必须在写入前调用。"""
    if not rules.is_state(machine, from_state):
        raise FlowError(
            "INVALID_STATE",
            f"{machine} 不存在状态 {from_state}",
            object=object,
        )
    if not rules.can_transition(machine, from_state, to_state):
        allowed = rules.transitions(machine).get(from_state, [])
        hint = "、".join(allowed) if allowed else "无（终态）"
        raise FlowError(
            "INVALID_TRANSITION",
            f"{machine} 不允许从 {from_state} 跳到 {to_state}",
            object=object,
            suggestion=f"{from_state} 允许的下一步：{hint}",
        )


def history_entry(*, from_state: str, to_state: str, actor: str, action: str,
                  rules_version: int, at: str | None = None) -> dict[str, Any]:
    return {
        "at": at or fsutil.now_iso(),
        "from": from_state,
        "to": to_state,
        "by": actor,
        "action": action,
        "rules_version": rules_version,
    }


def confirmation_entry(*, actor: str, role: str, decision: str, object: str,
                       content_hash: str | None = None, version: int | None = None,
                       at: str | None = None) -> dict[str, Any]:
    entry: dict[str, Any] = {
        "at": at or fsutil.now_iso(),
        "by": actor,
        "role": role,
        "decision": decision,
        "object": object,
    }
    if content_hash is not None:
        entry["hash"] = content_hash
    if version is not None:
        entry["version"] = version
    return entry


def append_history(metadata: dict[str, Any], entry: dict[str, Any]) -> dict[str, Any]:
    """追加式写入：只 append，不修改已有历史。返回同一 dict 便于链式使用。"""
    history = metadata.get("history")
    if not isinstance(history, list):
        history = []
    history.append(entry)
    metadata["history"] = history
    return metadata


def append_confirmation(metadata: dict[str, Any], entry: dict[str, Any]) -> dict[str, Any]:
    confirmations = metadata.get("confirmations")
    if not isinstance(confirmations, list):
        confirmations = []
    confirmations.append(entry)
    metadata["confirmations"] = confirmations
    return metadata


def apply_transition(metadata: dict[str, Any], *, machine: str, to_state: str, actor: str,
                     action: str, rules: Rules, role: str | None = None,
                     content_hash: str | None = None,
                     version: int | None = None) -> dict[str, Any]:
    """校验跳转并追加 history/confirmations。**不写文件**，由调用方原子写。"""
    from_state = str(metadata.get("status", ""))
    check_transition(rules, machine, from_state, to_state,
                     object=str(metadata.get("id", "")) or None)
    append_history(
        metadata,
        history_entry(from_state=from_state, to_state=to_state, actor=actor, action=action,
                      rules_version=rules.version),
    )
    if role:
        append_confirmation(
            metadata,
            confirmation_entry(actor=actor, role=role, decision=action,
                               object=str(metadata.get("id", "")),
                               content_hash=content_hash, version=version),
        )
    metadata["status"] = to_state
    return metadata
