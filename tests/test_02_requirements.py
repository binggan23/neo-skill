"""ticket 02：需求草稿、grilling 记录、分类/优先级与重复关联提示（`req` 命令组）。

全部走 `run_cli` 黑盒边界：只断言退出码、`--json` 信封字段、错误码与文件内容片段。
需要可预测时间的地方用 `NEO_SKILL_NOW`。
"""

from __future__ import annotations

from pathlib import Path

import pytest
import yaml

from conftest import read, run_cli, write

NOW = "2026-03-01T10:00:00+08:00"
ENV = {"NEO_SKILL_NOW": NOW}

TEAM_CONFIRMED = """---
confirmed: true
confirmed_by: alice
confirmed_at: 2026-03-01T09:00:00+08:00
members:
  - {id: alice, name: 爱丽丝, roles: [requirement_owner, project_owner]}
  - {id: bob, name: 鲍勃, roles: [stakeholder]}
  - {id: carol, name: 卡罗尔, roles: [developer]}
---

# 团队与职责

成员与职责映射见 front matter。
"""

TEAM_UNCONFIRMED = TEAM_CONFIRMED.replace("confirmed: true", "confirmed: false")

ITEM_1 = "03-requirements/items/REQ-001-batch-parse-resume.md"


def front_matter(path: Path) -> dict:
    text = read(path)
    assert text.startswith("---\n"), text[:40]
    head = text.split("---\n", 2)[1]
    data = yaml.safe_load(head)
    assert isinstance(data, dict)
    return data


@pytest.fixture()
def ready(inited_project: Path) -> Path:
    """已初始化、团队配置已确认的项目。"""
    write(inited_project / "00-management/team.md", TEAM_CONFIRMED)
    return inited_project


def create(project: Path, *extra: str, title: str = "batch parse resume",
           module: str = "resume"):
    return run_cli(project, "req", "create", "--title", title, "--module", module, *extra)


def item_path(project: Path, name: str = ITEM_1) -> Path:
    return project / name


# ------------------------------------------------------------------ 草稿创建
def test_create_allocates_id_and_writes_new_draft(ready: Path) -> None:
    result = create(ready, "--source", "客户访谈")

    assert result.code == 0, result
    assert result.json["success"] is True
    assert result.json["command"] == "req.create"
    assert result.artifact(ITEM_1)["action"] == "created"

    meta = front_matter(item_path(ready))
    assert meta["id"] == "REQ-001"
    assert meta["slug"] == "batch-parse-resume"
    assert meta["title"] == "batch parse resume"
    assert meta["module"] == "resume"
    assert meta["category"] == "new-feature"
    assert meta["source"] == "客户访谈"
    assert meta["status"] == "NEW"
    assert meta["release"] is None
    assert meta["prd"] is None
    assert meta["related"] == []
    assert meta["history"] == []
    assert meta["confirmations"] == []
    assert meta["grilling"]["frontier"] == []
    assert meta["grilling"]["facts"] == []
    assert meta["grilling"]["assumptions"] == []
    assert meta["grilling"]["open_questions"] == []
    assert meta["grilling"]["consensus_confirmed"] is False
    assert result.json["data"]["req"]["id"] == "REQ-001"


def test_created_draft_has_required_body_sections(ready: Path) -> None:
    assert create(ready).code == 0

    text = read(item_path(ready))

    assert "# REQ-001 batch parse resume" in text
    for section in ("## 背景", "## 用户", "## 问题", "## 场景", "## 期望结果", "## 来源"):
        assert section in text


def test_create_accepts_category_and_slug_override(ready: Path) -> None:
    result = create(ready, "--category", "defect", "--slug", "parse-failure")

    assert result.code == 0, result
    path = ready / "03-requirements/items/REQ-001-parse-failure.md"
    meta = front_matter(path)
    assert meta["category"] == "defect"
    assert meta["slug"] == "parse-failure"


def test_failed_create_does_not_consume_id(ready: Path) -> None:
    assert create(ready, "--category", "wish").code == 1

    counters = yaml.safe_load(read(ready / ".neo-skill/counters.yaml"))
    assert counters["REQ"] == 0
    assert create(ready).json["data"]["req"]["id"] == "REQ-001"


def test_create_rejects_unknown_category_without_writing(ready: Path) -> None:
    result = create(ready, "--category", "wish")

    assert result.code == 1
    assert result.error_codes() == ["INVALID_CATEGORY"]
    assert list((ready / "03-requirements/items").glob("*.md")) == []


def test_create_rejects_bad_module_slug(ready: Path) -> None:
    result = create(ready, module="Resume Parse")

    assert result.code == 1
    assert result.error_codes() == ["INVALID_MODULE"]
    assert list((ready / "03-requirements/items").glob("*.md")) == []


