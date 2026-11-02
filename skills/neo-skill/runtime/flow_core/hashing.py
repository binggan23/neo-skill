"""内容 hash 工具：PRD 基线、文件集合摘要。

约定（共享设计 §6）：`content_hash = "sha256:" + hex`，计算必须确定性且跨机器一致。
"""

from __future__ import annotations

import hashlib
from pathlib import Path
from typing import Iterable

import yaml


def sha256_bytes(data: bytes) -> str:
    return "sha256:" + hashlib.sha256(data).hexdigest()


def sha256_text(text: str) -> str:
    return sha256_bytes(text.encode("utf-8"))


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with open(path, "rb") as handle:
        for chunk in iter(lambda: handle.read(65536), b""):
            digest.update(chunk)
    return "sha256:" + digest.hexdigest()


def canonical_yaml(data: object) -> str:
    return yaml.safe_dump(data, sort_keys=True, allow_unicode=True, default_flow_style=False)


def digest_of(parts: Iterable[tuple[str, str]]) -> str:
    """按 (相对路径, 文件内容 sha256) 序列计算稳定摘要。

    `parts` 必须已按路径排序；这里再排一次以免调用方顺序影响结果。
    """
    digest = hashlib.sha256()
    for rel, file_hash in sorted(parts):
        digest.update(rel.encode("utf-8"))
        digest.update(b"\0")
        digest.update(file_hash.removeprefix("sha256:").encode("ascii"))
        digest.update(b"\n")
    return "sha256:" + digest.hexdigest()


def prd_content_hash(prd_dir: Path, metadata: dict) -> str:
    """PRD 基线 hash（共享设计 §6）。

    参与：`prd.md`、存在的 `test-cases.md`、`locked_fields` 指定的 metadata 字段。
    不参与：reviews/、changes/、history、confirmations、status、content_hash。
    """
    files: list[tuple[str, str]] = []
    for rel in ("prd.md", "test-cases.md"):
        candidate = prd_dir / rel
        if candidate.is_file():
            files.append((rel, sha256_file(candidate)))
    locked_fields = metadata.get("locked_fields") or []
    locked = {key: metadata.get(key) for key in sorted(locked_fields)}
    digest = hashlib.sha256()
    for rel, file_hash in sorted(files):
        digest.update(rel.encode("utf-8"))
        digest.update(b"\0")
        digest.update(file_hash.removeprefix("sha256:").encode("ascii"))
        digest.update(b"\n")
    digest.update(canonical_yaml(locked).encode("utf-8"))
    return "sha256:" + digest.hexdigest()
