"""`progress`：查询远端上游进度并生成可核查报告（SRS-FR-092、SRS-FR-093、SRS-FR-094）。

为什么要有这个命令组：进度结论必须能被核查，所以「从哪来」这件事要做成确定性代码，
而不是 Skill 散文里的自由发挥。本命令只做**只读**查询：不写任何文档、不推进任何状态、
不需要 `--actor` / `--confirm`。

三条硬边界（每条都有测试盯着）：

1. **绑定只用于定位**。`AGENTS.md`（其次 `README.md`）里的
   `<!-- neo-skill:upstream:begin -->` 区块只取 `upstream.id` 与 `upstream.sources`，
   区块外正文一律不读（复用 `upstream._scan` 的解析，不另写一份）。
   区块内就算写着「已发布 v9」，也不会被当成证据。
2. **远端证据是唯一进度来源**。只抓绑定/本次参数给出的 URL，不扫本地代码、
   业务文档、`08-releases/CHANGELOG.md` 或 `.scratch/` 草稿。
3. **本地 Git 元数据单列**。`dirty` / `ahead` / `behind` / `no_remote` / `unavailable`
   只进 `local_reminders`，人类可读输出里用独立标题「本地提醒（不在上游进度内）」，
   绝不并入 `published` / `ongoing`。Git 只看元数据（`status --porcelain`、
   `rev-list --count`），不读任何文件正文。

来源抓取：`https://` / `http://` 走标准库 `urllib.request`（带超时、`User-Agent`、
响应体上限 1 MiB）；`file://` 读本地文件（测试夹具与真实来源走**同一条解析路径**）。
其他 scheme、不可达、超时、非 UTF-8、解析不出来 → 该来源进 `uncertainty` 并写明原因，
**不影响其他来源**、也不让整次查询失败：G0 的口径是「知道多少说多少，不知道的要说不知道」。

档案两族，字段探测刻意宽松（不绑死某个 API 版本）：

- JSON：任意层级的「对象列表」，条目取 `name`/`title`/`subject`/`tag_name` 之一作摘要；
  状态按 `state`（closed/merged/done → 已发布；open/draft/todo → 进行中）→
  `merged`/`merged_at` → `draft` → `published_at`/`tag_name` 的顺序判定，
  全无线索时按「进行中」处理（宁可不夸大上游进度）。
- Markdown（CHANGELOG / roadmap 风格）：`##` 及更深标题是条目，标题里带
  `YYYY-MM-DD` 视为已发布版本条目，否则视为进行中；`- [x]` 已完成、`- [ ]` 未完成。
  `#` 一级标题视为文档标题，不算条目（否则每份 CHANGELOG 都会多出一条假「已完成」）。

矛盾与过期（至少一条可测规则，两条都已实现）：

- **conflicting**：同一事项名同时出现在「已发布」和「进行中」（同一来源内或跨来源）。
  这种条目两侧都不计入，只报不确定性——不确定的不能当结论用。
- **stale**：来源文本里能识别出日期时，取其中最新日期；早于 `STALE_DAYS`（=180 天）
  前即标记过期。当前时间可用 `NEO_SKILL_NOW` 覆盖（测试靠它固定时钟，否则断言随日期漂移）。

解析成功但一条条目都识别不出来 → `incomplete`（文档在，但这份文档不承载进度证据）。
"""

from __future__ import annotations

import argparse
import http.client
import json
import os
import re
import subprocess
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from typing import Any

from .. import fsutil
from ..commands import upstream as upstream_command
from ..output import FlowError, Result
from ..rules import Rules
from ..workspace import Workspace

NAME = "progress"
HELP = "查询上游远端进度：已发布 / 进行中 / 来源 / 不确定性 / 本地提醒（只读）"

FLOW_CORE = "flow-core"
UPSTREAM_PLACEHOLDER = "<上游 URL 或项目标识>"
SOURCE_PLACEHOLDER = "<远端进度来源>"

