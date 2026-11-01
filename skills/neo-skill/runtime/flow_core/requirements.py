"""REQ 对象：路径、读写投影、字段校验、完整性与重复/关联提示（ticket 02）。

为什么单独一个模块而不是塞进 `commands/req.py`：ticket 03 起（PRD 创建）必须能
「读一条需求并判断它是不是可用的 PLANNED 主需求」，这些读取/校验需要跨命令复用。
本模块只做纯读写与判定，**不**做状态跳转、不取锁、不写 history——
人工决策的顺序由 `state.py` 与 `commands/req.py` 负责。

状态值一律来自 `rules/flow.yaml`（`rules.initial_state("req")`、
`rules.transitions("req")`）；本模块只额外接受一个「草稿态」集合：
优先读 `rules.data["req"]["draft_states"]`，规则未声明时回退 `DRAFT_STATES`。
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Iterable

from . import fsutil, ids
from .output import FlowError
from .rules import Rules
from .workspace import Workspace

CATEGORIES: tuple[str, ...] = ("new-feature", "optimization", "defect", "tech-debt")
CATEGORY_LABELS = {
    "new-feature": "新功能",
    "optimization": "优化",
    "defect": "缺陷",
    "tech-debt": "技术债",
}

PRIORITY_SCHEMES: tuple[str, ...] = ("RICE", "MoSCoW")
MOSCOW_VALUES: tuple[str, ...] = ("must", "should", "could", "wont")

# 草稿必须逐步收集的正文小节（SRS-FR-030 / SRS-FR-033）
SECTION_TITLES: tuple[str, ...] = ("背景", "用户", "问题", "场景", "期望结果", "来源")

DRAFT_STATES: tuple[str, ...] = ("NEW", "TRIAGED")

SLUG_RE = re.compile(r"^[a-z0-9][a-z0-9-]*$")
RELEASE_RE = re.compile(r"^v\d+\.\d+\.\d+$")
_ID_RE = re.compile(r"^(?P<prefix>[A-Z]+)-(?P<num>\d+)$")

CANDIDATE_THRESHOLD = 0.3


# --------------------------------------------------------------------------
# 字段校验
# --------------------------------------------------------------------------
def normalize_id(raw: str, rules: Rules) -> str:
    """`REQ-1` / `1` / `req-001` 统一成 `REQ-001`。"""
    prefix = rules.id_prefixes.get("req", "REQ")
    text = str(raw or "").strip()
    if not text:
        raise FlowError("REQ_ID_REQUIRED", "缺少需求编号（--id）")
    upper = text.upper()
    if upper.isdigit():
        return ids.format_id(prefix, int(upper), rules.id_width)
    match = _ID_RE.match(upper)
    if match and match.group("prefix") == prefix.upper():
        return ids.format_id(prefix, int(match.group("num")), rules.id_width)
    return upper


def validate_module(raw: str) -> str:
    module = str(raw or "").strip()
    if not module:
        raise FlowError("INVALID_MODULE", "缺少模块（--module）",
                        suggestion="模块名用小写英文与连字符，例如 resume、resume-parse")
    if not SLUG_RE.match(module):
        raise FlowError(
            "INVALID_MODULE",
            f"模块名 `{module}` 不合法：只允许小写字母、数字与连字符，且以字母或数字开头",
            object=module,
            suggestion="例如 resume、resume-parse",
        )
    return module


def validate_category(raw: str | None) -> str:
    category = str(raw or "new-feature").strip()
    if category not in CATEGORIES:
        raise FlowError(
            "INVALID_CATEGORY",
            f"未知的需求分类 `{category}`",
            object=category,
            suggestion="可用分类：" + "、".join(f"{key}（{CATEGORY_LABELS[key]}）" for key in CATEGORIES),
        )
    return category


def validate_slug(raw: str) -> str:
    slug = str(raw or "").strip().lower()
    if not SLUG_RE.match(slug):
        raise FlowError(
            "INVALID_SLUG",
            f"slug `{slug}` 不合法：只允许小写字母、数字与连字符",
            object=slug,
            suggestion="例如 batch-parse、parse-failure",
        )
    return slug


def slugify(title: str) -> str:
    """把标题转成稳定 slug；纯中文标题返回空串（由调用方回退到编号）。"""
    text = str(title or "").lower()
    text = re.sub(r"[^a-z0-9]+", "-", text).strip("-")
    text = re.sub(r"-{2,}", "-", text)
    return text[:48].strip("-")


def validate_release(raw: str) -> str:
    release = str(raw or "").strip()
    if not RELEASE_RE.match(release):
        raise FlowError(
            "INVALID_RELEASE",
            f"目标版本 `{release}` 不合法：必须形如 v0.1.0",
            object=release,
            suggestion="使用 vMAJOR.MINOR.PATCH，例如 v0.1.0",
        )
    return release


def validate_related(raw: Iterable[str] | str | None, rules: Rules) -> list[str]:
    if raw is None:
        return []
    items = [raw] if isinstance(raw, str) else list(raw)
    values: list[str] = []
    for item in items:
        for part in str(item).split(","):
            name = part.strip()
            if not name:
                continue
            value = normalize_id(name, rules)
            if value not in values:
                values.append(value)
    return values


def draft_states(rules: Rules) -> tuple[str, ...]:
    declared = (rules.data.get("req") or {}).get("draft_states")
    if isinstance(declared, list) and declared:
        return tuple(str(item) for item in declared)
    return DRAFT_STATES


def is_draft(rules: Rules, status: str) -> bool:
    return status in draft_states(rules)


def prd_states(rules: Rules) -> tuple[str, ...]:
    """已交给 PRD 的状态：改期/取消排期必须走 PRD 变更，不得无记录处理（SRS-FR-035）。"""
    declared = (rules.data.get("req") or {}).get("prd_states")
    if isinstance(declared, list) and declared:
        return tuple(str(item) for item in declared)
    return ("IN_PRD", "DONE")


# --------------------------------------------------------------------------
# 对象
# --------------------------------------------------------------------------
@dataclass
class ReqRecord:
    path: Path
    rel: str
    meta: dict[str, Any]
    body: str

    @property
    def id(self) -> str:
        return str(self.meta.get("id", ""))

    @property
    def slug(self) -> str:
        return str(self.meta.get("slug", ""))

    @property
    def title(self) -> str:
        return str(self.meta.get("title", ""))

    @property
    def module(self) -> str:
        return str(self.meta.get("module", ""))

    @property
    def status(self) -> str:
        return str(self.meta.get("status", ""))

    @property
    def category(self) -> str:
        return str(self.meta.get("category", ""))

    @property
    def release(self) -> str | None:
        value = self.meta.get("release")
        return str(value) if value else None

    def to_dict(self, *, include_body: bool = False) -> dict[str, Any]:
        data: dict[str, Any] = {
            "id": self.id,
            "slug": self.slug,
            "title": self.title,
            "module": self.module,
            "category": self.category,
            "source": self.meta.get("source"),
            "status": self.status,
            "release": self.release,
            "prd": self.meta.get("prd"),
            "priority": self.meta.get("priority"),
            "related": list(self.meta.get("related") or []),
            "grilling": dict(self.meta.get("grilling") or {}),
            "path": self.rel,
        }
        if include_body:
            data["body"] = self.body
            data["history"] = list(self.meta.get("history") or [])
            data["confirmations"] = list(self.meta.get("confirmations") or [])
        return data


def item_path(workspace: Workspace, req_id: str, slug: str) -> Path:
    return workspace.req_items_dir / f"{req_id}-{slug}.md"


def initial_metadata(*, req_id: str, slug: str, title: str, module: str, category: str,
                     source: str | None, rules: Rules) -> dict[str, Any]:
    return {
        "id": req_id,
        "slug": slug,
        "title": title,
        "module": module,
        "category": category,
        "source": source,
        "status": rules.initial_state("req"),
        "release": None,
        "prd": None,
        "priority": None,
        "related": [],
        "grilling": {
            "frontier": [],
            "facts": [],
            "decisions": [],
            "assumptions": [],
            "open_questions": [],
            "consensus": None,
            "consensus_confirmed": False,
        },
        "created_at": fsutil.now_iso(),
        "history": [],
        "confirmations": [],
    }


def render_body(workspace: Workspace, req_id: str, title: str) -> str:
    """正文骨架：优先用项目模板 `.specify/templates/req.md`，缺失时用内置骨架。"""
    template = _template_text(workspace)
    sections = template
    lines = template.splitlines()
    if lines and lines[0].startswith("# "):
        sections = "\n".join(lines[1:]).lstrip("\n")
    return f"# {req_id} {title}\n\n{sections}".rstrip() + "\n"


def _template_text(workspace: Workspace) -> str:
    candidates = (
        workspace.templates_dir / "req.md",
        Path(__file__).resolve().parents[1] / "templates" / "req.md",
    )
    for path in candidates:
        if path.is_file():
            return fsutil.read_text(path)
    return _FALLBACK_TEMPLATE


_FALLBACK_TEMPLATE = """# REQ-XXX <需求标题>

