"""`upstream`：上游进度来源绑定的写入与查询（SRS-FR-091、SRS-FR-095）。

为什么要有独立命令组：**绑定本身是配置，不是进度事实**。绑定只回答「去哪个远端、
看哪些资料」，与「上游现在做到哪一步」必须分开（后者是 `progress`，另一个命令组）。
把它做成确定性命令而不是 Skill 散文，是为了三件可验证的事：

- 只写一个受管理区块，区块外内容一个字节都不动（不做整文件覆盖）；
- 已有不同绑定不得静默覆盖，必须 `--force` 才替换；
- 没有 Agent 指引时落到 `README.md` 的 `## 上游进度来源` 小节，人类能直接看见。

区块形状（`AGENTS.md` 与 `README.md` 都用同一对标记，`upstream show` 与进度查询
只解析区块内部，不读区块外的任何正文）：

    <!-- neo-skill:upstream:begin -->
    upstream:
      id: "https://github.com/example/upstream"
      recorded_at: '2026-11-01T10:00:00+08:00'
      recorded_by: "alice"
      note: "上游进度绑定（由 flow-core upstream bind 维护，请勿手改）；只用于定位远端进度来源，不代表任何进度事实"
      sources:
        - "https://github.com/example/upstream/releases"
    <!-- neo-skill:upstream:end -->

区块内**只有 YAML**：不写 `#` 注释行（在 Markdown 里那是标题），不写除说明字段
以外的散文；`note` 既保留人读说明又让整块可直接 `yaml.safe_load`。

目标文档选择顺序：`--doc agents|readme` 强制指定 → 有 `AGENTS.md` 用 `AGENTS.md`
→ 有 `README.md` 用 `README.md` → 两者都没有时**创建** `README.md`（绝不覆盖已有文件，
只有文件不存在才创建）。

人工确认口径（复用 `state.require_confirmation`，不新造错误码机制）：
`--actor` + `--confirm` 缺一 → `CONFIRMATION_REQUIRED`，且磁盘零写入。
绑定不是产品决策，所以**不要求特定职责**，也不因团队未配置而阻断：
只有 `00-management/team.md` 存在且已有成员、而 actor 不在名单里时才报 `ACTOR_UNKNOWN`。

幂等口径：已有区块的 `id` 与 `sources` 与本轮请求一致 → `action: unchanged`，
不重写文件，因此 `recorded_at` / `recorded_by` 保持首次写入的值。
已有区块但内容不同（或根本解析不出来）→ `UPSTREAM_BINDING_CONFLICT`，
只有显式 `--force` 才替换区块内容，仍不动区块外任何字节。
"""

from __future__ import annotations

import argparse
from dataclasses import dataclass, field
from typing import Any

from .. import fsutil, locks, state
from ..output import FlowError, Result
from ..rules import Rules
from ..workspace import PROJECT_LOCK_OBJECT, Workspace

NAME = "upstream"
HELP = "上游进度来源绑定：写入 / 展示 / 状态（只写受管理区块，保留区块外内容）"

FLOW_CORE = "flow-core"
BEGIN = "<!-- neo-skill:upstream:begin -->"
END = "<!-- neo-skill:upstream:end -->"
README_HEADING = "## 上游进度来源"

AGENTS_DOC = "AGENTS.md"
README_DOC = "README.md"
DOCS = {"agents": AGENTS_DOC, "readme": README_DOC}

ACTOR_PLACEHOLDER = "<成员 id>"
ID_PLACEHOLDER = "<上游 URL 或项目标识>"
SOURCE_PLACEHOLDER = "<远端进度来源>"
NOTE = "上游进度绑定（由 flow-core upstream bind 维护，请勿手改）；只用于定位远端进度来源，不代表任何进度事实"

BIND_COMMAND = (f"{FLOW_CORE} upstream bind --id {ID_PLACEHOLDER} "
                f"--source {SOURCE_PLACEHOLDER} --actor {ACTOR_PLACEHOLDER} --confirm")


@dataclass(frozen=True)
class Binding:
    """区块内可解析的绑定投影。`recorded_at` / `recorded_by` 只做审计，不参与比较。"""

    identifier: str
    sources: tuple[str, ...]
    recorded_by: str = ""
    recorded_at: str = ""

    def to_dict(self) -> dict[str, Any]:
        return {
            "identifier": self.identifier,
            "sources": list(self.sources),
            "recorded_by": self.recorded_by,
            "recorded_at": self.recorded_at,
        }


@dataclass(frozen=True)
class Located:
    """一次扫描结果：绑定是否存在、在哪个文档、内容是否可解析。"""

    document: str | None = None
    binding: Binding | None = None
    text: str = ""
    span: tuple[int, int] | None = None
    extra: dict[str, Any] = field(default_factory=dict)

    @property
    def configured(self) -> bool:
        return self.binding is not None


