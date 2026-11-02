"""ticket 03：PRD 草稿、结构自审、提交评审与人工批准（`prd` 命令组）。

全部走 `run_cli` 黑盒边界；对象一律先用 CLI 造出来。
人工决策命令的顺序按共享设计 §2.3：解析对象 → 团队/身份/职责 → 状态跳转 → 锁 →
追加记录 → 原子写 → 释放锁；任何一步失败都不改 metadata、不留锁。
"""

from __future__ import annotations

from pathlib import Path

import pytest
import yaml

from conftest import read, run_cli, write
from helpers_03 import (
    ENV,
    NOW,
    REQ_BODY,
    VALID_PRD,
    create_prd,
    planned_req,
    prd_dir,
    prd_meta,
    prd_text,
    req_meta,
    setup_team,
    update_body,
)


def error_codes(result) -> set[str]:
    return {item["code"] for item in (result.json or {}).get("errors", [])}


def issues(result, severity: str | None = None) -> list[dict]:
    items = list((result.json or {}).get("data", {}).get("issues", []))
    if severity is None:
        return items
    return [item for item in items if item.get("severity") == severity]


def issue_codes(result, severity: str | None = None) -> set[str]:
    return {item["code"] for item in issues(result, severity)}


# ------------------------------------------------------------------ 创建
def test_create_requires_planned_requirement(inited_project: Path) -> None:
    setup_team(inited_project)
    created = run_cli(inited_project, "req", "create", "--title", "简历批量解析",
                      "--module", "resume", "--slug", "batch-parse")
    assert created.code == 0, created
    before = read(inited_project / "03-requirements/items/REQ-001-batch-parse.md")

    result = run_cli(inited_project, "prd", "create", "--req", "REQ-001")

    assert result.code == 1
    assert error_codes(result) == {"REQ_NOT_PLANNED"}
    assert result.json["command"] == "prd.create"
    assert not (inited_project / "04-prds/resume").exists()
    assert read(inited_project / "03-requirements/items/REQ-001-batch-parse.md") == before


def test_create_rejects_second_primary_requirement(inited_project: Path) -> None:
    planned_req(inited_project, title="简历批量解析", slug="batch-parse")
    planned_req(inited_project, title="简历批量导出", slug="batch-export")

    result = run_cli(inited_project, "prd", "create", "--req", "REQ-001", "--req", "REQ-002")

    assert result.code == 1
    assert error_codes(result) == {"MULTIPLE_PRIMARY_REQS"}
    assert not (inited_project / "04-prds/resume").exists()


def test_create_uses_comma_separated_primary_list_too(inited_project: Path) -> None:
    planned_req(inited_project, title="简历批量解析", slug="batch-parse")

    result = run_cli(inited_project, "prd", "create", "--req", "REQ-001,REQ-002")

    assert result.code == 1
    assert error_codes(result) == {"MULTIPLE_PRIMARY_REQS"}


def test_create_builds_directory_and_metadata(inited_project: Path) -> None:
    planned_req(inited_project, title="简历批量解析", slug="batch-parse")

    payload = create_prd(inited_project, scenarios="SC-RES-01,SC-RES-02",
                         extra=("--feature", "resume-parse", "--release", "v0.1.0"))

    assert payload["success"] is True
    prd = payload["data"]["prd"]
    assert prd["id"] == "PRD-001"
    assert prd["status"] == "DRAFT"
    assert prd["version"] == 1
    assert prd["content_hash"] is None
    assert prd["source_reqs"] == ["REQ-001"]
    assert prd["scenarios"] == ["SC-RES-01", "SC-RES-02"]
    assert prd["module"] == "resume"
    assert prd["feature"] == "resume-parse"
    assert prd["release"] == "v0.1.0"
    assert prd["path"] == "04-prds/resume/PRD-001-batch-parse"

    directory = prd_dir(inited_project)
    assert (directory / "metadata.yaml").is_file()
    assert (directory / "prd.md").is_file()
    assert (directory / "reviews").is_dir()
    assert (directory / "changes").is_dir()

    meta = prd_meta(inited_project)
    assert meta["id"] == "PRD-001"
    assert meta["slug"] == "batch-parse"
    assert meta["source_reqs"] == ["REQ-001"]
    assert meta["related_reqs"] == []
    assert meta["status"] == "DRAFT"
    assert meta["content_hash"] is None
    assert meta["created_by"] is not None
    assert meta["created_at"]
    assert meta["history"][0]["to"] == "DRAFT"
    assert set(meta["locked_fields"]) == {
        "id", "slug", "module", "feature", "scenarios", "source_reqs", "release", "version"
    }
    assert "PRD-001" in prd_text(inited_project)
    assert "SC-RES-01" in prd_text(inited_project)


