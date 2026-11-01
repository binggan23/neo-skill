"""根目录解析、路径常量与项目骨架清单。

骨架清单是 `init` 的唯一事实来源：新增业务目录/文档只改这里的 `SKELETON`，
不要在各命令里散落路径字符串。
"""

from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path

META_DIR = ".neo-skill"
SPECIFY_DIR = ".specify"

LOCKS_DIR = f"{META_DIR}/locks"
TRANSACTIONS_DIR = f"{META_DIR}/transactions"
COUNTERS_FILE = f"{META_DIR}/counters.yaml"
INIT_STATE_FILE = f"{META_DIR}/init-state.yaml"

RULES_FILE = f"{SPECIFY_DIR}/rules/flow.yaml"
TEMPLATES_DIR = f"{SPECIFY_DIR}/templates"
CONSTITUTION_FILE = f"{SPECIFY_DIR}/constitution.md"

REQ_ITEMS_DIR = "03-requirements/items"
BACKLOG_FILE = "03-requirements/backlog.md"
PRDS_DIR = "04-prds"
PRD_INDEX_FILE = "04-prds/index.md"
SPECS_DIR = "06-specs"
ACCEPTANCE_DIR = "07-acceptance"
MODULES_DIR = "02-definition/modules"
FEATURE_MAP_FILE = "02-definition/feature-map.md"
TEAM_FILE = "00-management/team.md"

# 裁剪组 → 受影响的路径前缀（组名同时是 `init --prune` 的取值）
PRUNE_GROUPS: dict[str, tuple[str, ...]] = {
    "discovery": ("01-discovery",),
    "design": ("05-design",),
    "growth": ("09-growth",),
    "releases": ("08-releases",),
    "meetings": ("00-management/meetings",),
    "reports": ("00-management/reports",),
}

# 不可裁剪的核心能力（请求裁剪 → NOT_PRUNABLE）
NOT_PRUNABLE = (
    "00-management/charter.md",
    "02-definition/mvp-scope.md",
    "03-requirements",
    "04-prds",
    "07-acceptance",
    SPECIFY_DIR,
    META_DIR,
)

# 裁剪请求里常被用来指代核心内容的别名 → 错误提示用
CORE_ALIASES = {
    "core": "核心项目骨架",
    "specify": SPECIFY_DIR,
    "specs": SPECS_DIR,
    "acceptance": ACCEPTANCE_DIR,
    "requirements": "03-requirements",
    "prds": PRDS_DIR,
    "management": "00-management",
    "definition": "02-definition",
    "00-management": "00-management",
    "02-definition": "02-definition",
}


@dataclass(frozen=True)
class SkeletonEntry:
    """骨架条目。`kind` 决定生成方式，`group` 决定是否可裁剪。"""

    path: str
    kind: str  # dir | doc | rules | template | meta
    group: str | None = None
    title: str = ""
    hint: str = ""


# 业务文档：只写标题 + 待填写说明 + 占位，不伪造业务结论
_DOC_HINTS: dict[str, tuple[str, str]] = {
    "00-management/charter.md": (
        "立项书",
        "记录项目要解决的问题、目标用户、成功标准和边界。由项目负责人填写。",
    ),
    "00-management/roadmap.md": (
        "版本路线图",
        "记录版本节奏与每个版本的目标；P0 不实现完整 release 能力。",
    ),
    "00-management/risks.md": (
        "风险与问题清单",
        "记录已识别的风险、影响、应对措施与负责人。",
    ),
    "01-discovery/opportunity.md": (
        "机会描述",
        "记录问题、目标用户与为什么是现在。",
    ),
    "01-discovery/hypotheses.md": (
        "核心假设",
        "按「假设 → 验证方式 → 证据 → 结论」记录。",
    ),
    "01-discovery/insights.md": (
        "调研结论汇总",
        "汇总调研证据，作为评审输入。",
    ),
    "02-definition/vision.md": (
        "愿景与定位",
        "记录愿景、定位与价值主张。",
    ),
    "02-definition/business-model.md": (
        "商业模式",
        "记录商业模式画布要素。",
    ),
    "02-definition/metrics.md": (
        "指标体系",
        "记录北极星指标与支撑指标。",
    ),
    "02-definition/mvp-scope.md": (
        "MVP 范围",
        "记录必须做 / 不做 / 以后做的范围判断。不可裁剪。",
    ),
    "02-definition/glossary.md": (
        "术语表",
        "统一人和 AI 的用词，避免同义词漂移。",
    ),
    "05-design/design-system.md": (
        "设计规范",
        "记录视觉、组件与交互约定。",
    ),
    "06-specs/architecture.md": (
        "整体架构",
        "记录系统组成、边界与关键技术取舍。",
    ),
    "06-specs/data-model.md": (
        "数据模型",
        "记录实体、关系与约束。",
    ),
    "08-releases/CHANGELOG.md": (
        "变更日志",
        "按版本记录交付内容。",
    ),
    "09-growth/dashboards.md": (
        "看板与指标口径",
        "记录看板链接与指标口径。",
    ),
}


