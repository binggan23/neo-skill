"""ticket 01：`init` 的项目骨架、裁剪、幂等与冲突计划（黑盒 CLI）。"""

from __future__ import annotations

from pathlib import Path

import pytest

from conftest import read, run_cli, write

# 共享设计 §3 的 P0 骨架（关键路径抽样，目录以存在性断言）
REQUIRED_DIRS = [
    "00-management/decisions",
    "00-management/meetings",
    "00-management/reports",
    "01-discovery/market",
    "01-discovery/competitors",
    "01-discovery/users/personas",
    "01-discovery/users/interviews",
    "01-discovery/users/surveys",
    "01-discovery/experiments",
    "02-definition/modules",
    "03-requirements/items",
    "03-requirements/feedback",
    "04-prds",
    "05-design/assets",
    "06-specs/api",
    "06-specs/adr",
    "07-acceptance",
    "08-releases",
    "09-growth/analysis",
    "09-growth/experiments",
    "09-growth/retros",
    "src",
    "tests",
    "archive",
    ".specify/rules",
    ".specify/templates",
    ".specify/scripts",
    ".neo-skill/locks",
    ".neo-skill/transactions",
]

REQUIRED_FILES = [
    "README.md",
    "AGENTS.md",
    ".specify/constitution.md",
    ".specify/rules/flow.yaml",
    ".specify/scripts/README.md",
    "00-management/charter.md",
    "00-management/roadmap.md",
    "00-management/team.md",
    "00-management/risks.md",
    "01-discovery/opportunity.md",
    "01-discovery/hypotheses.md",
    "01-discovery/insights.md",
    "02-definition/vision.md",
    "02-definition/business-model.md",
    "02-definition/metrics.md",
    "02-definition/mvp-scope.md",
    "02-definition/glossary.md",
    "02-definition/feature-map.md",
    "03-requirements/backlog.md",
    "04-prds/index.md",
    "05-design/design-system.md",
    "06-specs/architecture.md",
    "06-specs/data-model.md",
    "08-releases/CHANGELOG.md",
    "09-growth/dashboards.md",
    ".neo-skill/counters.yaml",
    ".neo-skill/init-state.yaml",
]

EXPECTED_TEMPLATES = [
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
]


def test_init_creates_full_p0_skeleton(project: Path) -> None:
    result = run_cli(project, "init")

    assert result.code == 0
    assert result.json is not None
    assert result.json["success"] is True
    assert result.json["command"] == "init"
    assert result.json["errors"] == []

    missing_dirs = [d for d in REQUIRED_DIRS if not (project / d).is_dir()]
    missing_files = [f for f in REQUIRED_FILES if not (project / f).is_file()]
    assert missing_dirs == []
    assert missing_files == []


def test_init_copies_rules_baseline_and_templates(project: Path) -> None:
    assert run_cli(project, "init").code == 0

    project_rules = read(project / ".specify/rules/flow.yaml")
    packaged_rules = read(
        Path(__file__).resolve().parents[1]
        / "skills/neo-skill/runtime/rules/flow.yaml"
    )
    assert project_rules == packaged_rules
    assert "version: 1" in project_rules
    assert "prd:" in project_rules

    for name in EXPECTED_TEMPLATES:
        assert (project / ".specify/templates" / name).is_file(), name


def test_init_does_not_fabricate_business_content(project: Path) -> None:
    assert run_cli(project, "init").code == 0

    for rel in ("00-management/charter.md", "02-definition/vision.md", "README.md"):
        text = read(project / rel)
        assert "TODO" in text or "待填写" in text, rel
        # 不得伪造具体业务事实：不出现编造的人名/指标/结论标记
        assert "示例公司" not in text
        assert "100 万" not in text


def test_init_records_state_and_counters(project: Path) -> None:
    assert run_cli(project, "init").code == 0

    state = read(project / ".neo-skill/init-state.yaml")
    assert "pruned: []" in state
    assert "files:" in state
    assert "sha256:" in state

    counters = read(project / ".neo-skill/counters.yaml")
    for key in ("REQ", "PRD", "CR", "SC", "ADR", "DEC"):
        assert f"{key}:" in counters
    assert "REQ: 0" in counters


def test_init_is_idempotent(project: Path) -> None:
    assert run_cli(project, "init").code == 0
    readme_before = read(project / "README.md")
    state_before = read(project / ".neo-skill/init-state.yaml")

    second = run_cli(project, "init")

    assert second.code == 0
    assert second.json["success"] is True
    assert second.json["errors"] == []
    assert "created" not in second.actions().values()
    assert read(project / "README.md") == readme_before
    assert read(project / ".neo-skill/init-state.yaml") == state_before


def test_init_does_not_overwrite_existing_readme_and_plans_conflict(project: Path) -> None:
    original = "# 我的项目\n\n团队自己的说明。\n"
    write(project / "README.md", original)

    result = run_cli(project, "init")

    assert result.code == 0
    assert result.json["success"] is True
    assert read(project / "README.md") == original
    assert result.actions().get("README.md") == "planned"
    assert any("--apply-conflicts" in action for action in result.json["next_actions"])
    # 其它骨架仍然创建
    assert (project / "03-requirements/backlog.md").is_file()


def test_init_apply_conflicts_overwrites_after_plan(project: Path) -> None:
    write(project / "README.md", "# 旧说明\n")

    planned = run_cli(project, "init")
    assert planned.actions().get("README.md") == "planned"

    applied = run_cli(project, "init", "--apply-conflicts")

    assert applied.code == 0
    assert applied.actions().get("README.md") == "updated"
    text = read(project / "README.md")
    assert "旧说明" not in text
    assert "TODO" in text or "待填写" in text


