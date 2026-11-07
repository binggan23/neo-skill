"""ticket 11：上游绑定的写入与持久化（`upstream bind` / `show` / `status`，黑盒 CLI）。

对应 SRS-FR-091、SRS-FR-095：确认后把「上游标识 + 远端进度来源」记录到项目级
Agent 指引，没有该文件时落到 `README.md` 的明确小节；区块外内容一个字节都不能动；
已有不同绑定不得静默覆盖。

断言只看进程边界（退出码、`--json` 信封）与磁盘内容，不导入包内模块。
"""

from __future__ import annotations

from pathlib import Path

from conftest import read, run_cli, write

BEGIN = "<!-- neo-skill:upstream:begin -->"
END = "<!-- neo-skill:upstream:end -->"
HEADING = "## 上游进度来源"

ID_A = "https://github.com/example/upstream"
SOURCES_A = [
    "https://github.com/example/upstream/releases",
    "https://github.com/example/upstream/tree/main/CHANGELOG.md",
]

ID_B = "https://gitlab.com/other/upstream"
SOURCES_B = ["file:///tmp/other-roadmap.md"]


def bind(root: Path, *extra: str, identifier: str = ID_A, sources: list[str] | None = None,
         actor: str | None = "alice", confirm: bool = True):
    """统一构造 `upstream bind` 调用，便于各用例只改关心的参数。"""
    argv = ["upstream", "bind", "--id", identifier]
    for source in SOURCES_A if sources is None else sources:
        argv += ["--source", source]
    if actor:
        argv += ["--actor", actor]
    if confirm:
        argv.append("--confirm")
    argv += list(extra)
    return run_cli(root, *argv)


def snapshot(root: Path) -> dict[str, str]:
    """项目里所有文件的内容快照：用来证明失败分支「磁盘无任何写入」。"""
    return {
        path.relative_to(root).as_posix(): read(path)
        for path in sorted(root.rglob("*"))
        if path.is_file()
    }


TEAM_ROSTER = """---
confirmed: true
confirmed_by: carol
confirmed_at: 2026-01-01T00:00:00+08:00
members:
  - {id: alice, name: 张三, roles: [project_owner]}
---

# 团队与职责
"""


# --------------------------------------------------------------------------
# 写入与解析
# --------------------------------------------------------------------------
def test_bind_写入_agents_区块并能被_show_解析回同样的标识与来源(project: Path) -> None:
    original = "# 项目 Agent 指引\n\n- 区块外这一行必须原样保留。\n"
    write(project / "AGENTS.md", original)

    result = bind(project)

    assert result.code == 0, result
    assert result.json["success"] is True
    assert result.actions()["AGENTS.md"] == "updated"
    text = read(project / "AGENTS.md")
    assert BEGIN in text and END in text
    assert ID_A in text
    for source in SOURCES_A:
        assert source in text
    assert "alice" in text
    # 区块外内容逐字保留（原有内容仍是新文件的前缀）
    assert text.startswith(original)

    shown = run_cli(project, "upstream", "show")
    assert shown.code == 0, shown
    data = shown.json["data"]
    assert data["configured"] is True
    assert data["document"] == "AGENTS.md"
    assert data["identifier"] == ID_A
    assert data["sources"] == SOURCES_A
    assert data["recorded_by"] == "alice"
    assert data["recorded_at"]


def test_bind_首次写入_agents_时新建文件且区块完整(project: Path) -> None:
    result = bind(project, "--doc", "agents")

    assert result.code == 0, result
    assert result.actions()["AGENTS.md"] == "created"
    text = read(project / "AGENTS.md")
    assert text.count(BEGIN) == 1 and text.count(END) == 1
    # 区块在标记之间必须能被 YAML 解析（机器可解析，供后续进度查询复用）
    body = text.split(BEGIN, 1)[1].split(END, 1)[0]
    import yaml

    payload = yaml.safe_load(body)
    assert payload["upstream"]["id"] == ID_A
    assert payload["upstream"]["sources"] == SOURCES_A
    assert payload["upstream"]["recorded_by"] == "alice"
    assert payload["upstream"]["recorded_at"]


def test_无_agents_时落到_readme_并带标题且既有内容保留(project: Path) -> None:
    original = "# 我的项目\n\n这是已有的说明段落。\n"
    write(project / "README.md", original)

    result = bind(project)

    assert result.code == 0, result
    assert result.actions()["README.md"] == "updated"
    text = read(project / "README.md")
    assert text.count(HEADING) == 1
    assert BEGIN in text
    # 既有内容逐行保留，且标题在区块之前
    assert "# 我的项目" in text
    assert "这是已有的说明段落。" in text
    assert text.index(HEADING) < text.index(BEGIN)
    # 不回退创建 AGENTS.md
    assert not (project / "AGENTS.md").exists()

    shown = run_cli(project, "upstream", "show")
    assert shown.code == 0, shown
    assert shown.json["data"]["document"] == "README.md"


