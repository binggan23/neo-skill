"""对象锁（共享设计 §7）。

锁文件 `.neo-skill/locks/<object-id>.lock`（JSON），用 `O_CREAT|O_EXCL` 获取，
`finally` 释放。锁已存在 → `LOCK_CONFLICT`，错误里带持有 pid/时间/命令。
残留锁只由 `doctor` 报告，`recover` 才在人工确认后删除。
"""

from __future__ import annotations

import errno
import json
import os
from contextlib import contextmanager
from dataclasses import dataclass
from typing import Any
from pathlib import Path
from typing import Iterator

from .fsutil import now_iso
from .output import FlowError
from .workspace import Workspace


@dataclass(frozen=True)
class LockInfo:
    object: str
    pid: int
    created_at: str
    command: str
    rules_version: int

    def to_dict(self) -> dict[str, Any]:
        return {
            "object": self.object,
            "pid": self.pid,
            "created_at": self.created_at,
            "command": self.command,
            "rules_version": self.rules_version,
        }

    @classmethod
    def from_dict(cls, data: dict) -> "LockInfo":
        return cls(
            object=str(data.get("object", "")),
            pid=int(data.get("pid", 0) or 0),
            created_at=str(data.get("created_at", "")),
            command=str(data.get("command", "")),
            rules_version=int(data.get("rules_version", 0) or 0),
        )


def lock_path(workspace: Workspace, object_id: str) -> Path:
    return workspace.locks_dir / f"{object_id}.lock"


def read_lock(workspace: Workspace, object_id: str) -> LockInfo | None:
    """读取锁记录；文件存在但不可解析时返回 None（doctor 另行报告损坏）。"""
    path = lock_path(workspace, object_id)
    if not path.is_file():
        return None
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return None
    if not isinstance(data, dict):
        return None
    return LockInfo.from_dict(data)


def list_locks(workspace: Workspace) -> list[LockInfo]:
    if not workspace.locks_dir.is_dir():
        return []
    locks: list[LockInfo] = []
    for path in sorted(workspace.locks_dir.glob("*.lock")):
        info = read_lock(workspace, path.stem)
        if info is not None:
            locks.append(info)
    return locks


def pid_alive(pid: int) -> bool:
    """保守判断：只有确认不存在才返回 False，避免误杀仍在运行的流程。"""
    if pid <= 0:
        return False
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    except OSError:
        return True
    return True


def acquire(workspace: Workspace, object_id: str, *, command: str,
            rules_version: int = 1) -> LockInfo:
    """获取对象锁；冲突抛 `LOCK_CONFLICT`。"""
    workspace.locks_dir.mkdir(parents=True, exist_ok=True)
    path = lock_path(workspace, object_id)
    info = LockInfo(
        object=object_id,
        pid=os.getpid(),
        created_at=now_iso(),
        command=command,
        rules_version=rules_version,
    )
    payload = json.dumps(info.to_dict(), ensure_ascii=False, indent=2)
    try:
        fd = os.open(str(path), os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o644)
    except OSError as exc:
        if exc.errno != errno.EEXIST:
            raise FlowError("LOCK_ERROR", f"无法创建锁文件：{exc}", object=object_id) from exc
        holder = read_lock(workspace, object_id)
        detail = "持有者信息不可读"
        if holder is not None:
            detail = f"持有者 pid={holder.pid}，创建于 {holder.created_at}，命令 {holder.command}"
        raise FlowError(
            "LOCK_CONFLICT",
            f"对象 {object_id} 已被其他流程锁定（{detail}）",
            object=object_id,
            suggestion="等待对方结束；确认持有进程已结束后用 flow-core recover --release-lock "
                       f"{object_id} --actor <id> --confirm 清理残留锁",
        ) from exc
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            handle.write(payload)
            handle.flush()
            os.fsync(handle.fileno())
    except BaseException:
        release(workspace, object_id)
        raise
    return info


def release(workspace: Workspace, object_id: str) -> None:
    """释放锁；不存在时静默（幂等）。"""
    path = lock_path(workspace, object_id)
    try:
        path.unlink()
    except FileNotFoundError:
        return


def force_release(workspace: Workspace, object_id: str) -> bool:
    """强制删除锁文件（仅供 `recover --release-lock` 在人工确认后调用）。"""
    path = lock_path(workspace, object_id)
    if not path.exists():
        return False
    path.unlink()
    return True


def acquire_many(workspace: Workspace, object_ids: list[str], *, command: str,
                 rules_version: int = 1) -> list[LockInfo]:
    """按对象 ID 字典序获取多把锁；任一失败回滚已获取的锁。"""
    acquired: list[LockInfo] = []
    try:
        for object_id in sorted(set(object_ids)):
            acquired.append(acquire(workspace, object_id, command=command,
                                    rules_version=rules_version))
    except BaseException:
        for info in acquired:
            release(workspace, info.object)
        raise
    return acquired


@contextmanager
def object_lock(workspace: Workspace, object_id: str, *, command: str,
                rules_version: int = 1) -> Iterator[LockInfo]:
    """`with object_lock(...)`：正常或异常退出都释放。"""
    info = acquire(workspace, object_id, command=command, rules_version=rules_version)
    try:
        yield info
    finally:
        release(workspace, object_id)


@contextmanager
def object_locks(workspace: Workspace, object_ids: list[str], *, command: str,
                 rules_version: int = 1) -> Iterator[list[LockInfo]]:
    infos = acquire_many(workspace, object_ids, command=command, rules_version=rules_version)
    try:
        yield infos
    finally:
        for info in infos:
            release(workspace, info.object)
