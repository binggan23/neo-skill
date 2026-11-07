"""ticket 02：查询远端上游进度（`progress report`，黑盒 CLI）。

对应 SRS-FR-092、SRS-FR-093、SRS-FR-094：

- 远端证据必须分成「已发布/已完成」（`published`）与「进行中/开放」（`ongoing`），
  每条都带来源；
- 抓不到、scheme 不支持、解析不了、过期、互相矛盾 → 进 `uncertainty`，不整体失败；
- 没有绑定时可用 `--upstream` + `--source` 临时查询，**不写回任何文档**；
- 本地 Git 元数据只进 `local_reminders`（标签「本地提醒」），绝不混进远端结论；
- 本地业务文件（代码、CHANGELOG、issue 草稿）永远不作为进度证据。

夹具全部走 `file://`（不经网络）与临时 git 仓库（不联网），断言只看进程边界：
退出码、`--json` 信封与磁盘字节。
"""

from __future__ import annotations

import json
import subprocess
import threading
from functools import partial
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

from conftest import run_cli, write

# 固定「现在」：`stale` 是相对阈值（180 天），不固定时钟就等于让测试随日期漂移。
FIXED_NOW = "2026-06-01T00:00:00+08:00"

BEGIN = "<!-- neo-skill:upstream:begin -->"
END = "<!-- neo-skill:upstream:end -->"

UPSTREAM_ID = "https://github.com/example/upstream"

# 一个 GitHub releases 形状的 JSON（宽松字段：tag_name / name / draft / published_at）
RELEASES_JSON = {
    "releases": [
        {"tag_name": "v1.2.0", "name": "v1.2.0 稳定版", "draft": False,
         "published_at": "2026-05-20T00:00:00Z"},
        {"tag_name": "v1.3.0", "name": "v1.3.0 草案", "draft": True},
    ]
}

# issues/pulls 形状：有 state 用它，state=closed 视为已完成
ISSUES_JSON = {
    "issues": [
        {"title": "修复登录崩溃", "state": "open"},
        {"title": "新增 CSV 导出", "state": "closed"},
    ]
}

# roadmap 形状：带日期的标题是已发布版本条目，`- [x]` 已完成，`- [ ]` 进行中
ROADMAP_MD = """# 上游路线图

## 2026-05-01 导入向导

- [x] 导入向导
- [ ] 批量导出

## 多语言支持

## 离线模式
"""


def report(root: Path, *args: str, now: str | None = FIXED_NOW, json_output: bool = True):
    """统一构造 `progress report`：默认固定时钟，避免 stale 判定随真实日期漂移。"""
    env = {"NEO_SKILL_NOW": now} if now else None
    return run_cli(root, "progress", "report", *args, env=env, json_output=json_output)


def report_for(root: Path, *sources: str, identifier: str = UPSTREAM_ID, **kwargs):
    """临时查询（不写绑定）：`--upstream` + 一个以上 `--source`。"""
    argv: list[str] = ["--upstream", identifier]
    for source in sources:
        argv += ["--source", source]
    return report(root, *argv, **kwargs)


def binding(root: Path, *sources: str, identifier: str = UPSTREAM_ID) -> None:
    """用 ticket 01 的 `upstream bind` 写入绑定（走真实命令，不手写区块）。"""
    argv = ["upstream", "bind", "--id", identifier]
    for source in sources:
        argv += ["--source", source]
    result = run_cli(root, *argv, "--actor", "alice", "--confirm")
    assert result.code == 0, result


def fixture(tmp_path: Path, name: str, text: str) -> str:
    """把夹具写到 tmp_path/fixtures 并返回 `file://` URL（与远端抓取共用解析路径）。"""
    return write(tmp_path / "fixtures" / name, text).as_uri()


def summary_list(items: list[dict]) -> list[str]:
    return [item["summary"] for item in items]


def reasons(data: dict) -> list[str]:
    return [item["reason"] for item in data["uncertainty"]]


def snapshot(root: Path) -> dict[str, bytes]:
    # 用字节比较：`--root` 之外的任何写入（包括纯格式改动）都必须能被发现
    return {
        path.relative_to(root).as_posix(): path.read_bytes()
        for path in sorted(root.rglob("*"))
        if path.is_file()
    }