def _doc(path: str, group: str | None = None) -> SkeletonEntry:
    title, hint = _DOC_HINTS[path]
    return SkeletonEntry(path=path, kind="doc", group=group, title=title, hint=hint)


SKELETON: tuple[SkeletonEntry, ...] = (
    # 项目卡片与 AI 约定
    SkeletonEntry("README.md", "doc", title="项目卡片", hint="项目定位、当前阶段、当前版本、负责人与导航。"),
    SkeletonEntry("AGENTS.md", "agents", title="AI 工作流与硬规则", hint="目录约定、各 Skill 职责与协作规则。"),
    # .specify 项目规则基线
    SkeletonEntry(SPECIFY_DIR, "dir"),
    SkeletonEntry(f"{SPECIFY_DIR}/rules", "dir"),
    SkeletonEntry(f"{SPECIFY_DIR}/templates", "dir"),
    SkeletonEntry(f"{SPECIFY_DIR}/scripts", "dir"),
    SkeletonEntry(RULES_FILE, "rules"),
    SkeletonEntry(f"{SPECIFY_DIR}/scripts/README.md", "doc", title="流程脚本", hint="记录项目自有的辅助脚本及其用途。"),
    SkeletonEntry(CONSTITUTION_FILE, "constitution"),
    # 00-management
    SkeletonEntry("00-management", "dir"),
    _doc("00-management/charter.md"),
    _doc("00-management/roadmap.md"),
    SkeletonEntry("00-management/team.md", "team", title="团队与职责", hint="人员标识、姓名与职责映射。"),
    _doc("00-management/risks.md"),
    SkeletonEntry("00-management/decisions", "dir"),
    SkeletonEntry("00-management/meetings", "dir", group="meetings"),
    SkeletonEntry("00-management/reports", "dir", group="reports"),
    # 01-discovery
    SkeletonEntry("01-discovery", "dir", group="discovery"),
    _doc("01-discovery/opportunity.md", "discovery"),
    _doc("01-discovery/hypotheses.md", "discovery"),
    _doc("01-discovery/insights.md", "discovery"),
    SkeletonEntry("01-discovery/market", "dir", group="discovery"),
    SkeletonEntry("01-discovery/competitors", "dir", group="discovery"),
    SkeletonEntry("01-discovery/users", "dir", group="discovery"),
    SkeletonEntry("01-discovery/users/personas", "dir", group="discovery"),
    SkeletonEntry("01-discovery/users/interviews", "dir", group="discovery"),
    SkeletonEntry("01-discovery/users/surveys", "dir", group="discovery"),
    SkeletonEntry("01-discovery/experiments", "dir", group="discovery"),
    # 02-definition
    SkeletonEntry("02-definition", "dir"),
    _doc("02-definition/vision.md"),
    _doc("02-definition/business-model.md"),
    _doc("02-definition/metrics.md"),
    _doc("02-definition/mvp-scope.md"),
    _doc("02-definition/glossary.md"),
    SkeletonEntry(FEATURE_MAP_FILE, "doc", title="功能地图", hint="模块 / 功能 / 场景总表，受管理区块由 flow-core index 生成。"),
    SkeletonEntry(MODULES_DIR, "dir"),
    # 03-requirements
    SkeletonEntry("03-requirements", "dir"),
    SkeletonEntry(REQ_ITEMS_DIR, "dir"),
    SkeletonEntry("03-requirements/feedback", "dir"),
    SkeletonEntry(BACKLOG_FILE, "doc", title="需求总览", hint="受管理区块由 flow-core index 生成，区块外说明可自由编辑。"),
    # 04-prds
    SkeletonEntry(PRDS_DIR, "dir"),
    SkeletonEntry(PRD_INDEX_FILE, "doc", title="PRD 总览", hint="受管理区块由 flow-core index 生成，区块外说明可自由编辑。"),
    # 05-design
    SkeletonEntry("05-design", "dir", group="design"),
    _doc("05-design/design-system.md", "design"),
    SkeletonEntry("05-design/assets", "dir", group="design"),
    # 06-specs
    SkeletonEntry(SPECS_DIR, "dir"),
    _doc("06-specs/architecture.md"),
    _doc("06-specs/data-model.md"),
    SkeletonEntry("06-specs/api", "dir"),
    SkeletonEntry("06-specs/adr", "dir"),
    # 07-acceptance
    SkeletonEntry(ACCEPTANCE_DIR, "dir"),
    # 08-releases
    SkeletonEntry("08-releases", "dir", group="releases"),
    _doc("08-releases/CHANGELOG.md", "releases"),
    # 09-growth
    SkeletonEntry("09-growth", "dir", group="growth"),
    _doc("09-growth/dashboards.md", "growth"),
    SkeletonEntry("09-growth/analysis", "dir", group="growth"),
    SkeletonEntry("09-growth/experiments", "dir", group="growth"),
    SkeletonEntry("09-growth/retros", "dir", group="growth"),
    # 代码与归档
    SkeletonEntry("src", "dir"),
    SkeletonEntry("tests", "dir"),
    SkeletonEntry("archive", "dir"),
    # 运行时元数据
    SkeletonEntry(META_DIR, "dir"),
    SkeletonEntry(LOCKS_DIR, "dir"),
    SkeletonEntry(TRANSACTIONS_DIR, "dir"),
)