def test_ids_increase_and_are_never_reused(ready: Path) -> None:
    assert create(ready, title="first", module="a").code == 0
    assert create(ready, title="second", module="a").code == 0
    assert create(ready, title="third", module="a").code == 0
    # 模拟废弃：文件被移走也不回收编号
    (ready / "03-requirements/items/REQ-002-second.md").unlink()

    result = create(ready, title="fourth", module="a")

    assert result.code == 0, result
    assert result.json["data"]["req"]["id"] == "REQ-004"
    counters = yaml.safe_load(read(ready / ".neo-skill/counters.yaml"))
    assert counters["REQ"] == 4


def test_slug_stays_stable_after_title_change_attempt(ready: Path) -> None:
    assert create(ready).code == 0

    result = run_cli(ready, "req", "update", "--id", "REQ-001", "--set", "title=other name")

    assert result.code == 1
    assert result.error_codes() == ["IMMUTABLE_FIELD"]
    assert item_path(ready).is_file()
    assert front_matter(item_path(ready))["slug"] == "batch-parse-resume"


def test_slug_falls_back_to_module_for_chinese_title(ready: Path) -> None:
    assert create(ready, title="简历批量解析").code == 0
    assert create(ready, title="简历批量解析").code == 0

    paths = sorted(path.name for path in (ready / "03-requirements/items").glob("*.md"))

    assert paths == ["REQ-001-resume.md", "REQ-002-resume-02.md"]


def test_create_returns_candidates_for_possible_duplicates(ready: Path) -> None:
    assert create(ready, title="batch parse resume", module="resume").code == 0

    result = create(ready, title="batch parse resume bulk", module="resume")

    assert result.code == 0, result
    found = result.json["data"]["candidates"]
    assert [item["id"] for item in found] == ["REQ-001"]


# ------------------------------------------------------------------ 草稿恢复
def test_draft_can_be_resumed_and_updated(ready: Path) -> None:
    assert create(ready).code == 0

    shown = run_cli(ready, "req", "show", "--id", "REQ-001")

    assert shown.code == 0, shown
    assert shown.json["data"]["req"]["status"] == "NEW"
    assert "## 背景" in shown.json["data"]["req"]["body"]

    updated = run_cli(
        ready, "req", "update", "--id", "REQ-001",
        "--set", "category=optimization",
        "--set", "source=数据看板",
        "--set", "module=resume-parser",
        env=ENV,
    )

    assert updated.code == 0, updated
    assert updated.artifact(ITEM_1)["action"] == "updated"
    meta = front_matter(item_path(ready))
    assert meta["category"] == "optimization"
    assert meta["source"] == "数据看板"
    assert meta["module"] == "resume-parser"
    assert meta["status"] == "NEW"
    assert meta["updated_at"] == NOW


def test_body_file_restores_draft_body(ready: Path) -> None:
    assert create(ready).code == 0
    draft = ready / "draft.md"
    write(draft, "# REQ-001 batch parse resume\n\n## 背景\n\n客户每月人工解析 2000 份简历。\n")

    result = run_cli(ready, "req", "update", "--id", "REQ-001", "--body-file", str(draft))

    assert result.code == 0, result
    text = read(item_path(ready))
    assert "客户每月人工解析 2000 份简历。" in text
    assert "<!-- TODO: 为什么现在要处理这个问题" not in text
    assert front_matter(item_path(ready))["status"] == "NEW"


def test_missing_body_file_leaves_draft_untouched(ready: Path) -> None:
    assert create(ready).code == 0
    before = read(item_path(ready))

    result = run_cli(ready, "req", "update", "--id", "REQ-001", "--body-file",
                     str(ready / "nope.md"))

    assert result.code == 1
    assert result.error_codes() == ["BODY_FILE_NOT_FOUND"]
    assert read(item_path(ready)) == before


def test_update_rejects_unknown_field(ready: Path) -> None:
    assert create(ready).code == 0

    result = run_cli(ready, "req", "update", "--id", "REQ-001", "--set", "status=TRIAGED")

    assert result.code == 1
    assert result.error_codes() == ["IMMUTABLE_FIELD"]
    assert front_matter(item_path(ready))["status"] == "NEW"


def test_update_requires_some_change(ready: Path) -> None:
    assert create(ready).code == 0

    result = run_cli(ready, "req", "update", "--id", "REQ-001")

    assert result.code == 1
    assert result.error_codes() == ["NOTHING_TO_UPDATE"]