# --------------------------------------------------------------------------
# 参数
# --------------------------------------------------------------------------
def add_arguments(parser: argparse.ArgumentParser) -> None:
    from ..cli import global_options_parser

    parents = [global_options_parser()]
    subparsers = parser.add_subparsers(dest="upstream_command", metavar="<子命令>",
                                       required=True)

    bind = subparsers.add_parser(
        "bind", parents=parents,
        help="写入上游绑定（先展示内容再写；只改受管理区块）")
    bind.add_argument("--id", required=True, metavar="URL|IDENT",
                      help="上游 URL 或项目标识（不得从 origin/目录名自动推断）")
    bind.add_argument("--source", required=True, action="append", dest="sources",
                      metavar="SOURCE",
                      help="远端进度来源，可重复（至少一个；https:// 或 file://）")
    bind.add_argument("--doc", choices=sorted(DOCS), default=None,
                      help="目标文档：agents=AGENTS.md，readme=README.md（缺省自动选择）")
    bind.add_argument("--force", action="store_true",
                      help="已有不同绑定时覆盖区块内容（仍然只动区块）")

    subparsers.add_parser("show", parents=parents,
                          help="展示当前绑定与来源文档（未配置 → 业务失败）")
    subparsers.add_parser("status", parents=parents,
                          help="只读报告绑定状态（未配置也算成功，供接入流程判断）")


def run(workspace: Workspace, rules: Rules, args: argparse.Namespace) -> Result:
    sub = getattr(args, "upstream_command", None)
    handler = _HANDLERS.get(sub or "")
    if handler is None:
        raise FlowError(
            "USAGE",
            "缺少 upstream 子命令",
            suggestion="可用子命令：" + "、".join(sorted(_HANDLERS)),
        )
    try:
        return handler(workspace, rules, args)
    except FlowError as exc:
        if not exc.command:
            exc.command = f"{NAME}.{sub}"
        raise


# --------------------------------------------------------------------------
# 子命令
# --------------------------------------------------------------------------
def _bind(workspace: Workspace, rules: Rules, args: argparse.Namespace) -> Result:
    actor = state.require_confirmation(
        str(getattr(args, "actor", None) or "").strip() or None,
        bool(getattr(args, "confirm", False)),
        command="upstream.bind",
    )

    identifier = str(getattr(args, "id", None) or "").strip()
    sources = _normalize_sources(getattr(args, "sources", None))
    if not identifier or not sources:
        missing = []
        if not identifier:
            missing.append("--id")
        if not sources:
            missing.append("--source")
        raise FlowError(
            "UPSTREAM_BINDING_INCOMPLETE",
            f"上游绑定信息不完整（缺少 {'、'.join(missing)}），未写入任何文件",
            object=identifier or None,
            suggestion="补齐上游标识与至少一个远端进度来源后重试；不绑定也可以继续接入，"
                       "只是进度查询处于未配置状态",
            next_actions=[BIND_COMMAND],
        )

    # 绑定不是产品决策：团队未配置不阻断；只在已登记成员名单里核对身份，不要求职责。
    team = state.load_team(workspace)
    if team is not None and team.members:
        state.require_member(team, actor, object=identifier)

    document = _select_document(workspace, getattr(args, "doc", None))
    path = workspace.path(document)
    exists = path.is_file()
    text = fsutil.read_text(path) if exists else ""

    located = _locate(text, document)
    force = bool(getattr(args, "force", False))
    if located.span is not None:
        existing = located.binding
        if existing is None:
            if not force:
                raise _conflict(document, identifier,
                               reason="已有区块内容无法按预期解析，不做静默替换")
        elif existing.identifier == identifier and list(existing.sources) == sources:
            # 幂等：绑定本身没变，不重写，保留首次写入的人与时间
            result = Result(command=f"{NAME}.bind")
            result.add(document, "upstream", "unchanged")
            result.data.update(_data_of(located, document))
            result.data["forced"] = False
            result.notes.append(f"上游绑定未变化，未改动 {document}")
            result.next_actions.append(f"{FLOW_CORE} upstream show")
            return result
        elif not force:
            raise _conflict(document, identifier,
                           reason=f"已有绑定 {existing.identifier} 与本次请求不同")

    block = _render_block(identifier, sources, actor, fsutil.now_iso())
    merged = _merge_document(text, block, with_heading=(document == README_DOC))

    result = Result(command=f"{NAME}.bind")
    with locks.object_lock(workspace, PROJECT_LOCK_OBJECT, command=f"{NAME}.bind",
                           rules_version=rules.version):
        fsutil.atomic_write_text(path, merged)
    result.add(document, "upstream", "updated" if exists else "created")
    result.data.update(_data_of(_locate(merged, document), document))
    result.data["forced"] = force and located.span is not None
    result.notes.append(
        f"上游绑定已写入 {document} 的受管理区块（{BEGIN} … {END}），区块外内容未改动")
    result.next_actions.append(f"{FLOW_CORE} upstream show")
    return result


