"""多文件事务（共享设计 §7、HLD §8.3）。

为什么需要它：验收收尾要一次性同步「功能现状 → 场景状态 → 索引 → 主需求」。
主需求 `IN_PRD → DONE` 必须在其它同步全部落地之后才写（SRS-FR-076），
而中间任何一步失败都必须**保留人工验收结论**并留下可恢复的审计记录（SRS-FR-077）。

磁盘布局：

    .neo-skill/transactions/<txid>/
        tx.yaml         事务记录（id/object/state/created_at/ops）
        staged/NN.bin   每个 op 的目标内容（NN = order，两位补零）

tx.yaml 形状（共享设计 §7）：

    id: TX-20260101-0001
    object: PRD-001
    state: PREPARED          # PREPARED | COMMITTING | COMMITTED | FAILED
    created_at: "..."
    updated_at: "..."
    command: acceptance.closeout
    actor: alice
    ops:
      - {order: 1, path: "02-definition/modules/resume/features/parse.md",
         baseline: "sha256:..." | null, staged: "staged/01.bin", action: update}

`baseline` 是写入前目标文件的内容 sha256；文件原本不存在时是 `null`。
`recover` 重新比对当前文件与 baseline：不一致 → `BASELINE_CONFLICT`，**不写任何文件**。

状态机：

    PREPARED ──commit──▶ COMMITTING ──全部替换──▶ COMMITTED
        │                     │
        └──── 任一步失败 ─────┴──▶ FAILED（保留 tx 目录与暂存内容）

审计记录只增不删（BR-019）：`COMMITTED` 的事务目录也保留。
"""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Iterable

from . import fsutil, hashing
from .output import FlowError
from .workspace import Workspace

STATE_PREPARED = "PREPARED"
STATE_COMMITTING = "COMMITTING"
STATE_COMMITTED = "COMMITTED"
STATE_FAILED = "FAILED"
STATES = (STATE_PREPARED, STATE_COMMITTING, STATE_COMMITTED, STATE_FAILED)

# 单个目标在某一时刻的落地情况
OP_PENDING = "pending"
OP_APPLIED = "applied"
OP_CONFLICT = "conflict"

# 事务目录内的固定文件名
TX_FILE = "tx.yaml"
STAGED_DIR = "staged"

FLOW_CORE = "flow-core"

# 仅供测试的显式故障注入钩子：`NEO_SKILL_FAIL_TX_STEP=<order>` 让某个 order 的替换失败。
# 这是测试契约的一部分，默认不设置即完全无副作用；生产路径不得依赖它。
FAIL_STEP_ENV = "NEO_SKILL_FAIL_TX_STEP"


# --------------------------------------------------------------------------
# 数据对象
# --------------------------------------------------------------------------
@dataclass
class Op:
    """一个待替换的目标文件。"""

    order: int
    path: str
    baseline: str | None
    content: str
    action: str = "update"   # create | update

    @property
    def staged(self) -> str:
        return f"{STAGED_DIR}/{self.order:02d}.bin"

    def to_dict(self) -> dict[str, Any]:
        return {
            "order": self.order,
            "path": self.path,
            "baseline": self.baseline,
            "staged": self.staged,
            "action": self.action,
        }


@dataclass
class Transaction:
    """`tx.yaml` 的内存投影。"""

    id: str
    object: str
    state: str
    created_at: str
    ops: list[Op] = field(default_factory=list)
    updated_at: str | None = None
    command: str | None = None
    actor: str | None = None
    error: str | None = None
    directory: Path | None = None
    # `begin()`/`load()` 绑定的工作区，使 `commit(tx)` 这种单参调用也能工作
    workspace: Workspace | None = field(default=None, repr=False, compare=False)

    # ---- 读写 ----
    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "object": self.object,
            "state": self.state,
            "created_at": self.created_at,
            "updated_at": self.updated_at,
            "command": self.command,
            "actor": self.actor,
            "error": self.error,
            "ops": [op.to_dict() for op in self.ops],
        }

    @property
    def pending(self) -> list[Op]:
        """尚未落地的目标：按 order 排序（`COMMITTED` 时为空）。"""
        if self.state == STATE_COMMITTED:
            return []
        return sorted(self.ops, key=lambda item: item.order)

    def paths(self) -> list[str]:
        return [op.path for op in sorted(self.ops, key=lambda item: item.order)]


# --------------------------------------------------------------------------
# 路径与编号
# --------------------------------------------------------------------------
def root_dir(workspace: Workspace) -> Path:
    return workspace.transactions_dir


def tx_dir(workspace: Workspace, tx_id: str) -> Path:
    return root_dir(workspace) / tx_id