# 过期阈值（天）：180 天差不多是两个发布周期。取常数而不是配置项，是为了
# 「过期就是过期」可复核；测试用 NEO_SKILL_NOW 固定时钟，不靠改阈值凑断言。
STALE_DAYS = 180
# 抓取超时与响应体上限：远端不可达时宁可快速失败进 uncertainty，也不要卡住查询。
FETCH_TIMEOUT = 15.0
MAX_BYTES = 1 << 20
USER_AGENT = "neo-skill-flow-core-progress/0.1.1 (+read-only upstream progress report)"
GIT_TIMEOUT = 15.0

REPORT_HEADING = "上游进度报告"
LOCAL_HEADING = "本地提醒（不在上游进度内）"

SUPPORTED_SCHEMES = ("http", "https", "file")

_DATE_RE = re.compile(r"\b(\d{4})-(\d{2})-(\d{2})\b")
_CHECKBOX_RE = re.compile(r"^\s*[-*+]\s*\[(?P<mark>[ xX])\]\s*(?P<text>.*\S)\s*$")
_HEADING_RE = re.compile(r"^(?P<hashes>#{1,6})\s+(?P<text>.+?)\s*#*\s*$")

# 条目摘要字段的探测顺序：release 用 name/tag_name，issue/PR 用 title。
_TITLE_KEYS = ("name", "title", "subject", "tag_name", "tag", "summary")

PUBLISHED = "published"
ONGOING = "ongoing"

TEMP_QUERY_COMMAND = (f"{FLOW_CORE} progress report --upstream {UPSTREAM_PLACEHOLDER} "
                      f"--source {SOURCE_PLACEHOLDER}")


# --------------------------------------------------------------------------
# 参数
# --------------------------------------------------------------------------
def add_arguments(parser: argparse.ArgumentParser) -> None:
    from ..cli import global_options_parser

    parents = [global_options_parser()]
    subparsers = parser.add_subparsers(dest="progress_command", metavar="<子命令>",
                                       required=True)

    report = subparsers.add_parser("report", parents=parents,
                                   help="只读查询上游进度（不改文档、不需要 --confirm）")
    report.add_argument("--upstream", default=None, metavar="URL|IDENT",
                        help="本次临时查询的上游 URL/标识（无绑定时与 --source 一起用）")
    report.add_argument("--source", action="append", dest="sources", default=None,
                        metavar="SOURCE",
                        help="本次临时查询的远端进度来源，可重复（https:// / http:// / file://）")


def run(workspace: Workspace, rules: Rules, args: argparse.Namespace) -> Result:
    sub = getattr(args, "progress_command", None)
    handler = _HANDLERS.get(sub or "")
    if handler is None:
        raise FlowError(
            "USAGE",
            "缺少 progress 子命令",
            suggestion="可用子命令：" + "、".join(sorted(_HANDLERS)),
        )
    try:
        return handler(workspace, rules, args)
    except FlowError as exc:
        if not exc.command:
            exc.command = f"{NAME}.{sub}"
        raise


# --------------------------------------------------------------------------
# 报告
# --------------------------------------------------------------------------
def _report(workspace: Workspace, rules: Rules, args: argparse.Namespace) -> Result:
    identifier, sources, origin = _resolve_inputs(workspace, args)

    inspected = [_inspect(url) for url in sources]
    entries, uncertainty = _classify(inspected)

    # 本地通道：只读 Git 元数据，永远不参与上面两个桶的判定。
    reminders = _local_reminders(workspace.root)

    data: dict[str, Any] = {
        "upstream": {"identifier": identifier, "origin": origin},
        "published": [_entry_dict(item) for item in entries if item.bucket == PUBLISHED],
        "ongoing": [_entry_dict(item) for item in entries if item.bucket == ONGOING],
        "sources": [_source_dict(item) for item in inspected],
        "uncertainty": uncertainty,
        "local_reminders": reminders,
    }

    result = Result(command=f"{NAME}.report")
    result.data.update(data)
    result.notes.extend(_human_notes(data))
    if origin == "temporary":
        result.notes.append("本次是临时查询：没有读取项目绑定，也没有写回任何文档")
        result.next_actions.append(upstream_command.BIND_COMMAND)
    return result


