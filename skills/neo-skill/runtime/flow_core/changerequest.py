"""CR（变更请求）：路径、编号、front matter、渲染与投影（ticket 04）。

为什么单独一个模块：
- `prd` 命令组（`commands/prd_change.py`）需要读写 `04-prds/<module>/PRD-NNN-<slug>/changes/CR-NNN-<slug>.md`；
- 05（开发门禁）、07（验收）、09（lint）都要判断「当前 PRD 有没有未决 CR」以及查询历史批准/验收证据；
- 所以 CR 的路径、front matter 形状与投影必须只有一处定义。

CR 文件形状（共享设计 §4.2）：

    changes/CR-NNN-<slug>.md
    ---
    id / prd / title / reason / impact{spec,tasks,tests,design,version} / content /
    requested_by / requested_at / status / version_before / version_after /
    approved_by / approved_at / rejected_by / rejected_at / reject_reason
    ---

    ## 变更内容 / ## 变更原因 / ## 影响范围 / ## 版本变化 / ## 审批

`changes/` **不参与** PRD `content_hash`（共享设计 §6），所以追加 CR 不会让基线失效；
基线失效只由 metadata 的 `baseline_invalidated` 标记驱动（见 `flow_core/baseline.py`）。
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Iterable

from . import fsutil
from .metadata import CHANGES_DIR, PrdRecord
from .output import FlowError
from .rules import Rules
from .workspace import Workspace

STATUS_OPEN = "OPEN"
STATUS_APPROVED = "APPROVED"
STATUS_REJECTED = "REJECTED"

# CR 影响面五项（SRS-FR-049）
IMPACT_KEYS: tuple[str, ...] = ("spec", "tasks", "tests", "design", "version")
BOOLEAN_IMPACT_KEYS: tuple[str, ...] = ("spec", "tasks", "tests", "design")

DEFAULT_SLUG = "change"

_CR_ID_RE = re.compile(r"^(?P<prefix>CR)-(?P<num>\d+)$")


# --------------------------------------------------------------------------
# 编号与影响面
# --------------------------------------------------------------------------
def normalize_id(raw: str, rules: Rules) -> str:
    """`CR-1` / `1` / `cr-001` 统一成 `CR-001`。"""
    prefix = rules.id_prefixes.get("cr", "CR")
    text = str(raw or "").strip().upper()
    if not text:
        raise FlowError("CR_ID_REQUIRED", "缺少 CR 编号（--cr）",
                        suggestion="用 flow-core prd cr list --id PRD-001 查看已有 CR")
    if text.isdigit():
        return f"{prefix}-{int(text):0{rules.id_width}d}"
    match = _CR_ID_RE.match(text)
    if match and match.group("prefix") == prefix.upper():
        return f"{prefix}-{int(match.group('num')):0{rules.id_width}d}"
    return text


def format_id(number: int, rules: Rules) -> str:
    return f"{rules.id_prefixes.get('cr', 'CR')}-{number:0{rules.id_width}d}"


def parse_impact(raw: Iterable[str] | str | None) -> dict[str, Any]:
    """解析 `--impact spec=true,tasks=false,version=v0.2.0`。

    未知键 → `INVALID_IMPACT`（不写任何文件）。布尔项缺省 False，`version` 缺省 None。
    """
    impact: dict[str, Any] = {key: False for key in BOOLEAN_IMPACT_KEYS}
    impact["version"] = None
    if raw is None:
        return impact
    items = [raw] if isinstance(raw, str) else list(raw)
    for item in items:
        for part in str(item).split(","):
            text = part.strip()
            if not text:
                continue
            if "=" not in text:
                raise FlowError(
                    "INVALID_IMPACT",
                    f"`--impact {text}` 不是 key=value 形式",
                    object=text,
                    suggestion="例如 --impact spec=true,tasks=true,version=v0.2.0",
                )
            key, value = (piece.strip() for piece in text.split("=", 1))
            if key not in IMPACT_KEYS:
                raise FlowError(
                    "INVALID_IMPACT",
                    f"未知的影响面 `{key}`",
                    object=key,
                    suggestion="可写的影响面：" + "、".join(IMPACT_KEYS),
                )
            if key == "version":
                # 目标版本必须是合法的 vMAJOR.MINOR.PATCH（复用 metadata 的校验，不另写规则）
                from .metadata import validate_release

                impact["version"] = validate_release(value) if value else None
            else:
                impact[key] = _as_bool(value, key)
    return impact


def _as_bool(value: str, key: str) -> bool:
    text = value.strip().lower()
    if text in ("1", "true", "yes", "y", "是", "需要", "affected"):
        return True
    if text in ("0", "false", "no", "n", "否", "不需要", "none", ""):
        return False
    raise FlowError(
        "INVALID_IMPACT",
        f"影响面 `{key}` 的取值 `{value}` 不是布尔值",
        object=key,
        suggestion="用 true / false 表示是否受影响",
    )


def summarize(text: str | None, *, fallback: str) -> str:
    """取正文的第一句实质性内容作为 `content` 摘要（跳过标题、列表符号与分隔线）。"""
    for line in (text or "").splitlines():
        stripped = line.strip()
        if not stripped or stripped.startswith(("#", "-", "*", ">", "|", "---")):
            continue
        return stripped
    return fallback


def validate_slug(raw: str | None, *, reason: str = "") -> str:
    """CR 文件名里的 slug：缺省从原因推导，仍为空时用 `change`。"""
    from .metadata import validate_slug as _validate

    candidate = str(raw or "").strip()
    if not candidate:
        candidate = _slugify(reason) or DEFAULT_SLUG
    return _validate(candidate)


def _slugify(text: str) -> str:
    lowered = str(text or "").lower()
    lowered = re.sub(r"[^a-z0-9]+", "-", lowered).strip("-")
    return re.sub(r"-{2,}", "-", lowered)[:40].strip("-")


# --------------------------------------------------------------------------
# 对象
# --------------------------------------------------------------------------
@dataclass
class CrRecord:
    path: Path
    rel: str
    meta: dict[str, Any]
    body: str

    @property
    def id(self) -> str:
        return str(self.meta.get("id", ""))

    @property
    def prd(self) -> str:
        return str(self.meta.get("prd", ""))

    @property
    def status(self) -> str:
        return str(self.meta.get("status", ""))

    @property
    def reason(self) -> str:
        return str(self.meta.get("reason", ""))

    @property
    def version_before(self) -> int | None:
        value = self.meta.get("version_before")
        return int(value) if value is not None else None

    @property
    def version_after(self) -> int | None:
        value = self.meta.get("version_after")
        return int(value) if value is not None else None

    @property
    def impact(self) -> dict[str, Any]:
        value = self.meta.get("impact")
        return dict(value) if isinstance(value, dict) else {}

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "prd": self.prd,
            "title": str(self.meta.get("title", "")),
            "reason": self.reason,
            "impact": self.impact,
            "content": str(self.meta.get("content", "")),
            "status": self.status,
            "requested_by": self.meta.get("requested_by"),
            "requested_at": self.meta.get("requested_at"),
            "version_before": self.version_before,
            "version_after": self.version_after,
            "approved_by": self.meta.get("approved_by"),
            "approved_at": self.meta.get("approved_at"),
            "rejected_by": self.meta.get("rejected_by"),
            "rejected_at": self.meta.get("rejected_at"),
            "reject_reason": self.meta.get("reject_reason"),
            "path": self.rel,
        }


def changes_dir(record: PrdRecord) -> Path:
    return record.directory / CHANGES_DIR


def list_crs(workspace: Workspace, record: PrdRecord) -> list[CrRecord]:
    directory = changes_dir(record)
    if not directory.is_dir():
        return []
    records: list[CrRecord] = []
    for path in sorted(directory.glob("CR-*.md")):
        try:
            records.append(_record(workspace, path))
        except Exception:  # 损坏的 CR 文件不影响清单，由 lint 报告
            continue
    records.sort(key=lambda item: _sort_key(item.id))
    return records


def _sort_key(cr_id: str) -> tuple[int, str]:
    match = _CR_ID_RE.match(cr_id.upper())
    return (int(match.group("num")) if match else 0, cr_id)


def find(workspace: Workspace, record: PrdRecord, cr_id: str) -> CrRecord | None:
    directory = changes_dir(record)
    if not directory.is_dir():
        return None
    for path in sorted(directory.glob(f"{cr_id}-*.md")):
        return _record(workspace, path)
    return None


def load(workspace: Workspace, record: PrdRecord, raw: str, rules: Rules) -> CrRecord:
    cr_id = normalize_id(raw, rules)
    found = find(workspace, record, cr_id)
    if found is None:
        raise FlowError(
            "CR_NOT_FOUND",
            f"PRD {record.id} 下找不到 CR {cr_id}",
            object=cr_id,
            suggestion=f"用 flow-core prd cr list --id {record.id} 查看已有 CR",
        )
    return found


def open_crs(workspace: Workspace, record: PrdRecord) -> list[CrRecord]:
    return [item for item in list_crs(workspace, record) if item.status == STATUS_OPEN]


def next_number(record: PrdRecord) -> int:
    """PRD 内序号：已有 `CR-NNN` 的最大值 + 1（只增不复用，BR-019）。

    编号属于 PRD（`CR-NNN` 在 PRD 目录内唯一），所以不占用项目级 `CR` 计数器，
    避免不同 PRD 共用一套序号。文件被手工删除也不会回收编号以外的历史。
    """
    highest = 0
    directory = changes_dir(record)
    if not directory.is_dir():
        return 1
    for path in directory.glob("CR-*-*.md"):
        head = path.name.split("-")
        if len(head) >= 2 and head[1].isdigit():
            highest = max(highest, int(head[1]))
    return highest + 1


def _record(workspace: Workspace, path: Path) -> CrRecord:
    text = fsutil.read_text(path)
    meta, body = fsutil.parse_front_matter(text)
    if not isinstance(meta, dict) or not meta.get("id"):
        raise FlowError("CR_METADATA_INVALID", f"{workspace.rel(path)} 缺少 CR front matter",
                        object=workspace.rel(path))
    return CrRecord(path=path, rel=workspace.rel(path), meta=meta, body=body)


# --------------------------------------------------------------------------
# 写入与渲染
# --------------------------------------------------------------------------
def initial_metadata(*, cr_id: str, prd_id: str, title: str, reason: str,
                     impact: dict[str, Any], content: str, actor: str,
                     version_before: int | None) -> dict[str, Any]:
    return {
        "id": cr_id,
        "prd": prd_id,
        "title": title,
        "reason": reason,
        "impact": impact,
        "content": content,
        "requested_by": actor,
        "requested_at": fsutil.now_iso(),
        "status": STATUS_OPEN,
        "version_before": version_before,
        "version_after": None,
        "approved_by": None,
        "approved_at": None,
        "rejected_by": None,
        "rejected_at": None,
        "reject_reason": None,
    }


def render_body(*, cr_id: str, title: str, content: str, reason: str,
                impact: dict[str, Any], version_before: int | None,
                detail: str | None = None) -> str:
    lines = [f"# {cr_id} {title}", "", "## 变更内容", ""]
    lines.extend((detail or content).rstrip().splitlines())
    lines += ["", "## 变更原因", "", reason, "", "## 影响范围", "",
              "| 影响面 | 是否受影响 |", "| --- | --- |"]
    labels = {"spec": "Spec", "tasks": "Tasks", "tests": "测试", "design": "设计"}
    for key in BOOLEAN_IMPACT_KEYS:
        lines.append(f"| {labels[key]} | {'是' if impact.get(key) else '否'} |")
    lines.append(f"| 版本 | {impact.get('version') or '不变'} |")
    lines += ["", "## 版本变化", "", f"- 变更前版本：{version_before}",
              "- 变更后版本：由 `prd cr approve` 写入（版本 +1 并生成新 hash）", "",
              "## 审批", "",
              "待需求负责人执行 `flow-core prd cr approve`；拒绝则执行 "
              "`flow-core prd cr reject --reason <理由>`。", ""]
    return "\n".join(lines)


def save(workspace: Workspace, cr: CrRecord) -> None:
    text = fsutil.render_front_matter(cr.meta, cr.body)
    fsutil.atomic_write_text(cr.path, text)


def write(workspace: Workspace, record: PrdRecord, *, cr_id: str, slug: str,
          title: str, reason: str, impact: dict[str, Any], content: str,
          actor: str, detail: str | None = None) -> CrRecord:
    path = changes_dir(record) / f"{cr_id}-{slug}.md"
    meta = initial_metadata(cr_id=cr_id, prd_id=record.id, title=title, reason=reason,
                            impact=impact, content=content, actor=actor,
                            version_before=record.version)
    body = render_body(cr_id=cr_id, title=title, content=content, reason=reason,
                       impact=impact, version_before=record.version, detail=detail)
    cr = CrRecord(path=path, rel=workspace.rel(path), meta=meta, body=body)
    save(workspace, cr)
    return cr


# --------------------------------------------------------------------------
# 历史证据投影
# --------------------------------------------------------------------------
def approvals(workspace: Workspace, record: PrdRecord) -> list[dict[str, Any]]:
    """历史批准记录（来自 metadata.confirmations，追加式，不覆盖）。

    被后续 CR 重新批准取代的旧批准追加 `superseded_by_cr` / `superseded_by_version`
    （由 `changes/` 里的 CR 记录推导，不改写历史条目）。
    """
    superseders = {
        item.version_before: item
        for item in list_crs(workspace, record)
        if item.status == STATUS_APPROVED and item.version_before is not None
    }
    entries: list[dict[str, Any]] = []
    for entry in record.meta.get("confirmations") or []:
        if not isinstance(entry, dict) or entry.get("decision") != "approved":
            continue
        version = entry.get("version")
        data: dict[str, Any] = {
            "version": version,
            "content_hash": entry.get("hash"),
            "approved_by": entry.get("by"),
            "approved_at": entry.get("at"),
            "current": version == record.version,
        }
        successor = superseders.get(version)
        if successor is not None and (version is None or version < record.version):
            data["superseded_by_cr"] = successor.id
            data["superseded_by_version"] = successor.version_after
        entries.append(data)
    entries.sort(key=lambda item: (item["version"] is None, item["version"] or 0))
    return entries