def test_无_agents_也无_readme_时创建_readme_并写入区块(project: Path) -> None:
    result = bind(project)

    assert result.code == 0, result
    assert result.actions()["README.md"] == "created"
    assert not (project / "AGENTS.md").exists()
    text = read(project / "README.md")
    assert text.count(HEADING) == 1
    assert BEGIN in text and END in text


def test_readme_已有同名标题时复用而不重复插入(project: Path) -> None:
    original = (
        "# 我的项目\n\n"
        f"{HEADING}\n\n"
        "这里已经有人写了说明。\n\n"
        "## 其他小节\n\n收尾。\n"
    )
    write(project / "README.md", original)

    result = bind(project)

    assert result.code == 0, result
    text = read(project / "README.md")
    assert text.count(HEADING) == 1, text
    assert text.count(BEGIN) == 1
    # 既有内容与顺序不被重排/删除
    for fragment in ("# 我的项目", "这里已经有人写了说明。", "## 其他小节", "收尾。"):
        assert fragment in text
    assert text.index("# 我的项目") < text.index(HEADING)
    assert text.index(HEADING) < text.index(BEGIN)


def test_doc_参数可强制指定_readme_且不动既有_agents(project: Path) -> None:
    agents = "# Agent 指引\n\n保持不动。\n"
    write(project / "AGENTS.md", agents)

    result = bind(project, "--doc", "readme")

    assert result.code == 0, result
    assert result.actions()["README.md"] == "created"
    assert "README.md" in result.actions()
    assert "AGENTS.md" not in result.actions()
    assert read(project / "AGENTS.md") == agents
    assert run_cli(project, "upstream", "show").json["data"]["document"] == "README.md"


# --------------------------------------------------------------------------
# 确认与团队
# --------------------------------------------------------------------------
def test_缺_actor_或_confirm_时不写入(project: Path) -> None:
    write(project / "AGENTS.md", "# 指引\n")
    before = snapshot(project)

    no_confirm = bind(project, confirm=False)
    no_actor = bind(project, actor=None)
    neither = bind(project, actor=None, confirm=False)

    for result in (no_confirm, no_actor, neither):
        assert result.code == 1, result
        assert result.error_codes() == ["CONFIRMATION_REQUIRED"]
        # 失败要给出具体的重试方向（suggestion / next_actions 至少一处点名缺失参数）
        hint = " ".join(
            [str(result.json["errors"][0].get("suggestion") or "")]
            + [str(item) for item in result.json["next_actions"]]
        )
        assert "--actor" in hint or "--confirm" in hint, hint
    assert snapshot(project) == before


def test_团队已配置_actor_不在名单时_actor_unknown_且不写入(project: Path) -> None:
    write(project / "00-management/team.md", TEAM_ROSTER)
    write(project / "AGENTS.md", "# 指引\n")
    before = snapshot(project)

    result = bind(project, actor="bob")

    assert result.code == 1, result
    assert result.error_codes() == ["ACTOR_UNKNOWN"]
    assert snapshot(project) == before

    # 名单内的成员可以绑定（绑定不是产品决策，不要求特定职责）
    ok = bind(project, actor="alice")
    assert ok.code == 0, ok


def test_团队未配置时不阻断绑定(project: Path) -> None:
    # 没有 00-management/team.md：绑定照常写入，不报 TEAM_NOT_CONFIGURED
    result = bind(project, actor="nobody")

    assert result.code == 0, result
    assert read(project / "README.md").count(BEGIN) == 1


# --------------------------------------------------------------------------
# 信息不完整
# --------------------------------------------------------------------------
def test_空标识属于信息不完整且不写入(project: Path) -> None:
    write(project / "AGENTS.md", "# 指引\n")
    before = snapshot(project)

    result = bind(project, identifier="  ")

    assert result.code == 1, result
    assert result.error_codes() == ["UPSTREAM_BINDING_INCOMPLETE"]
    assert result.json["next_actions"]
    assert snapshot(project) == before
    assert run_cli(project, "upstream", "show").error_codes() == ["UPSTREAM_NOT_CONFIGURED"]