# --------------------------------------------------------------------------
# 远端证据：已发布 / 进行中 必须分开，且都带来源
# --------------------------------------------------------------------------
def test_已发布与进行中分开且每条带来源(tmp_path: Path) -> None:
    releases = fixture(tmp_path, "releases.json", json.dumps(RELEASES_JSON))
    issues = fixture(tmp_path, "issues.json", json.dumps(ISSUES_JSON))
    roadmap = fixture(tmp_path, "roadmap.md", ROADMAP_MD)

    result = report_for(tmp_path, releases, issues, roadmap)

    assert result.code == 0, result
    data = result.json["data"]
    published = summary_list(data["published"])
    ongoing = summary_list(data["ongoing"])

    # JSON：draft=false 是已发布，draft=true 是进行中
    assert "v1.2.0 稳定版" in published
    assert "v1.3.0 草案" in ongoing
    # issues：closed → 已发布，open → 进行中
    assert "新增 CSV 导出" in published
    assert "修复登录崩溃" in ongoing
    # markdown：`- [x]` 已完成、`- [ ]` 进行中、带日期的标题是已发布版本条目
    assert "导入向导" in published
    assert "批量导出" in ongoing
    assert "多语言支持" in ongoing
    assert "2026-05-01 导入向导" in published
    # 两族证据不得互相串场
    assert set(published) & set(ongoing) == set()
    # 每条结论都标来源，且来源就是本次给的 URL
    for item in data["published"] + data["ongoing"]:
        assert item["source"] in {releases, issues, roadmap}

    kinds = {item["url"]: item["kind"] for item in data["sources"]}
    assert kinds == {releases: "json", issues: "json", roadmap: "markdown"}
    for item in data["sources"]:
        assert item["accessible"] is True
        assert item["fetched_at"] == FIXED_NOW
        assert item["summary"]
        assert item["url"] in {releases, issues, roadmap}
    assert data["uncertainty"] == []
    assert data["upstream"] == {"identifier": UPSTREAM_ID, "origin": "temporary"}


# --------------------------------------------------------------------------
# 不确定性：不可达 / 不支持 / 解析不了 / 矛盾 / 过期
# --------------------------------------------------------------------------
def test_不可访问来源只进不确定性_其他来源仍出结论(tmp_path: Path) -> None:
    missing = (tmp_path / "nope" / "releases.json").as_uri()
    roadmap = fixture(tmp_path, "roadmap.md", ROADMAP_MD)

    result = report_for(tmp_path, missing, roadmap)

    assert result.code == 0, result
    data = result.json["data"]
    assert "批量导出" in summary_list(data["ongoing"])
    entry = next(item for item in data["uncertainty"] if item["source"] == missing)
    assert entry["reason"] == "inaccessible"
    assert entry["detail"]
    # 不可访问的来源仍出现在 sources 里，但明确标为不可访问
    failed = next(item for item in data["sources"] if item["url"] == missing)
    assert failed["accessible"] is False


def test_不支持的_scheme_记为_unsupported_scheme(tmp_path: Path) -> None:
    roadmap = fixture(tmp_path, "roadmap.md", ROADMAP_MD)

    result = report_for(tmp_path, "ftp://example.com/releases.json", roadmap)

    assert result.code == 0, result
    data = result.json["data"]
    assert "unsupported_scheme" in reasons(data)
    assert "批量导出" in summary_list(data["ongoing"])


def test_解析不了的正文记为_unparsable(tmp_path: Path) -> None:
    broken = fixture(tmp_path, "broken.json", "{这不是 JSON")
    roadmap = fixture(tmp_path, "roadmap.md", ROADMAP_MD)

    result = report_for(tmp_path, broken, roadmap)

    assert result.code == 0, result
    data = result.json["data"]
    entry = next(item for item in data["uncertainty"] if item["source"] == broken)
    assert entry["reason"] == "unparsable"
    assert "批量导出" in summary_list(data["ongoing"])