def next_id(workspace: Workspace) -> str:
    """`TX-YYYYMMDD-NNNN`：当天序号单调递增（只增不复用）。"""
    stamp = (fsutil.now_iso() or "")[:10].replace("-", "") or "00000000"
    prefix = f"TX-{stamp}-"
    existing: list[int] = []
    directory = root_dir(workspace)
    if directory.is_dir():
        for path in directory.iterdir():
            if not path.is_dir() or not path.name.startswith(prefix):
                continue
            tail = path.name[len(prefix):]
            if tail.isdigit():
                existing.append(int(tail))
    return f"{prefix}{max(existing, default=0) + 1:04d}"


# --------------------------------------------------------------------------
# 开始事务
# --------------------------------------------------------------------------
def begin(workspace: Workspace, object_id: str,
          ops: Iterable[dict[str, Any]] | Iterable[Op], *,
          command: str | None = None, actor: str | None = None) -> Transaction:
    """写入 `tx.yaml` + `staged/NN.bin`，state = `PREPARED`。

    `ops` 每项至少要有 `order` / `path` / `content`；`baseline` 缺省时按当前文件内容现算，
    `action` 缺省时按文件是否存在推断。
    """
    normalized = [_normalize_op(workspace, item) for item in ops]
    if not normalized:
        raise FlowError("TX_EMPTY", "事务至少要有一个同步目标",
                        object=object_id,
                        suggestion="这是内部错误：调用方应先生成目标清单")
    orders = [op.order for op in normalized]
    if len(set(orders)) != len(orders):
        raise FlowError("TX_INVALID", "事务目标的 order 有重复", object=object_id)
    paths = [op.path for op in normalized]
    if len(set(paths)) != len(paths):
        raise FlowError("TX_INVALID", "事务目标有重复路径", object=object_id)

    tx = Transaction(
        id=next_id(workspace),
        object=object_id,
        state=STATE_PREPARED,
        created_at=fsutil.now_iso(),
        ops=sorted(normalized, key=lambda item: item.order),
        updated_at=fsutil.now_iso(),
        command=command,
        actor=actor,
    )
    directory = tx_dir(workspace, tx.id)
    (directory / STAGED_DIR).mkdir(parents=True, exist_ok=True)
    for op in tx.ops:
        fsutil.atomic_write_text(directory / op.staged, op.content)
    tx.directory = directory
    tx.workspace = workspace
    _write_tx(workspace, tx)
    return tx


def _normalize_op(workspace: Workspace, raw: dict[str, Any] | Op) -> Op:
    if isinstance(raw, Op):
        return raw
    path = str(raw.get("path") or "")
    if not path:
        raise FlowError("TX_INVALID", "事务目标缺少 path")
    target = workspace.path(path)
    exists = target.is_file()
    baseline = raw.get("baseline")
    if baseline is None and exists:
        baseline = hashing.sha256_file(target)
    return Op(
        order=int(raw.get("order") or 0),
        path=path,
        baseline=baseline,
        content=str(raw.get("content") or ""),
        action=str(raw.get("action") or ("update" if exists else "create")),
    )


# --------------------------------------------------------------------------
# 提交
# --------------------------------------------------------------------------
def commit(workspace: Workspace | Transaction, tx: Transaction | None = None) -> Transaction:
    """`PREPARED → COMMITTING → COMMITTED`：按 order 逐项原子替换。

    支持 `commit(workspace, tx)` 与 `commit(tx)`（后者用 `begin()`/`load()` 绑定的工作区）。

    任一步失败：state 置 `FAILED`（保留 tx 目录、暂存内容与错误），抛 `FlowError`。
    """
    workspace, tx = _resolve(workspace, tx)
    _set_state(workspace, tx, STATE_COMMITTING)
    try:
        for op in sorted(tx.ops, key=lambda item: item.order):
            _replace_one(workspace, tx, op)
    except BaseException as exc:
        tx.error = _error_text(exc)
        _set_state(workspace, tx, STATE_FAILED)
        if isinstance(exc, FlowError):
            raise
        raise FlowError(
            "TX_WRITE_FAILED",
            f"事务 {tx.id} 写入 {tx.error} 时失败，已保留暂存内容供 recover 恢复",
            object=tx.object,
            suggestion=f"确认磁盘与权限后执行 {FLOW_CORE} recover run --tx {tx.id} "
                       f"--actor <成员 id> --confirm",
            next_actions=[f"{FLOW_CORE} recover run --tx {tx.id} --actor <成员 id> --confirm"],
        ) from exc
    _set_state(workspace, tx, STATE_COMMITTED)
    return tx