def test_create_moves_primary_requirement_to_in_prd(inited_project: Path) -> None:
    planned_req(inited_project, title="简历批量解析", slug="batch-parse")
    req_before = req_meta(inited_project)
    confirmations_before = list(req_before["confirmations"])

    create_prd(inited_project, scenarios="SC-RES-01")

    meta = req_meta(inited_project)
    assert meta["status"] == "IN_PRD"
    assert meta["prd"] == "PRD-001"
    # 机械状态：只追加 history，不追加 confirmations
    assert meta["confirmations"] == confirmations_before
    assert meta["history"][-1]["from"] == "PLANNED"
    assert meta["history"][-1]["to"] == "IN_PRD"
    assert meta["history"][-1]["action"] == "prd.create"


def test_create_keeps_related_reqs_out_of_source_reqs(inited_project: Path) -> None:
    planned_req(inited_project, title="简历批量解析", slug="batch-parse")
    planned_req(inited_project, title="简历批量导出", slug="batch-export")

    create_prd(inited_project, req_id="REQ-001", scenarios="SC-RES-01",
               extra=("--related", "REQ-002"))

    meta = prd_meta(inited_project)
    assert meta["source_reqs"] == ["REQ-001"]
    assert meta["related_reqs"] == ["REQ-002"]


def test_create_rejects_too_many_scenarios(inited_project: Path) -> None:
    planned_req(inited_project, title="简历批量解析", slug="batch-parse")
    many = ",".join(f"SC-RES-{index:02d}" for index in range(1, 10))

    result = run_cli(inited_project, "prd", "create", "--req", "REQ-001", "--scenarios", many)

    assert result.code == 1
    assert "TOO_MANY_SCENARIOS" in error_codes(result)
    suggestion = result.json["errors"][0].get("suggestion") or ""
    assert "拆分" in suggestion
    assert not (inited_project / "04-prds/resume").exists()
    assert req_meta(inited_project)["status"] == "PLANNED"


def test_create_failure_keeps_counters_and_requirement_untouched(inited_project: Path) -> None:
    setup_team(inited_project)
    created = run_cli(inited_project, "req", "create", "--title", "简历批量解析",
                      "--module", "resume", "--slug", "batch-parse")
    assert created.code == 0, created
    write(inited_project / "draft.md", REQ_BODY)
    run_cli(inited_project, "req", "update", "--id", "REQ-001", "--body-file",
            str(inited_project / "draft.md"))
    run_cli(inited_project, "req", "confirm", "--id", "REQ-001", "--actor", "bob", "--confirm",
            env=ENV)
    counters_before = yaml.safe_load(
        read(inited_project / ".neo-skill/counters.yaml"))
    item_before = read(inited_project / "03-requirements/items/REQ-001-batch-parse.md")

    result = run_cli(inited_project, "prd", "create", "--req", "REQ-001")

    assert result.code == 1
    assert yaml.safe_load(read(inited_project / ".neo-skill/counters.yaml")) == counters_before
    assert read(inited_project / "03-requirements/items/REQ-001-batch-parse.md") == item_before
    assert not list((inited_project / ".neo-skill/locks").glob("*.lock"))


# ------------------------------------------------------------------ 结构校验
def test_validate_reports_missing_sections_without_touching_body(inited_project: Path) -> None:
    planned_req(inited_project, title="简历批量解析", slug="batch-parse")
    create_prd(inited_project, scenarios="SC-RES-01")
    body_before = prd_text(inited_project)
    meta_before = read(prd_dir(inited_project) / "metadata.yaml")

    result = run_cli(inited_project, "prd", "validate", "--id", "PRD-001")

    assert result.code == 1
    assert result.json["success"] is False
    assert "MISSING_SECTION" in issue_codes(result, "error")
    assert issues(result, "error")[0]["severity"] == "error"
    first = issues(result, "error")[0]
    assert set(first) >= {"code", "severity", "object", "message", "suggestion"}
    assert prd_text(inited_project) == body_before
    assert read(prd_dir(inited_project) / "metadata.yaml") == meta_before


