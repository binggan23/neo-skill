"""PRD 基线状态：hash、失效标记与失效证据（ticket 04；05 门禁 / 07 验收 / 09 lint 复用）。

为什么单独一个模块：
- `dev` 门禁（ticket 05）、`lint`（ticket 09）、`acceptance`（ticket 07）都要问同一个问题：
  「这个 PRD 的当前内容还在不在它的批准基线上？」；
- 判定必须只有一处实现，否则门禁与 lint 会给出互相矛盾的结论；
- 所以本模块提供 **可 import 的函数** `evaluate(ws, rules, record) -> BaselineStatus`，
  `prd baseline-status` / `prd verify-hash` 只是它的薄壳。

判定顺序（`reasons` 里按此顺序给出全部原因，不只报第一条）：

1. `BASELINE_MISSING`：从未批准（`content_hash` 为空）；
2. `HASH_MISMATCH`：正文或锁定字段被直接改过（hash 与会话内重算不一致）；
3. `BASELINE_INVALIDATED`：存在未决/已解决的 CR 失效标记且标记仍为 `active: true`。

`valid = True` 当且仅当 `reasons` 为空。

失效标记写在 PRD `metadata.yaml` 的 `baseline_invalidated`（**不在** `locked_fields` 里，
所以写标记本身不会改 hash）：

    baseline_invalidated:
      at: "2026-03-01T10:00:00+08:00"
      by: alice
      cr: CR-001
      previous_status: DEVELOPING
      previous_hash: "sha256:..."
      active: true              # CR 重新批准或内容未变即拒绝后置 false
      resolved_cr: null         # 解决它的 CR（批准或拒绝的那个）
      resolved_at: null
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from . import changerequest as crs, fsutil
from .metadata import PrdRecord
from .rules import Rules
from .workspace import Workspace

REASON_MISSING = "BASELINE_MISSING"
REASON_MISMATCH = "HASH_MISMATCH"
REASON_INVALIDATED = "BASELINE_INVALIDATED"

# 下游基线标记文件名（停在已有目录里，不创建目录）
MARKER_FILE = ".baseline.yaml"
ACCEPTANCE_REPORT = "report.md"
MARKER_BEGIN = "<!-- neo-skill:baseline-invalid:begin -->"
MARKER_END = "<!-- neo-skill:baseline-invalid:end -->"


@dataclass
class BaselineStatus:
    """基线状态投影（`prd baseline-status` 的 `data` 就是这个 dataclass）。"""

    prd: str
    version: int
    content_hash: str | None
    expected: str | None
    current: str
    valid: bool
    reasons: list[str] = field(default_factory=list)
    invalidated_by: dict[str, Any] | None = None
    superseded_evidence: list[dict[str, Any]] = field(default_factory=list)
    open_cr: str | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "prd": self.prd,
            "version": self.version,
            "content_hash": self.content_hash,
            "expected": self.expected,
            "current": self.current,
            "valid": self.valid,
            "reasons": list(self.reasons),
            "invalidated_by": self.invalidated_by,
            "superseded_evidence": list(self.superseded_evidence),
            "open_cr": self.open_cr,
        }


def evaluate(workspace: Workspace, rules: Rules, record: PrdRecord) -> BaselineStatus:
    """判定 PRD 的基线是否有效。只读：不修改 metadata、正文或任何文件。"""
    expected = record.content_hash
    current = record.compute_content_hash()
    reasons: list[str] = []
    if not expected:
        reasons.append(REASON_MISSING)
    elif expected != current:
        reasons.append(REASON_MISMATCH)

    invalidated = invalidation(record)
    if invalidated is not None and invalidated.get("active", False):
        reasons.append(REASON_INVALIDATED)

    open_cr = None
    pending = crs.open_crs(workspace, record)
    if pending:
        open_cr = pending[0].id

    return BaselineStatus(
        prd=record.id,
        version=record.version,
        content_hash=expected,
        expected=expected,
        current=current,
        valid=not reasons,
        reasons=reasons,
        invalidated_by=invalidated,
        superseded_evidence=superseded_evidence(workspace, record),
        open_cr=open_cr,
    )


def invalidation(record: PrdRecord) -> dict[str, Any] | None:
    """读取 metadata 里的失效标记（`active: true` 表示当前内容仍未重新批准）。"""
    value = record.meta.get("baseline_invalidated")
    return dict(value) if isinstance(value, dict) and value else None


def active_invalidation(*args: Any) -> dict[str, Any] | None:
    """基线是否「仍然失效」，两种调用形状（ticket 09 追加，保持向后兼容）：

    1. `active_invalidation(record)` —— 只看 metadata 的 `baseline_invalidated.active`
       （ticket 04/05 的用法，语义不变）；
    2. `active_invalidation(workspace, rules, record)` —— **叠加未决变更**：
       PRD 里还有 status 为 OPEN 的 CR 时，即使正文 hash 没变、甚至失效标记已被手工
       抹掉，也认为基线失效（共同设计 §13 待办 3：`BASELINE_STALE` 不能只看 hash）。

    返回 None 表示有效；否则返回证据字典：

        {"source": "marker" | "open_cr" | "marker+open_cr",
         "cr": CR 编号或 None, "marker": 失效标记或 None, "reasons": [...]}
    """
    if len(args) == 1:
        marker = invalidation(args[0])
        if marker is not None and marker.get("active", False):
            return marker
        return None
    if len(args) != 3:
        raise TypeError(
            "active_invalidation() 接受 (record) 或 (workspace, rules, record)")
    workspace, _rules, record = args
    return active_invalidation_evidence(workspace, record)


def active_invalidation_evidence(workspace: Workspace,
                                 record: PrdRecord) -> dict[str, Any] | None:
    """失效证据（含未决 CR）。供 lint / 验收 / 收尾按需叠加判定。"""
    marker = invalidation(record)
    active_marker = marker if marker is not None and marker.get("active", False) else None
    pending = crs.open_crs(workspace, record)
    open_cr = pending[0] if pending else None
    if active_marker is None and open_cr is None:
        return None
    reasons: list[str] = []
    if active_marker is not None:
        reasons.append(REASON_INVALIDATED)
    if open_cr is not None:
        reasons.append("CR_OPEN")
    source = "marker" if open_cr is None else (
        "open_cr" if active_marker is None else "marker+open_cr")
    return {
        "source": source,
        "cr": open_cr.id if open_cr is not None else (active_marker or {}).get("cr"),
        "marker": active_marker,
        "reasons": reasons,
    }


def mark_invalidated(*, cr_id: str, previous_status: str, previous_hash: str | None,
                     actor: str, at: str) -> dict[str, Any]:
    """构造失效标记（内存对象）；写入由调用方原子完成。"""
    return {
        "at": at,
        "by": actor,
        "cr": cr_id,
        "previous_status": previous_status,
        "previous_hash": previous_hash,
        "active": True,
        "resolved_cr": None,
        "resolved_at": None,
    }


def mark_resolved(marker: dict[str, Any], *, cr_id: str, at: str) -> dict[str, Any]:
    """标记失效已解除（CR 重新批准，或内容未变时 CR 被拒）。返回同一 dict。"""
    marker["active"] = False
    marker["resolved_cr"] = cr_id
    marker["resolved_at"] = at
    return marker


# --------------------------------------------------------------------------
# 下游基线失效标记（Spec / Plan / Tasks / 验收报告）
# --------------------------------------------------------------------------
def spec_dir(workspace: Workspace, record: PrdRecord):
    """`06-specs/<module>/PRD-NNN-<slug>/`（ticket 05 产出；这里只读定位）。"""
    return workspace.specs_dir / (record.module or "_") / record.directory.name


def acceptance_dir(workspace: Workspace, record: PrdRecord):
    """`07-acceptance/<module>/PRD-NNN-<slug>/`（ticket 07 产出；这里只读定位）。"""
    return workspace.acceptance_dir / (record.module or "_") / record.directory.name


def mark_downstream(workspace: Workspace, record: PrdRecord, *, cr_id: str,
                   prd_hash: str | None, at: str, actor: str) -> list[str]:
    """在**已存在**的 Spec / 验收产物上标基线失效，返回被标记的相对路径。

    不存在就跳过：CR 不能凭空造出 `06-specs/`、`verification.md`、`07-acceptance/`
    （那些归 ticket 05/06/07）。标记只追加，不改原有正文。

    `.baseline.yaml` 由 ticket 05 的 `dev start` 写入（`baseline_hash`/`baseline_version` 等）；
    这里**合并非覆盖**：保留 05 写的字段，只追加失效标记，否则 05 的「Spec 基线是否陈旧」
    判断会失去比较基准。
    """
    marked: list[str] = []
    additions = {
        "prd": record.id,
        "prd_version": record.version,
        "baseline_valid": False,
        "invalidated_by": cr_id,
        "invalidated_at": at,
        "invalidated_by_actor": actor,
        "invalidated_reason": "PRD 内容基线已变更，重新批准前不得继续正式开发（SRS-FR-050）",
    }

    specs = spec_dir(workspace, record)
    if specs.is_dir():
        path = specs / MARKER_FILE
        existing = fsutil.load_yaml(path, default={})
        payload = dict(existing) if isinstance(existing, dict) else {}
        payload.update(additions)
        if prd_hash:  # 没有 05 基线时也留下当时的 PRD hash，便于追溯
            payload.setdefault("baseline_hash", prd_hash)
        fsutil.atomic_write_yaml(path, payload)
        marked.append(workspace.rel(path))

    report = acceptance_dir(workspace, record) / ACCEPTANCE_REPORT
    if report.is_file():
        text = fsutil.read_text(report)
        block = _annotation(cr_id=cr_id, at=at, actor=actor, prd_hash=prd_hash,
                            version=record.version)
        if MARKER_BEGIN in text:  # 幂等：替换标记块，不重复追加
            head = text.split(MARKER_BEGIN)[0].rstrip("\n")
            tail = text.split(MARKER_END, 1)[1].lstrip("\n")
            text = head + "\n\n" + block + ("\n" + tail if tail else "")
        else:
            text = text.rstrip("\n") + "\n\n" + block
        fsutil.atomic_write_text(report, text)
        marked.append(workspace.rel(report))
    return marked


def _annotation(*, cr_id: str, at: str, actor: str, prd_hash: str | None,
                version: int) -> str:
    lines = [
        MARKER_BEGIN,
        "",
        f"> **基线失效**：PRD 已提交变更 {cr_id}（{at}，{actor}），当前验收证据不再有效。",
        ">",
        f"> - 原基线 hash：`{prd_hash or '未记录'}`（版本 {version}）",
        "> - 处理方式：按 CR 重新批准 PRD 后重跑验收；旧结论保留但不得沿用（SRS-BR-016）。",
        "",
        MARKER_END,
    ]
    return "\n".join(lines)


def superseded_evidence(workspace: Workspace, record: PrdRecord) -> list[dict[str, Any]]:
    """被 CR 取代的历史证据（旧批准 + 旧验收结论），保留原始字段只加失效标记。"""
    evidence: list[dict[str, Any]] = []

    for entry in crs.approvals(workspace, record):
        if entry.get("superseded_by_cr"):
            evidence.append({
                "kind": "approval",
                "version": entry["version"],
                "content_hash": entry["content_hash"],
                "recorded_by": entry["approved_by"],
                "recorded_at": entry["approved_at"],
                "cr": entry["superseded_by_cr"],
                "superseded_by_version": entry.get("superseded_by_version"),
            })

    acceptance = record.meta.get("acceptance")
    if isinstance(acceptance, dict) and (
        acceptance.get("superseded_by_cr") or acceptance.get("superseded_by_version")
    ):
        evidence.append({
            "kind": "acceptance",
            "version": acceptance.get("version"),
            "content_hash": acceptance.get("content_hash"),
            "result": acceptance.get("result"),
            "recorded_by": acceptance.get("accepted_by"),
            "recorded_at": acceptance.get("accepted_at"),
            "cr": acceptance.get("superseded_by_cr"),
            "superseded_by_version": acceptance.get("superseded_by_version"),
        })
    return evidence