def _replace_one(workspace: Workspace, tx: Transaction, op: Op) -> None:
    if _inject_failure(op):
        raise FlowError(
            "TX_STEP_FAILED",
            f"事务 {tx.id} 的第 {op.order} 步（{op.path}）写入失败",
            object=op.path,
            suggestion=f"这是注入的故障（{FAIL_STEP_ENV}={op.order}）",
        )
    staged = (tx.directory or tx_dir(workspace, tx.id)) / op.staged
    if not staged.is_file():
        raise FlowError(
            "TX_STAGED_MISSING",
            f"事务 {tx.id} 缺少暂存内容 {op.staged}",
            object=op.path,
            suggestion="暂存内容被删除，无法安全完成该事务：核对目标文件后重新执行命令",
        )
    fsutil.atomic_write_text(workspace.path(op.path), fsutil.read_text(staged))


def _inject_failure(op: Op) -> bool:
    """测试钩子：`NEO_SKILL_FAIL_TX_STEP=<order>` 让该 order 的替换失败。"""
    raw = os.environ.get(FAIL_STEP_ENV)
    if not raw:
        return False
    try:
        return int(raw.strip()) == op.order
    except ValueError:
        return False


# --------------------------------------------------------------------------
# 恢复
# --------------------------------------------------------------------------
def recover_one(workspace: Workspace | Transaction, tx: Transaction | None = None) -> Transaction:
    """重新比对基线 → `COMMITTING` → 替换 → `COMMITTED`。

    三态判定（每个目标）：

    - `pending`：当前内容 == `baseline`（还没换）；
    - `applied`：当前内容 == 暂存内容的 sha256（上一次 commit 已经写过），不算冲突；
    - `conflict`：其它情况（含被新建/删除）→ `BASELINE_CONFLICT`，**不写任何文件**，
      事务状态保持不变，由人工决定怎么处理（不得静默覆盖）。
    """
    workspace, tx = _resolve(workspace, tx)
    if tx.state == STATE_COMMITTED:
        return tx
    conflicts = baseline_conflicts(workspace, tx)
    if conflicts:
        raise FlowError(
            "BASELINE_CONFLICT",
            "恢复被拒绝：以下目标在事务准备后被改动过："
            + "；".join(f"{item['path']}（期望 {item['expected'] or '不存在'}，"
                        f"实际 {item['actual'] or '不存在'}）" for item in conflicts),
            object=conflicts[0]["path"],
            suggestion="事务不会覆盖别人的新修改。请先确认这些改动是否要保留："
                       f"保留则重新执行 {FLOW_CORE} acceptance closeout（会重算基线）；"
                       f"放弃则把改动还原成事务记录里的基线后再执行 {FLOW_CORE} recover run "
                       f"--tx {tx.id} --actor <成员 id> --confirm",
            next_actions=[
                f"{FLOW_CORE} recover list",
                f"{FLOW_CORE} acceptance closeout-status --prd {tx.object}",
            ],
        )
    return commit(workspace, tx)


def baseline_conflicts(workspace: Workspace, tx: Transaction) -> list[dict[str, Any]]:
    """逐项判定每个目标：返回冲突清单（空 = 可以安全继续）。"""
    return [item for item in op_states(workspace, tx).values()
            if item["state"] == OP_CONFLICT]


def op_states(workspace: Workspace, tx: Transaction) -> dict[int, dict[str, Any]]:
    """每个 order 的当前落地情况：`pending` / `applied` / `conflict`。"""
    result: dict[int, dict[str, Any]] = {}
    for op in sorted(tx.ops, key=lambda item: item.order):
        target = workspace.path(op.path)
        actual = hashing.sha256_file(target) if target.is_file() else None
        staged = (tx.directory or tx_dir(workspace, tx.id)) / op.staged
        expected_content = (hashing.sha256_file(staged)
                            if staged.is_file() else None)
        if actual == op.baseline:
            state = OP_PENDING
        elif expected_content is not None and actual == expected_content:
            state = OP_APPLIED
        else:
            state = OP_CONFLICT
        result[op.order] = {
            "order": op.order,
            "path": op.path,
            "state": state,
            "expected": op.baseline,
            "actual": actual,
        }
    return result


def pending_ops(workspace: Workspace, tx: Transaction) -> list[str]:
    """恢复后仍需落地的目标（尚未替换成暂存内容）。"""
    if tx.state == STATE_COMMITTED:
        return []
    return [item["path"] for item in op_states(workspace, tx).values()
            if item["state"] != OP_APPLIED]