def test_validate_accepts_complete_prd(inited_project: Path) -> None:
    planned_req(inited_project, title="简历批量解析", slug="batch-parse")
    create_prd(inited_project, scenarios="SC-RES-01,SC-RES-02")
    update_body(inited_project, VALID_PRD)

    result = run_cli(inited_project, "prd", "validate", "--id", "PRD-001")

    assert result.code == 0, result
    assert result.json["success"] is True
    assert issues(result, "error") == []


def test_validate_reports_scenario_without_ac(inited_project: Path) -> None:
    planned_req(inited_project, title="简历批量解析", slug="batch-parse")
    create_prd(inited_project, scenarios="SC-RES-01,SC-RES-02")
    body = VALID_PRD.replace(
        """- AC-RES-02-01
  - Given 某份简历解析失败并记录错误码
  - When 招聘专员点击重试
  - Then 该份简历重新进入解析队列且错误码被清除""",
        "暂无验收标准。",
    )
    update_body(inited_project, body)

    result = run_cli(inited_project, "prd", "validate", "--id", "PRD-001")

    assert result.code == 1
    assert "SCENARIO_WITHOUT_AC" in issue_codes(result, "error")


def test_validate_reports_too_many_scenarios_in_body(inited_project: Path) -> None:
    planned_req(inited_project, title="简历批量解析", slug="batch-parse")
    create_prd(inited_project, scenarios="SC-RES-01")
    extra = "".join(
        f"""
### SC-RES-{index:02d} 场景 {index}

描述。

**验收标准**

- AC-RES-{index:02d}-01
  - Given 前置条件
  - When 动作
  - Then 结果
"""
        for index in range(3, 11)
    )
    # 追加的场景必须落在「## 场景」小节内，否则会被算作其他小节的子标题
    update_body(inited_project, VALID_PRD.replace("## 非功能需求", extra + "\n## 非功能需求"))

    result = run_cli(inited_project, "prd", "validate", "--id", "PRD-001")

    assert result.code == 1
    assert "TOO_MANY_SCENARIOS" in issue_codes(result, "error")


def test_validate_new_feature_without_current_state_is_not_blocked(
    inited_project: Path,
) -> None:
    planned_req(inited_project, title="简历批量解析", slug="batch-parse")
    create_prd(inited_project, scenarios="SC-RES-01,SC-RES-02")
    update_body(inited_project, VALID_PRD)

    result = run_cli(inited_project, "prd", "validate", "--id", "PRD-001")

    assert "MISSING_CURRENT_STATE" not in issue_codes(result)


def test_validate_flags_missing_current_state_declaration(inited_project: Path) -> None:
    planned_req(inited_project, title="简历批量解析", slug="batch-parse")
    create_prd(inited_project, scenarios="SC-RES-01,SC-RES-02")
    body = VALID_PRD.replace(
        """## 现状与变化

新功能，无现状。

""",
        "",
    )
    update_body(inited_project, body)

    result = run_cli(inited_project, "prd", "validate", "--id", "PRD-001")

    assert result.code == 1
    assert "MISSING_CURRENT_STATE" in issue_codes(result, "error")


def test_validate_warns_about_ambiguous_words_without_failing(inited_project: Path) -> None:
    planned_req(inited_project, title="简历批量解析", slug="batch-parse")
    create_prd(inited_project, scenarios="SC-RES-01,SC-RES-02")
    body = VALID_PRD.replace(
        "目标：单批 100 份简历在 5 分钟内完成解析。",
        "目标：让解析尽量快速、体验友好。",
    )
    update_body(inited_project, body)

    result = run_cli(inited_project, "prd", "validate", "--id", "PRD-001")

    assert result.code == 0, result
    assert result.json["success"] is True
    assert "AMBIGUOUS_WORD" in issue_codes(result, "warning")


