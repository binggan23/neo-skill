"""受管理区块索引（共享设计 §9、SRS-FR-085）。

三个目标文件由 `flow-core index` 独占生成受管理区块：

    03-requirements/backlog.md      需求清单
    02-definition/feature-map.md    模块 / 功能 / 场景 / PRD 总表
    04-prds/index.md                PRD 清单

区块形状：

    <!-- neo-skill:managed:begin fingerprint=<区块 body 的 sha256> -->
    ...generated...
    <!-- neo-skill:managed:end -->

约定：

- **内容确定性**：只读 REQ/PRD 事实，按 ID 排序，不含时间戳；同输入同输出（NFR-009）。
- **区块外内容原样保留**：只替换两个标记之间的文本；缺区块时追加到文件末尾。
- **人工修改检测**：现有区块 body 的 sha256 与 `fingerprint` 不一致 → `MANAGED_BLOCK_MODIFIED`，
  需要 `--confirm`（+`--actor`）才覆盖（FR-085）。
- **只写这三个文件**：索引失败不改变任何业务状态（不碰 REQ/PRD metadata）。

`acceptance closeout`（ticket 08）复用本模块的区块原语 `split()` / `merge()` /
`render_block()` 写功能现状与场景状态表，保证「受管理区块」只有一套实现。
"""

from __future__ import annotations

import hashlib
import re
from dataclasses import dataclass, field
from typing import Any, Iterable

import yaml

from . import fsutil, metadata, requirements
from .output import FlowError
from .rules import Rules
from .workspace import Workspace

BLOCK_BEGIN_MARK = "<!-- neo-skill:managed:begin"
BLOCK_END = "<!-- neo-skill:managed:end -->"
_BEGIN_RE = re.compile(
    r"<!--\s*neo-skill:managed:begin(?:\s+fingerprint=(?P<fingerprint>[0-9a-f]*))?\s*-->")

DEFAULT_TARGETS: tuple[str, ...] = (
    "03-requirements/backlog.md",
    "02-definition/feature-map.md",
    "04-prds/index.md",
)

FLOW_CORE = "flow-core"


# --------------------------------------------------------------------------
# 区块原语（index 与 closeout 共用）
# --------------------------------------------------------------------------
@dataclass(frozen=True)
class BlockSpan:
    """已解析的受管理区块在原文中的位置与内容。"""

    begin_start: int
    begin_end: int
    end_start: int
    end_end: int
    body: str
    fingerprint: str | None

    @property
    def intact(self) -> bool:
        """`fingerprint` 与 body 的实际 sha256 一致（没有被人工改过）。"""
        return self.fingerprint is not None and self.fingerprint == body_fingerprint(self.body)


def body_fingerprint(body: str) -> str:
    return hashlib.sha256(body.encode("utf-8")).hexdigest()


def split(text: str) -> BlockSpan | None:
    """定位第一个受管理区块；没有区块（或标记不成对）返回 None。"""
    match = _BEGIN_RE.search(text)
    if not match:
        return None
    end_start = text.find(BLOCK_END, match.end())
    if end_start < 0:
        return None
    body_start = match.end()
    if text[body_start:body_start + 1] == "\n":
        body_start += 1
    return BlockSpan(
        begin_start=match.start(),
        begin_end=match.end(),
        end_start=end_start,
        end_end=end_start + len(BLOCK_END),
        body=text[body_start:end_start],
        fingerprint=match.group("fingerprint") or None,
    )


def render_block(body: str) -> str:
    """把 body 包成受管理区块（body 必须以换行结尾，保证指纹可复算）。"""
    if not body.endswith("\n"):
        body += "\n"
    return (
        f"{BLOCK_BEGIN_MARK} fingerprint={body_fingerprint(body)} -->\n"
        f"{body}"
        f"{BLOCK_END}"
    )


def merge(text: str, body: str) -> str:
    """替换（或追加）受管理区块，区块外内容原样保留。"""
    block = render_block(body)
    span = split(text)
    if span is None:
        base = text.rstrip("\n")
        return (base + "\n\n" + block + "\n") if base else (block + "\n")
    return text[:span.begin_start] + block + text[span.end_end:]


def read_body(text: str) -> str | None:
    span = split(text)
    return span.body if span is not None else None


def is_modified(text: str) -> bool:
    """现有区块是否被人工改过（无区块不算改动）。"""
    span = split(text)
    return span is not None and not span.intact


# --------------------------------------------------------------------------
# 结果对象
# --------------------------------------------------------------------------
@dataclass
class BlockPlan:
    """一个目标文件的一次生成结果（尚未写入）。"""

    path: str
    body: str
    text: str          # 生成后的完整文件内容
    existed: bool
    modified: bool     # 现有区块与 fingerprint 不一致
    changed: bool      # 生成后内容与当前文件不同

    def to_dict(self) -> dict[str, Any]:
        return {
            "path": self.path,
            "existed": self.existed,
            "modified": self.modified,
            "changed": self.changed,
            "action": ("updated" if self.existed else "created") if self.changed
                      else "unchanged",
        }


