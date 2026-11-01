"""结构检查（ticket 09；SRS-FR-084、SRS §9「直接绕过 Skill 编辑文件：lint 尽可能发现」）。

**定位**：`lint` 是**流程治理**能力，不是不可绕过的安全边界。它只能发现「已经写坏的
项目事实」（缺失产物、ID/目录不一致、孤儿 Spec、失效引用、基线失效、验收后未同步），
无法阻止有人直接改文件；发现之后一律给对象、错误码与恢复建议，由人或 CI 决定怎么修。

**只读**：本模块不写任何文件、不取锁、不改状态。判定一律复用已有域模块，不重算：

| 判什么 | 复用谁 |
| --- | --- |
| 基线 hash / 失效标记 / 未决 CR | `baseline.evaluate()` + `baseline.active_invalidation(ws, rules, record)` |
| 验证证据是否过期 | `verification.evaluate()` |
| 验收结论是否失效 | `acceptance.evaluate()` |
| 验收后是否收尾 | `closeout.evaluate_closeout()` |
| 受管理区块 | `index.evaluate()` |

**性能**（SRS-NFR-004）：对象清单只读一次，代价高的判定（验证/验收/收尾）只在
「真的可能有问题」的状态上跑——`verification` 只对已启动开发的 PRD，`acceptance` 只对
已记录验收的 PRD，`closeout` 只对 `ACCEPTED` 的 PRD。1000 需求 / 200 PRD 的夹具上
整条 lint 仍是一次文件遍历量级。
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any, Callable, Iterable

from . import acceptance, baseline, closeout, fsutil, index, metadata, requirements, rules as rules_mod
from .rules import Rules
from .workspace import SKELETON, TEMPLATE_NAMES, Workspace

FLOW_CORE = "flow-core"

SEVERITY_ERROR = "error"
SEVERITY_WARNING = "warning"

_REQ_FILE_RE = re.compile(r"^(REQ-\d+)-(?P<slug>.+)\.md$")
_PRD_DIR_RE = re.compile(r"^(PRD-\d+)-(?P<slug>.+)$")

# 检查类清单（`--only` 的合法取值；也是文档与测试的单一事实来源）
CHECK_CODES: dict[str, tuple[str, str]] = {
    # code: (topic, 默认严重级别)
    "PROJECT_NOT_INITIALIZED": ("project", SEVERITY_ERROR),
    "MISSING_REQUIRED_PATH": ("project", SEVERITY_ERROR),
    "CONSTITUTION_MISSING": ("project", SEVERITY_ERROR),
    "CONSTITUTION_NOT_CONFIRMED": ("project", SEVERITY_ERROR),
    "TEAM_NOT_CONFIGURED": ("project", SEVERITY_ERROR),
    "ID_MISMATCH": ("identity", SEVERITY_ERROR),
    "MODULE_MISMATCH": ("identity", SEVERITY_ERROR),
    "DUPLICATE_ID": ("identity", SEVERITY_ERROR),
    "COUNTER_REGRESSION": ("identity", SEVERITY_ERROR),
    "ORPHAN_SPEC": ("reference", SEVERITY_ERROR),
    "BROKEN_REFERENCE": ("reference", SEVERITY_ERROR),
    "INVALID_STATE": ("state", SEVERITY_ERROR),
    "BASELINE_MISSING": ("baseline", SEVERITY_ERROR),
    "HASH_MISMATCH": ("baseline", SEVERITY_ERROR),
    "BASELINE_INVALIDATED": ("baseline", SEVERITY_ERROR),
    "VERIFICATION_MISSING": ("verification", SEVERITY_WARNING),
    "VERIFICATION_STALE": ("verification", SEVERITY_ERROR),
    "ACCEPTANCE_STALE": ("acceptance", SEVERITY_ERROR),
    "CLOSEOUT_INCOMPLETE": ("closeout", SEVERITY_ERROR),
    "INDEX_STALE": ("index", SEVERITY_WARNING),
    "MANAGED_BLOCK_MODIFIED": ("index", SEVERITY_ERROR),
    "RULES_MISMATCH": ("rules", SEVERITY_ERROR),
}

# 每个错误码的恢复建议（进 `suggestion` 与 `next_actions`；文案可读优先）
SUGGESTIONS: dict[str, tuple[str, str]] = {
    "PROJECT_NOT_INITIALIZED": (
        "项目还没有初始化，检查无法进行",
        "先执行 flow-core init 生成目录骨架、规则基线与模板",
    ),
    "MISSING_REQUIRED_PATH": (
        "必需的文件或目录缺失（可能是被手工删除或裁剪时误删）",
        "用 flow-core init 补齐缺失骨架；被裁剪的目录请用 init --prune 记录后再补",
    ),
    "CONSTITUTION_MISSING": (
        "缺少项目约束文件",
        "执行 flow-core init 生成 .specify/constitution.md，再由项目负责人确认",
    ),
    "CONSTITUTION_NOT_CONFIRMED": (
        "项目约束尚未确认",
        "由项目负责人核对后把 .specify/constitution.md 的 confirmed 改为 true",
    ),
    "TEAM_NOT_CONFIGURED": (
        "团队配置缺失、没有成员或尚未确认",
        "补齐 00-management/team.md 的成员与职责，并把 confirmed 改为 true",
    ),
    "ID_MISMATCH": (
        "文件/目录名里的编号与 front matter 或 metadata 的 id 不一致",
        "把文件名（或 metadata 的 id）改回同一个编号；编号创建后不可变",
    ),
    "MODULE_MISMATCH": (
        "对象所在目录的模块与 metadata 的 module 不一致",
        "把对象移回 metadata 声明的模块目录，或修正 metadata.module",
    ),
    "DUPLICATE_ID": (
        "同一编号对应多个文件/目录",
        "删除或改名重复对象；编号只增不复用，不许两个对象共用一个编号",
    ),
    "COUNTER_REGRESSION": (
        "计数器小于已存在对象的编号（编号被回退或被手工改写）",
        "把 .neo-skill/counters.yaml 的值改回不小于已用最大编号，避免下一个对象复用编号",
    ),
    "ORPHAN_SPEC": (
        "06-specs 下有 Spec 目录，但对应 PRD 不存在或已 REJECTED/SUPERSEDED",
        "为孤儿 Spec 补建 PRD 走流程，或归档后删除该目录",
    ),
    "BROKEN_REFERENCE": (
        "引用指向不存在的对象",
        "修正引用；跨对象关系必须指向真实存在的 REQ/PRD",
    ),
    "INVALID_STATE": (
        "状态之间的关系不成立（例如需求已 DONE 但 PRD 未 ACCEPTED）",
        "按状态机补齐上游步骤；不要手工改状态字段",
    ),
    "BASELINE_MISSING": (
        "PRD 从未批准，没有内容基线",
        "走完 prd submit-review → prd approve，批准时才写 content_hash",
    ),
    "HASH_MISMATCH": (
        "PRD 正文或锁定字段与批准的基线 hash 不一致（疑似绕过 CLI 直接改）",
        "用 prd cr create 登记变更并重新批准；直接改回正文则用 prd verify-hash 复核",
    ),
    "BASELINE_INVALIDATED": (
        "基线已失效：存在未决变更（CR）或失效标记仍为 active",
        "处理未决 CR（prd cr approve / prd cr reject），让基线重新有效",
    ),
    "VERIFICATION_MISSING": (
        "已启动开发但还没有任何验证证据",
        "用 dev verify 执行验证并保留原始输出；提交验收前必须有通过证据",
    ),
    "VERIFICATION_STALE": (
        "验证证据已失效（PRD、代码或原始输出发生变化）",
        "重新执行 dev verify 并 dev submit-acceptance，旧证据不得沿用",
    ),
    "ACCEPTANCE_STALE": (
        "已记录的验收结论失效（PRD/代码/证据/清单变化）",
        "按 SRS-FR-051 走 prd cr 重新评审与验收；ACCEPTED 是终态，不要直接改验收结论",
    ),
    "CLOSEOUT_INCOMPLETE": (
        "PRD 已验收，但功能现状/场景/索引/主需求没有同步完成",
        "执行 acceptance closeout 完成收尾；中途失败用 acceptance closeout --retry 续跑",
    ),
    "INDEX_STALE": (
        "受管理索引与当前事实不一致",
        "执行 flow-core index generate 重新生成（区块外内容保持不变）",
    ),
    "MANAGED_BLOCK_MODIFIED": (
        "受管理区块被人工修改（fingerprint 与区块内容不符）",
        "确认人工改动可以被覆盖后执行 flow-core index generate --confirm --actor <成员 id>",
    ),
    "RULES_MISMATCH": (
        "项目规则基线与随包规则不一致（版本缺失或字段缺失）",
        "对比 .specify/rules/flow.yaml 与随包 rules/flow.yaml，补齐缺失字段或升级 version",
    ),
}


# --------------------------------------------------------------------------
# 结果对象
# --------------------------------------------------------------------------
@dataclass
class LintReport:
    """一次 lint 的结论。`findings` 已按 (severity, code, object) 稳定排序。"""

    findings: list[dict[str, Any]] = field(default_factory=list)
    checked: list[str] = field(default_factory=list)
    counts: dict[str, int] = field(default_factory=dict)

    @property
    def errors(self) -> list[dict[str, Any]]:
        return [item for item in self.findings if item["severity"] == SEVERITY_ERROR]

    @property
    def warnings(self) -> list[dict[str, Any]]:
        return [item for item in self.findings if item["severity"] == SEVERITY_WARNING]

    @property
    def ok(self) -> bool:
        return not self.errors

    def to_dict(self) -> dict[str, Any]:
        return {
            "ok": self.ok,
            "counts": dict(self.counts),
            "checked": list(self.checked),
            "findings": [dict(item) for item in self.findings],
        }


def finding(code: str, *, object: str = "", path: str = "", message: str = "",
            suggestion: str = "", severity: str | None = None,
            next_action: str = "") -> dict[str, Any]:
    """构造一条发现。`code/severity/object/message/suggestion/path` 是固定形状。"""
    topic, default_severity = CHECK_CODES.get(code, ("other", SEVERITY_ERROR))
    hint_message, hint_suggestion = SUGGESTIONS.get(code, ("", ""))
    return {
        "code": code,
        "severity": severity or default_severity,
        "topic": topic,
        "object": object,
        "message": message or hint_message,
        "suggestion": suggestion or hint_suggestion,
        "path": path,
        "next_action": next_action,
    }


# --------------------------------------------------------------------------
# 上下文：对象清单只读一次
# --------------------------------------------------------------------------
@dataclass
class LintContext:
    workspace: Workspace
    rules: Rules
    pruned: list[str] = field(default_factory=list)
    reqs: list[requirements.ReqRecord] = field(default_factory=list)
    prds: list[metadata.PrdRecord] = field(default_factory=list)
    counters: dict[str, int] = field(default_factory=dict)
    spec_dirs: list[str] = field(default_factory=list)

    # ---- 派生查询 ----
    def prd(self, prd_id: str) -> metadata.PrdRecord | None:
        for record in self.prds:
            if record.id == prd_id:
                return record
        return None

    def req(self, req_id: str) -> requirements.ReqRecord | None:
        for record in self.reqs:
            if record.id == req_id:
                return record
        return None

    def rel(self, path) -> str:
        return self.workspace.rel(path)


def _load_context(workspace: Workspace, rules: Rules) -> LintContext:
    state = fsutil.load_yaml(workspace.init_state_file, default={})
    pruned = [str(item) for item in ((state or {}).get("pruned") or [])]
    spec_dirs: list[str] = []
    if workspace.specs_dir.is_dir():
        for module_dir in sorted(workspace.specs_dir.glob("*/PRD-*")):
            if module_dir.is_dir():
                spec_dirs.append(workspace.rel(module_dir))
    from . import ids

    return LintContext(
        workspace=workspace,
        rules=rules,
        pruned=pruned,
        reqs=requirements.list_requirements(workspace),
        prds=metadata.list_prds(workspace),
        counters=ids.load_counters(workspace),
        spec_dirs=spec_dirs,
    )


# --------------------------------------------------------------------------
# 检查项
# --------------------------------------------------------------------------
def _check_project(ctx: LintContext) -> list[dict[str, Any]]:
    workspace = ctx.workspace
    issues: list[dict[str, Any]] = []

    for entry in SKELETON:
        if entry.group and entry.group in ctx.pruned:
            continue
        target = workspace.path(entry.path)
        if entry.kind == "dir":
            if not target.is_dir():
                issues.append(finding(
                    "MISSING_REQUIRED_PATH", object=entry.path, path=entry.path,
                    message=f"必需目录缺失：{entry.path}",
                ))
            continue
        if not target.is_file():
            issues.append(finding(
                "MISSING_REQUIRED_PATH", object=entry.path, path=entry.path,
                message=f"必需文件缺失：{entry.path}",
            ))

    for name in TEMPLATE_NAMES:
        rel = f".specify/templates/{name}"
        if not workspace.templates_dir.joinpath(name).is_file():
            issues.append(finding(
                "MISSING_REQUIRED_PATH", object=rel, path=rel,
                message=f"必需模板缺失：{rel}",
            ))

    constitution = workspace.constitution_file
    if not constitution.is_file():
        issues.append(finding(
            "CONSTITUTION_MISSING", object=".specify/constitution.md",
            path=".specify/constitution.md",
            message="缺少 .specify/constitution.md，无法确认项目约束",
        ))
    else:
        meta, _body = fsutil.parse_front_matter(fsutil.read_text(constitution))
        if meta.get("confirmed") is not True:
            issues.append(finding(
                "CONSTITUTION_NOT_CONFIRMED", object=".specify/constitution.md",
                path=".specify/constitution.md",
                message="项目约束尚未确认（front matter 的 confirmed 不是 true）",
            ))

    from . import state as state_mod

    team = state_mod.load_team(workspace)
    if team is None or not team.members:
        issues.append(finding(
            "TEAM_NOT_CONFIGURED", object="00-management/team.md",
            path="00-management/team.md",
            message="团队配置缺失或没有任何成员",
        ))
    elif not team.confirmed:
        issues.append(finding(
            "TEAM_NOT_CONFIGURED", object="00-management/team.md",
            path="00-management/team.md",
            message="团队配置尚未确认（front matter 的 confirmed 不是 true）",
        ))
    return issues


def _check_identity(ctx: LintContext) -> list[dict[str, Any]]:
    issues: list[dict[str, Any]] = []

    for record in ctx.reqs:
        match = _REQ_FILE_RE.match(record.path.name)
        file_id = match.group(1) if match else ""
        if file_id and record.id and file_id != record.id:
            issues.append(finding(
                "ID_MISMATCH", object=record.id, path=record.rel,
                message=f"文件名编号 {file_id} 与 front matter 的 id {record.id} 不一致",
            ))
        if record.status and not ctx.rules.is_state("req", record.status):
            issues.append(finding(
                "INVALID_STATE", object=record.id, path=record.rel,
                message=f"需求状态 {record.status} 不在规则声明的 req 状态里",
            ))

    for record in ctx.prds:
        match = _PRD_DIR_RE.match(record.directory.name)
        dir_id = match.group(1) if match else ""
        if dir_id and record.id and dir_id != record.id:
            issues.append(finding(
                "ID_MISMATCH", object=record.id, path=record.rel,
                message=f"目录编号 {dir_id} 与 metadata 的 id {record.id} 不一致",
            ))
        if record.status and not ctx.rules.is_state("prd", record.status):
            issues.append(finding(
                "INVALID_STATE", object=record.id, path=record.rel,
                message=f"PRD 状态 {record.status} 不在规则声明的 prd 状态里",
            ))
        parent_module = record.directory.parent.name
        if record.module and parent_module != record.module:
            issues.append(finding(
                "MODULE_MISMATCH", object=record.id, path=record.rel,
                message=f"{record.rel} 位于模块 {parent_module}，但 metadata.module 是 {record.module}",
            ))

    seen: dict[str, list[str]] = {}
    for record in ctx.reqs:
        seen.setdefault(record.id, []).append(record.rel)
    for record in ctx.prds:
        seen.setdefault(record.id, []).append(record.rel)
    for object_id, paths in seen.items():
        if object_id and len(paths) > 1:
            issues.append(finding(
                "DUPLICATE_ID", object=object_id, path=paths[0],
                message=f"编号 {object_id} 出现在多个位置：" + "、".join(paths),
            ))

    issues.extend(_check_counters(ctx))
    return issues


def _check_counters(ctx: LintContext) -> list[dict[str, Any]]:
    issues: list[dict[str, Any]] = []
    pairs = (
        ("REQ", "req", [record.id for record in ctx.reqs]),
        ("PRD", "prd", [record.id for record in ctx.prds]),
    )
    for counter_key, prefix, ids in pairs:
        highest = 0
        for object_id in ids:
            tail = object_id.rsplit("-", 1)[-1]
            if tail.isdigit():
                highest = max(highest, int(tail))
        if not highest:
            continue
        recorded = int(ctx.counters.get(counter_key, 0) or 0)
        if recorded < highest:
            issues.append(finding(
                "COUNTER_REGRESSION", object=counter_key, path=".neo-skill/counters.yaml",
                message=f"计数器 {counter_key}={recorded} 小于已用最大编号 "
                        f"{prefix.upper()}-{highest:03d}，下一个对象会复用编号",
            ))
    return issues


def _check_references(ctx: LintContext) -> list[dict[str, Any]]:
    issues: list[dict[str, Any]] = []

    for record in ctx.prds:
        for req_id in record.source_reqs:
            if ctx.req(req_id) is None:
                issues.append(finding(
                    "BROKEN_REFERENCE", object=record.id, path=record.rel,
                    message=f"主需求 {req_id} 不存在",
                ))
        for req_id in record.related_reqs:
            if ctx.req(req_id) is None:
                issues.append(finding(
                    "BROKEN_REFERENCE", object=record.id, path=record.rel,
                    message=f"相关需求 {req_id} 不存在",
                ))

    for record in ctx.reqs:
        linked = str(record.meta.get("prd") or "").strip()
        if linked and ctx.prd(linked) is None:
            issues.append(finding(
                "BROKEN_REFERENCE", object=record.id, path=record.rel,
                message=f"关联 PRD {linked} 不存在",
            ))

    for rel in ctx.spec_dirs:
        directory = ctx.workspace.path(rel)
        match = _PRD_DIR_RE.match(directory.name)
        prd_id = match.group(1) if match else ""
        target = ctx.prd(prd_id) if prd_id else None
        if target is None:
            issues.append(finding(
                "ORPHAN_SPEC", object=prd_id or directory.name, path=rel,
                message=f"{rel} 没有对应的 PRD（{prd_id or directory.name} 不存在）",
            ))
        elif target.status in ("REJECTED", "SUPERSEDED"):
            issues.append(finding(
                "ORPHAN_SPEC", object=prd_id, path=rel,
                message=f"{rel} 对应的 PRD 已是 {target.status}，Spec 不再有归属",
            ))
        spec_md = directory / "spec.md"
        if spec_md.is_file():
            front, _body = fsutil.parse_front_matter(fsutil.read_text(spec_md))
            declared = str(front.get("prd") or "").strip()
            if declared and (declared != prd_id or ctx.prd(declared) is None):
                issues.append(finding(
                    "BROKEN_REFERENCE", object=declared, path=ctx.rel(spec_md),
                    message=f"spec.md 的 prd 指向 {declared}，但该 PRD 不存在或与目录 {prd_id} 不一致",
                ))
    return issues


def _check_state_relations(ctx: LintContext) -> list[dict[str, Any]]:
    issues: list[dict[str, Any]] = []
    for record in ctx.reqs:
        if record.status == "DONE":
            linked = str(record.meta.get("prd") or "").strip()
            target = ctx.prd(linked) if linked else None
            if target is None:
                issues.append(finding(
                    "INVALID_STATE", object=record.id, path=record.rel,
                    message=f"需求已 DONE，但没有关联任何 PRD（无法证明已交付）",
                ))
            elif target.status != "ACCEPTED":
                issues.append(finding(
                    "INVALID_STATE", object=record.id, path=record.rel,
                    message=f"需求已 DONE，但主 PRD {target.id} 是 {target.status}（应为 ACCEPTED）",
                ))
        elif record.status == "IN_PRD" and not str(record.meta.get("prd") or "").strip():
            issues.append(finding(
                "INVALID_STATE", object=record.id, path=record.rel,
                message="需求处于 IN_PRD，但没有关联 PRD",
            ))
    return issues


def _check_baselines(ctx: LintContext) -> list[dict[str, Any]]:
    issues: list[dict[str, Any]] = []
    for record in ctx.prds:
        status = baseline.evaluate(ctx.workspace, ctx.rules, record)
        if baseline.REASON_MISSING in status.reasons:
            issues.append(finding(
                "BASELINE_MISSING", object=record.id, path=record.rel,
                message="PRD 从未批准，没有内容基线（content_hash 为空）",
            ))
        if baseline.REASON_MISMATCH in status.reasons:
            issues.append(finding(
                "HASH_MISMATCH", object=record.id, path=record.rel,
                message=f"PRD {record.id} 当前内容 hash 与批准基线不一致"
                        f"（expected {status.expected}，current {status.current}）",
            ))
        evidence = baseline.active_invalidation(ctx.workspace, ctx.rules, record)
        if evidence is not None:
            cr_part = f"未决变更 {evidence.get('cr')}" if evidence.get("cr") else "失效标记"
            issues.append(finding(
                "BASELINE_INVALIDATED", object=record.id, path=record.rel,
                message=f"PRD {record.id} 的基线已失效（{cr_part} 尚未处理）",
            ))
    return issues


def _check_verification(ctx: LintContext) -> list[dict[str, Any]]:
    from . import verification

    issues: list[dict[str, Any]] = []
    strict_states = ("READY_FOR_ACCEPTANCE", "ACCEPTED")
    for record in ctx.prds:
        if record.status not in ("DEVELOPING", *strict_states):
            continue
        status = verification.evaluate(ctx.workspace, ctx.rules, record)
        severity = SEVERITY_ERROR if record.status in strict_states else SEVERITY_WARNING
        if status.runs == 0:
            issues.append(finding(
                "VERIFICATION_MISSING", object=record.id, path=record.rel,
                severity=severity,
                message=f"PRD {record.id} 已是 {record.status}，但没有任何验证证据",
            ))
        elif status.stale:
            issues.append(finding(
                "VERIFICATION_STALE", object=record.id, path=record.rel,
                message=f"PRD {record.id} 的验证证据已失效（{'、'.join(status.stale_reasons)}）",
            ))
    return issues


def _check_acceptance(ctx: LintContext) -> list[dict[str, Any]]:
    issues: list[dict[str, Any]] = []
    for record in ctx.prds:
        if not isinstance(record.meta.get("acceptance"), dict):
            continue
        status = acceptance.evaluate(ctx.workspace, ctx.rules, record)
        if status.stale:
            issues.append(finding(
                "ACCEPTANCE_STALE", object=record.id, path=record.rel,
                message=f"PRD {record.id} 的验收结论已失效"
                        f"（{'、'.join(status.stale_reasons)}）",
            ))
    return issues


def _check_closeout(ctx: LintContext) -> list[dict[str, Any]]:
    issues: list[dict[str, Any]] = []
    for record in ctx.prds:
        if record.status != "ACCEPTED":
            continue
        data = closeout.evaluate_closeout(ctx.workspace, ctx.rules, record)
        if data.get("closeout_ok"):
            continue
        missing: list[str] = []
        if data.get("closeout_state") != "COMMITTED":
            missing.append(f"收尾状态 {data.get('closeout_state') or '未开始'}")
        if data.get("main_req_status") != "DONE":
            missing.append(f"主需求 {data.get('main_req') or '未绑定'} 是 "
                           f"{data.get('main_req_status') or '未知'}")
        if data.get("pending_ops"):
            missing.append(f"{len(data['pending_ops'])} 个目标未同步")
        if not data.get("index_ok"):
            missing.append("索引未同步")
        if not missing:
            missing.append("验收结论未确认")
        issues.append(finding(
            "CLOSEOUT_INCOMPLETE", object=record.id, path=record.rel,
            message=f"PRD {record.id} 已验收但收尾未完成（{'；'.join(missing)}）",
            next_action=f"{FLOW_CORE} acceptance closeout --prd {record.id}",
        ))
    return issues


def _check_index(ctx: LintContext) -> list[dict[str, Any]]:
    issues: list[dict[str, Any]] = []
    state = index.evaluate(ctx.workspace, ctx.rules)
    for problem in state["problems"]:
        topic, _, path = problem.partition(":")
        if topic == "MANAGED_BLOCK_MODIFIED":
            issues.append(finding(
                "MANAGED_BLOCK_MODIFIED", object=path, path=path,
                message=f"{path} 的受管理区块被人工修改（fingerprint 不符）",
            ))
        elif topic.startswith("INDEX_"):
            detail = {
                "INDEX_MISSING": "索引文件不存在",
                "INDEX_BLOCK_MISSING": "索引文件里没有受管理区块",
                "INDEX_STALE": "索引内容与当前事实不一致",
            }.get(topic, topic)
            issues.append(finding(
                "INDEX_STALE", object=path, path=path,
                message=f"{path}：{detail}",
            ))
    return issues


def _check_rules(ctx: LintContext) -> list[dict[str, Any]]:
    """项目规则基线 vs 随包规则：版本与字段一致性（`lint rules` 主体）。"""
    issues: list[dict[str, Any]] = []
    workspaced = ctx.workspace.project_rules_file
    packaged_file = rules_mod.packaged_rules_file()
    if not workspaced.is_file():
        issues.append(finding(
            "RULES_MISMATCH", object=".specify/rules/flow.yaml",
            path=".specify/rules/flow.yaml",
            message="项目没有规则基线（.specify/rules/flow.yaml 缺失）",
        ))
        return issues
    if not packaged_file.is_file():  # pragma: no cover - 安装损坏
        return issues
    try:
        packaged_data = fsutil.read_yaml(packaged_file)
        if not isinstance(packaged_data, dict):
            return issues
        packaged = rules_mod.Rules(data=packaged_data, source=packaged_file,
                                   origin="packaged")
    except Exception:  # pragma: no cover - 随包规则损坏
        return issues
    project_data = ctx.rules.data

    if int(project_data.get("version", 0) or 0) != packaged.version:
        issues.append(finding(
            "RULES_MISMATCH", object="version", path=".specify/rules/flow.yaml",
            message=f"项目规则 version={project_data.get('version')}、随包 version="
                    f"{packaged.version}，升级会改变状态机行为",
        ))

    for key in ("limits", "ids", "roles", "gates", "directories", "req", "prd"):
        if key not in project_data:
            issues.append(finding(
                "RULES_MISMATCH", object=key, path=".specify/rules/flow.yaml",
                message=f"项目规则缺少 `{key}` 字段（随包规则有）",
            ))

    project_gates = project_data.get("gates") if isinstance(project_data.get("gates"), dict) else {}
    for command in sorted((packaged.data.get("gates") or {}).keys()):
        if command not in project_gates:
            issues.append(finding(
                "RULES_MISMATCH", object=command, path=".specify/rules/flow.yaml",
                message=f"项目规则缺少门禁 `{command}`（人工决策命令会失败）",
            ))

    for machine in ("req", "prd"):
        try:
            wanted = set(packaged.states(machine))
            have = set(ctx.rules.states(machine))
        except Exception:  # pragma: no cover - 规则损坏
            continue
        missing = sorted(wanted - have)
        if missing:
            issues.append(finding(
                "RULES_MISMATCH", object=machine, path=".specify/rules/flow.yaml",
                message=f"{machine} 状态机缺少状态：{'、'.join(missing)}",
            ))
    return issues


CHECKS: tuple[tuple[str, Callable[[LintContext], list[dict[str, Any]]]], ...] = (
    ("project", _check_project),
    ("identity", _check_identity),
    ("reference", _check_references),
    ("state", _check_state_relations),
    ("baseline", _check_baselines),
    ("verification", _check_verification),
    ("acceptance", _check_acceptance),
    ("closeout", _check_closeout),
    ("index", _check_index),
    ("rules", _check_rules),
)

_SEVERITY_ORDER = {SEVERITY_ERROR: 0, SEVERITY_WARNING: 1}


def parse_only(raw: Iterable[str] | str | None) -> list[str]:
    """`--only CODE,CODE` → 规范化的错误码列表；未知码 → `UNKNOWN_CHECK_CODE`。"""
    from .output import FlowError

    if not raw:
        return []
    items: list[str] = []
    for chunk in ([raw] if isinstance(raw, str) else raw):
        for token in str(chunk).replace("，", ",").split(","):
            code = token.strip().upper()
            if code and code not in items:
                items.append(code)
    unknown = [code for code in items if code not in CHECK_CODES]
    if unknown:
        raise FlowError(
            "UNKNOWN_CHECK_CODE",
            f"未知的检查类：{'、'.join(unknown)}",
            object=unknown[0],
            suggestion="可用检查类：" + "、".join(sorted(CHECK_CODES)),
        )
    return items


def run(workspace: Workspace, rules: Rules, *, only: Iterable[str] | None = None) -> LintReport:
    """执行全部（或用 `only` 过滤后的）检查。只读，不写任何文件。"""
    wanted = list(only or [])
    topics = {CHECK_CODES[code][0] for code in wanted} if wanted else None

    report = LintReport()
    if not workspace.initialized:
        report.findings = [finding(
            "PROJECT_NOT_INITIALIZED", object=str(workspace.root), path=".neo-skill/init-state.yaml",
            message=f"{workspace.root} 不是已初始化的 Neo Skill 项目（缺少 init-state.yaml）",
            next_action=f"{FLOW_CORE} init",
        )]
        report.checked = sorted({CHECK_CODES["PROJECT_NOT_INITIALIZED"][0]})
        report.counts = _count(report.findings)
        return report

    ctx = _load_context(workspace, rules)
    findings: list[dict[str, Any]] = []
    checked: list[str] = []
    for topic, check in CHECKS:
        if topics is not None and topic not in topics:
            continue
        checked.append(topic)
        findings.extend(check(ctx))

    if wanted:
        findings = [item for item in findings if item["code"] in wanted]

    findings.sort(key=lambda item: (_SEVERITY_ORDER.get(item["severity"], 9),
                                    item["code"], item["object"], item["path"]))
    report.findings = findings
    report.checked = checked
    report.counts = _count(findings)
    return report


def _count(findings: list[dict[str, Any]]) -> dict[str, int]:
    errors = sum(1 for item in findings if item["severity"] == SEVERITY_ERROR)
    warnings = sum(1 for item in findings if item["severity"] == SEVERITY_WARNING)
    return {"error": errors, "warning": warnings, "total": len(findings)}
