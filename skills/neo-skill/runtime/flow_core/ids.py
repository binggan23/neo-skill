"""单调编号（共享设计 §4.4）。

`.neo-skill/counters.yaml` 记录每个前缀的当前最大编号。分配 = 读取 → +1 → 原子写回 →
使用新值；只增不复用，废弃对象不回收编号。
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from . import fsutil
from .workspace import COUNTER_KEYS, Workspace

DEFAULT_COUNTERS: dict[str, int] = {key: 0 for key in COUNTER_KEYS}


def load_counters(workspace: Workspace) -> dict[str, int]:
    data: Any = fsutil.load_yaml(workspace.counters_file, default=None)
    counters = dict(DEFAULT_COUNTERS)
    if isinstance(data, dict):
        for key, value in data.items():
            if isinstance(value, int):
                counters[str(key)] = value
    return counters


def format_id(prefix: str, value: int, width: int = 3) -> str:
    return f"{prefix}-{value:0{width}d}"


def peek_next(workspace: Workspace, key: str) -> int:
    """只读预测下一个编号，不写回（用于 dry-run / 变更计划）。"""
    counters = load_counters(workspace)
    return int(counters.get(key, 0)) + 1


def allocate(workspace: Workspace, key: str, *, width: int = 3, prefix: str | None = None) -> str:
    """分配并持久化一个新编号，返回完整 ID（如 `REQ-001`）。"""
    counters = load_counters(workspace)
    next_value = int(counters.get(key, 0)) + 1
    counters[key] = next_value
    ordered = {name: int(counters.get(name, 0)) for name in dict.fromkeys(
        (*COUNTER_KEYS, *counters.keys()))}
    fsutil.atomic_write_yaml(workspace.counters_file, ordered)
    return format_id(prefix or key, next_value, width)


def next_sequence(workspace: Workspace, key: str) -> int:
    """当前序列号（未分配）；`format_id` 之前用。"""
    return int(load_counters(workspace).get(key, 0))


def counters_file_for(root: Path) -> Path:
    return Workspace(root).counters_file