_HANDLERS = {"report": _report}


def _resolve_inputs(workspace: Workspace,
                    args: argparse.Namespace) -> tuple[str, list[str], str]:
    """决定这次查谁：显式参数 > 持久化绑定 > 报错（绝不猜 origin/目录名）。"""
    cli_identifier = str(getattr(args, "upstream", None) or "").strip()
    cli_sources = _normalize_sources(getattr(args, "sources", None))
    located = upstream_command._scan(workspace)

    if cli_identifier and cli_sources:
        # 用户显式给了本次参数：按临时查询处理，连绑定都不看（行为可预测）。
        return cli_identifier, cli_sources, "temporary"
    if located.configured and located.binding is not None:
        return (located.binding.identifier, list(located.binding.sources), "binding")
    if cli_identifier or cli_sources:
        raise _input_required("本次参数不完整：`--upstream` 与至少一个 `--source` 必须同时给出")
    if located.span is not None:
        # 区块在但解析不出来：与 `upstream show` 同口径，不谎报成「未配置」。
        problems = "；".join(located.extra.get("parse_problems") or []) or "字段缺失"
        raise FlowError(
            "UPSTREAM_BINDING_INVALID",
            f"{located.document} 中已有上游绑定区块，但无法解析为可用配置（{problems}）",
            object=located.document,
            suggestion="核对区块内 YAML；也可以先用 --upstream/--source 做一次临时查询",
            next_actions=[TEMP_QUERY_COMMAND],
        )
    raise _input_required("当前项目没有上游绑定，也没有给出本次查询的上游与来源")


def _input_required(reason: str) -> FlowError:
    return FlowError(
        "UPSTREAM_QUERY_INPUT_REQUIRED",
        f"{reason}，无法定位远端进度资料。",
        suggestion="长期使用请先写入绑定；只想查一次则用 --upstream + --source 临时查询"
                   "（临时查询不写回任何文档）",
        next_actions=[upstream_command.BIND_COMMAND, TEMP_QUERY_COMMAND],
    )


def _normalize_sources(raw: list[str] | None) -> list[str]:
    """去重保序：同一来源重复出现只抓一次。"""
    seen: list[str] = []
    for item in raw or []:
        value = str(item).strip()
        if value and value not in seen:
            seen.append(value)
    return seen


# --------------------------------------------------------------------------
# 抓取与解析
# --------------------------------------------------------------------------
@dataclass(frozen=True)
class Problem:
    """一条不确定性。`reason` 取值见模块 docstring。"""

    source: str
    reason: str
    detail: str

    def to_dict(self) -> dict[str, str]:
        return {"source": self.source, "reason": self.reason, "detail": self.detail}


@dataclass(frozen=True)
class Entry:
    """一条进度证据。`bucket` 只有 published / ongoing 两种。"""

    summary: str
    source: str
    bucket: str


@dataclass
class Inspected:
    """单个来源的完整观察结果（含失败）。"""

    url: str
    accessible: bool = False
    kind: str | None = None
    fetched_at: str | None = None
    summary: str = ""
    entries: list[Entry] = field(default_factory=list)
    problems: list[Problem] = field(default_factory=list)
    text: str = ""
    latest_date: date | None = None


