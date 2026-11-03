"""ticket 09：`prd request-changes`（共同设计 §13 待办 1）黑盒测试。

`IN_REVIEW → CHANGES_REQUESTED`，职责读 `gates.prd.request-changes`
（`requirement_owner`）。命令必须遵守共享设计 §2.3 的固定顺序：
解析对象 → `CONFIRMATION_REQUIRED` → 团队/身份/职责 → 状态跳转 → 取锁 →
追加 history/confirmations → 原子写 → 释放锁。任何前置失败都零副作用。
"""

from __future__ import annotations

from pathlib import Path

import yaml

import helpers_07 as h7
from conftest import read, run_cli, write
from helpers_03 import ENV, create_prd, planned_req, prd_dir, update_body

PRD_ID = "PRD-001"


def metadata_text(project: Path) -> str:
    return read(prd_dir(project) / "metadata.yaml")


def metadata(project: Path) -> dict:
    return yaml.safe_load(metadata_text(project))


def in_review(project: Path) -> Path:
    """DRAFT → 提交评审（`IN_REVIEW`），团队与 constitution 已就位。"""
    assert run_cli(project, "init").code == 0
    planned_req(project, title="简历批量解析", slug="batch-parse")
    create_prd(project, scenarios="SC-RES-01,SC-RES-02")
    update_body(project, h7.VALID_PRD)
    write(project / ".specify/constitution.md", h7.CONSTITUTION)
    submitted = run_cli(project, "prd", "submit-review", "--id", PRD_ID, "--actor", "bob",
                        "--confirm", env=ENV)
    assert submitted.code == 0, submitted
    assert metadata(project)["status"] == "IN_REVIEW"
    return project


def request_changes(project: Path, *args: str, env: dict | None = None):
    return run_cli(project, "prd", "request-changes", *args,
                   env=env if env is not None else ENV)


def error_codes(result) -> set[str]:
    return {item["code"] for item in (result.json or {}).get("errors", [])}


# --------------------------------------------------------------------------
# 正常路径
# --------------------------------------------------------------------------
def test_request_changes_moves_to_changes_requested(project: Path):
    in_review(project)
    result = request_changes(project, "--id", PRD_ID, "--actor", "alice", "--confirm",
                             "--reason", "场景 SC-RES-02 的验收标准不可测量")
    assert result.code == 0, result
    meta = metadata(project)
    assert meta["status"] == "CHANGES_REQUESTED"


def test_request_changes_appends_history_and_confirmation(project: Path):
    in_review(project)
    before = metadata(project)
    result = request_changes(project, "--id", PRD_ID, "--actor", "alice", "--confirm",
                             "--reason", "范围需要缩小")
    assert result.code == 0, result
    meta = metadata(project)
    assert len(meta["history"]) == len(before["history"]) + 1
    entry = meta["history"][-1]
    assert entry["from"] == "IN_REVIEW"
    assert entry["to"] == "CHANGES_REQUESTED"
    assert entry["by"] == "alice"
    assert entry["action"] == "prd.request-changes"
    assert entry["at"] == ENV["NEO_SKILL_NOW"]
    assert entry["reason"] == "范围需要缩小"

    assert len(meta["confirmations"]) == len(before["confirmations"]) + 1
    record = meta["confirmations"][-1]
    assert record["by"] == "alice"
    assert record["role"] == "requirement_owner"
    assert record["decision"] == "prd.request-changes"
    assert record["object"] == PRD_ID


def test_request_changes_keeps_approved_hash_untouched(project: Path):
    in_review(project)
    before = metadata(project)
    result = request_changes(project, "--id", PRD_ID, "--actor", "alice", "--confirm",
                             "--reason", "需要补充非功能需求")
    assert result.code == 0, result
    after = metadata(project)
    assert after["content_hash"] == before["content_hash"]
    assert after["approved_by"] == before["approved_by"] is None


def test_request_changes_data_reports_prd(project: Path):
    in_review(project)
    result = request_changes(project, "--id", PRD_ID, "--actor", "alice", "--confirm",
                             "--reason", "补充数据需求")
    assert result.code == 0, result
    assert result.json["data"]["prd"]["id"] == PRD_ID
    assert result.json["data"]["prd"]["status"] == "CHANGES_REQUESTED"
    assert result.json["data"]["reason"] == "补充数据需求"
    assert result.json["data"]["from"] == "IN_REVIEW"