TEMPLATE_NAMES: tuple[str, ...] = (
    "req.md",
    "prd.md",
    "spec.md",
    "plan.md",
    "tasks.md",
    "verification.md",
    "demo.md",
    "checklist.md",
    "report.md",
    "constitution.md",
)

COUNTER_KEYS: tuple[str, ...] = ("REQ", "PRD", "CR", "SC", "ADR", "DEC")

# 项目级写入（init 等跨对象操作）使用这个锁对象；业务对象用 REQ-xxx / PRD-xxx。
PROJECT_LOCK_OBJECT = "PROJECT"


class Workspace:
    """项目根与派生路径。所有命令都通过它定位文件，禁止手写相对路径。"""

    def __init__(self, root: Path) -> None:
        self.root = Path(root).resolve()

    # ---- 路径 ----
    def path(self, *parts: str) -> Path:
        return self.root.joinpath(*parts)

    @property
    def meta_dir(self) -> Path:
        return self.path(META_DIR)

    @property
    def locks_dir(self) -> Path:
        return self.path(LOCKS_DIR)

    @property
    def transactions_dir(self) -> Path:
        return self.path(TRANSACTIONS_DIR)

    @property
    def counters_file(self) -> Path:
        return self.path(COUNTERS_FILE)

    @property
    def init_state_file(self) -> Path:
        return self.path(INIT_STATE_FILE)

    @property
    def project_rules_file(self) -> Path:
        return self.path(RULES_FILE)

    @property
    def templates_dir(self) -> Path:
        return self.path(TEMPLATES_DIR)

    @property
    def constitution_file(self) -> Path:
        return self.path(CONSTITUTION_FILE)

    @property
    def team_file(self) -> Path:
        return self.path(TEAM_FILE)

    @property
    def backlog_file(self) -> Path:
        return self.path(BACKLOG_FILE)

    @property
    def prd_index_file(self) -> Path:
        return self.path(PRD_INDEX_FILE)

    @property
    def feature_map_file(self) -> Path:
        return self.path(FEATURE_MAP_FILE)

    @property
    def req_items_dir(self) -> Path:
        return self.path(REQ_ITEMS_DIR)

    @property
    def prds_dir(self) -> Path:
        return self.path(PRDS_DIR)

    @property
    def specs_dir(self) -> Path:
        return self.path(SPECS_DIR)

    @property
    def acceptance_dir(self) -> Path:
        return self.path(ACCEPTANCE_DIR)

    @property
    def modules_dir(self) -> Path:
        return self.path(MODULES_DIR)

    def rel(self, path: Path) -> str:
        """项目内相对路径（POSIX 分隔符），用于信封与 metadata。"""
        try:
            return Path(path).resolve().relative_to(self.root).as_posix()
        except ValueError:
            return Path(path).as_posix()

    @property
    def initialized(self) -> bool:
        return self.init_state_file.is_file()

    def __repr__(self) -> str:  # pragma: no cover - 调试用
        return f"Workspace({self.root})"


def resolve_root(explicit: str | os.PathLike[str] | None = None,
                 start: Path | None = None) -> Path:
    """根目录解析：`--root` → 向上找 `.neo-skill/` 或 `.specify/` → cwd。"""
    if explicit:
        return Path(explicit).expanduser().resolve()
    current = (start or Path.cwd()).resolve()
    for candidate in (current, *current.parents):
        if (candidate / META_DIR).is_dir() or (candidate / SPECIFY_DIR).is_dir():
            return candidate
    return current


def prune_group_of(rel_path: str) -> str | None:
    """返回路径所属裁剪组（未分组返回 None）。"""
    for group, prefixes in PRUNE_GROUPS.items():
        for prefix in prefixes:
            if rel_path == prefix or rel_path.startswith(prefix + "/"):
                return group
    return None


def is_not_prunable(rel_path: str) -> bool:
    for core in NOT_PRUNABLE:
        if rel_path == core or rel_path.startswith(core + "/"):
            return True
    return False