def applied_ops(workspace: Workspace, tx: Transaction) -> list[str]:
    if tx.state == STATE_COMMITTED:
        return tx.paths()
    return [item["path"] for item in op_states(workspace, tx).values()
            if item["state"] == OP_APPLIED]


# --------------------------------------------------------------------------
# 读取
# --------------------------------------------------------------------------
def load(workspace: Workspace, tx_id: str) -> Transaction:
    directory = tx_dir(workspace, tx_id)
    path = directory / TX_FILE
    if not path.is_file():
        raise FlowError(
            "TX_NOT_FOUND",
            f"找不到事务 {tx_id}",
            object=tx_id,
            suggestion=f"用 {FLOW_CORE} recover list 查看未完成的事务",
            next_actions=[f"{FLOW_CORE} recover list"],
        )
    tx = _read_tx(directory)
    tx.workspace = workspace
    return tx


def list_transactions(workspace: Workspace) -> list[Transaction]:
    directory = root_dir(workspace)
    if not directory.is_dir():
        return []
    found: list[Transaction] = []
    for path in sorted(directory.iterdir()):
        if not path.is_dir() or not (path / TX_FILE).is_file():
            continue
        try:
            tx = _read_tx(path)
            tx.workspace = workspace
            found.append(tx)
        except FlowError:  # 损坏的事务记录不影响清单（由 recover list 的 error 字段报告）
            continue
    return found


def unfinished(workspace: Workspace) -> list[Transaction]:
    return [tx for tx in list_transactions(workspace)
            if tx.state in (STATE_PREPARED, STATE_COMMITTING, STATE_FAILED)]


def find_for_object(workspace: Workspace, object_id: str, *,
                    states: Iterable[str] = (STATE_PREPARED, STATE_COMMITTING,
                                             STATE_FAILED)) -> Transaction | None:
    """按对象找最新一条事务（默认只看未完成状态）。"""
    wanted = tuple(states)
    candidates = [tx for tx in list_transactions(workspace)
                  if tx.object == object_id and tx.state in wanted]
    return candidates[-1] if candidates else None


def _read_tx(directory: Path) -> Transaction:
    data = fsutil.read_yaml(directory / TX_FILE)
    if not isinstance(data, dict):
        raise FlowError("TX_INVALID", f"{directory / TX_FILE} 不是 YAML 映射", object=str(directory))
    ops = [
        Op(
            order=int(item.get("order") or 0),
            path=str(item.get("path") or ""),
            baseline=(str(item["baseline"]) if item.get("baseline") else None),
            content="",
            action=str(item.get("action") or "update"),
        )
        for item in data.get("ops") or []
        if isinstance(item, dict)
    ]
    return Transaction(
        id=str(data.get("id") or directory.name),
        object=str(data.get("object") or ""),
        state=str(data.get("state") or STATE_PREPARED),
        created_at=str(data.get("created_at") or ""),
        ops=ops,
        updated_at=data.get("updated_at"),
        command=data.get("command"),
        actor=data.get("actor"),
        error=data.get("error"),
        directory=directory,
    )


def _write_tx(workspace: Workspace, tx: Transaction) -> None:
    directory = tx.directory or tx_dir(workspace, tx.id)
    directory.mkdir(parents=True, exist_ok=True)
    fsutil.atomic_write_yaml(directory / TX_FILE, tx.to_dict())


def _set_state(workspace: Workspace, tx: Transaction, state: str) -> None:
    """原子写回状态（每次状态变化都落盘，便于断电后恢复）。"""
    tx.state = state
    tx.updated_at = fsutil.now_iso()
    _write_tx(workspace, tx)


def _resolve(workspace: Workspace | Transaction,
             tx: Transaction | None) -> tuple[Workspace, Transaction]:
    """兼容 `f(ws, tx)` 与单参 `f(tx)` 两种调用形状。"""
    if tx is None:
        if not isinstance(workspace, Transaction):
            raise FlowError("TX_INVALID", "调用事务函数时必须给出事务对象")
        tx = workspace
        if tx.workspace is None:
            raise FlowError(
                "TX_INVALID",
                f"事务 {tx.id} 没有绑定工作区，请用事务(工作区, tx) 形式调用",
                object=tx.id,
            )
        return tx.workspace, tx
    if isinstance(workspace, Transaction):  # pragma: no cover - 参数写反
        raise FlowError("TX_INVALID", "事务函数的第一个参数必须是工作区")
    return workspace, tx


def _error_text(exc: BaseException) -> str:
    if isinstance(exc, FlowError):
        return exc.message
    return f"{exc.__class__.__name__}: {exc}"
