"""PRD 对象：目录布局、metadata 读写投影与结构自审（ticket 03；04/05/07/08 复用）。

为什么单独一个模块：
- ticket 03（`prd` 命令组）需要「创建 → 草稿修改 → 自审 → 提交评审 → 批准」全套读写；
- ticket 04（CR）、05（开发门禁）、07（验收）、08（收尾）都要读同一个 PRD 的
  metadata、正文与 `content_hash`，所以路径与投影必须只有一处定义。

本模块只做**纯读写与判定**：不做状态跳转、不取锁、不写 history——
人工决策的顺序由 `state.py` 与 `commands/prd.py` 负责。

目录与 metadata 形状见共享设计 §4.2：

    04-prds/<module>/PRD-NNN-<slug>/
        metadata.yaml
        prd.md
        test-cases.md          （可选）
        reviews/YYYY-MM-DD-review.md
        changes/CR-NNN-<slug>.md

状态值与可改状态集合一律来自 `rules/flow.yaml`（`prd` 状态机 + `prd.draft_states` /
`prd.cr_states`），本模块不硬编码状态值。
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Iterable

from . import fsutil, hashing, ids
from .output import FlowError
from .rules import Rules
from .workspace import Workspace

# 批准时锁定的 metadata 字段（共享设计 §4.2）：参与 content_hash
LOCKED_FIELDS: tuple[str, ...] = (
    "id", "slug", "module", "feature", "scenarios", "source_reqs", "release", "version",
)

# 草稿期可直接修改的 metadata 字段（`prd update --set`）
SETTABLE_FIELDS: tuple[str, ...] = ("title", "feature", "release", "related_reqs")

DRAFT_STATES: tuple[str, ...] = ("DRAFT", "CHANGES_REQUESTED")
CR_STATES: tuple[str, ...] = (
    "APPROVED", "DEVELOPING", "READY_FOR_ACCEPTANCE", "ACCEPTED", "SUPERSEDED",
)

SLUG_RE = re.compile(r"^[a-z0-9][a-z0-9-]*$")
RELEASE_RE = re.compile(r"^v\d+\.\d+\.\d+$")
_ID_RE = re.compile(r"^(?P<prefix>[A-Z]+)-(?P<num>\d+)$")
_SCENARIO_RE = re.compile(r"^SC-[A-Za-z0-9]+-\d{1,4}$")

METADATA_FILE = "metadata.yaml"
BODY_FILE = "prd.md"
TEST_CASES_FILE = "test-cases.md"
REVIEWS_DIR = "reviews"
CHANGES_DIR = "changes"


# --------------------------------------------------------------------------
# 字段校验与归一化
# --------------------------------------------------------------------------
def normalize_id(raw: str, rules: Rules) -> str:
    """`PRD-1` / `1` / `prd-001` 统一成 `PRD-001`。"""
    prefix = rules.id_prefixes.get("prd", "PRD")
    text = str(raw or "").strip()
    if not text:
        raise FlowError("PRD_ID_REQUIRED", "缺少 PRD 编号（--id）")
    upper = text.upper()
    if upper.isdigit():
        return ids.format_id(prefix, int(upper), rules.id_width)
    match = _ID_RE.match(upper)
    if match and match.group("prefix") == prefix.upper():
        return ids.format_id(prefix, int(match.group("num")), rules.id_width)
    return upper


def validate_slug(raw: str, *, field: str = "slug") -> str:
    slug = str(raw or "").strip().lower()
    if not SLUG_RE.match(slug):
        raise FlowError(
            "INVALID_SLUG",
            f"{field} 取值 `{slug}` 不合法：只允许小写字母、数字与连字符",
            object=slug,
            suggestion="例如 batch-parse、resume-parse",
        )
    return slug


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


def parse_scenarios(raw: Iterable[str] | str | None) -> list[str]:
    """解析 `--scenarios a,b`（可重复）；保持顺序、去重、统一大写。不校验数量。"""
    if raw is None:
        return []
    items = [raw] if isinstance(raw, str) else list(raw)
    values: list[str] = []
    for item in items:
        for part in str(item).split(","):
            name = part.strip().upper()
            if not name:
                continue
            if not _SCENARIO_RE.match(name):
                raise FlowError(
                    "INVALID_SCENARIO",
                    f"场景编号 `{part.strip()}` 不合法：必须形如 SC-<模块>-<序号>",
                    object=part.strip(),
                    suggestion="例如 SC-RES-01；编号格式见项目文档 §三 编号与命名规范",
                )
            if name not in values:
                values.append(name)
    return values


def draft_states(rules: Rules) -> tuple[str, ...]:
    declared = (rules.data.get("prd") or {}).get("draft_states")
    if isinstance(declared, list) and declared:
        return tuple(str(item) for item in declared)
    return DRAFT_STATES


def cr_states(rules: Rules) -> tuple[str, ...]:
    """必须走 CR 才能改内容/重新进评审的状态（SRS-FR-049/050，BR-012）。"""
    declared = (rules.data.get("prd") or {}).get("cr_states")
    if isinstance(declared, list) and declared:
        return tuple(str(item) for item in declared)
    return CR_STATES


def is_editable(rules: Rules, status: str) -> bool:
    return status in draft_states(rules)


def max_scenarios(rules: Rules) -> int:
    return rules.limit("max_scenarios_per_prd", 8)


# --------------------------------------------------------------------------
# 对象
# --------------------------------------------------------------------------
@dataclass
class PrdRecord:
    directory: Path
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
    def feature(self) -> str:
        return str(self.meta.get("feature", "") or "")

    @property
    def status(self) -> str:
        return str(self.meta.get("status", ""))

    @property
    def version(self) -> int:
        return int(self.meta.get("version", 1) or 1)

    @property
    def content_hash(self) -> str | None:
        value = self.meta.get("content_hash")
        return str(value) if value else None

    @property
    def source_reqs(self) -> list[str]:
        return [str(item) for item in (self.meta.get("source_reqs") or [])]

    @property
    def related_reqs(self) -> list[str]:
        return [str(item) for item in (self.meta.get("related_reqs") or [])]

    @property
    def scenarios(self) -> list[str]:
        return [str(item) for item in (self.meta.get("scenarios") or [])]

    @property
    def release(self) -> str | None:
        value = self.meta.get("release")
        return str(value) if value else None

    @property
    def metadata_path(self) -> Path:
        return self.directory / METADATA_FILE

    @property
    def body_path(self) -> Path:
        return self.directory / BODY_FILE

    def to_dict(self, *, include_body: bool = False) -> dict[str, Any]:
        data: dict[str, Any] = {
            "id": self.id,
            "slug": self.slug,
            "title": self.title,
            "module": self.module,
            "feature": self.feature,
            "scenarios": self.scenarios,
            "related_reqs": self.related_reqs,
            "source_reqs": self.source_reqs,
            "release": self.release,
            "status": self.status,
            "version": self.version,
            "content_hash": self.content_hash,
            "created_at": self.meta.get("created_at"),
            "created_by": self.meta.get("created_by"),
            "submitted_by": self.meta.get("submitted_by"),
            "approved_by": self.meta.get("approved_by"),
            "approved_at": self.meta.get("approved_at"),
            "path": self.rel,
        }
        if include_body:
            data["body"] = self.body
            data["history"] = list(self.meta.get("history") or [])
            data["confirmations"] = list(self.meta.get("confirmations") or [])
        return data

    def files(self) -> list[str]:
        """参与 hash 的文件（相对 PRD 目录），按共享设计 §6 的固定顺序。"""
        present = [name for name in (BODY_FILE, TEST_CASES_FILE)
                   if (self.directory / name).is_file()]
        return present

    def compute_content_hash(self) -> str:
        return hashing.prd_content_hash(self.directory, self.meta)


def directory_path(workspace: Workspace, *, module: str, prd_id: str, slug: str) -> Path:
    return workspace.prds_dir / module / f"{prd_id}-{slug}"


def initial_metadata(*, prd_id: str, slug: str, title: str, module: str, feature: str,
                     scenarios: list[str], source_reqs: list[str], related_reqs: list[str],
                     release: str | None, actor: str, rules: Rules) -> dict[str, Any]:
    return {
        "id": prd_id,
        "slug": slug,
        "title": title,
        "module": module,
        "feature": feature,
        "scenarios": list(scenarios),
        "related_reqs": list(related_reqs),
        "source_reqs": list(source_reqs),
        "release": release,
        "status": rules.initial_state("prd"),
        "version": 1,
        "content_hash": None,
        "locked_fields": list(LOCKED_FIELDS),
        "created_at": fsutil.now_iso(),
        "created_by": actor,
        "submitted_by": None,
        "approved_by": None,
        "approved_at": None,
        "acceptance": None,
        "closeout": None,
        "history": [],
        "confirmations": [],
    }


# --------------------------------------------------------------------------
# 正文渲染
# --------------------------------------------------------------------------
def render_body(workspace: Workspace, *, prd_id: str, title: str, module: str,
                feature: str, scenarios: list[str], source_reqs: list[str],
                release: str | None) -> str:
    """按项目模板（回退随包模板）生成 PRD 正文骨架，并预填场景/AC 编号骨架。

    只写结构与待填占位，**不伪造**业务结论（共享设计 §3）。
    """
    template = _template_text(workspace)
    lines = template.splitlines()
    body = "\n".join(lines[1:]).lstrip("\n") if lines and lines[0].startswith("# ") else template
    body = _fill_scenarios(body, scenarios)
    header = f"# {prd_id} {title}"
    meta_line = (f"<!-- 模块 {module}"
                 + (f"，功能 {feature}" if feature else "")
                 + (f"，目标版本 {release}" if release else "")
                 + f"，主需求 {'、'.join(source_reqs)} -->")
    return f"{header}\n\n{meta_line}\n\n{body}".rstrip() + "\n"


def _fill_scenarios(body: str, scenarios: list[str]) -> str:
    """把模板里「场景」小节的占位替换成按编号预填的场景/AC 骨架。"""
    if not scenarios:
        return body
    stubs: list[str] = []
    for scenario in scenarios:
        ac_prefix = scenario.replace("SC-", "AC-", 1)
        stubs.extend([
            f"### {scenario} <待填写场景名>",
            "",
            "<!-- TODO: 场景描述：谁、在什么条件下、做什么、期望看到什么。 -->",
            "",
            "**验收标准**",
            "",
            f"- {ac_prefix}-01",
            "  - Given <!-- TODO: 前置条件 -->",
            "  - When <!-- TODO: 触发动作 -->",
            "  - Then <!-- TODO: 可观察结果 -->",
            "",
        ])
    rendered = "\n".join(stubs).rstrip() + "\n"

    heading = "## 场景"
    index = body.find(heading)
    if index < 0:
        return body.rstrip() + "\n\n" + heading + "\n\n" + rendered
    start = index + len(heading)
    following = body.find("\n## ", start)
    tail = body[following:] if following >= 0 else ""
    return body[:start] + "\n\n" + rendered + tail


def _template_text(workspace: Workspace) -> str:
    candidates = (
        workspace.templates_dir / "prd.md",
        Path(__file__).resolve().parents[1] / "templates" / "prd.md",
    )
    for path in candidates:
        if path.is_file():
            return fsutil.read_text(path)
    return _FALLBACK_TEMPLATE


_FALLBACK_TEMPLATE = """# PRD-XXX <功能名称>