@dataclass
class IndexResult:
    plans: list[BlockPlan] = field(default_factory=list)

    @property
    def modified(self) -> list[str]:
        return [plan.path for plan in self.plans if plan.modified]

    @property
    def changed(self) -> list[str]:
        return [plan.path for plan in self.plans if plan.changed]

    def to_dict(self) -> dict[str, Any]:
        return {
            "files": [plan.path for plan in self.plans],
            "blocks": [plan.to_dict() for plan in self.plans],
            "modified": self.modified,
            "changed": self.changed,
        }


# --------------------------------------------------------------------------
# 目标与内容生成
# --------------------------------------------------------------------------
def targets(rules: Rules) -> tuple[str, ...]:
    """受管理区块的目标文件（读规则 `managed_blocks`，回退默认三个）。"""
    declared = rules.data.get("managed_blocks")
    if isinstance(declared, list) and declared:
        return tuple(str(item) for item in declared)
    return DEFAULT_TARGETS


def backlog_body(workspace: Workspace, overrides: dict[str, str] | None = None) -> str:
    """`03-requirements/backlog.md`：按需求编号排序。

    `overrides` 是「即将写入、但磁盘上还没有」的文件内容（键为项目内相对路径）。
    `acceptance closeout` 用它让索引反映「主需求即将 DONE」的收尾后状态，
    否则索引刚生成就会过期。
    """
    records = _req_records(workspace, overrides)
    if not records:
        return "（暂无需求）\n"
    lines = [
        "| 需求 | 标题 | 模块 | 分类 | 状态 | 版本 | 主 PRD |",
        "| --- | --- | --- | --- | --- | --- | --- |",
    ]
    for record in sorted(records, key=lambda item: item.id):
        lines.append(
            f"| {record.id} | {_cell(record.title)} | {record.module or '—'} "
            f"| {record.category or '—'} | {record.status} "
            f"| {record.release or '—'} | {record.meta.get('prd') or '—'} |")
    return "\n".join(lines) + "\n"


def feature_map_body(workspace: Workspace, overrides: dict[str, str] | None = None) -> str:
    """`02-definition/feature-map.md`：模块 / 功能 / 场景 / PRD / 状态，按 ID 排序。"""
    records = _prd_records(workspace, overrides)
    if not records:
        return "（暂无 PRD）\n"
    lines = [
        "| 模块 | 功能 | 场景 | PRD | 状态 | 主需求 |",
        "| --- | --- | --- | --- | --- | --- |",
    ]
    rows = sorted(records, key=lambda item: (item.module or "", item.feature or "", item.id))
    for record in rows:
        lines.append(
            f"| {record.module or '—'} | {record.feature or '—'} "
            f"| {'、'.join(record.scenarios) or '—'} | {record.id} | {record.status} "
            f"| {'、'.join(record.source_reqs) or '—'} |")
    return "\n".join(lines) + "\n"


def prd_index_body(workspace: Workspace, overrides: dict[str, str] | None = None) -> str:
    """`04-prds/index.md`：按 PRD 编号排序。"""
    records = _prd_records(workspace, overrides)
    if not records:
        return "（暂无 PRD）\n"
    lines = [
        "| PRD | 标题 | 模块 | 版本 | 状态 | 主需求 |",
        "| --- | --- | --- | --- | --- | --- |",
    ]
    for record in sorted(records, key=lambda item: item.id):
        lines.append(
            f"| {record.id} | {_cell(record.title)} | {record.module or '—'} "
            f"| v{record.version} | {record.status} "
            f"| {'、'.join(record.source_reqs) or '—'} |")
    return "\n".join(lines) + "\n"


def body_for(workspace: Workspace, path: str, rules: Rules,
             overrides: dict[str, str] | None = None) -> str:
    """按目标路径生成区块 body；未知目标 → `INDEX_TARGET_UNKNOWN`。"""
    if path.endswith("03-requirements/backlog.md"):
        return backlog_body(workspace, overrides)
    if path.endswith("02-definition/feature-map.md"):
        return feature_map_body(workspace, overrides)
    if path.endswith("04-prds/index.md"):
        return prd_index_body(workspace, overrides)
    raise FlowError(
        "INDEX_TARGET_UNKNOWN",
        f"不知道如何生成受管理区块：{path}",
        object=path,
        suggestion="受管理区块目标只有 backlog.md、feature-map.md、04-prds/index.md，"
                   "扩容请改 .specify/rules/flow.yaml 的 managed_blocks 并同步生成逻辑",
    )