def test_failure_envelope_uses_dotted_command_name(ready: Path) -> None:
    result = run_cli(ready, "req", "show", "--id", "REQ-404")

    assert result.code == 1
    assert result.json["command"] == "req.show"


def test_show_unknown_requirement_reports_not_found(ready: Path) -> None:
    result = run_cli(ready, "req", "show", "--id", "REQ-404")

    assert result.code == 1
    assert result.error_codes() == ["REQ_NOT_FOUND"]
    assert result.json["errors"][0]["object"] == "REQ-404"


def test_show_reports_prd_readiness(ready: Path) -> None:
    assert create(ready).code == 0

    draft = run_cli(ready, "req", "show", "--id", "REQ-001")

    assert draft.code == 0, draft
    readiness = draft.json["data"]["prd_readiness"]
    assert readiness["ready"] is False
    assert any("PLANNED" in reason for reason in readiness["reasons"])
    assert any("目标版本" in reason for reason in readiness["reasons"])


# ------------------------------------------------------------------ 清单
def test_list_filters_by_status_and_module(ready: Path) -> None:
    assert create(ready, title="first one", module="resume").code == 0
    assert create(ready, title="second one", module="billing").code == 0

    everything = run_cli(ready, "req", "list")
    by_module = run_cli(ready, "req", "list", "--module", "billing")
    by_status = run_cli(ready, "req", "list", "--status", "NEW")
    nothing = run_cli(ready, "req", "list", "--status", "PLANNED")

    assert everything.code == 0, everything
    assert [item["id"] for item in everything.json["data"]["requirements"]] == ["REQ-001", "REQ-002"]
    assert everything.json["data"]["count"] == 2
    assert [item["id"] for item in by_module.json["data"]["requirements"]] == ["REQ-002"]
    assert by_status.json["data"]["count"] == 2
    assert nothing.json["data"]["requirements"] == []


def test_list_on_empty_project_is_empty(project: Path) -> None:
    result = run_cli(project, "req", "list")

    assert result.code == 0, result
    assert result.json["data"]["requirements"] == []


# ------------------------------------------------------------------ 重复/关联提示
def test_candidates_report_existing_requirement_and_feature(ready: Path) -> None:
    assert create(ready, title="batch parse resume", module="resume").code == 0
    write(
        ready / "02-definition/modules/resume/features/parse.md",
        "# 简历批量解析\n\n现状：仅支持单份上传。\n",
    )

    result = run_cli(ready, "req", "candidates", "--title", "batch parse resume",
                     "--module", "resume")

    assert result.code == 0, result
    candidates = result.json["data"]["candidates"]
    kinds = {(item["kind"], item["id"]) for item in candidates}
    assert ("req", "REQ-001") in kinds
    assert ("feature", "parse") in kinds
    assert result.json["data"]["count"] == len(candidates)
    for item in candidates:
        assert item["score"] > 0
        assert item["reasons"]
        assert item["path"]


def test_candidates_never_modify_or_merge_requirements(ready: Path) -> None:
    assert create(ready, title="batch parse resume", module="resume").code == 0
    assert create(ready, title="batch parse resume v2", module="resume").code == 0
    before = {path.name: read(path) for path in (ready / "03-requirements/items").glob("*.md")}

    result = run_cli(ready, "req", "candidates", "--title", "batch parse resume")

    assert result.code == 0, result
    after = {path.name: read(path) for path in (ready / "03-requirements/items").glob("*.md")}
    assert after == before
    assert result.json["artifacts"] == []


def test_candidates_without_matches_returns_empty_list(ready: Path) -> None:
    result = run_cli(ready, "req", "candidates", "--title", "unrelated thing")

    assert result.code == 0, result
    assert result.json["data"]["candidates"] == []


# ------------------------------------------------------------------ grilling
def test_align_records_grilling_tree_and_keeps_new(ready: Path) -> None:
    assert create(ready).code == 0

    result = run_cli(
        ready, "req", "align", "--id", "REQ-001",
        "--frontier", "谁在什么场景下解析简历",
        "--frontier", "批量上限是多少",
        "--fact", "现有上传接口只接受单文件（src/upload.py）",
        "--assumption", "单批不超过 500 份",
        "--open-question", "是否需要保留解析历史",
        "--consensus", "支持一次上传多份并异步解析",
        env=ENV,
    )

    assert result.code == 0, result
    meta = front_matter(item_path(ready))
    assert meta["status"] == "NEW"
    assert meta["grilling"]["frontier"] == ["谁在什么场景下解析简历", "批量上限是多少"]
    assert meta["grilling"]["facts"] == ["现有上传接口只接受单文件（src/upload.py）"]
    assert meta["grilling"]["assumptions"] == ["单批不超过 500 份"]
    assert meta["grilling"]["open_questions"] == ["是否需要保留解析历史"]
    assert meta["grilling"]["consensus"] == "支持一次上传多份并异步解析"
    assert meta["grilling"]["consensus_confirmed"] is False
    assert meta["history"] == []