def test_validate_rejects_untestable_ac(inited_project: Path) -> None:
    planned_req(inited_project, title="简历批量解析", slug="batch-parse")
    create_prd(inited_project, scenarios="SC-RES-01,SC-RES-02")
    body = VALID_PRD.replace(
        """- AC-RES-01-02
  - Given 已上传 100 份简历
  - When 上传队列开始解析
  - Then 5 分钟内 100 条解析结果全部可见""",
        "- AC-RES-01-02 解析过程体验流畅。",
    )
    update_body(inited_project, body)

    result = run_cli(inited_project, "prd", "validate", "--id", "PRD-001")

    assert result.code == 1, result
    assert "TESTABILITY_WEAK" in issue_codes(result, "error")


def test_validate_warns_about_term_inconsistency_with_glossary(inited_project: Path) -> None:
    planned_req(inited_project, title="简历批量解析", slug="batch-parse")
    create_prd(inited_project, scenarios="SC-RES-01,SC-RES-02")
    write(inited_project / "02-definition/glossary.md",
          "# 术语表\n\n- 简历（同义：履历）\n")
    update_body(inited_project, VALID_PRD.replace("招聘专员一次上传多份简历", "招聘专员一次上传多份履历"))

    result = run_cli(inited_project, "prd", "validate", "--id", "PRD-001")

    assert result.code == 0, result
    assert "TERM_INCONSISTENT" in issue_codes(result, "warning")


def test_self_review_is_read_only_and_matches_validate(inited_project: Path) -> None:
    planned_req(inited_project, title="简历批量解析", slug="batch-parse")
    create_prd(inited_project, scenarios="SC-RES-01,SC-RES-02")
    body_before = prd_text(inited_project)
    meta_before = read(prd_dir(inited_project) / "metadata.yaml")
    validated = run_cli(inited_project, "prd", "validate", "--id", "PRD-001")

    result = run_cli(inited_project, "prd", "self-review", "--id", "PRD-001")

    assert result.code == 0, result
    assert result.json["success"] is True
    assert result.json["artifacts"] == []
    assert result.json["data"]["checklist"]
    assert issue_codes(result) == issue_codes(validated)
    assert prd_text(inited_project) == body_before
    assert read(prd_dir(inited_project) / "metadata.yaml") == meta_before


# ------------------------------------------------------------------ 提交评审
def test_submit_review_requires_actor_and_confirm(inited_project: Path) -> None:
    planned_req(inited_project, title="简历批量解析", slug="batch-parse")
    create_prd(inited_project, scenarios="SC-RES-01")
    update_body(inited_project, VALID_PRD)

    missing_confirm = run_cli(inited_project, "prd", "submit-review", "--id", "PRD-001",
                              "--actor", "bob")
    missing_actor = run_cli(inited_project, "prd", "submit-review", "--id", "PRD-001",
                            "--confirm")

    assert missing_confirm.code == 1
    assert error_codes(missing_confirm) == {"CONFIRMATION_REQUIRED"}
    assert missing_actor.code == 1
    assert error_codes(missing_actor) == {"CONFIRMATION_REQUIRED"}
    assert prd_meta(inited_project)["status"] == "DRAFT"


def test_submit_review_requires_matching_role(inited_project: Path) -> None:
    planned_req(inited_project, title="简历批量解析", slug="batch-parse")
    create_prd(inited_project, scenarios="SC-RES-01")
    update_body(inited_project, VALID_PRD)

    result = run_cli(inited_project, "prd", "submit-review", "--id", "PRD-001",
                     "--actor", "alice", "--confirm")

    assert result.code == 1
    assert error_codes(result) == {"ROLE_MISMATCH"}
    assert prd_meta(inited_project)["status"] == "DRAFT"


def test_submit_review_unknown_actor_keeps_draft(inited_project: Path) -> None:
    planned_req(inited_project, title="简历批量解析", slug="batch-parse")
    create_prd(inited_project, scenarios="SC-RES-01")

    result = run_cli(inited_project, "prd", "submit-review", "--id", "PRD-001",
                     "--actor", "dave", "--confirm")

    assert result.code == 1
    assert error_codes(result) == {"ACTOR_UNKNOWN"}
    assert prd_meta(inited_project)["status"] == "DRAFT"