def test_缺_source_是参数用法错误且不写入(project: Path) -> None:
    before = snapshot(project)

    result = bind(project, sources=[])

    assert result.code == 2, result
    assert snapshot(project) == before
    assert not (project / "AGENTS.md").exists()
    assert not (project / "README.md").exists()

    show = run_cli(project, "upstream", "show")
    assert show.code == 1
    assert show.error_codes() == ["UPSTREAM_NOT_CONFIGURED"]
    assert show.json["next_actions"]


def test_未配置时_show_失败而_status_只读报告未配置(project: Path) -> None:
    show = run_cli(project, "upstream", "show")
    assert show.code == 1
    assert show.error_codes() == ["UPSTREAM_NOT_CONFIGURED"]
    assert any("upstream bind" in item for item in show.json["next_actions"])

    status = run_cli(project, "upstream", "status")
    assert status.code == 0, status
    assert status.json["data"]["configured"] is False
    assert status.json["data"]["identifier"] is None
    assert status.json["data"]["sources"] == []


# --------------------------------------------------------------------------
# 冲突与幂等
# --------------------------------------------------------------------------
def test_已有不同绑定不静默覆盖_force_才覆盖且仍只动区块(project: Path) -> None:
    head = "# 指引\n\n- 区块外内容。\n"
    write(project / "AGENTS.md", head)
    assert bind(project).code == 0
    after_first = read(project / "AGENTS.md")

    conflict = bind(project, identifier=ID_B, sources=SOURCES_B, actor="alice")

    assert conflict.code == 1, conflict
    assert conflict.error_codes() == ["UPSTREAM_BINDING_CONFLICT"]
    assert any("--force" in item for item in conflict.json["next_actions"])
    assert read(project / "AGENTS.md") == after_first

    forced = bind(project, "--force", identifier=ID_B, sources=SOURCES_B, actor="alice")

    assert forced.code == 0, forced
    assert forced.actions()["AGENTS.md"] == "updated"
    text = read(project / "AGENTS.md")
    assert text.startswith(head)
    assert text.count(BEGIN) == 1
    assert ID_B in text and ID_A not in text
    shown = run_cli(project, "upstream", "show").json["data"]
    assert shown["identifier"] == ID_B
    assert shown["sources"] == SOURCES_B


def test_重复写入相同绑定是幂等_unchanged_且文件字节不变(project: Path) -> None:
    write(project / "AGENTS.md", "# 指引\n")
    assert bind(project).code == 0
    after_first = read(project / "AGENTS.md")

    again = bind(project)

    assert again.code == 0, again
    assert again.actions()["AGENTS.md"] == "unchanged"
    assert read(project / "AGENTS.md") == after_first


def test_已存在但无法解析的区块也需_force_才替换(project: Path) -> None:
    write(project / "AGENTS.md", f"# 指引\n\n{BEGIN}\n这是人手写的、不是 YAML\n{END}\n")
    before = read(project / "AGENTS.md")

    conflict = bind(project)

    assert conflict.code == 1, conflict
    assert conflict.error_codes() == ["UPSTREAM_BINDING_CONFLICT"]
    assert read(project / "AGENTS.md") == before

    forced = bind(project, "--force")

    assert forced.code == 0, forced
    text = read(project / "AGENTS.md")
    assert text.startswith("# 指引\n")
    assert text.count(BEGIN) == 1
    assert run_cli(project, "upstream", "show").json["data"]["identifier"] == ID_A


def test_同时_与_init_的受管理区块共存(project: Path) -> None:
    """`init` 维护 `neo-skill:begin` 区块，`upstream` 维护自己的区块，互不得删另一个。"""
    assert bind(project, "--doc", "agents").code == 0

    inited = run_cli(project, "init")

    assert inited.code == 0, inited
    text = read(project / "AGENTS.md")
    assert "<!-- neo-skill:begin -->" in text
    assert BEGIN in text and END in text
    assert ID_A in text
    shown = run_cli(project, "upstream", "show")
    assert shown.code == 0, shown
    assert shown.json["data"]["identifier"] == ID_A

    # 反过来：init 先建立 AGENTS.md，再 bind 也不丢 init 的区块
    other = project / "second"
    other.mkdir()
    assert run_cli(other, "init").code == 0
    assert bind(other).code == 0
    again = read(other / "AGENTS.md")
    assert "<!-- neo-skill:begin -->" in again
    assert BEGIN in again


def test_来源去重且顺序保留(project: Path) -> None:
    result = bind(project, sources=[SOURCES_A[0], SOURCES_A[0], SOURCES_A[1]])

    assert result.code == 0, result
    assert run_cli(project, "upstream", "show").json["data"]["sources"] == SOURCES_A