def _inspect(url: str) -> Inspected:
    """抓一个来源并解析；任何失败都落成 `problems`，绝不抛异常出去。"""
    result = Inspected(url=url)
    scheme = urllib.parse.urlsplit(url).scheme.lower()
    if scheme not in SUPPORTED_SCHEMES:
        result.problems.append(Problem(
            url, "unsupported_scheme",
            f"不支持的来源协议 {scheme or '(缺失)'}；本版本只支持 https:// / http:// / file://"))
        result.summary = "协议不支持，未抓取"
        return result

    text, failure = _read(url, scheme)
    if failure is not None:
        reason, detail = failure
        result.problems.append(Problem(url, reason, detail))
        result.summary = f"未取得内容（{detail}）"
        return result

    result.accessible = True
    result.fetched_at = fsutil.now_iso()
    result.text = text
    result.latest_date = _latest_date(text)

    parsed, kind, parse_failure = _parse(text)
    result.kind = kind
    if parse_failure is not None:
        reason, detail = parse_failure
        result.problems.append(Problem(url, reason, detail))
        result.summary = f"内容无法解析为进度资料（{detail}）"
        return result

    result.entries = [Entry(summary=name, source=url, bucket=bucket) for name, bucket in parsed]
    published = sum(1 for item in result.entries if item.bucket == PUBLISHED)
    ongoing = sum(1 for item in result.entries if item.bucket == ONGOING)
    result.summary = f"{kind} 文档：识别 {len(result.entries)} 条（已发布 {published}、进行中 {ongoing}）"
    if not result.entries:
        result.problems.append(Problem(
            url, "incomplete", "文档可解析，但没有识别出任何版本/待办条目"))
    return result


def _read(url: str, scheme: str) -> tuple[str, tuple[str, str] | None]:
    """返回 `(文本, 失败原因)`；`file://` 与远端读出来的是同一个形状。"""
    if scheme == "file":
        raw, failure = _read_file(url)
    else:
        raw, failure = _read_http(url)
    if failure is not None:
        return "", failure
    try:
        return raw.decode("utf-8"), None
    except UnicodeDecodeError:
        return "", ("unparsable", "内容不是 UTF-8 文本，无法作为进度资料")


def _read_file(url: str) -> tuple[bytes, tuple[str, str] | None]:
    parts = urllib.parse.urlsplit(url)
    if parts.netloc and parts.netloc.lower() not in ("", "localhost"):
        return b"", ("inaccessible", f"file:// 只支持本机路径，无法访问主机 {parts.netloc}")
    path = Path(urllib.request.url2pathname(urllib.parse.unquote(parts.path)))
    try:
        raw = path.read_bytes()
    except OSError as exc:
        return b"", ("inaccessible", f"读取本地来源失败：{exc.strerror or exc}")
    if len(raw) > MAX_BYTES:
        return b"", ("unparsable", f"内容超过 {MAX_BYTES} 字节上限，未解析")
    return raw, None


def _read_http(url: str) -> tuple[bytes, tuple[str, str] | None]:
    request = urllib.request.Request(
        url, headers={"User-Agent": USER_AGENT, "Accept": "application/json, text/markdown, text/plain"})
    try:
        with urllib.request.urlopen(request, timeout=FETCH_TIMEOUT) as response:
            raw = response.read(MAX_BYTES + 1)
    except urllib.error.HTTPError as exc:  # 先于 URLError：HTTPError 是它的子类
        return b"", ("inaccessible", f"HTTP {exc.code}：{exc.reason}")
    except (urllib.error.URLError, TimeoutError, OSError, http.client.HTTPException, ValueError) as exc:
        return b"", ("inaccessible", f"抓取失败：{getattr(exc, 'reason', exc)}")
    if len(raw) > MAX_BYTES:
        return b"", ("unparsable", f"响应体超过 {MAX_BYTES} 字节上限，未解析")
    return raw, None