def _show(workspace: Workspace, rules: Rules, args: argparse.Namespace) -> Result:
    located = _scan(workspace)
    if not located.configured:
        raise FlowError(
            "UPSTREAM_NOT_CONFIGURED",
            "当前项目还没有可解析的上游绑定（AGENTS.md 与 README.md 中都没有绑定区块）",
            suggestion="项目负责人确认上游信息后写入绑定；也可以继续接入，进度查询暂不可用",
            next_actions=[BIND_COMMAND],
        )
    result = Result(command=f"{NAME}.show")
    result.data.update(_data_of(located, located.document or ""))
    result.notes.append(f"上游绑定位于 {located.document}，来源 {len(located.binding.sources)} 个")
    result.notes.append("绑定只用于定位远端，不是上游进度事实")
    return result


def _status(workspace: Workspace, rules: Rules, args: argparse.Namespace) -> Result:
    located = _scan(workspace)
    result = Result(command=f"{NAME}.status")
    result.data.update(_data_of(located, located.document or ""))
    if located.configured:
        result.notes.append(f"已绑定上游 {located.binding.identifier}（{located.document}）")
    else:
        result.notes.append("未配置上游绑定：进度查询不可用，接入流程可继续")
        result.next_actions.append(BIND_COMMAND)
    return result


_HANDLERS = {"bind": _bind, "show": _show, "status": _status}


# --------------------------------------------------------------------------
# 文档选择、区块定位与合并
# --------------------------------------------------------------------------
def _select_document(workspace: Workspace, doc: str | None) -> str:
    """目标文档：显式 `--doc` > 已有 AGENTS.md > 已有 README.md > 新建 README.md。"""
    if doc:
        return DOCS[doc]
    if workspace.path(AGENTS_DOC).is_file():
        return AGENTS_DOC
    return README_DOC


def _normalize_sources(raw: list[str] | None) -> list[str]:
    """去重保序：同一来源重复出现只记一次，顺序按用户给的顺序。"""
    seen: list[str] = []
    for item in raw or []:
        value = str(item).strip()
        if value and value not in seen:
            seen.append(value)
    return seen


def _scan(workspace: Workspace) -> Located:
    """按 AGENTS.md → README.md 的顺序找第一个含绑定区块的文档。"""
    for document in (AGENTS_DOC, README_DOC):
        path = workspace.path(document)
        if not path.is_file():
            continue
        located = _locate(fsutil.read_text(path), document)
        if located.span is not None:
            return located
    return Located()


def _locate(text: str, document: str) -> Located:
    span = _find_block(text)
    if span is None:
        return Located(document=None, text=text)
    binding, problems = _parse_block(text[span[0]:span[1]])
    extra = {"parse_problems": problems} if problems else {}
    return Located(document=document, binding=binding, text=text, span=span, extra=extra)


def _find_block(text: str) -> tuple[int, int] | None:
    """返回 `(BEGIN 起点, END 之后可能的一个换行)`；标记不成对视为没有区块。"""
    start = text.find(BEGIN)
    if start < 0:
        return None
    end = text.find(END, start + len(BEGIN))
    if end < 0:
        return None
    stop = end + len(END)
    if text[stop:stop + 1] == "\n":
        stop += 1
    return (start, stop)


def _parse_block(chunk: str) -> tuple[Binding | None, list[str]]:
    """解析区块内部的 YAML。不做猜测：字段缺失即视为不可解析（交由 `--force` 处理）。"""
    body = chunk[len(BEGIN):]
    if END in body:
        body = body[:body.index(END)]
    try:
        payload = fsutil.parse_yaml_text(body)
    except (ValueError, fsutil.FsError) as exc:
        return None, [str(exc)]
    if not isinstance(payload, dict):
        return None, ["区块内容不是 YAML 映射"]
    upstream = payload.get("upstream")
    if not isinstance(upstream, dict):
        return None, ["缺少 upstream 映射"]
    identifier = str(upstream.get("id", "") or "").strip()
    raw_sources = upstream.get("sources")
    sources = [str(item).strip() for item in raw_sources] if isinstance(raw_sources, list) else []
    problems = []
    if not identifier:
        problems.append("缺少 upstream.id")
    if not sources or not all(sources):
        problems.append("upstream.sources 为空或含空项")
    if problems:
        return None, problems
    return (
        Binding(
            identifier=identifier,
            sources=tuple(sources),
            recorded_by=str(upstream.get("recorded_by", "") or ""),
            recorded_at=str(upstream.get("recorded_at", "") or ""),
        ),
        [],
    )