def test_submit_review_records_submitter_and_writes_review_file(
    inited_project: Path,
) -> None:
    planned_req(inited_project, title="简历批量解析", slug="batch-parse")
    create_prd(inited_project, scenarios="SC-RES-01,SC-RES-02")
    update_body(inited_project, VALID_PRD)

    result = run_cli(inited_project, "prd", "submit-review", "--id", "PRD-001",
                     "--actor", "bob", "--confirm", "--note", "请重点看范围一节", env=ENV)

    assert result.code == 0, result
    meta = prd_meta(inited_project)
    assert meta["status"] == "IN_REVIEW"
    assert meta["submitted_by"] == "bob"
    assert meta["history"][-1]["from"] == "DRAFT"
    assert meta["history"][-1]["to"] == "IN_REVIEW"
    assert meta["history"][-1]["action"] == "prd.submit-review"

    review = prd_dir(inited_project) / "reviews/2026-03-01-review.md"
    assert review.is_file()
    text = read(review)
    assert "bob" in text
    assert NOW in text
    assert "请重点看范围一节" in text


# ------------------------------------------------------------------ 批准
def test_approve_is_a_separate_confirmation_point(inited_project: Path) -> None:
    planned_req(inited_project, title="简历批量解析", slug="batch-parse")
    create_prd(inited_project, scenarios="SC-RES-01,SC-RES-02")
    update_body(inited_project, VALID_PRD)
    run_cli(inited_project, "prd", "submit-review", "--id", "PRD-001", "--actor", "bob",
            "--confirm", env=ENV)

    result = run_cli(inited_project, "prd", "approve", "--id", "PRD-001", "--actor", "alice",
                     "--confirm", env=ENV)

    assert result.code == 0, result
    meta = prd_meta(inited_project)
    assert meta["status"] == "APPROVED"
    assert meta["approved_by"] == "alice"
    assert meta["approved_at"] == NOW
    assert meta["version"] == 1
    assert meta["content_hash"].startswith("sha256:")
    assert len(meta["content_hash"]) == len("sha256:") + 64
    confirmation = meta["confirmations"][-1]
    assert confirmation["by"] == "alice"
    assert confirmation["role"] == "requirement_owner"
    assert confirmation["decision"] == "approved"
    assert confirmation["object"] == "PRD-001"
    assert confirmation["hash"] == meta["content_hash"]
    assert confirmation["version"] == 1
    assert meta["history"][-1]["to"] == "APPROVED"


def test_approve_requires_validation_passed(inited_project: Path) -> None:
    planned_req(inited_project, title="简历批量解析", slug="batch-parse")
    create_prd(inited_project, scenarios="SC-RES-01")
    run_cli(inited_project, "prd", "submit-review", "--id", "PRD-001", "--actor", "bob",
            "--confirm", env=ENV)
    before = read(prd_dir(inited_project) / "metadata.yaml")

    result = run_cli(inited_project, "prd", "approve", "--id", "PRD-001", "--actor", "alice",
                     "--confirm", env=ENV)

    assert result.code == 1
    assert error_codes(result) == {"VALIDATION_FAILED"}
    assert read(prd_dir(inited_project) / "metadata.yaml") == before


def test_approve_requires_requirement_owner(inited_project: Path) -> None:
    planned_req(inited_project, title="简历批量解析", slug="batch-parse")
    create_prd(inited_project, scenarios="SC-RES-01,SC-RES-02")
    update_body(inited_project, VALID_PRD)
    run_cli(inited_project, "prd", "submit-review", "--id", "PRD-001", "--actor", "bob",
            "--confirm", env=ENV)

    for actor in ("bob", "carol"):
        result = run_cli(inited_project, "prd", "approve", "--id", "PRD-001",
                         "--actor", actor, "--confirm", env=ENV)
        assert result.code == 1
        assert error_codes(result) == {"ROLE_MISMATCH"}
    assert prd_meta(inited_project)["status"] == "IN_REVIEW"