## 背景

<!-- TODO: 为什么现在要处理这个问题。 -->

## 用户

<!-- TODO: 受影响的人是谁。 -->

## 问题

<!-- TODO: 现状是什么。 -->

## 场景

<!-- TODO: 用可观察的步骤描述典型场景。 -->

## 期望结果

<!-- TODO: 问题解决后可观察到的变化。 -->

## 来源

<!-- TODO: 记录来源与原始材料位置。 -->
"""


# --------------------------------------------------------------------------
# 读写
# --------------------------------------------------------------------------
def items_dir(workspace: Workspace) -> Path:
    return workspace.req_items_dir


def load(workspace: Workspace, req_id: str, *, rules: Rules) -> ReqRecord:
    target = normalize_id(req_id, rules)
    directory = items_dir(workspace)
    matches: list[Path] = []
    if directory.is_dir():
        matches = sorted(directory.glob(f"{target}-*.md"))
        if not matches:
            exact = directory / f"{target}.md"
            if exact.is_file():
                matches = [exact]
    if not matches:
        raise FlowError(
            "REQ_NOT_FOUND",
            f"找不到需求 {target}",
            object=target,
            suggestion="用 flow-core req list 查看已有需求，或先用 flow-core req create 创建草稿",
        )
    path = matches[0]
    meta, body = fsutil.parse_front_matter(fsutil.read_text(path))
    meta.setdefault("id", target)
    return ReqRecord(path=path, rel=workspace.rel(path), meta=meta, body=body)


def save(workspace: Workspace, record: ReqRecord) -> None:
    fsutil.atomic_write_text(record.path, fsutil.render_front_matter(record.meta, record.body))


def list_requirements(workspace: Workspace) -> list[ReqRecord]:
    directory = items_dir(workspace)
    if not directory.is_dir():
        return []
    records: list[ReqRecord] = []
    for path in sorted(directory.glob("*.md")):
        try:
            meta, body = fsutil.parse_front_matter(fsutil.read_text(path))
        except Exception:  # 损坏文件不影响清单，由 lint 报告
            continue
        if not str(meta.get("id", "")).startswith("REQ-"):
            continue
        records.append(ReqRecord(path=path, rel=workspace.rel(path), meta=meta, body=body))
    records.sort(key=lambda item: item.id)
    return records


# --------------------------------------------------------------------------
# 完整性与共识
# --------------------------------------------------------------------------
def section_bodies(body: str) -> dict[str, str]:
    sections: dict[str, str] = {}
    current: str | None = None
    buffer: list[str] = []
    for line in body.splitlines():
        if line.startswith("## "):
            if current is not None:
                sections[current] = "\n".join(buffer)
            current = line[3:].strip()
            buffer = []
        elif current is not None:
            buffer.append(line)
    if current is not None:
        sections[current] = "\n".join(buffer)
    return sections


def strip_comments(text: str) -> str:
    return re.sub(r"<!--.*?-->", "", text, flags=re.DOTALL)


def missing_sections(body: str) -> list[str]:
    """返回「仍然是模板占位/空内容」的小节名；空列表表示关键信息齐全。"""
    sections = section_bodies(body)
    missing: list[str] = []
    for name in SECTION_TITLES:
        content = strip_comments(sections.get(name, "")).strip()
        if not content:
            missing.append(name)
    return missing


def grilling(record: ReqRecord) -> dict[str, Any]:
    raw = record.meta.get("grilling")
    data: dict[str, Any] = dict(raw) if isinstance(raw, dict) else {}
    for key in ("frontier", "facts", "decisions", "assumptions", "open_questions"):
        value = data.get(key)
        data[key] = list(value) if isinstance(value, list) else []
    data.setdefault("design_tree", None)
    data.setdefault("consensus", None)
    data["consensus_confirmed"] = bool(data.get("consensus_confirmed", False))
    return data


def prd_readiness(workspace: Workspace, rules: Rules, record: ReqRecord) -> dict[str, Any]:
    """`req show` 的只读投影：这条需求现在能不能作为主需求创建 PRD（BR-010）。

    与 `require_planned` 同一套判断，区别是本函数返回原因列表而不抛错，
    便于 Skill 在 grilling 阶段就告诉用户「还差什么」。
    """
    gates = rules.data.get("gates") or {}
    gate = gates.get("req.plan") if isinstance(gates.get("req.plan"), dict) else {}
    planned_state = str(gate.get("to") or "PLANNED")
    reasons: list[str] = []
    if record.status != planned_state:
        reasons.append(f"当前状态是 {record.status}，需要 {planned_state}（先 req confirm 再 req plan）")
    if not record.release:
        reasons.append("没有确认目标版本（req plan --release）")
    if record.meta.get("prd"):
        reasons.append(f"已关联 PRD {record.meta['prd']}")
    missing = missing_sections(record.body)
    if missing:
        reasons.append("正文关键小节仍是占位：" + "、".join(missing))
    return {
        "ready": not reasons,
        "reasons": reasons,
        "planned_state": planned_state,
        "release": record.release,
    }


def require_planned(workspace: Workspace, rules: Rules, req_id: str) -> ReqRecord:
    """ticket 03 的「主需求 → PRD」前置校验入口（BR-010）。

    校验：需求存在、状态恰好是 `gates.req.plan.to`（默认 `PLANNED`）、目标版本已确认、
    尚未关联 PRD。任一不满足就抛带 `next_actions` 的 `FlowError`，**不写任何文件**。
    ticket 03 只需 `from ..requirements import require_planned`，不要重复实现这层判断。
    """
    record = load(workspace, req_id, rules=rules)
    gates = rules.data.get("gates") or {}
    gate = gates.get("req.plan") if isinstance(gates.get("req.plan"), dict) else {}
    planned_state = str(gate.get("to") or "PLANNED")
    if record.status != planned_state:
        raise FlowError(
            "REQ_NOT_PLANNED",
            f"需求 {record.id} 是 {record.status}，不是 {planned_state}，不能创建正式 PRD",
            object=record.id,
            suggestion="先完成 grilling 确认与人工排期（req confirm / req plan）",
            next_actions=[
                f"flow-core req triage-prompt --id {record.id}",
                f"flow-core req plan --id {record.id} --actor <需求负责人> --confirm --release <版本>",
            ],
        )
    if not record.release:
        raise FlowError(
            "RELEASE_REQUIRED",
            f"需求 {record.id} 没有确认目标版本，不能创建正式 PRD",
            object=record.id,
            suggestion="与需求负责人确认版本后用 req plan 记录",
            next_actions=[
                f"flow-core req plan --id {record.id} --actor <需求负责人> --confirm --release <版本>",
            ],
        )
    if record.meta.get("prd"):
        raise FlowError(
            "REQ_ALREADY_IN_PRD",
            f"需求 {record.id} 已关联 PRD {record.meta['prd']}",
            object=record.id,
            suggestion="一个需求只能作为一个 PRD 的主需求；其他 PRD 只能作为相关需求引用",
        )
    return record


# --------------------------------------------------------------------------
# 重复/关联提示
# --------------------------------------------------------------------------
def _tokens(text: str) -> set[str]:
    lowered = str(text or "").lower()
    words = set(re.findall(r"[a-z0-9]+", lowered))
    for run in re.findall(r"[\u4e00-\u9fff]+", lowered):
        if len(run) == 1:
            words.add(run)
        for index in range(len(run) - 1):
            words.add(run[index:index + 2])
    return words


def similarity(left: set[str], right: set[str]) -> float:
    if not left or not right:
        return 0.0
    return len(left & right) / len(left | right)


def candidates(workspace: Workspace, *, title: str, module: str | None = None) -> list[dict[str, Any]]:
    """比对已有需求与功能现状，返回相似/关联提示。**只读**，不改任何状态。"""
    query = _tokens(title)
    found: list[dict[str, Any]] = []

    for record in list_requirements(workspace):
        overlap = query & _tokens(f"{record.title} {record.slug} {record.module}")
        score = similarity(query, _tokens(f"{record.title} {record.slug}"))
        reasons: list[str] = []
        if overlap:
            score = max(score, min(1.0, score + 0.05 * len(overlap)))
            reasons.append("标题/关键词重叠：" + "、".join(sorted(overlap)))
        if module and record.module == module:
            score = max(score, score + 0.1)
            reasons.append(f"同模块 {module}")
        if score < CANDIDATE_THRESHOLD:
            continue
        found.append({
            "kind": "req",
            "id": record.id,
            "title": record.title,
            "module": record.module,
            "status": record.status,
            "path": record.rel,
            "score": round(min(1.0, score), 2),
            "reasons": reasons or ["标题相近"],
        })

    for path, feature_module in _feature_docs(workspace):
        stem = path.stem
        heading = _first_heading(path)
        feature_tokens = _tokens(f"{stem} {heading} {feature_module}")
        overlap = query & feature_tokens
        score = similarity(query, feature_tokens)
        reasons: list[str] = []
        if overlap:
            reasons.append("功能现状名称重叠：" + "、".join(sorted(overlap)))
        if module and feature_module == module:
            score = max(score, score + 0.1)
            reasons.append(f"同模块 {feature_module}")
        if score < CANDIDATE_THRESHOLD:
            continue
        found.append({
            "kind": "feature",
            "id": stem,
            "title": heading or stem,
            "module": feature_module,
            "status": "现状文档",
            "path": workspace.rel(path),
            "score": round(min(1.0, score), 2),
            "reasons": reasons or ["功能现状相近"],
        })

    found.sort(key=lambda item: (-item["score"], item["kind"], item["id"]))
    return found


def _feature_docs(workspace: Workspace) -> list[tuple[Path, str]]:
    root = workspace.modules_dir
    if not root.is_dir():
        return []
    docs: list[tuple[Path, str]] = []
    for module_dir in sorted(root.iterdir()):
        features = module_dir / "features"
        if not features.is_dir():
            continue
        for path in sorted(features.glob("*.md")):
            docs.append((path, module_dir.name))
    return docs


def _first_heading(path: Path) -> str:
    try:
        text = fsutil.read_text(path)
    except OSError:  # pragma: no cover - 权限类异常
        return ""
    for line in text.splitlines():
        if line.startswith("# "):
            return line[2:].strip()
    return ""