def test_同一事项状态矛盾记为_conflicting(tmp_path: Path) -> None:
    # 同一来源里「导入向导」既是已完成条目又是未完成条目
    contradictory = fixture(
        tmp_path,
        "contradictory.md",
        "# 上游状态\n\n## 导入向导\n\n- [x] 导入向导\n- [ ] 导入向导\n",
    )
    roadmap = fixture(tmp_path, "roadmap.md", ROADMAP_MD)

    result = report_for(tmp_path, contradictory, roadmap)

    assert result.code == 0, result
    data = result.json["data"]
    entry = next(item for item in data["uncertainty"] if item["source"] == contradictory)
    assert entry["reason"] == "conflicting"
    assert "导入向导" in entry["detail"]
    # 矛盾的条目不得被硬塞进任何一侧结论
    assert "导入向导" not in summary_list(data["published"])
    assert "导入向导" not in summary_list(data["ongoing"])
    # 其他条目不受影响
    assert "批量导出" in summary_list(data["ongoing"])


def test_两个来源对同一事项状态矛盾也记为_conflicting(tmp_path: Path) -> None:
    done = fixture(tmp_path, "done.md", "# 发布记录\n\n- [x] 批量导出\n")
    todo = fixture(tmp_path, "todo.md", "# 待办\n\n- [ ] 批量导出\n")

    result = report_for(tmp_path, done, todo)

    assert result.code == 0, result
    data = result.json["data"]
    assert {item["reason"] for item in data["uncertainty"]} == {"conflicting"}
    assert "批量导出" not in summary_list(data["published"])
    assert "批量导出" not in summary_list(data["ongoing"])


def test_来源内容过期记为_stale(tmp_path: Path) -> None:
    # 最新可识别日期是 2024-01-02，固定时钟 2026-06-01，远超 180 天阈值
    old = fixture(
        tmp_path,
        "old-roadmap.md",
        "# 上游路线图\n\n## 2024-01-02 导入向导\n\n- [ ] 批量导出\n",
    )

    result = report_for(tmp_path, old)

    assert result.code == 0, result
    data = result.json["data"]
    assert "stale" in reasons(data)
    # 过期只影响可信度，不删掉证据本身
    assert "批量导出" in summary_list(data["ongoing"])


# --------------------------------------------------------------------------
# 绑定 vs 临时查询
# --------------------------------------------------------------------------
def test_没有绑定时可临时查询且文档字节完全不变(project: Path) -> None:
    write(project / "AGENTS.md", "# 项目指引\n\n- 区块外内容。\n")
    roadmap = fixture(project, "roadmap.md", ROADMAP_MD)
    before = snapshot(project)
    assert run_cli(project, "upstream", "show").error_codes() == ["UPSTREAM_NOT_CONFIGURED"]

    result = report_for(project, roadmap)

    assert result.code == 0, result
    data = result.json["data"]
    assert data["upstream"] == {"identifier": UPSTREAM_ID, "origin": "temporary"}
    assert "批量导出" in summary_list(data["ongoing"])
    # 临时查询绝不写回文档（字节级比较，fixtures/ 之外什么都不许变）
    after = snapshot(project)
    assert {k: v for k, v in after.items() if not k.startswith("fixtures/")} == {
        k: v for k, v in before.items() if not k.startswith("fixtures/")
    }
    assert run_cli(project, "upstream", "show").error_codes() == ["UPSTREAM_NOT_CONFIGURED"]


def test_没有绑定也没有临时信息时报查询输入缺失(project: Path) -> None:
    write(project / "AGENTS.md", "# 项目指引\n")
    before = snapshot(project)

    result = report(project)

    assert result.code == 1, result
    assert result.error_codes() == ["UPSTREAM_QUERY_INPUT_REQUIRED"]
    hint = " ".join(
        [str(result.json["errors"][0].get("suggestion") or "")]
        + [str(item) for item in result.json["next_actions"]]
    )
    assert "--upstream" in hint and "--source" in hint, hint
    assert snapshot(project) == before


def test_只给_upstream_不给_source_时也报输入缺失(project: Path) -> None:
    result = report(project, "--upstream", UPSTREAM_ID)

    assert result.code == 1, result
    assert result.error_codes() == ["UPSTREAM_QUERY_INPUT_REQUIRED"]