def test_approve_requires_confirmation_flags(inited_project: Path) -> None:
    planned_req(inited_project, title="简历批量解析", slug="batch-parse")
    create_prd(inited_project, scenarios="SC-RES-01,SC-RES-02")
    update_body(inited_project, VALID_PRD)
    run_cli(inited_project, "prd", "submit-review", "--id", "PRD-001", "--actor", "bob",
            "--confirm", env=ENV)

    result = run_cli(inited_project, "prd", "approve", "--id", "PRD-001", "--actor", "alice")

    assert result.code == 1
    assert error_codes(result) == {"CONFIRMATION_REQUIRED"}
    assert prd_meta(inited_project)["status"] == "IN_REVIEW"


def test_approve_from_draft_is_invalid_transition(inited_project: Path) -> None:
    planned_req(inited_project, title="简历批量解析", slug="batch-parse")
    create_prd(inited_project, scenarios="SC-RES-01,SC-RES-02")
    update_body(inited_project, VALID_PRD)
    before = read(prd_dir(inited_project) / "metadata.yaml")

    result = run_cli(inited_project, "prd", "approve", "--id", "PRD-001", "--actor", "alice",
                     "--confirm", env=ENV)

    assert result.code == 1
    assert error_codes(result) == {"INVALID_TRANSITION"}
    assert read(prd_dir(inited_project) / "metadata.yaml") == before


def test_submit_review_after_approval_points_to_cr(inited_project: Path) -> None:
    planned_req(inited_project, title="简历批量解析", slug="batch-parse")
    create_prd(inited_project, scenarios="SC-RES-01,SC-RES-02")
    update_body(inited_project, VALID_PRD)
    run_cli(inited_project, "prd", "submit-review", "--id", "PRD-001", "--actor", "bob",
            "--confirm", env=ENV)
    run_cli(inited_project, "prd", "approve", "--id", "PRD-001", "--actor", "alice",
            "--confirm", env=ENV)

    result = run_cli(inited_project, "prd", "submit-review", "--id", "PRD-001",
                     "--actor", "bob", "--confirm", env=ENV)

    assert result.code == 1
    assert error_codes(result) == {"PRD_LOCKED"}
    assert "CR" in " ".join(result.json["next_actions"]) + (result.json["errors"][0].get(
        "suggestion") or "")
    assert prd_meta(inited_project)["status"] == "APPROVED"


# ------------------------------------------------------------------ 修改与锁定
def test_update_locked_after_approval(inited_project: Path) -> None:
    planned_req(inited_project, title="简历批量解析", slug="batch-parse")
    create_prd(inited_project, scenarios="SC-RES-01,SC-RES-02")
    update_body(inited_project, VALID_PRD)
    run_cli(inited_project, "prd", "submit-review", "--id", "PRD-001", "--actor", "bob",
            "--confirm", env=ENV)
    run_cli(inited_project, "prd", "approve", "--id", "PRD-001", "--actor", "alice",
            "--confirm", env=ENV)
    body_before = prd_text(inited_project)

    result = run_cli(inited_project, "prd", "update", "--id", "PRD-001", "--body-file",
                     str(inited_project / "prd-body-draft.md"))

    assert result.code == 1
    assert error_codes(result) == {"PRD_LOCKED"}
    assert prd_text(inited_project) == body_before


def test_update_requires_something_to_change(inited_project: Path) -> None:
    planned_req(inited_project, title="简历批量解析", slug="batch-parse")
    create_prd(inited_project, scenarios="SC-RES-01")

    result = run_cli(inited_project, "prd", "update", "--id", "PRD-001")

    assert result.code == 1
    assert error_codes(result) == {"NOTHING_TO_UPDATE"}


def test_update_rejects_immutable_fields(inited_project: Path) -> None:
    planned_req(inited_project, title="简历批量解析", slug="batch-parse")
    create_prd(inited_project, scenarios="SC-RES-01")

    result = run_cli(inited_project, "prd", "update", "--id", "PRD-001", "--set",
                     "source_reqs=REQ-002")

    assert result.code == 1
    assert error_codes(result) == {"IMMUTABLE_FIELD"}
    assert prd_meta(inited_project)["source_reqs"] == ["REQ-001"]