def test_align_accumulates_rounds_and_reads_consensus_file(ready: Path) -> None:
    assert create(ready).code == 0
    assert run_cli(ready, "req", "align", "--id", "REQ-001",
                   "--frontier", "第一轮问题").code == 0
    consensus = ready / "consensus.md"
    write(consensus, "共识：先做简历解析，不做投递。\n")

    result = run_cli(ready, "req", "align", "--id", "REQ-001",
                     "--frontier", "第二轮问题", "--consensus-file", str(consensus))

    assert result.code == 0, result
    meta = front_matter(item_path(ready))
    assert meta["grilling"]["frontier"] == ["第一轮问题", "第二轮问题"]
    assert meta["grilling"]["consensus"] == "共识：先做简历解析，不做投递。"


def test_align_requires_something_to_record(ready: Path) -> None:
    assert create(ready).code == 0

    result = run_cli(ready, "req", "align", "--id", "REQ-001")

    assert result.code == 1
    assert result.error_codes() == ["NOTHING_TO_UPDATE"]


def test_align_records_design_tree_and_decisions(ready: Path) -> None:
    assert create(ready).code == 0
    tree = ready / "tree.md"
    write(tree, "- 目标：批量解析\n  - 场景：一次 100 份\n  - 范围：不含投递\n")

    result = run_cli(ready, "req", "align", "--id", "REQ-001",
                     "--design-tree", str(tree),
                     "--decision", "先做解析，不做投递",
                     "--fact", "现有接口只支持单文件")

    assert result.code == 0, result
    grilling = front_matter(item_path(ready))["grilling"]
    assert grilling["design_tree"].startswith("- 目标：批量解析")
    assert grilling["decisions"] == ["先做解析，不做投递"]
    assert grilling["facts"] == ["现有接口只支持单文件"]


def test_align_rejects_missing_design_tree_file(ready: Path) -> None:
    assert create(ready).code == 0
    before = read(item_path(ready))

    result = run_cli(ready, "req", "align", "--id", "REQ-001",
                     "--design-tree", str(ready / "nope.md"))

    assert result.code == 1
    assert result.error_codes() == ["BODY_FILE_NOT_FOUND"]
    assert read(item_path(ready)) == before


# ------------------------------------------------------------------ 分类与优先级
def test_classify_records_category(ready: Path) -> None:
    assert create(ready).code == 0

    result = run_cli(ready, "req", "classify", "--id", "REQ-001", "--category", "tech-debt")

    assert result.code == 0, result
    assert front_matter(item_path(ready))["category"] == "tech-debt"


def test_classify_rejects_unknown_category(ready: Path) -> None:
    assert create(ready).code == 0

    result = run_cli(ready, "req", "classify", "--id", "REQ-001", "--category", "spike")

    assert result.code == 1
    assert result.error_codes() == ["INVALID_CATEGORY"]
    assert front_matter(item_path(ready))["category"] == "new-feature"


def test_set_priority_records_scheme_score_and_reason(ready: Path) -> None:
    assert create(ready).code == 0

    rice = run_cli(ready, "req", "set-priority", "--id", "REQ-001",
                   "--scheme", "RICE", "--score", "42", "--reason", "影响 3 个客户")
    moscow = run_cli(ready, "req", "set-priority", "--id", "REQ-001",
                     "--scheme", "MoSCoW", "--score", "must", "--reason", "上线阻塞")

    assert rice.code == 0, rice
    assert moscow.code == 0, moscow
    priority = front_matter(item_path(ready))["priority"]
    assert priority == {"scheme": "MoSCoW", "score": "must", "reason": "上线阻塞"}


def test_set_priority_rejects_bad_scheme_and_score(ready: Path) -> None:
    assert create(ready).code == 0

    bad_scheme = run_cli(ready, "req", "set-priority", "--id", "REQ-001",
                         "--scheme", "WSJF", "--score", "3", "--reason", "试试")
    bad_score = run_cli(ready, "req", "set-priority", "--id", "REQ-001",
                        "--scheme", "RICE", "--score", "很高", "--reason", "试试")

    assert bad_scheme.code == 1
    assert bad_scheme.error_codes() == ["INVALID_PRIORITY_SCHEME"]
    assert bad_score.code == 1
    assert bad_score.error_codes() == ["INVALID_PRIORITY_SCORE"]
    assert front_matter(item_path(ready))["priority"] is None