def _cell(value: Any) -> str:
    return str(value or "—").replace("|", "\\|").replace("\n", " ")


# --------------------------------------------------------------------------
# 计划与生成
# --------------------------------------------------------------------------
def plan(workspace: Workspace, rules: Rules, *,
         paths: Iterable[str] | None = None,
         overrides: dict[str, str] | None = None) -> IndexResult:
    """计算三个目标文件的新内容（**不写文件**）。

    人工修改过的区块会被记录在 `IndexResult.modified` 里，由调用方决定是否要 `--confirm`。
    `overrides` 见 `backlog_body()`。
    """
    result = IndexResult()
    for path in (tuple(paths) if paths is not None else targets(rules)):
        target = workspace.path(path)
        existed = target.is_file()
        current = fsutil.read_text(target) if existed else ""
        body = body_for(workspace, path, rules, overrides)
        text = merge(current, body)
        result.plans.append(BlockPlan(
            path=path,
            body=body,
            text=text,
            existed=existed,
            modified=is_modified(current),
            changed=text != current,
        ))
    return result


def require_confirm(result: IndexResult, *, confirm: bool) -> None:
    """有区块被人工改过且没有显式确认 → `MANAGED_BLOCK_MODIFIED`（不写任何文件）。"""
    if not result.modified or confirm:
        return
    paths = "、".join(result.modified)
    raise FlowError(
        "MANAGED_BLOCK_MODIFIED",
        f"受管理区块被人工修改过：{paths}",
        object=result.modified[0],
        suggestion="确认这些修改可以被索引覆盖后，加 --confirm（并申报 --actor）重新执行；"
                   "区块外的说明不会被覆盖",
        next_actions=[f"{FLOW_CORE} index --confirm --actor <成员 id>"],
    )


def generate(workspace: Workspace, rules: Rules, *, confirm: bool = False,
             paths: Iterable[str] | None = None,
             overrides: dict[str, str] | None = None) -> IndexResult:
    """生成受管理区块并写入（只写索引文件，失败不改业务状态）。"""
    result = plan(workspace, rules, paths=paths, overrides=overrides)
    require_confirm(result, confirm=confirm)
    for item in result.plans:
        if item.changed:
            fsutil.atomic_write_text(workspace.path(item.path), item.text)
    return result


def _req_records(workspace: Workspace, overrides: dict[str, str] | None) -> list[Any]:
    """需求清单；`overrides` 里给出的文件内容优先于磁盘（用于「即将写入」的内容）。"""
    records = requirements.list_requirements(workspace)
    if not overrides:
        return records
    patched: list[Any] = []
    for record in records:
        text = overrides.get(record.rel)
        if text is None:
            patched.append(record)
            continue
        meta, body = fsutil.parse_front_matter(text)
        patched.append(requirements.ReqRecord(path=record.path, rel=record.rel,
                                             meta=meta, body=body))
    return patched


def _prd_records(workspace: Workspace, overrides: dict[str, str] | None) -> list[Any]:
    records = metadata.list_prds(workspace)
    if not overrides:
        return records
    patched: list[Any] = []
    for record in records:
        rel = f"{record.rel}/{metadata.METADATA_FILE}"
        text = overrides.get(rel)
        if text is None:
            patched.append(record)
            continue
        meta = yaml.safe_load(text) or {}
        patched.append(metadata.PrdRecord(directory=record.directory, rel=record.rel,
                                          meta=meta, body=record.body))
    return patched


def evaluate(workspace: Workspace, rules: Rules,
             overrides: dict[str, str] | None = None) -> dict[str, Any]:
    """只读判定：索引是否与当前事实一致（供 `closeout-status` / lint 使用）。"""
    problems: list[str] = []
    files: list[dict[str, Any]] = []
    for path in targets(rules):
        target = workspace.path(path)
        if not target.is_file():
            problems.append(f"INDEX_MISSING:{path}")
            files.append({"path": path, "status": "missing"})
            continue
        current = fsutil.read_text(target)
        span = split(current)
        if span is None:
            problems.append(f"INDEX_BLOCK_MISSING:{path}")
            files.append({"path": path, "status": "no_block"})
            continue
        if not span.intact:
            problems.append(f"MANAGED_BLOCK_MODIFIED:{path}")
            files.append({"path": path, "status": "modified"})
            continue
        expected = merge(current, body_for(workspace, path, rules, overrides))
        status = "ok" if expected == current else "stale"
        if status != "ok":
            problems.append(f"INDEX_STALE:{path}")
        files.append({"path": path, "status": status})
    return {
        "ok": not problems,
        "problems": problems,
        "files": files,
        "targets": list(targets(rules)),
    }