def test_有绑定时_origin_为_binding_且不读本地业务内容(project: Path) -> None:
    # 本地业务文件里写着「已发布 v9.9.9」：如果被当成证据，报告里就会漏出这个字符串
    write(project / "08-releases" / "CHANGELOG.md", "# 本地变更\n\n## 已发布 v9.9.9\n")
    write(project / ".scratch" / "plan.md", "本地草稿：已发布 v9.9.9，剩余工作见 roadmap\n")
    roadmap = fixture(project, "roadmap.md", ROADMAP_MD)
    binding(project, roadmap)

    result = report(project)

    assert result.code == 0, result
    data = result.json["data"]
    assert data["upstream"] == {"identifier": UPSTREAM_ID, "origin": "binding"}
    assert "批量导出" in summary_list(data["ongoing"])
    assert "v9.9.9" not in json.dumps(result.json, ensure_ascii=False)


# --------------------------------------------------------------------------
# 本地 Git 元数据：独立通道，标签「本地提醒」
# --------------------------------------------------------------------------
def git(cwd: Path, *args: str) -> None:
    """临时仓库里的 git 操作：带上身份参数，不依赖用户全局配置，不联网。"""
    subprocess.run(
        ["git", "-C", str(cwd), "-c", "user.email=t@example.com", "-c", "user.name=tester",
         "-c", "commit.gpgsign=false", *args],
        capture_output=True, text=True, check=True, timeout=60,
    )


def make_repo(root: Path, *, with_remote: bool = False) -> Path | None:
    """建一个有 1 个提交的临时仓库；`with_remote` 时同时建裸仓并 push -u。"""
    repo = root / "repo"
    repo.mkdir()
    git(repo, "init", "-q", "-b", "main")
    write(repo / "AGENTS.md", "# 项目指引\n")
    git(repo, "add", "-A")
    git(repo, "commit", "-q", "-m", "init")
    if not with_remote:
        return None
    remote = root / "remote.git"
    subprocess.run(["git", "init", "-q", "--bare", str(remote)], capture_output=True,
                   check=True, timeout=60)
    git(repo, "remote", "add", "origin", str(remote))
    git(repo, "push", "-q", "-u", "origin", "main")
    return remote


def local_kinds(repo: Path, roadmap: str) -> tuple[list[str], dict]:
    result = report_for(repo, roadmap)
    assert result.code == 0, result
    data = result.json["data"]
    return [item["kind"] for item in data["local_reminders"]], data


def test_本地提醒_干净且有远端时不提示且不影响结论(tmp_path: Path) -> None:
    make_repo(tmp_path, with_remote=True)
    roadmap = fixture(tmp_path, "roadmap.md", ROADMAP_MD)

    kinds, data = local_kinds(tmp_path / "repo", roadmap)

    assert kinds == []
    assert "批量导出" in summary_list(data["ongoing"])
    assert "v1.2.0 稳定版" not in summary_list(data["published"])  # 本地没有这类证据


def test_本地提醒_无远端只提示_no_remote(tmp_path: Path) -> None:
    make_repo(tmp_path, with_remote=False)
    roadmap = fixture(tmp_path, "roadmap.md", ROADMAP_MD)

    kinds, data = local_kinds(tmp_path / "repo", roadmap)

    assert kinds == ["no_remote"]
    assert "批量导出" in summary_list(data["ongoing"])


def test_本地提醒_dirty(tmp_path: Path) -> None:
    make_repo(tmp_path, with_remote=True)
    roadmap = fixture(tmp_path, "roadmap.md", ROADMAP_MD)
    write(tmp_path / "repo" / "notes.md", "未提交的本地改动\n")

    kinds, data = local_kinds(tmp_path / "repo", roadmap)

    assert "dirty" in kinds
    detail = next(item["detail"] for item in data["local_reminders"] if item["kind"] == "dirty")
    assert detail
    # 未提交改动只算本地提醒，不得变成远端进度证据
    assert "未提交的本地改动" not in json.dumps(data, ensure_ascii=False)


def test_本地提醒_ahead(tmp_path: Path) -> None:
    make_repo(tmp_path, with_remote=True)
    repo = tmp_path / "repo"
    write(repo / "second.md", "第二个提交\n")
    git(repo, "add", "-A")
    git(repo, "commit", "-q", "-m", "second")
    roadmap = fixture(tmp_path, "roadmap.md", ROADMAP_MD)

    kinds, _ = local_kinds(repo, roadmap)

    assert "ahead" in kinds