def test_update_can_change_scenarios_and_feature(inited_project: Path) -> None:
    planned_req(inited_project, title="简历批量解析", slug="batch-parse")
    create_prd(inited_project, scenarios="SC-RES-01")

    result = run_cli(inited_project, "prd", "update", "--id", "PRD-001", "--set",
                     "feature=resume-parse", "--scenarios", "SC-RES-01,SC-RES-03", env=ENV)

    assert result.code == 0, result
    meta = prd_meta(inited_project)
    assert meta["feature"] == "resume-parse"
    assert meta["scenarios"] == ["SC-RES-01", "SC-RES-03"]


# ------------------------------------------------------------------ 拒绝
def test_reject_from_draft_and_from_review(inited_project: Path) -> None:
    planned_req(inited_project, title="简历批量解析", slug="batch-parse")
    create_prd(inited_project, scenarios="SC-RES-01")

    rejected = run_cli(inited_project, "prd", "reject", "--id", "PRD-001", "--actor", "alice",
                       "--confirm", "--reason", "范围过大，需要重新拆分", env=ENV)
    assert rejected.code == 0, rejected
    assert prd_meta(inited_project)["status"] == "REJECTED"
    assert prd_meta(inited_project)["history"][-1]["reason"] == "范围过大，需要重新拆分"


def test_reject_requires_reason(inited_project: Path) -> None:
    planned_req(inited_project, title="简历批量解析", slug="batch-parse")
    create_prd(inited_project, scenarios="SC-RES-01")

    result = run_cli(inited_project, "prd", "reject", "--id", "PRD-001", "--actor", "alice",
                     "--confirm", env=ENV)

    assert result.code == 1
    assert error_codes(result) == {"REASON_REQUIRED"}
    assert prd_meta(inited_project)["status"] == "DRAFT"


def test_reject_from_review_state(inited_project: Path) -> None:
    planned_req(inited_project, title="简历批量解析", slug="batch-parse")
    create_prd(inited_project, scenarios="SC-RES-01")
    run_cli(inited_project, "prd", "submit-review", "--id", "PRD-001", "--actor", "bob",
            "--confirm", env=ENV)

    result = run_cli(inited_project, "prd", "reject", "--id", "PRD-001", "--actor", "alice",
                     "--confirm", "--reason", "需求本身不成立", env=ENV)

    assert result.code == 0, result
    assert prd_meta(inited_project)["status"] == "REJECTED"


# ------------------------------------------------------------------ 投影
def test_show_and_list_projections(inited_project: Path) -> None:
    planned_req(inited_project, title="简历批量解析", slug="batch-parse")
    create_prd(inited_project, scenarios="SC-RES-01,SC-RES-02")

    shown = run_cli(inited_project, "prd", "show", "--id", "PRD-001")
    listed = run_cli(inited_project, "prd", "list")

    assert shown.code == 0, shown
    prd = shown.json["data"]["prd"]
    assert prd["status"] == "DRAFT"
    assert prd["content_hash"] is None
    assert prd["version"] == 1
    assert prd["source_reqs"] == ["REQ-001"]
    assert "prd.md" in prd["body"] or prd["body"]

    assert listed.code == 0, listed
    assert listed.json["data"]["count"] == 1
    assert listed.json["data"]["prds"][0]["id"] == "PRD-001"


def test_list_filters_by_status_and_module(inited_project: Path) -> None:
    planned_req(inited_project, title="简历批量解析", slug="batch-parse")
    create_prd(inited_project, scenarios="SC-RES-01")

    matched = run_cli(inited_project, "prd", "list", "--status", "DRAFT", "--module", "resume")
    empty = run_cli(inited_project, "prd", "list", "--status", "APPROVED")

    assert matched.json["data"]["count"] == 1
    assert empty.json["data"]["count"] == 0


def test_show_unknown_prd_reports_not_found(inited_project: Path) -> None:
    setup_team(inited_project)

    result = run_cli(inited_project, "prd", "show", "--id", "PRD-404")

    assert result.code == 1
    assert error_codes(result) == {"PRD_NOT_FOUND"}


def test_missing_subcommand_is_usage_error(inited_project: Path) -> None:
    result = run_cli(inited_project, "prd")

    assert result.code == 2


