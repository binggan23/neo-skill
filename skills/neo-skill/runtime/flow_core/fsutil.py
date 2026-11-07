"""文件读写基础：原子写、文本/YAML、front matter、时间戳。

所有写入都显式 UTF-8，且单文件更新一律走 `atomic_write_text`（临时文件 → fsync →
`os.replace`），保证失败时不破坏已有正式文件、不残留临时文件。
"""

from __future__ import annotations

import os
import re
import tempfile
from datetime import datetime
from pathlib import Path
from typing import Any

import yaml

FRONT_MATTER_DELIM = "---"
_FRONT_MATTER_RE = re.compile(r"\A---\n(.*?)\n---\n?(.*)\Z", re.DOTALL)


class FsError(Exception):
    """文件操作失败（由命令层翻译成结构化错误）。"""

    code = "IO_ERROR"

    def __init__(self, message: str, path: Path | str | None = None) -> None:
        super().__init__(message)
        self.message = message
        self.path = str(path) if path is not None else None


def now_iso() -> str:
    """带时区的 ISO-8601 秒级时间。

    `NEO_SKILL_NOW` 可覆盖当前时间，便于测试得到可预测记录。
    """
    override = os.environ.get("NEO_SKILL_NOW")
    if override:
        return override.strip()
    return datetime.now().astimezone().isoformat(timespec="seconds")


def read_text(path: Path) -> str:
    return path.read_text(encoding="utf-8")


def write_text(path: Path, text: str) -> None:
    """非原子写入，仅用于不参与审计的临时/测试文件。正式文件用 `atomic_write_text`。"""
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")


def atomic_write_text(path: Path, text: str) -> None:
    """同目录临时文件 + fsync + 原子替换。"""
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp_name = tempfile.mkstemp(dir=str(path.parent), prefix=f".{path.name}.", suffix=".tmp")
    tmp_path = Path(tmp_name)
    try:
        with os.fdopen(fd, "w", encoding="utf-8", newline="\n") as handle:
            handle.write(text)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(tmp_path, path)
    except BaseException:
        try:
            tmp_path.unlink()
        except FileNotFoundError:
            pass
        raise
    _fsync_dir(path.parent)


def _fsync_dir(directory: Path) -> None:
    try:
        dir_fd = os.open(str(directory), os.O_RDONLY)
    except OSError:
        return
    try:
        os.fsync(dir_fd)
    except OSError:
        pass
    finally:
        os.close(dir_fd)


def read_yaml(path: Path) -> Any:
    try:
        return yaml.safe_load(read_text(path))
    except yaml.YAMLError as exc:  # pragma: no cover - 防御
        raise FsError(f"YAML 解析失败：{exc}", path) from exc


def parse_yaml_text(text: str) -> Any:
    """解析一段内嵌 YAML（如文档里的受管理区块 body）；非法 → `FsError`。

    与 `read_yaml` 的区别只在不落盘：调用方需要从 Markdown 区块里解析结构化内容。
    """
    try:
        return yaml.safe_load(text)
    except yaml.YAMLError as exc:
        raise FsError(f"YAML 解析失败：{exc}") from exc


def load_yaml(path: Path, default: Any = None) -> Any:
    """读取 YAML；文件不存在返回 `default`。"""
    if not path.is_file():
        return default
    data = read_yaml(path)
    return default if data is None else data


def dump_yaml(data: Any) -> str:
    return yaml.safe_dump(data, sort_keys=False, allow_unicode=True, default_flow_style=False)


def atomic_write_yaml(path: Path, data: Any) -> None:
    atomic_write_text(path, dump_yaml(data))


def parse_front_matter(text: str) -> tuple[dict[str, Any], str]:
    """拆分 YAML front matter 与正文；无 front matter 时返回 `({}, 原文)`。"""
    match = _FRONT_MATTER_RE.match(text)
    if not match:
        return {}, text
    raw, body = match.group(1), match.group(2)
    data = yaml.safe_load(raw) or {}
    if not isinstance(data, dict):
        raise FsError("front matter 必须是 YAML 映射")
    return data, body.lstrip("\n")


def render_front_matter(data: dict[str, Any], body: str) -> str:
    head = yaml.safe_dump(data, sort_keys=False, allow_unicode=True, default_flow_style=False)
    return f"{FRONT_MATTER_DELIM}\n{head}{FRONT_MATTER_DELIM}\n\n{body.lstrip(chr(10))}"