def _parse(text: str) -> tuple[list[tuple[str, str]], str | None, tuple[str, str] | None]:
    """先按 JSON 试，再按 Markdown 试；两族都认不出来才算解析失败。"""
    payload, json_error = _try_json(text)
    if json_error is None:
        entries = _entries_from_json(payload)
        return entries, "json", None
    entries = _entries_from_markdown(text)
    if entries or _looks_like_markdown(text):
        return entries, "markdown", None
    return [], None, ("unparsable", f"既不是 JSON 列表也不是带标题/勾选项的 Markdown（{json_error}）")


def _try_json(text: str) -> tuple[Any, str | None]:
    stripped = text.strip()
    if not stripped or stripped[0] not in "[{":
        return None, "内容不是 JSON"
    try:
        return json.loads(stripped), None
    except json.JSONDecodeError as exc:
        return None, f"JSON 解析失败：第 {exc.lineno} 行 {exc.msg}"


def _entries_from_json(payload: Any) -> list[tuple[str, str]]:
    """在任意层级里找「对象列表」，条目字段探测保持宽松（不绑死 API 版本）。"""
    entries: list[tuple[str, str]] = []
    for item in _walk_object_lists(payload, depth=0):
        summary = _title_of(item)
        if not summary:
            continue
        bucket = _bucket_of(item)
        if bucket is not None:
            entries.append((summary, bucket))
    return entries


def _walk_object_lists(payload: Any, *, depth: int) -> list[dict[str, Any]]:
    """递归收集「看起来像条目」的映射：必须有标题类字段，避免把统计数字当条目。"""
    items: list[dict[str, Any]] = []
    if depth > 3:  # 层级上限：进度文档不会更深，防止畸形输入拖慢扫描
        return items
    if isinstance(payload, list):
        for element in payload:
            if isinstance(element, dict):
                if _title_of(element):
                    items.append(element)
                else:
                    items.extend(_walk_object_lists(element, depth=depth + 1))
            elif isinstance(element, list):
                items.extend(_walk_object_lists(element, depth=depth + 1))
        return items
    if isinstance(payload, dict):
        for value in payload.values():
            if isinstance(value, (list, dict)):
                items.extend(_walk_object_lists(value, depth=depth + 1))
    return items


def _title_of(item: dict[str, Any]) -> str:
    for key in _TITLE_KEYS:
        value = item.get(key)
        if isinstance(value, str) and value.strip():
            return value.strip()
    return ""


_DONE_STATES = frozenset({"closed", "merged", "done", "completed", "published", "released", "shipped"})
_OPEN_STATES = frozenset({"open", "opened", "in_progress", "in progress", "draft", "pending", "todo",
                          "planned", "started"})


def _bucket_of(item: dict[str, Any]) -> str | None:
    """判定一条 JSON 条目的状态。线索全无时按「进行中」，宁可低估上游进度。"""
    state = str(item.get("state") or "").strip().lower()
    if state in _DONE_STATES:
        return PUBLISHED
    if state in _OPEN_STATES:
        return ONGOING
    if item.get("merged") is True or item.get("merged_at"):
        return PUBLISHED
    if "draft" in item:
        # release 的 draft=true 是「尚未发布」；PR 的 draft=true 同理。
        return ONGOING if item.get("draft") else PUBLISHED
    if item.get("closed") is True or item.get("closed_at"):
        return PUBLISHED
    if item.get("published_at") or item.get("released_at") or item.get("tag_name"):
        return PUBLISHED
    if _DATE_RE.search(_title_of(item)):
        # 标题自带日期通常意味着版本/里程碑，这条线索最弱，放在最后兜底。
        return PUBLISHED
    return ONGOING


def _looks_like_markdown(text: str) -> bool:
    for line in text.splitlines():
        if _CHECKBOX_RE.match(line) or _HEADING_RE.match(line):
            return True
    return False