def _render_block(identifier: str, sources: list[str], actor: str, recorded_at: str) -> str:
    """手工渲染固定形状的 YAML：字段顺序与人读的缩进都是契约的一部分。

    不用 `yaml.safe_dump` 整块渲染有两个原因：列表缩进不可控（PyYAML 不缩进
    序列），且区块内不能出现以 `#` 开头的行——在 Markdown 里那是标题。
    说明文字放在 `note` 字段里，既人读又不被误当标题。
    """
    lines = [
        BEGIN,
        "upstream:",
        f"  id: {_scalar(identifier)}",
        f"  recorded_at: {_scalar(recorded_at)}",
        f"  recorded_by: {_scalar(actor)}",
        f"  note: {_scalar(NOTE)}",
        "  sources:",
        *[f"    - {_scalar(source)}" for source in sources],
        END,
        "",
    ]
    return "\n".join(lines)


def _scalar(value: str) -> str:
    """单行 YAML 标量：由 PyYAML 决定要不要加引号，避免手写转义出错。"""
    dumped = fsutil.dump_yaml(value).strip()
    if "\n" in dumped:  # 多行值不在本区块的契约内，强制退化成 JSON 字符串
        import json

        return json.dumps(value, ensure_ascii=False)
    return dumped


def _merge_document(text: str, block: str, *, with_heading: bool) -> str:
    """替换或追加区块；区块外内容原样保留，README 情形保证有 `## 上游进度来源`。"""
    span = _find_block(text)
    if span is not None:
        head, tail = text[:span[0]], text[span[1]:]
        if with_heading and not _has_heading(head + tail):
            # 区块在、但标题被人删了：只在区块前补回标题，不动其他位置
            head = f"{head.rstrip(chr(10))}\n\n{README_HEADING}\n\n" if head.strip() else (
                f"{README_HEADING}\n\n")
        return head + block + tail
    if not with_heading:
        return _join(text, block)
    if _has_heading(text):
        return _insert_in_heading_section(text, block)
    return _join(text, f"{README_HEADING}\n\n{block}")


def _has_heading(text: str) -> bool:
    return any(line.strip() == README_HEADING for line in text.splitlines())


def _insert_in_heading_section(text: str, block: str) -> str:
    """标题已存在：把区块插到该小节的**末尾**（纯插入，不重排既有内容）。

    为什么不插在标题正下方：那会把用户原本写在该小节里的说明挤到区块后面，
    等于重排了区块外内容。纯插入保证「区块外内容一个字节都不动」。
    """
    lines = text.splitlines(keepends=True)
    start = next(
        (index for index, line in enumerate(lines) if line.strip() == README_HEADING),
        None,
    )
    if start is None:  # pragma: no cover - 由调用方保证标题存在
        return _join(text, block)
    stop = len(lines)
    for index in range(start + 1, len(lines)):
        if lines[index].startswith("## "):
            stop = index
            break
    head = "".join(lines[:stop])
    tail = "".join(lines[stop:])
    head = head.rstrip("\n")
    merged = f"{head}\n\n{block}"
    return merged + ("\n" + tail if tail else "")


def _join(base: str, addition: str) -> str:
    """把 `addition` 追加到 `base` 之后，空行分隔，不改 `base` 已有字节。"""
    head = base.rstrip("\n")
    if not head:
        return addition
    return f"{head}\n\n{addition}"


# --------------------------------------------------------------------------
# 信封数据
# --------------------------------------------------------------------------
def _data_of(located: Located, document: str) -> dict[str, Any]:
    """`show` / `status` 共用的 `data` 字段（ticket 02 直接按这些键取值）。"""
    data: dict[str, Any] = {
        "configured": located.configured,
        "document": document or None,
        "identifier": None,
        "sources": [],
        "recorded_by": None,
        "recorded_at": None,
        "parse_problems": list(located.extra.get("parse_problems") or []),
    }
    if located.binding is not None:
        data.update(
            {
                "identifier": located.binding.identifier,
                "sources": list(located.binding.sources),
                "recorded_by": located.binding.recorded_by or None,
                "recorded_at": located.binding.recorded_at or None,
            }
        )
    return data


def _conflict(document: str, identifier: str, *, reason: str) -> FlowError:
    return FlowError(
        "UPSTREAM_BINDING_CONFLICT",
        f"{document} 中已有上游绑定，与本次请求不一致，未做任何写入（{reason}）",
        object=f"{document}#{identifier}",
        suggestion="先确认哪份绑定才是权威来源；确认要替换时加 --force（仍然只改受管理区块）",
        next_actions=[
            f"{FLOW_CORE} upstream show",
            f"{FLOW_CORE} upstream bind --id {ID_PLACEHOLDER} --source {SOURCE_PLACEHOLDER} "
            f"--actor {ACTOR_PLACEHOLDER} --confirm --force",
        ],
    )