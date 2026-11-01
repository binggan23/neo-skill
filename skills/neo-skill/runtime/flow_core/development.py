"""开发域：门禁（dev gate）、Spec/Plan/Tasks 生成、`.baseline.yaml` 与范围检查（ticket 05）。

为什么单独一个模块：
- `dev gate` 的九项检查是**只读判定**，ticket 06（验证与提交验收）要复用同一套门禁，
  不能复制两份；
- `06-specs/<module>/PRD-NNN-<slug>/` 的路径、front matter 与 `.baseline.yaml` 约定
  必须只有一处定义，否则 ticket 04（CR 使基线失效）、06、09（lint）会各写一份。

本模块只做读写与判定：**不取锁、不改状态、不写 history**。锁、状态跳转与写入顺序由
`flow_core/commands/dev.py` 按共享设计 §2.3 组织。

约定（共享设计 §4.3、§6）：

    06-specs/<module>/PRD-NNN-<slug>/
        spec.md   plan.md   tasks.md
        .baseline.yaml
        verification.md    （ticket 06）

`.baseline.yaml` 由 `dev start` 写，`prd cr`（ticket 04）会把 `baseline_valid` 置 false
并追加 `invalidated_by` / `invalidated_at`；本模块只读取，不覆盖 ticket 04 追加的字段。

允许的状态集合、允许的变更路径前缀、PRD 基线允许的状态全部读 `rules/flow.yaml`
（`dev` 段），代码里只保留与规则缺字段时等价的默认值。
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from pathlib import PurePosixPath
from typing import Any, Iterable

from . import fsutil, metadata, requirements as reqs
from .output import FlowError
from .rules import Rules
from .workspace import Workspace

SPEC_FILE = "spec.md"
PLAN_FILE = "plan.md"
TASKS_FILE = "tasks.md"
VERIFICATION_FILE = "verification.md"
BASELINE_FILE = ".baseline.yaml"

SPEC_FILES: tuple[str, ...] = (SPEC_FILE, PLAN_FILE, TASKS_FILE)

# 规则缺字段时的等价默认值（与 rules/flow.yaml 的 dev 段保持一致）
DEFAULT_START_STATES: tuple[str, ...] = ("APPROVED", "DEVELOPING")
DEFAULT_ALLOWED_PATHS: tuple[str, ...] = ("src", "tests", "06-specs")

FLOW_CORE = "flow-core"
CR_HINT = "flow-core prd cr --id {prd_id} --reason \"<变更原因>\""

_SCENARIO_TOKEN_RE = re.compile(r"\bSC-[A-Za-z0-9]+-\d{1,4}\b")
_AC_TOKEN_RE = re.compile(r"\bAC-[A-Za-z0-9]+-\d{1,4}(?:-\d{1,4})?\b")


# --------------------------------------------------------------------------
# 规则视图（全部来自 rules/flow.yaml 的 dev 段）
# --------------------------------------------------------------------------
def _dev_section(rules: Rules) -> dict[str, Any]:
    section = rules.data.get("dev")
    return section if isinstance(section, dict) else {}


def start_states(rules: Rules) -> tuple[str, ...]:
    """允许启动开发的 PRD 状态：`APPROVED`（首次）+ `DEVELOPING`（幂等重启）。"""
    declared = _dev_section(rules).get("start_states")
    if isinstance(declared, list) and declared:
        return tuple(str(item) for item in declared)
    return DEFAULT_START_STATES


def allowed_path_prefixes(rules: Rules) -> tuple[str, ...]:
    declared = _dev_section(rules).get("allowed_paths")
    if isinstance(declared, list) and declared:
        return tuple(str(item) for item in declared)
    return DEFAULT_ALLOWED_PATHS


def spec_files(rules: Rules) -> tuple[str, ...]:
    declared = _dev_section(rules).get("spec_files")
    if isinstance(declared, list) and declared:
        return tuple(str(item) for item in declared)
    return SPEC_FILES


# --------------------------------------------------------------------------
# 路径与基线文件
# --------------------------------------------------------------------------
def spec_dir(workspace: Workspace, record: metadata.PrdRecord) -> str:
    """`06-specs/<module>/PRD-NNN-<slug>`（共享设计 §4.3，与 PRD 目录同构）。"""
    return f"{workspace.rel(workspace.specs_dir)}/{record.module}/{record.directory.name}"


def spec_path(workspace: Workspace, record: metadata.PrdRecord, name: str) -> str:
    return f"{spec_dir(workspace, record)}/{name}"


def baseline_path(workspace: Workspace, record: metadata.PrdRecord) -> str:
    return spec_path(workspace, record, BASELINE_FILE)


def existing_spec_files(workspace: Workspace, record: metadata.PrdRecord,
                        rules: Rules) -> dict[str, bool]:
    directory = workspace.path(spec_dir(workspace, record))
    return {
        name.removesuffix(".md"): (directory / name).is_file()
        for name in spec_files(rules)
    }


def read_baseline(workspace: Workspace, record: metadata.PrdRecord) -> dict[str, Any] | None:
    """读取 `.baseline.yaml`；不存在返回 None。**不修改**任何字段（ticket 04 会追加字段）。"""
    path = workspace.path(baseline_path(workspace, record))
    if not path.is_file():
        return None
    data = fsutil.read_yaml(path)
    return dict(data) if isinstance(data, dict) else None


def baseline_is_valid(data: dict[str, Any] | None) -> bool:
    """`.baseline.yaml` 自身是否被标记为有效（默认有效）。"""
    if not data:
        return False
    return bool(data.get("baseline_valid", True))


def build_baseline(workspace: Workspace, record: metadata.PrdRecord) -> dict[str, Any]:
    """`dev start` 写入的基线记录。字段稳定、可直接被 ticket 04/06/09 读取。"""
    return {
        "prd": record.id,
        "module": record.module,
        "spec_dir": spec_dir(workspace, record),
        "baseline_hash": record.content_hash,
        "baseline_version": record.version,
        "created_at": fsutil.now_iso(),
        "baseline_valid": True,
    }


def write_baseline(workspace: Workspace, record: metadata.PrdRecord) -> str:
    path = workspace.path(baseline_path(workspace, record))
    fsutil.atomic_write_yaml(path, build_baseline(workspace, record))
    return workspace.rel(path)


# --------------------------------------------------------------------------
# Spec / Plan / Tasks 渲染
# --------------------------------------------------------------------------
def render_spec(workspace: Workspace, record: metadata.PrdRecord, *, name: str,
                rules: Rules) -> str:
    """按项目模板（回退随包模板）生成 Spec/Plan/Tasks 骨架，并预填 PRD 场景引用。

    只写结构与 `<!-- TODO -->` 占位，**不伪造**技术方案或验证结论（共享设计 §3）。
    """
    body = _template_body(workspace, name)
    body = _replace_placeholders(body, record)
    if name == SPEC_FILE:
        body = _fill_spec_scenarios(body, record)
    if name == TASKS_FILE:
        body = _fill_task_scenarios(body, record)
    front = {
        "prd": record.id,
        "baseline_version": record.version,
        "baseline_hash": record.content_hash,
        "module": record.module,
        "status": "DRAFT",
    }
    return fsutil.render_front_matter(front, body)


def refresh_front_matter(path, record: metadata.PrdRecord) -> str:
    """把已有产物的 front matter 基线字段更新到 PRD 当前基线，正文原样保留。

    CR 重新批准后 PRD 版本与 hash 都变了，旧 Spec/Plan/Tasks 必须明确标成新基线，
    否则 ticket 09 的 lint 会看到「孤儿基线」。返回更新后的全文。
    """
    text = fsutil.read_text(path)
    head, body = fsutil.parse_front_matter(text)
    if not head:
        return text
    head = dict(head)
    head.update({
        "prd": record.id,
        "baseline_version": record.version,
        "baseline_hash": record.content_hash,
        "module": record.module,
    })
    return fsutil.render_front_matter(head, body)


def _template_body(workspace: Workspace, name: str) -> str:
    candidates = (
        workspace.templates_dir / name,
        _packaged_templates_dir() / name,
    )
    for path in candidates:
        if path.is_file():
            return _strip_title(fsutil.read_text(path))
    return f"# {name}\n\n<!-- TODO: 请补充内容。 -->\n"


def _packaged_templates_dir():
    from pathlib import Path

    return Path(__file__).resolve().parents[1] / "templates"


def _strip_title(text: str) -> str:
    lines = text.splitlines()
    if lines and lines[0].startswith("# "):
        return "\n".join(lines[1:]).lstrip("\n")
    return text


def _replace_placeholders(body: str, record: metadata.PrdRecord) -> str:
    for label in ("Spec", "Plan", "Tasks"):
        body = body.replace(f"# {label}：PRD-XXX <功能名称>",
                            f"# {label}：{record.id} {record.title}")
    body = body.replace("PRD-XXX", record.id)
    body = body.replace("<功能名称>", record.title)
    body = body.replace("- PRD 版本: <!-- TODO -->", f"- PRD 版本: {record.version}")
    body = body.replace("- PRD 基线: <!-- TODO: sha256:... -->",
                        f"- PRD 基线: {record.content_hash}")
    body = body.replace("- 代码版本: <!-- TODO: git:<sha> 或 sha256:<hex> -->",
                        "- 代码版本: <!-- TODO: 由验证步骤记录 git:<sha> 或 sha256:<hex> -->")
    return body.lstrip("\n")


def _scenario_rows(record: metadata.PrdRecord) -> list[str]:
    return [f"| {scenario} | <!-- TODO: 实现落点 --> |" for scenario in record.scenarios]


def _fill_spec_scenarios(body: str, record: metadata.PrdRecord) -> str:
    marker = "| <!-- TODO --> | <!-- TODO --> |"
    if marker in body:
        return body.replace(marker, "\n".join(_scenario_rows(record)))
    return body


def _fill_task_scenarios(body: str, record: metadata.PrdRecord) -> str:
    marker = "| <!-- TODO --> | <!-- TODO --> | TODO | <!-- TODO --> |"
    rows = [f"| <!-- TODO: 任务 --> | {scenario} | TODO | <!-- TODO: 产出 --> |"
            for scenario in record.scenarios]
    if marker in body:
        return body.replace(marker, "\n".join(rows))
    return body


# --------------------------------------------------------------------------
# 门禁
# --------------------------------------------------------------------------
@dataclass
class Check:
    name: str
    ok: bool
    code: str | None = None
    detail: str = ""
    suggestion: str | None = None
    skipped: bool = False

    def to_dict(self) -> dict[str, Any]:
        data: dict[str, Any] = {
            "name": self.name,
            "ok": self.ok,
            "code": self.code,
            "detail": self.detail,
            "status": "ok" if self.ok else "fail",
        }
        if self.suggestion:
            data["suggestion"] = self.suggestion
        if self.skipped:
            data["skipped"] = True
        return data


@dataclass
class GateReport:
    prd_id: str | None
    record: metadata.PrdRecord | None = None
    checks: list[Check] = field(default_factory=list)
    binding: dict[str, Any] = field(default_factory=dict)

    @property
    def failures(self) -> list[Check]:
        """真正需要出现在 `errors[]` 里的失败项（跳过项不算）。"""
        return [item for item in self.checks if not item.ok and not item.skipped and item.code]

    @property
    def ok(self) -> bool:
        return not self.failures

    def check(self, name: str) -> Check | None:
        for item in self.checks:
            if item.name == name:
                return item
        return None

    def to_dicts(self) -> list[dict[str, Any]]:
        return [item.to_dict() for item in self.checks]


def evaluate_gate(workspace: Workspace, rules: Rules, prd_id: str | None) -> GateReport:
    """`dev gate` / `dev start` 共用的九项只读检查（ticket 06 复用）。

    检查顺序固定（便于 Skill 逐条解释）：绑定 → 存在 → 状态 → 批准记录 → 基线 hash →
    项目约束 → 团队 → 引用关系 → 模块目录。
    """
    report = GateReport(prd_id=prd_id)
    if not prd_id or not str(prd_id).strip():
        report.checks.append(Check(
            "binding", False, "PRD_NOT_BOUND",
            "开发入口必须显式绑定一个 PRD（缺少 --prd）",
            "用 --prd PRD-001 显式声明要开发哪一份 PRD；不要依赖「最近修改」之类的隐式推断",
        ))
        report.binding = {"prd": None}
        _skip_rest(report, from_check=0)
        return report

    target = metadata.normalize_id(prd_id, rules)
    report.prd_id = target
    record = metadata.find(workspace, target)
    report.binding = {"prd": target}
    if record is None:
        report.checks.append(Check(
            "binding", True, None, f"已显式绑定 {target}"),
        )
        report.checks.append(Check(
            "prd_exists", False, "PRD_NOT_FOUND", f"找不到 PRD {target}",
            "用 flow-core prd list 查看已有 PRD，或先用 flow-core prd create 创建草稿",
        ))
        _skip_rest(report, from_check=2)
        return report

    report.record = record
    report.binding = _binding(workspace, record)
    report.checks.append(Check("binding", True, None, f"已显式绑定 {record.id}"))
    report.checks.append(Check("prd_exists", True, None, f"{record.rel}"))
    report.checks.append(_check_state(rules, record))
    report.checks.append(_check_approval(record))
    report.checks.append(_check_hash(record))
    report.checks.append(_check_constitution(workspace))
    report.checks.append(_check_team(workspace, record))
    report.checks.append(_check_references(workspace, rules, record))
    report.checks.append(_check_module(workspace, record))
    report.checks.append(_check_spec_baseline(workspace, record))
    return report


def _binding(workspace: Workspace, record: metadata.PrdRecord) -> dict[str, Any]:
    return {
        "prd": record.id,
        "title": record.title,
        "module": record.module,
        "feature": record.feature,
        "status": record.status,
        "version": record.version,
        "content_hash": record.content_hash,
        "scenarios": record.scenarios,
        "source_reqs": record.source_reqs,
        "release": record.release,
        "prd_dir": record.rel,
        "spec_dir": spec_dir(workspace, record),
    }


def _skip_rest(report: GateReport, *, from_check: int) -> None:
    for name in ("prd_exists", "prd_state", "approval", "baseline_hash", "constitution",
                 "team", "references", "module", "spec_baseline")[from_check:]:
        report.checks.append(Check(name, False, None, "未执行：前置检查未通过", skipped=True))


def _check_state(rules: Rules, record: metadata.PrdRecord) -> Check:
    allowed = start_states(rules)
    if record.status in allowed:
        return Check("prd_state", True, None, f"状态 {record.status} 允许开发")
    return Check(
        "prd_state", False, "PRD_STATE_NOT_ALLOWED",
        f"PRD {record.id} 当前状态 {record.status}，不允许启动开发（允许 {'、'.join(allowed)}）",
        "先走完 PRD 评审与人工批准（prd submit-review + 需求负责人 prd approve）；"
        "DEVELOPING 只在幂等重启时允许",
    )


def _check_approval(record: metadata.PrdRecord) -> Check:
    approved_by = record.meta.get("approved_by")
    approved_at = record.meta.get("approved_at")
    if approved_by and approved_at:
        return Check("approval", True, None,
                     f"已由 {approved_by} 于 {approved_at} 批准")
    missing = [name for name, value in (("approved_by", approved_by),
                                        ("approved_at", approved_at)) if not value]
    return Check(
        "approval", False, "NOT_APPROVED",
        f"PRD {record.id} 没有有效的批准记录（缺少 {'、'.join(missing)}）",
        "由需求负责人执行 flow-core prd approve --id "
        f"{record.id} --actor <需求负责人> --confirm",
    )


def _check_hash(record: metadata.PrdRecord) -> Check:
    expected = record.content_hash
    actual = record.compute_content_hash()
    if not expected:
        return Check(
            "baseline_hash", False, "BASELINE_MISSING",
            f"PRD {record.id} 还没有批准基线（content_hash 为空）",
            "先完成人工批准生成基线，再启动开发",
        )
    if expected != actual:
        return Check(
            "baseline_hash", False, "HASH_MISMATCH",
            f"PRD {record.id} 的内容与批准基线不一致（正文或锁定字段被修改）",
            "已批准内容的变化必须走 CR 并重新批准："
            + CR_HINT.format(prd_id=record.id)
            + "；不要直接改 prd.md 或锁定字段",
        )
    return Check("baseline_hash", True, None, f"基线一致（{expected}）")


def read_constitution(workspace: Workspace) -> dict[str, Any] | None:
    path = workspace.constitution_file
    if not path.is_file():
        return None
    data, _body = fsutil.parse_front_matter(fsutil.read_text(path))
    return data


def _check_constitution(workspace: Workspace) -> Check:
    data = read_constitution(workspace)
    if data is None:
        return Check(
            "constitution", False, "CONSTITUTION_MISSING",
            "缺少项目约束 .specify/constitution.md",
            "运行 flow-core init 生成模板，并由项目负责人确认技术与测试约束",
        )
    if not bool(data.get("confirmed", False)):
        return Check(
            "constitution", False, "CONSTITUTION_NOT_CONFIRMED",
            "项目约束尚未确认（.specify/constitution.md 的 confirmed 不是 true）",
            "由项目负责人补齐技术栈/测试/安全/架构约束并把 confirmed 改为 true，"
            "再启动开发（BR-018）",
        )
    return Check("constitution", True, None, "项目约束已确认")


def _check_team(workspace: Workspace, record: metadata.PrdRecord) -> Check:
    from . import state

    team = state.load_team(workspace)
    if team is None or not team.members:
        return Check(
            "team", False, "TEAM_NOT_CONFIGURED",
            "项目还没有有效的团队配置（00-management/team.md 缺失或没有成员）",
            "补齐团队与职责映射并由项目负责人确认后再启动开发",
        )
    if not team.confirmed:
        return Check(
            "team", False, "TEAM_NOT_CONFIGURED",
            "团队配置尚未确认（00-management/team.md 的 confirmed 不是 true）",
            "由项目负责人核对成员与职责后把 confirmed 改为 true",
        )
    return Check("team", True, None, f"团队已确认（{len(team.members)} 名成员）")


def _check_references(workspace: Workspace, rules: Rules,
                      record: metadata.PrdRecord) -> Check:
    source = record.source_reqs
    if len(source) != 1:
        return Check(
            "references", False, "REQ_REFERENCE_INVALID",
            f"PRD {record.id} 的主需求必须恰好一个，当前 {len(source)} 个"
            f"（{'、'.join(source) or '空'}）",
            "用 flow-core prd update 修正 source_reqs；已批准内容的变化必须走 CR",
        )
    req_id = source[0]
    try:
        req = reqs.load(workspace, req_id, rules=rules)
    except FlowError:
        return Check(
            "references", False, "REQ_REFERENCE_INVALID",
            f"PRD {record.id} 的主需求 {req_id} 不存在",
            "确认需求编号；PRD 与需求的引用关系不合法时不得启动开发",
        )
    allowed_states = reqs.prd_states(rules)
    if req.status not in allowed_states:
        return Check(
            "references", False, "REQ_REFERENCE_INVALID",
            f"主需求 {req_id} 当前状态 {req.status}，不属于 {'、'.join(allowed_states)}",
            "PRD 只能绑定已进入 PRD 流程的主需求；先完成需求排期与 PRD 创建",
        )
    if str(req.meta.get("prd") or "") != record.id:
        return Check(
            "references", False, "REQ_REFERENCE_INVALID",
            f"主需求 {req_id} 的 prd 字段指向 {req.meta.get('prd') or '空'}，不是 {record.id}",
            f"确认 {req_id} 与 {record.id} 的绑定关系；不要手工拼接引用",
        )
    return Check("references", True, None, f"主需求 {req_id}（{req.status}）引用一致")


def _check_module(workspace: Workspace, record: metadata.PrdRecord) -> Check:
    expected_dir = f"04-prds/{record.module}/{record.directory.name}"
    actual_dir = workspace.rel(record.directory)
    if actual_dir == expected_dir:
        return Check("module", True, None, actual_dir)
    return Check(
        "module", False, "MODULE_MISMATCH",
        f"PRD 目录 {actual_dir} 与 metadata 的 module `{record.module}` 不一致"
        f"（应为 {expected_dir}）",
        "目录与模块必须一致：移动目录或修正 metadata 的 module；"
        "已批准内容的变化必须走 CR，不要直接搬目录",
    )


def baseline_is_stale(data: dict[str, Any] | None, record: metadata.PrdRecord) -> bool:
    """Spec 基线是否与 PRD 当前基线不一致（或已被 CR 标记失效）。"""
    if not data:
        return True
    if not baseline_is_valid(data):
        return True
    return bool(data.get("baseline_hash")) and data.get("baseline_hash") != record.content_hash


def _check_spec_baseline(workspace: Workspace, record: metadata.PrdRecord) -> Check:
    data = read_baseline(workspace, record)
    if data is None:
        return Check("spec_baseline", True, None, "尚未创建 Spec 基线（首次启动会创建）")
    stale = baseline_is_stale(data, record)
    if stale and record.status == "APPROVED":
        # CR 后重新批准：旧基线失效但 PRD 已换新基线，dev start 会重建 Spec 基线（不阻断）。
        blocker = data.get("invalidated_by") or "内容变化"
        return Check("spec_baseline", True, None,
                     f"旧 Spec 基线已失效（{blocker}），本次启动将按新基线重建")
    if not baseline_is_valid(data):
        blocker = data.get("invalidated_by") or "CR"
        return Check(
            "spec_baseline", False, "BASELINE_INVALID",
            f"{record.id} 的开发基线已失效（{blocker}），不能继续开发",
            "基线失效后必须重新评审并批准 PRD，再重新建立 Spec 基线",
        )
    if data.get("baseline_hash") and data.get("baseline_hash") != record.content_hash:
        return Check(
            "spec_baseline", False, "BASELINE_INVALID",
            f"{record.id} 的 Spec 基线版本与 PRD 当前基线不一致",
            "按新基线重新生成 Spec/Plan/Tasks：重新批准后执行 flow-core dev start",
        )
    return Check("spec_baseline", True, None,
                 f"Spec 基线有效（版本 {data.get('baseline_version')}）")


# --------------------------------------------------------------------------
# 范围检查
# --------------------------------------------------------------------------
@dataclass
class ScopeReport:
    referenced_scenarios: list[str] = field(default_factory=list)
    referenced_acs: list[str] = field(default_factory=list)
    unknown_scenarios: list[str] = field(default_factory=list)
    unknown_acs: list[str] = field(default_factory=list)
    paths: list[str] = field(default_factory=list)
    path_violations: list[str] = field(default_factory=list)
    files: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return {
            "referenced_scenarios": self.referenced_scenarios,
            "referenced_acs": self.referenced_acs,
            "unknown_scenarios": self.unknown_scenarios,
            "unknown_acs": self.unknown_acs,
            "paths": self.paths,
            "path_violations": self.path_violations,
            "files": self.files,
        }


def ac_scenario(ac_id: str) -> str | None:
    """`AC-RES-01-02` → `SC-RES-01`（PRD 场景编号与 AC 编号的固定对应）。"""
    if not ac_id.startswith("AC-"):
        return None
    parts = ac_id.split("-")[1:]
    if len(parts) < 2:
        return None
    return f"SC-{parts[0]}-{parts[1]}"


def parse_paths(raw: Iterable[str] | str | None) -> list[str]:
    if raw is None:
        return []
    items = [raw] if isinstance(raw, str) else list(raw)
    values: list[str] = []
    for item in items:
        for part in str(item).split(","):
            text = part.strip().replace("\\", "/")
            if text and text not in values:
                values.append(text)
    return values


def path_violation(rel_path: str, allowed: tuple[str, ...]) -> str | None:
    """越界路径（相对项目根）返回原因；合法返回 None。"""
    text = rel_path.strip()
    if not text:
        return "空路径"
    if text.startswith("/") or re.match(r"^[A-Za-z]:", text):
        return "绝对路径不在项目内"
    parts = PurePosixPath(text).parts
    if ".." in parts:
        return "路径包含 `..`，已越出项目目录"
    if not parts:
        return "空路径"
    if parts[0] not in allowed:
        return f"只允许改动 {'、'.join(allowed)} 下的文件"
    return None


def check_scope(workspace: Workspace, rules: Rules, record: metadata.PrdRecord, *,
                paths: Iterable[str] | None = None) -> ScopeReport:
    """检查 Spec/Tasks 引用的场景/AC 与列出的变更路径是否都在 PRD 范围内（BR-012/BR-013）。"""
    directory = workspace.path(spec_dir(workspace, record))
    if not directory.is_dir():
        raise FlowError(
            "SPEC_NOT_FOUND",
            f"{record.id} 还没有开发产物（{spec_dir(workspace, record)} 不存在）",
            object=record.id,
            suggestion="先执行 flow-core dev start --prd " + record.id
                       + " --actor <开发人员> --confirm",
        )

    report = ScopeReport()
    known = set(record.scenarios)
    for name in spec_files(rules):
        path = directory / name
        if not path.is_file():
            continue
        report.files.append(workspace.rel(path))
        text = fsutil.read_text(path)
        for token in _SCENARIO_TOKEN_RE.findall(text):
            if token not in report.referenced_scenarios:
                report.referenced_scenarios.append(token)
            if token not in known and token not in report.unknown_scenarios:
                report.unknown_scenarios.append(token)
        for token in _AC_TOKEN_RE.findall(text):
            if token not in report.referenced_acs:
                report.referenced_acs.append(token)
            scenario = ac_scenario(token)
            if scenario and scenario not in known and token not in report.unknown_acs:
                report.unknown_acs.append(token)

    allowed = allowed_path_prefixes(rules)
    for item in parse_paths(paths):
        report.paths.append(item)
        reason = path_violation(item, allowed)
        if reason:
            report.path_violations.append(item)
    return report