def _entries_from_markdown(text: str) -> list[tuple[str, str]]:
    """`##` 及更深的标题是条目；`- [x]` 已完成、`- [ ]` 未完成。一级标题是文档名。"""
    entries: list[tuple[str, str]] = []
    for line in text.splitlines():
        checkbox = _CHECKBOX_RE.match(line)
        if checkbox:
            bucket = PUBLISHED if checkbox.group("mark").strip().lower() == "x" else ONGOING
            entries.append((checkbox.group("text").strip(), bucket))
            continue
        heading = _HEADING_RE.match(line)
        if heading and len(heading.group("hashes")) >= 2:
            title = heading.group("text").strip()
            if title:
                bucket = PUBLISHED if _DATE_RE.search(title) else ONGOING
                entries.append((title, bucket))
    return entries


def _latest_date(text: str) -> date | None:
    """取文本里能识别的最新日期（用于 stale 判定）。"""
    latest: date | None = None
    for match in _DATE_RE.finditer(text):
        try:
            found = date(int(match.group(1)), int(match.group(2)), int(match.group(3)))
        except ValueError:
            continue
        if latest is None or found > latest:
            latest = found
    return latest


# --------------------------------------------------------------------------
# 分类：矛盾、过期
# --------------------------------------------------------------------------
def _classify(inspected: list[Inspected]) -> tuple[list[Entry], list[dict[str, str]]]:
    """汇总证据并做矛盾/过期判定；后两者只进不确定性，不删改证据来源本身。"""
    deduped: dict[tuple[str, str, str], Entry] = {}
    for source in inspected:
        for entry in source.entries:
            deduped.setdefault((entry.source, entry.bucket, entry.summary), entry)
    entries = list(deduped.values())

    buckets: dict[str, set[str]] = {}
    for entry in entries:
        buckets.setdefault(entry.summary, set()).add(entry.bucket)
    conflicting = {name for name, seen in buckets.items() if len(seen) > 1}

    problems: list[dict[str, str]] = [item.to_dict() for source in inspected
                                      for item in source.problems]
    if conflicting:
        kept: list[Entry] = []
        reported: set[tuple[str, str]] = set()
        for entry in entries:
            if entry.summary in conflicting:
                # 同一事项在两侧都有证据：两侧都不计入，只报不确定性。
                key = (entry.summary, entry.source)
                if key not in reported:
                    reported.add(key)
                    problems.append(Problem(
                        entry.source, "conflicting",
                        f"「{entry.summary}」同时出现已发布与未完成证据，未计入任何一侧").to_dict())
                continue
            kept.append(entry)
        entries = kept

    now = _now()
    threshold = now.date() - timedelta(days=STALE_DAYS)
    for source in inspected:
        if source.latest_date is not None and source.latest_date < threshold:
            problems.append(Problem(
                source.url, "stale",
                f"来源内最新日期 {source.latest_date.isoformat()} 早于 "
                f"{threshold.isoformat()}（阈值 {STALE_DAYS} 天），可能已过期").to_dict())
    return entries, problems


def _now() -> datetime:
    """当前时间；`NEO_SKILL_NOW` 可覆盖（与 `fsutil.now_iso` 同一约定，便于测试固定时钟）。"""
    override = os.environ.get("NEO_SKILL_NOW", "").strip()
    if override:
        try:
            parsed = datetime.fromisoformat(override)
        except ValueError:
            parsed = None
        if parsed is not None:
            return parsed if parsed.tzinfo else parsed.replace(tzinfo=timezone.utc)
    return datetime.now().astimezone()