## 背景与目标

<!-- TODO: 业务背景、目标与衡量方式。 -->

## 范围

<!-- 必须做 / 不做 / 以后做 -->

## 现状与变化

<!-- TODO: 已有功能现状；新功能且无现状时写「新功能，无现状」。 -->

## 场景

<!-- TODO: 场景上限 8 个。 -->

## 非功能需求

<!-- TODO: 性能、安全、可用性、兼容性等可验证要求。 -->

## 数据需求

<!-- TODO: 涉及的数据、来源、保留与隐私要求。 -->

## 依赖与风险

<!-- TODO: 外部依赖、技术风险、回滚方式。 -->

## 待确认问题

<!-- TODO: 尚未决定的事项，以及需要谁决定。 -->
"""


# --------------------------------------------------------------------------
# 读写
# --------------------------------------------------------------------------
def load(workspace: Workspace, prd_id: str, *, rules: Rules) -> PrdRecord:
    target = normalize_id(prd_id, rules)
    found = find(workspace, target)
    if found is None:
        raise FlowError(
            "PRD_NOT_FOUND",
            f"找不到 PRD {target}",
            object=target,
            suggestion="用 flow-core prd list 查看已有 PRD，或先用 flow-core prd create 创建草稿",
        )
    return found


def find(workspace: Workspace, prd_id: str) -> PrdRecord | None:
    """按 ID 定位 PRD 目录（`04-prds/<module>/PRD-NNN-<slug>/`）。"""
    root = workspace.prds_dir
    if not root.is_dir():
        return None
    for meta_path in sorted(root.glob(f"*/{prd_id}-*/{METADATA_FILE}")):
        return _record(workspace, meta_path.parent)
    return None


def list_prds(workspace: Workspace) -> list[PrdRecord]:
    root = workspace.prds_dir
    if not root.is_dir():
        return []
    records: list[PrdRecord] = []
    for meta_path in sorted(root.glob(f"*/*/{METADATA_FILE}")):
        try:
            records.append(_record(workspace, meta_path.parent))
        except Exception:  # 损坏文件不影响清单，由 lint 报告
            continue
    records.sort(key=lambda item: item.id)
    return records


def _record(workspace: Workspace, directory: Path) -> PrdRecord:
    meta = fsutil.read_yaml(directory / METADATA_FILE)
    if not isinstance(meta, dict):
        raise FlowError("PRD_METADATA_INVALID",
                        f"{workspace.rel(directory)} 的 metadata.yaml 不是 YAML 映射",
                        object=workspace.rel(directory))
    body_path = directory / BODY_FILE
    body = fsutil.read_text(body_path) if body_path.is_file() else ""
    return PrdRecord(directory=directory, rel=workspace.rel(directory), meta=meta, body=body)


def save(workspace: Workspace, record: PrdRecord) -> None:
    """只写 metadata；正文由调用方按需写（`save_body`）。"""
    fsutil.atomic_write_yaml(record.metadata_path, record.meta)


def save_body(workspace: Workspace, record: PrdRecord, body: str) -> None:
    fsutil.atomic_write_text(record.body_path, body)
    record.body = body


def review_path(record: PrdRecord, at: str | None = None) -> Path:
    """`reviews/YYYY-MM-DD-review.md`；同一天多次提交追加序号。"""
    stamp = (at or fsutil.now_iso())[:10]
    base = record.directory / REVIEWS_DIR / f"{stamp}-review.md"
    if not base.exists():
        return base
    index = 2
    while True:
        candidate = record.directory / REVIEWS_DIR / f"{stamp}-review-{index:02d}.md"
        if not candidate.exists():
            return candidate
        index += 1


# --------------------------------------------------------------------------
# 结构自审（ticket 03 的 validate / self-review 共用；ticket 04/05 可复用）
# --------------------------------------------------------------------------
class SectionSpec:
    __slots__ = ("name", "aliases")

    def __init__(self, name: str, aliases: tuple[str, ...]) -> None:
        self.name = name
        self.aliases = aliases


# 必备章节（SRS-FR-042）。「现状与变化」单独判定（SRS-FR-044），避免重复报错。
REQUIRED_SECTIONS: tuple[SectionSpec, ...] = (
    SectionSpec("背景与目标", ("背景与目标", "背景目标")),
    SectionSpec("范围", ("范围", "范围与非范围")),
    SectionSpec("场景", ("场景",)),
    SectionSpec("非功能需求", ("非功能需求", "非功能")),
    SectionSpec("数据需求", ("数据需求", "埋点与数据需求")),
    SectionSpec("依赖与风险", ("依赖与风险", "依赖风险")),
    SectionSpec("待确认问题", ("待确认问题", "待确认")),
)
CURRENT_STATE_SECTIONS: tuple[str, ...] = ("现状与变化", "现状")
NEW_FEATURE_DECLARATIONS: tuple[str, ...] = (
    "新功能，无现状", "新功能,无现状", "新功能无现状", "无现状（新功能）",
)
SCOPE_OUT_KEYWORDS: tuple[str, ...] = ("不做", "不在本次范围", "非目标", "本期不做", "以后做")

# 「说不清」的词（SRS 自审清单 §4.7）
AMBIGUOUS_WORDS: tuple[str, ...] = (
    "快速", "尽快", "友好", "适当", "尽量", "尽可能", "合理地", "合理",
    "更好", "优秀", "若干", "等等", "大幅", "显著提升",
)

_AC_LINE_RE = re.compile(r"^\s*[-*+]\s*(AC-[A-Za-z0-9][A-Za-z0-9-]*)")
_SCENARIO_HEADING_RE = re.compile(r"^###\s+(SC-[A-Za-z0-9]+-\d{1,4})\b")
_GIVEN = ("given", "假设", "假定", "给定", "假如", "前置条件", "前置")
_WHEN = ("when", "当", "触发")
_THEN = ("then", "则", "结果", "预期")

SEVERITY_ERROR = "error"
SEVERITY_WARNING = "warning"


def issue(code: str, *, severity: str, object: str, message: str,
          suggestion: str = "") -> dict[str, Any]:
    return {
        "code": code,
        "severity": severity,
        "object": object,
        "message": message,
        "suggestion": suggestion,
    }


def has_error(issues: Iterable[dict[str, Any]]) -> bool:
    return any(item.get("severity") == SEVERITY_ERROR for item in issues)


def review(workspace: Workspace, record: PrdRecord, rules: Rules) -> list[dict[str, Any]]:
    """结构自审：只读，返回问题清单（不修改正文、不修改 metadata）。"""
    issues: list[dict[str, Any]] = []
    sections = _sections(record.body)
    issues.extend(_check_sections(record, sections))
    issues.extend(_check_current_state(workspace, record, sections))
    issues.extend(_check_scenarios(record, sections, rules))
    issues.extend(_check_scope(sections))
    issues.extend(_check_ambiguous(record.body))
    issues.extend(_check_terms(workspace, record.body))
    issues.extend(_check_cross_documents(workspace, record))
    return issues


def checklist(record: PrdRecord, issues: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """`prd self-review` 的清单投影：把问题按自审项归组。"""
    groups = (
        ("章节完整", "MISSING_SECTION"),
        ("范围明确", "SCOPE_UNSPECIFIED"),
        ("现状与变化", "MISSING_CURRENT_STATE"),
        ("场景数量", "TOO_MANY_SCENARIOS"),
        ("场景有 AC", "SCENARIO_WITHOUT_AC"),
        ("AC 可测试", "TESTABILITY_WEAK"),
        ("用词明确", "AMBIGUOUS_WORD"),
        ("术语一致", "TERM_INCONSISTENT"),
        ("跨文档一致", "CROSS_DOC_CONFLICT"),
    )
    items: list[dict[str, Any]] = []
    for label, code in groups:
        matched = [item for item in issues if item.get("code") == code]
        if any(item["severity"] == SEVERITY_ERROR for item in matched):
            status = "fail"
        elif matched:
            status = "warn"
        else:
            status = "ok"
        detail = "；".join(item["message"] for item in matched) if matched else "通过"
        items.append({"item": label, "code": code, "status": status, "detail": detail})
    return items


def _sections(body: str) -> dict[str, str]:
    """按二级标题切分正文；`###` 子标题归属其上级小节。"""
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


def _strip_comments(text: str) -> str:
    return re.sub(r"<!--.*?-->", "", text, flags=re.DOTALL)


def _section_body(sections: dict[str, str], aliases: Iterable[str]) -> str | None:
    for name in aliases:
        if name in sections:
            return sections[name]
    return None


PLACEHOLDER_TOKENS: tuple[str, ...] = ("<待填写", "<待补充", "TODO:", "PRD-XXX", "SC-XXX", "AC-XXX")


def _meaningful(text: str | None) -> str:
    """去掉注释与模板占位后仍剩下实质内容 → 视为该小节已填写。"""
    if text is None:
        return ""
    stripped = _strip_comments(text).strip()
    if not stripped:
        return ""
    if any(token in stripped for token in PLACEHOLDER_TOKENS):
        # 只保留不含占位符的行，看看还有没有真实内容
        keep = [line for line in stripped.splitlines()
                if line.strip() and not any(token in line for token in PLACEHOLDER_TOKENS)]
        return "\n".join(keep).strip()
    return stripped


def _check_sections(record: PrdRecord, sections: dict[str, str]) -> list[dict[str, Any]]:
    issues: list[dict[str, Any]] = []
    for spec in REQUIRED_SECTIONS:
        if _meaningful(_section_body(sections, spec.aliases)):
            continue
        issues.append(issue(
            "MISSING_SECTION",
            severity=SEVERITY_ERROR,
            object=spec.name,
            message=f"缺少必要的「{spec.name}」小节或内容仍是模板占位",
            suggestion=f"补写「## {spec.name}」小节；模板见 .specify/templates/prd.md",
        ))
    return issues


def _check_current_state(workspace: Workspace, record: PrdRecord,
                         sections: dict[str, str]) -> list[dict[str, Any]]:
    content = _section_body(sections, CURRENT_STATE_SECTIONS)
    meaningful = _meaningful(content)
    if meaningful:
        return []
    if declares_new_feature(record.body):
        return []
    feature_doc = feature_doc_path(workspace, record.module, record.feature)
    hint = f"引用 {workspace.rel(feature_doc)}" if feature_doc.is_file() else "写明现状来源"
    return [issue(
        "MISSING_CURRENT_STATE",
        severity=SEVERITY_ERROR,
        object=record.id,
        message="缺少「现状与变化」：既没有功能现状，" + "也没有明确声明「新功能，无现状」",
        suggestion=f"补写「## 现状与变化」：{hint}；新功能请明确写「新功能，无现状」（SRS-FR-044）",
    )]


def _check_scenarios(record: PrdRecord, sections: dict[str, str],
                     rules: Rules) -> list[dict[str, Any]]:
    issues: list[dict[str, Any]] = []
    limit = max_scenarios(rules)
    content = _section_body(sections, ("场景",)) or ""
    blocks = _scenario_blocks(content)
    declared = record.scenarios
    total = max(len(blocks), len(declared))
    if total > limit:
        issues.append(issue(
            "TOO_MANY_SCENARIOS",
            severity=SEVERITY_ERROR,
            object=record.id,
            message=f"场景数 {total} 超过上限 {limit}，需要拆分",
            suggestion=f"按功能边界把场景拆到多个 PRD（上限见 limits.max_scenarios_per_prd={limit}）",
        ))

    for name, block in blocks:
        if not _ac_blocks(block):
            issues.append(issue(
                "SCENARIO_WITHOUT_AC",
                severity=SEVERITY_ERROR,
                object=name,
                message=f"场景 {name} 没有验收标准（AC）",
                suggestion="为该场景补至少一条编号 AC，并按 Given / When / Then 写明",
            ))
            continue
        for ac_id, ac_block in _ac_blocks(block):
            missing = _missing_gwt(ac_block)
            if not missing:
                continue
            issues.append(issue(
                "TESTABILITY_WEAK",
                severity=SEVERITY_ERROR,
                object=ac_id,
                message=f"{ac_id} 缺少 {'/'.join(missing)}，无法验证",
                suggestion="把该 AC 改写成 Given（前置）/ When（动作）/ Then（可观察结果）三段式",
            ))

    for scenario in declared:
        if blocks and scenario not in dict(blocks):
            issues.append(issue(
                "SCENARIO_NOT_IN_BODY",
                severity=SEVERITY_WARNING,
                object=scenario,
                message=f"metadata 声明的场景 {scenario} 在正文中找不到对应小节",
                suggestion="在「## 场景」下补 ### " + scenario + " 小节，或从 metadata 的场景列表移除",
            ))
    return issues


def _scenario_blocks(content: str) -> list[tuple[str, str]]:
    blocks: list[tuple[str, str]] = []
    current: str | None = None
    buffer: list[str] = []
    for line in content.splitlines():
        match = _SCENARIO_HEADING_RE.match(line)
        if match:
            if current is not None:
                blocks.append((current, "\n".join(buffer)))
            current = match.group(1).upper()
            buffer = []
        elif current is not None:
            buffer.append(line)
    if current is not None:
        blocks.append((current, "\n".join(buffer)))
    return blocks


def _ac_blocks(block: str) -> list[tuple[str, str]]:
    items: list[tuple[str, str]] = []
    current: str | None = None
    buffer: list[str] = []
    for line in block.splitlines():
        match = _AC_LINE_RE.match(line)
        if match:
            if current is not None:
                items.append((current, "\n".join(buffer)))
            current = match.group(1).upper()
            buffer = [line]
        elif current is not None:
            buffer.append(line)
    if current is not None:
        items.append((current, "\n".join(buffer)))
    return items


_GWT_TOKEN_RE = re.compile(r"[\w\u3400-\u9fff]+", re.UNICODE)


def _label_pattern(tokens: tuple[str, ...]) -> str:
    """构造标签正则。

    ASCII 标签（given/when/then）要求前后是词边界，避免 because/otherwise 误命中；
    中文标签（假设/当/则…）允许后面直接接内容（中文没有词间空格），
    但仍要求前面是边界，避免「适当」「结果」在普通句子里被当成标签。
    """
    parts: list[str] = []
    for token in tokens:
        if token.isascii():
            parts.append(rf"\b{re.escape(token)}\b")
        else:
            parts.append(rf"(?:^|[\s:：*+\-、，,])({re.escape(token)})")
    return "|".join(parts)


def _gwt_content(text: str, tokens: tuple[str, ...]) -> str:
    """返回标签之后、下一个 GWT 标签之前的**实质**内容（不是纯标签/标点）。"""
    match = re.search(_label_pattern(tokens), text, re.IGNORECASE | re.MULTILINE)
    if not match:
        return ""
    rest = text[match.end():]
    # 去掉标签后的分隔符（冒号、破折号、空白等），只留正文。
    rest = re.sub(r"^[\s:：\-—、，,]*", "", rest)
    all_tokens = _GIVEN + _WHEN + _THEN
    next_label = re.search(_label_pattern(all_tokens), rest,
                           re.IGNORECASE | re.MULTILINE)
    if next_label:
        rest = rest[:next_label.start()]
    # 内容不能只是标点、项目符号或另一个 GWT 标签本身。
    words = _GWT_TOKEN_RE.findall(rest)
    kept = " ".join(word for word in words
                    if word.lower() not in all_tokens)
    return kept.strip()


def _missing_gwt(ac_block: str) -> list[str]:
    text = _strip_comments(ac_block)
    missing: list[str] = []
    if not _gwt_content(text, _GIVEN):
        missing.append("Given")
    if not _gwt_content(text, _WHEN):
        missing.append("When")
    if not _gwt_content(text, _THEN):
        missing.append("Then")
    return missing


def _check_scope(sections: dict[str, str]) -> list[dict[str, Any]]:
    content = _meaningful(_section_body(sections, ("范围", "范围与非范围")))
    if not content:
        return []
    if any(keyword in content for keyword in SCOPE_OUT_KEYWORDS):
        return []
    return [issue(
        "SCOPE_UNSPECIFIED",
        severity=SEVERITY_WARNING,
        object="范围",
        message="「范围」没有写明本期不做什么",
        suggestion="补一句「本期不做：…」，避免开发阶段范围蔓延（SRS 自审清单）",
    )]


def _check_ambiguous(body: str) -> list[dict[str, Any]]:
    text = _strip_comments(body)
    issues: list[dict[str, Any]] = []
    for word in AMBIGUOUS_WORDS:
        if word in text:
            issues.append(issue(
                "AMBIGUOUS_WORD",
                severity=SEVERITY_WARNING,
                object=word,
                message=f"正文出现说不清的词「{word}」",
                suggestion="换成可验证的量化描述（数值、范围、阈值）",
            ))
    return issues


def _check_terms(workspace: Workspace, body: str) -> list[dict[str, Any]]:
    entries = glossary_synonyms(workspace)
    text = _strip_comments(body)
    issues: list[dict[str, Any]] = []
    for canonical, synonyms in entries:
        if canonical not in text:
            continue
        used = [word for word in synonyms if word in text]
        if not used:
            continue
        issues.append(issue(
            "TERM_INCONSISTENT",
            severity=SEVERITY_WARNING,
            object=canonical,
            message=f"同一概念混用了「{canonical}」与「{'、'.join(used)}」",
            suggestion=f"按 02-definition/glossary.md 统一用「{canonical}」",
        ))
    return issues


def glossary_synonyms(workspace: Workspace) -> list[tuple[str, list[str]]]:
    """从 `02-definition/glossary.md` 读出「术语 → 同义词」映射（容忍常见写法）。"""
    path = workspace.path("02-definition/glossary.md")
    if not path.is_file():
        return []
    entries: list[tuple[str, list[str]]] = []
    pattern = re.compile(r"同义(?:词)?\s*[:：]\s*([^\n)）]+)")
    for line in fsutil.read_text(path).splitlines():
        if "TODO" in line or "待填写" in line:
            continue
        match = pattern.search(line)
        if not match:
            continue
        head = line[:match.start()]
        head = head.strip(" -|*#\t")
        head = head.split("（")[0].split("(")[0].strip(" ：:|")
        if not head:
            continue
        synonyms = [word.strip() for word in re.split(r"[、,，/\s]+", match.group(1)) if word.strip()]
        if synonyms:
            entries.append((head, synonyms))
    return entries


def _check_cross_documents(workspace: Workspace, record: PrdRecord) -> list[dict[str, Any]]:
    issues: list[dict[str, Any]] = []

    # 1) 其他 PRD 已占用的场景编号
    for other in list_prds(workspace):
        if other.id == record.id:
            continue
        shared = sorted(set(record.scenarios) & set(other.scenarios))
        if shared:
            issues.append(issue(
                "CROSS_DOC_CONFLICT",
                severity=SEVERITY_WARNING,
                object=other.id,
                message=f"场景 {'、'.join(shared)} 同时出现在 {other.id}",
                suggestion="一个场景只属于一个 PRD；确认归属后从其中一处移除",
            ))

    # 2) 已存在功能现状文档，却声明「新功能，无现状」
    feature_doc = feature_doc_path(workspace, record.module, record.feature)
    if declares_new_feature(record.body) and feature_doc.is_file():
        issues.append(issue(
            "CROSS_DOC_CONFLICT",
            severity=SEVERITY_WARNING,
            object=workspace.rel(feature_doc),
            message="正文声明「新功能，无现状」，但功能现状文档已存在",
            suggestion="把「## 现状与变化」改为引用现有功能现状文档并写清本次变化",
        ))
    return issues


def declares_new_feature(body: str) -> bool:
    """正文（去掉注释与模板占位）是否明确声明「新功能，无现状」（SRS-FR-044）。

    只看正文实质内容：模板注释里也写着这句话，不能算声明。
    """
    text = _strip_comments(body)
    if not any(token in text for token in NEW_FEATURE_DECLARATIONS):
        return False
    return bool(_meaningful(text))


def feature_doc_path(workspace: Workspace, module: str, feature: str) -> Path:
    """功能现状文档路径：`02-definition/modules/<module>/features/<feature>.md`。"""
    return workspace.modules_dir / (module or "_") / "features" / f"{feature or '_'}.md"