# ------------------------------------------------------------------ 骨架与边界
def test_fresh_skeleton_fails_validation(inited_project: Path) -> None:
    """新建骨架只有占位内容，校验必须报章节缺失（不能把模板占位当成已填写）。"""
    planned_req(inited_project, title="简历批量解析", slug="batch-parse")
    create_prd(inited_project, scenarios="SC-RES-01")

    result = run_cli(inited_project, "prd", "validate", "--id", "PRD-001")

    assert result.code == 1
    assert "MISSING_SECTION" in issue_codes(result, "error")
    assert "MISSING_CURRENT_STATE" in issue_codes(result, "error")


def test_create_rejects_requirement_already_in_prd(inited_project: Path) -> None:
    planned_req(inited_project, title="简历批量解析", slug="batch-parse")
    create_prd(inited_project, scenarios="SC-RES-01")

    result = run_cli(inited_project, "prd", "create", "--req", "REQ-001")

    assert result.code == 1
    # 主需求已转 IN_PRD：require_planned 先报状态不满足（ticket 02 的判定顺序）
    assert error_codes(result) <= {"REQ_NOT_PLANNED", "REQ_ALREADY_IN_PRD"}
    assert error_codes(result)
    assert prd_meta(inited_project)["status"] == "DRAFT"
    assert len(list((inited_project / "04-prds").glob("resume/*"))) == 1


def test_create_rejects_malformed_scenario_id(inited_project: Path) -> None:
    planned_req(inited_project, title="简历批量解析", slug="batch-parse")

    result = run_cli(inited_project, "prd", "create", "--req", "REQ-001",
                     "--scenarios", "batch-upload")

    assert result.code == 1
    assert error_codes(result) == {"INVALID_SCENARIO"}
    assert not (inited_project / "04-prds/resume").exists()


def test_create_rejects_unknown_related_requirement(inited_project: Path) -> None:
    planned_req(inited_project, title="简历批量解析", slug="batch-parse")

    result = run_cli(inited_project, "prd", "create", "--req", "REQ-001",
                     "--related", "REQ-404")

    assert result.code == 1
    assert error_codes(result) == {"REQ_NOT_FOUND"}
    assert not (inited_project / "04-prds/resume").exists()


def test_create_defaults_slug_module_and_release_from_requirement(
    inited_project: Path,
) -> None:
    planned_req(inited_project, title="简历批量解析", slug="batch-parse", release="v0.2.0")

    create_prd(inited_project, scenarios="SC-RES-01")

    meta = prd_meta(inited_project)
    assert meta["slug"] == "batch-parse"
    assert meta["module"] == "resume"
    assert meta["release"] == "v0.2.0"
    assert meta["feature"] == "batch-parse"


def test_changes_requested_can_be_edited_again(inited_project: Path) -> None:
    """`CHANGES_REQUESTED` 允许继续改草稿（规则 prd.draft_states 驱动）。"""
    planned_req(inited_project, title="简历批量解析", slug="batch-parse")
    create_prd(inited_project, scenarios="SC-RES-01")
    update_body(inited_project, VALID_PRD)
    run_cli(inited_project, "prd", "submit-review", "--id", "PRD-001", "--actor", "bob",
            "--confirm", env=ENV)
    path = prd_dir(inited_project) / "metadata.yaml"
    meta = yaml.safe_load(read(path))
    meta["status"] = "CHANGES_REQUESTED"
    write(path, yaml.safe_dump(meta, sort_keys=False, allow_unicode=True))

    result = run_cli(inited_project, "prd", "update", "--id", "PRD-001", "--set",
                     "feature=resume-parse", env=ENV)

    assert result.code == 0, result
    assert prd_meta(inited_project)["feature"] == "resume-parse"


def test_validate_does_not_create_or_touch_reviews(inited_project: Path) -> None:
    planned_req(inited_project, title="简历批量解析", slug="batch-parse")
    create_prd(inited_project, scenarios="SC-RES-01")

    run_cli(inited_project, "prd", "validate", "--id", "PRD-001")

    assert sorted((prd_dir(inited_project) / "reviews").glob("*")) == []
    assert sorted((prd_dir(inited_project) / "changes").glob("*")) == []