def test_init_keeps_existing_agents_content_and_manages_its_block(project: Path) -> None:
    custom = "# AGENTS.md\n\n团队自己的约定：提交前跑 lint。\n"
    write(project / "AGENTS.md", custom)

    assert run_cli(project, "init").code == 0
    after_first = read(project / "AGENTS.md")
    assert "团队自己的约定：提交前跑 lint。" in after_first
    assert "<!-- neo-skill:begin -->" in after_first
    assert "<!-- neo-skill:end -->" in after_first

    assert run_cli(project, "init").code == 0
    assert read(project / "AGENTS.md") == after_first


def test_init_preserves_existing_code_and_business_docs(project: Path) -> None:
    write(project / "src/app.py", "print('hello')\n")
    write(project / "02-definition/vision.md", "# 我们已有的愿景\n")
    write(project / "03-requirements/backlog.md", "# 已有需求池\n\nREQ-001 ...\n")

    assert run_cli(project, "init").code == 0

    assert read(project / "src/app.py") == "print('hello')\n"
    assert read(project / "02-definition/vision.md") == "# 我们已有的愿景\n"
    assert read(project / "03-requirements/backlog.md") == "# 已有需求池\n\nREQ-001 ...\n"


def test_init_prunes_explicit_groups(project: Path) -> None:
    result = run_cli(project, "init", "--prune", "discovery,design,growth,meetings,reports")

    assert result.code == 0
    for rel in ("01-discovery", "05-design", "09-growth"):
        assert not (project / rel).exists(), rel
    assert not (project / "00-management/meetings").exists()
    assert not (project / "00-management/reports").exists()
    # 不可裁剪的核心内容仍然完整
    assert (project / "00-management/charter.md").is_file()
    assert (project / "02-definition/mvp-scope.md").is_file()
    assert (project / "03-requirements/items").is_dir()
    assert (project / "04-prds").is_dir()
    assert (project / "07-acceptance").is_dir()
    assert (project / ".specify/rules/flow.yaml").is_file()
    assert "discovery" in read(project / ".neo-skill/init-state.yaml")


def test_init_prune_is_sticky_across_reruns(project: Path) -> None:
    assert run_cli(project, "init", "--prune", "growth").code == 0
    assert not (project / "09-growth").exists()

    assert run_cli(project, "init").code == 0
    assert not (project / "09-growth").exists()
    assert "growth" in read(project / ".neo-skill/init-state.yaml")


@pytest.mark.parametrize("group", ["03-requirements", "04-prds", "07-acceptance", "specify", "core"])
def test_init_rejects_pruning_core(project: Path, group: str) -> None:
    result = run_cli(project, "init", "--prune", group)

    assert result.code == 1
    assert result.json["success"] is False
    assert "NOT_PRUNABLE" in result.error_codes()
    # 失败无副作用
    assert list(project.iterdir()) == []


def test_init_rejects_unknown_prune_group(project: Path) -> None:
    result = run_cli(project, "init", "--prune", "nonsense")

    assert result.code == 1
    assert "UNKNOWN_PRUNE_GROUP" in result.error_codes()
    assert list(project.iterdir()) == []


def test_init_human_output_is_chinese_and_prefixed(project: Path) -> None:
    result = run_cli(project, "init", json_output=False)

    assert result.code == 0
    assert "OK:" in result.stdout
    assert "flow-core" in result.stdout
    # 人类输出不打印 JSON 结构体
    assert '"artifacts"' not in result.stdout


def test_unknown_command_is_usage_error(project: Path) -> None:
    result = run_cli(project, "nonsense")

    assert result.code == 2


def test_init_only_replaces_managed_agents_block(project: Path) -> None:
    before = (
        "# AGENTS.md\n\n"
        "## 团队约定\n\n"
        "提交前必须跑 lint 与类型检查。\n\n"
        "<!-- neo-skill:begin -->\n"
        "旧版本受管理内容\n"
        "<!-- neo-skill:end -->\n\n"
        "## 附录\n\n"
        "这段在区块之后，必须保留。\n"
    )
    write(project / "AGENTS.md", before)

    result = run_cli(project, "init")

    assert result.code == 0
    assert result.actions().get("AGENTS.md") == "updated"
    after = read(project / "AGENTS.md")
    assert "提交前必须跑 lint 与类型检查。" in after
    assert "这段在区块之后，必须保留。" in after
    assert "旧版本受管理内容" not in after
    assert after.count("<!-- neo-skill:begin -->") == 1
    assert after.count("<!-- neo-skill:end -->") == 1


def test_init_appends_managed_block_when_agents_has_no_marker(project: Path) -> None:
    write(project / "AGENTS.md", "# 团队 AGENTS\n\n只有团队自己的内容。\n")

    assert run_cli(project, "init").code == 0

    after = read(project / "AGENTS.md")
    assert after.startswith("# 团队 AGENTS")
    assert "只有团队自己的内容。" in after
    assert "<!-- neo-skill:begin -->" in after


def test_init_creates_missing_root_directory(tmp_path: Path) -> None:
    nested = tmp_path / "a" / "b" / "proj"

    result = run_cli(nested, "init", cwd=tmp_path)

    assert result.code == 0
    assert (nested / ".neo-skill/init-state.yaml").is_file()


def test_init_conflict_plan_covers_business_doc_edit(project: Path) -> None:
    assert run_cli(project, "init").code == 0
    write(project / "02-definition/mvp-scope.md", "# MVP 范围\n\n我们自己的判断。\n")

    result = run_cli(project, "init")

    assert result.code == 0
    assert result.actions().get("02-definition/mvp-scope.md") == "planned"
    assert "我们自己的判断。" in read(project / "02-definition/mvp-scope.md")