def test_本地提醒_behind(tmp_path: Path) -> None:
    make_repo(tmp_path, with_remote=True)
    repo = tmp_path / "repo"
    write(repo / "second.md", "第二个提交\n")
    git(repo, "add", "-A")
    git(repo, "commit", "-q", "-m", "second")
    git(repo, "push", "-q")
    git(repo, "reset", "-q", "--hard", "HEAD~1")
    roadmap = fixture(tmp_path, "roadmap.md", ROADMAP_MD)

    kinds, _ = local_kinds(repo, roadmap)

    assert "behind" in kinds


def test_本地提醒_非仓库时为_unavailable(project: Path) -> None:
    roadmap = fixture(project, "roadmap.md", ROADMAP_MD)

    kinds, data = local_kinds(project, roadmap)

    assert kinds == ["unavailable"]
    assert "批量导出" in summary_list(data["ongoing"])


# --------------------------------------------------------------------------
# 输出形态：JSON 与人类可读都要有，人类可读里本地提醒用独立标题
# --------------------------------------------------------------------------
def test_人类可读输出含本地提醒标题与远端分节(tmp_path: Path) -> None:
    make_repo(tmp_path, with_remote=False)
    roadmap = fixture(tmp_path, "roadmap.md", ROADMAP_MD)

    result = report_for(tmp_path / "repo", roadmap, json_output=False)

    assert result.code == 0, result
    text = result.stdout
    assert "上游进度报告" in text
    assert "已发布" in text
    assert "进行中" in text
    assert "本地提醒（不在上游进度内）" in text
    assert "批量导出" in text
    # 本地提醒小节在远端小节之后，且不与远端结论混排
    assert text.index("进行中") < text.index("本地提醒（不在上游进度内）")


def test_human_输出在失败时也指出缺失输入(project: Path) -> None:
    result = report(project, json_output=False)

    assert result.code == 1, result
    assert "UPSTREAM_QUERY_INPUT_REQUIRED" in result.stdout


# --------------------------------------------------------------------------
# http:// 抓取路径：本机回环 HTTP 服务，不依赖任何真实上游服务
# --------------------------------------------------------------------------


class _QuietHandler(SimpleHTTPRequestHandler):
    """静默的静态文件服务：测试输出里不刷访问日志。"""

    def log_message(self, format: str, *args: object) -> None:  # 覆盖父类签名，测试输出不刷访问日志
        return


def test_http_来源可抓取且识别为已发布(tmp_path: Path) -> None:
    served = tmp_path / "served"
    write(served / "releases.json", json.dumps(RELEASES_JSON))
    server = ThreadingHTTPServer(("127.0.0.1", 0), partial(_QuietHandler, directory=str(served)))
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        url = f"http://127.0.0.1:{server.server_port}/releases.json"
        result = report_for(tmp_path, url)
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=10)

    assert result.code == 0, result
    data = result.json["data"]
    assert "v1.2.0 稳定版" in summary_list(data["published"])
    assert data["sources"][0]["accessible"] is True
    assert data["sources"][0]["kind"] == "json"


def test_可解析但没有进度条目的来源记为_incomplete(tmp_path: Path) -> None:
    # 合法的 JSON / Markdown，但里面没有任何版本或待办条目：文档在，证据不在。
    empty = fixture(tmp_path, "empty.json", json.dumps({"meta": {"count": 0}}))
    prose = fixture(tmp_path, "prose.md", "这里只是一段说明，没有标题也没有勾选项。\n")
    roadmap = fixture(tmp_path, "roadmap.md", ROADMAP_MD)

    result = report_for(tmp_path, empty, prose, roadmap)

    assert result.code == 0, result
    data = result.json["data"]
    assert "incomplete" in reasons(data)
    assert "批量导出" in summary_list(data["ongoing"])


def test_绑定区块坏了时报_binding_invalid_而不是_query_input_required(project: Path) -> None:
    write(project / "AGENTS.md", f"# 指引\n\n{BEGIN}\n这是人手写的、不是 YAML\n{END}\n")

    result = report(project)

    assert result.code == 1, result
    assert result.error_codes() == ["UPSTREAM_BINDING_INVALID"]
    assert any("--upstream" in item for item in result.json["next_actions"])