# --------------------------------------------------------------------------
# 本地通道：只读 Git 元数据
# --------------------------------------------------------------------------
def _local_reminders(root: Path) -> list[dict[str, str]]:
    """`dirty` / `ahead` / `behind` / `no_remote` / `unavailable`。

    刻意只看计数，不看文件名、更不读文件内容：本地提醒的用途是「提醒你别把本地当成上游」，
    不是第二条进度计算（SRS-FR-093）。
    """
    status = _git(root, "status", "--porcelain")
    if status is None:
        return [{"kind": "unavailable",
                 "detail": "未检测到可用的 Git 仓库或 git 命令，无法给出本地提醒"}]

    reminders: list[dict[str, str]] = []
    changed = [line for line in status.splitlines() if line.strip()]
    if changed:
        reminders.append({"kind": "dirty",
                          "detail": f"工作区有 {len(changed)} 项未提交改动（本地状态，不代表上游进度）"})

    ahead_raw = _git(root, "rev-list", "--count", "@{u}..HEAD")
    behind_raw = _git(root, "rev-list", "--count", "HEAD..@{u}")
    if ahead_raw is None or behind_raw is None:
        reminders.append({"kind": "no_remote",
                          "detail": "当前分支没有可比对的跟踪远端，无法判断本地领先/落后"})
        return reminders
    ahead = _count(ahead_raw)
    behind = _count(behind_raw)
    if ahead:
        reminders.append({"kind": "ahead",
                          "detail": f"本地领先跟踪远端 {ahead} 个提交（尚未推送，不代表上游进度）"})
    if behind:
        reminders.append({"kind": "behind",
                          "detail": f"本地落后跟踪远端 {behind} 个提交（上游可能有新变化）"})
    return reminders


def _git(root: Path, *args: str) -> str | None:
    """执行只读 git 子命令；不可用或失败返回 None（不抛异常、不写任何东西）。"""
    try:
        proc = subprocess.run(
            ["git", "-C", str(root), *args],
            capture_output=True, text=True, encoding="utf-8", timeout=GIT_TIMEOUT, check=False,
        )
    except (OSError, subprocess.SubprocessError):
        return None
    if proc.returncode != 0:
        return None
    return proc.stdout


def _count(raw: str) -> int:
    try:
        return max(0, int(raw.strip()))
    except ValueError:
        return 0


# --------------------------------------------------------------------------
# 信封与人类可读输出
# --------------------------------------------------------------------------
def _entry_dict(entry: Entry) -> dict[str, str]:
    return {"summary": entry.summary, "source": entry.source}


def _source_dict(inspected: Inspected) -> dict[str, Any]:
    return {
        "url": inspected.url,
        "accessible": inspected.accessible,
        "fetched_at": inspected.fetched_at,
        "kind": inspected.kind,
        "summary": inspected.summary,
    }


def _human_notes(data: dict[str, Any]) -> list[str]:
    """人类可读输出：远端三节 + 不确定性 + 独立命名的本地提醒小节。"""
    upstream = data["upstream"]
    origin = "持久化绑定" if upstream["origin"] == "binding" else "本次临时提供（未写入文档）"
    notes = [f"{REPORT_HEADING}：上游 {upstream['identifier']}（来源：{origin}）"]

    notes.append("已发布/已完成（远端事实）：" if data["published"] else "已发布/已完成（远端事实）：无")
    for item in data["published"]:
        notes.append(f"  - {item['summary']} ← {item['source']}")

    notes.append("进行中/开放（远端事实）：" if data["ongoing"] else "进行中/开放（远端事实）：无")
    for item in data["ongoing"]:
        notes.append(f"  - {item['summary']} ← {item['source']}")

    notes.append("来源：")
    for item in data["sources"]:
        if item["accessible"]:
            notes.append(f"  - [可访问] {item['url']}（{item['kind']}）{item['summary']}")
        else:
            notes.append(f"  - [不可访问] {item['url']}：{item['summary']}")

    notes.append("不确定性：" if data["uncertainty"] else "不确定性：无")
    for item in data["uncertainty"]:
        notes.append(f"  - [{item['reason']}] {item['source']}：{item['detail']}")

    notes.append(f"{LOCAL_HEADING}：")
    if data["local_reminders"]:
        for item in data["local_reminders"]:
            notes.append(f"  - [{item['kind']}] {item['detail']}")
    else:
        notes.append("  - 无（工作区干净且与跟踪远端一致）")
    return notes