def test_changes_requested_can_be_resubmitted(project: Path):
    """打回后必须能改正文并重新提交评审（否则流程死锁）。"""
    in_review(project)
    assert request_changes(project, "--id", PRD_ID, "--actor", "alice", "--confirm",
                           "--reason", "补数据需求").code == 0
    edited = update_body(project, h7.VALID_PRD + "\n<!-- 已按评审意见补充 -->\n")
    assert edited is not None
    resubmitted = run_cli(project, "prd", "submit-review", "--id", PRD_ID, "--actor", "bob",
                          "--confirm", env=ENV)
    assert resubmitted.code == 0, resubmitted
    assert metadata(project)["status"] == "IN_REVIEW"


# --------------------------------------------------------------------------
# 前置校验：全部零副作用
# --------------------------------------------------------------------------
def test_missing_actor_is_rejected(project: Path):
    in_review(project)
    before = metadata_text(project)
    result = request_changes(project, "--id", PRD_ID, "--confirm", "--reason", "补数据需求")
    assert result.code == 1, result
    assert "CONFIRMATION_REQUIRED" in error_codes(result)
    assert metadata_text(project) == before


def test_missing_confirm_is_rejected(project: Path):
    in_review(project)
    before = metadata_text(project)
    result = request_changes(project, "--id", PRD_ID, "--actor", "alice",
                             "--reason", "补数据需求")
    assert result.code == 1, result
    assert "CONFIRMATION_REQUIRED" in error_codes(result)
    assert metadata_text(project) == before


def test_missing_reason_is_rejected(project: Path):
    in_review(project)
    before = metadata_text(project)
    result = request_changes(project, "--id", PRD_ID, "--actor", "alice", "--confirm")
    assert result.code == 1, result
    assert "REASON_REQUIRED" in error_codes(result)
    assert metadata_text(project) == before


def test_wrong_role_is_rejected(project: Path):
    """`bob` 只有 stakeholder 职责，不能打回（规则 gates.prd.request-changes）。"""
    in_review(project)
    before = metadata_text(project)
    result = request_changes(project, "--id", PRD_ID, "--actor", "bob", "--confirm",
                             "--reason", "补数据需求")
    assert result.code == 1, result
    assert "ROLE_MISMATCH" in error_codes(result)
    assert metadata_text(project) == before


def test_unknown_actor_is_rejected(project: Path):
    in_review(project)
    result = request_changes(project, "--id", PRD_ID, "--actor", "nobody", "--confirm",
                             "--reason", "补数据需求")
    assert result.code == 1, result
    assert "ACTOR_UNKNOWN" in error_codes(result)


def test_invalid_transition_from_draft(project: Path):
    """DRAFT 不能直接打回：规则表只有 IN_REVIEW → CHANGES_REQUESTED。"""
    planned_req(project, title="简历批量解析", slug="batch-parse")
    create_prd(project, scenarios="SC-RES-01,SC-RES-02")
    update_body(project, h7.VALID_PRD)
    before = metadata_text(project)
    result = request_changes(project, "--id", PRD_ID, "--actor", "alice", "--confirm",
                             "--reason", "补数据需求")
    assert result.code == 1, result
    assert "INVALID_TRANSITION" in error_codes(result)
    assert metadata_text(project) == before


def test_unknown_prd_is_rejected(project: Path):
    planned_req(project, title="简历批量解析", slug="batch-parse")
    result = request_changes(project, "--id", "PRD-404", "--actor", "alice", "--confirm",
                             "--reason", "补数据需求")
    assert result.code == 1, result
    assert "PRD_NOT_FOUND" in error_codes(result)


def test_request_changes_is_reflected_by_lint(project: Path):
    """打回后 PRD 未批准 → lint 的 BASELINE_MISSING（CHANGES_REQUESTED 不是批准态）。"""
    in_review(project)
    assert request_changes(project, "--id", PRD_ID, "--actor", "alice", "--confirm",
                           "--reason", "补数据需求").code == 0
    linted = run_cli(project, "lint", env=ENV)
    found = {item["code"] for item in linted.json["data"]["findings"]}
    assert "BASELINE_MISSING" in found, linted.json["data"]["findings"]
