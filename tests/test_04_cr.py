"""ticket 04：通过 CR 安全变更已批准 PRD（PRD 基线失效与重新批准）。

契约来源：
- 共享设计 §4.2 / §6（PRD metadata、hash 算法）、§2.3（人工决策固定顺序）；
- SRS-FR-049～051、§6.3、BR-012/016/019。

全部通过 CLI 黑盒断言（共享设计 §10）：退出码、`--json` 信封字段与错误码、
文件是否存在与内容片段、metadata 关键字段。只有「构造特定状态」（如 `DEVELOPING`、
既有验收结论）的测试才直接改文件，并在测试里注明原因。
"""

from __future__ import annotations

from pathlib import Path

import pytest
import yaml

from conftest import read, run_cli, write
from helpers_03 import (
    ENV,
    VALID_PRD,
    create_prd,
    planned_req,
    prd_dir,
    prd_meta,
    prd_text,
    update_body,
)

CR_REASON = "范围增加批量导出"


def error_codes(result) -> set[str]:
    return {item["code"] for item in (result.json or {}).get("errors", [])}


def baseline_status(project: Path, prd_id: str = "PRD-001"):
    return run_cli(project, "prd", "baseline-status", "--id", prd_id)


def cr_create(project: Path, *extra: str, actor: str = "alice", expect: int = 0,
              reason: str = CR_REASON):
    args = ["prd", "cr", "create", "--id", "PRD-001", "--actor", actor, "--confirm",
            "--reason", reason, *extra]
    result = run_cli(project, *args, env=ENV)
    assert result.code == expect, result
    return result


def cr_file(project: Path, cr_id: str = "CR-001") -> Path:
    matches = sorted((prd_dir(project) / "changes").glob(f"{cr_id}-*.md"))
    assert len(matches) == 1, f"{cr_id} 的 change 文件数量异常：{matches}"
    return matches[0]


def cr_files(project: Path) -> list[Path]:
    return sorted((prd_dir(project) / "changes").glob("CR-*.md"))


def cr_front_matter(path: Path) -> dict:
    text = read(path)
    head = text.split("---\n", 2)[1]
    data = yaml.safe_load(head)
    assert isinstance(data, dict)
    return data


def meta_path(project: Path) -> Path:
    return prd_dir(project) / "metadata.yaml"


def patch_meta(project: Path, **changes) -> dict:
    """直接改 metadata：只用于构造 CLI 尚不能到达的状态（如 DEVELOPING、既有验收结论）。"""
    path = meta_path(project)
    data = yaml.safe_load(read(path))
    data.update(changes)
    write(path, yaml.safe_dump(data, sort_keys=False, allow_unicode=True))
    return data


@pytest.fixture()
def approved(inited_project: Path) -> Path:
    planned_req(inited_project, title="简历批量解析", slug="batch-parse")
    create_prd(inited_project, scenarios="SC-RES-01,SC-RES-02")
    update_body(inited_project, VALID_PRD)
    submitted = run_cli(inited_project, "prd", "submit-review", "--id", "PRD-001",
                        "--actor", "bob", "--confirm", env=ENV)
    assert submitted.code == 0, submitted
    result = run_cli(inited_project, "prd", "approve", "--id", "PRD-001", "--actor", "alice",
                     "--confirm", env=ENV)
    assert result.code == 0, result
    return inited_project


def with_cr(project: Path, *extra: str) -> Path:
    created = cr_create(project, *extra)
    assert created.code == 0, created
    return project


# ------------------------------------------------------------------ 基线状态与直接篡改
def test_baseline_status_reports_valid_after_approval(approved: Path) -> None:
    meta = prd_meta(approved)

    result = baseline_status(approved)

    assert result.code == 0, result
    data = result.json["data"]
    assert data["content_hash"] == meta["content_hash"]
    assert data["expected"] == meta["content_hash"]
    assert data["current"] == meta["content_hash"]
    assert data["valid"] is True
    assert data["invalidated_by"] is None
    assert data["superseded_evidence"] == []


def test_direct_tamper_is_reported_as_invalid_baseline(approved: Path) -> None:
    expected = prd_meta(approved)["content_hash"]
    write(prd_dir(approved) / "prd.md",
          prd_text(approved).replace("5 分钟内", "15 分钟内"))

    status = baseline_status(approved)
    verified = run_cli(approved, "prd", "verify-hash", "--id", "PRD-001")

    assert status.code == 1
    data = status.json["data"]
    assert data["valid"] is False
    assert data["expected"] == expected
    assert data["current"] != expected
    assert "HASH_MISMATCH" in data["reasons"]
    assert verified.code == 1
    assert error_codes(verified) == {"HASH_MISMATCH"}


def test_baseline_status_without_approval_reports_missing(inited_project: Path) -> None:
    planned_req(inited_project, title="简历批量解析", slug="batch-parse")
    create_prd(inited_project, scenarios="SC-RES-01")

    result = baseline_status(inited_project)

    assert result.code == 1
    assert result.json["data"]["valid"] is False
    assert "BASELINE_MISSING" in result.json["data"]["reasons"]


def test_baseline_status_requires_existing_prd(inited_project: Path) -> None:
    result = baseline_status(inited_project, prd_id="PRD-404")

    assert result.code == 1
    assert error_codes(result) == {"PRD_NOT_FOUND"}


# ------------------------------------------------------------------ 创建 CR
def test_create_cr_records_required_fields(approved: Path) -> None:
    version_before = prd_meta(approved)["version"]

    result = cr_create(
        approved,
        "--impact", "spec=true,tasks=true,tests=true,design=false,version=v0.2.0",
    )

    assert result.code == 0, result
    cr = result.json["data"]["cr"]
    assert cr["id"] == "CR-001"
    assert cr["prd"] == "PRD-001"
    assert cr["status"] == "OPEN"
    assert cr["reason"] == CR_REASON
    assert cr["requested_by"] == "alice"
    assert cr["version_before"] == version_before
    assert cr["version_after"] is None
    assert cr["impact"]["spec"] is True
    assert cr["impact"]["design"] is False
    assert cr["impact"]["version"] == "v0.2.0"

    path = cr_file(approved)
    assert path.is_file()
    head = cr_front_matter(path)
    assert head["id"] == "CR-001"
    assert head["prd"] == "PRD-001"
    assert head["status"] == "OPEN"
    assert head["requested_at"]
    assert set(head["impact"]) == {"spec", "tasks", "tests", "design", "version"}
    assert CR_REASON in read(path)


def test_create_cr_moves_approved_prd_to_in_review(approved: Path) -> None:
    hash_before = prd_meta(approved)["content_hash"]

    cr_create(approved)

    meta = prd_meta(approved)
    assert meta["status"] == "IN_REVIEW"
    assert meta["baseline_invalidated"]["cr"] == "CR-001"
    assert meta["baseline_invalidated"]["previous_status"] == "APPROVED"
    assert meta["baseline_invalidated"]["previous_hash"] == hash_before
    assert meta["baseline_invalidated"]["active"] is True
    assert meta["baseline_invalidated"]["by"] == "alice"
    assert meta["baseline_invalidated"]["at"]
    assert meta["history"][-1]["to"] == "IN_REVIEW"
    assert meta["history"][-1]["from"] == "APPROVED"


def confirm_constitution(project: Path) -> None:
    """constitution 确认是 ticket 05 的 dev 门禁前置；这里只改 confirmed 标记。"""
    path = project / ".specify/constitution.md"
    write(path, read(path).replace("confirmed: false", "confirmed: true"))


def start_dev(project: Path, *, actor: str = "carol") -> None:
    confirm_constitution(project)
    result = run_cli(project, "dev", "start", "--prd", "PRD-001", "--actor", actor,
                     "--confirm", env=ENV)
    assert result.code == 0, result


def test_cr_from_developing_returns_prd_to_in_review(approved: Path) -> None:
    start_dev(approved)
    assert prd_meta(approved)["status"] == "DEVELOPING"

    cr_create(approved)

    assert prd_meta(approved)["status"] == "IN_REVIEW"
    assert prd_meta(approved)["baseline_invalidated"]["previous_status"] == "DEVELOPING"


def test_cr_blocks_development_until_reapproved(approved: Path) -> None:
    start_dev(approved)
    cr_create(approved)

    status = run_cli(approved, "dev", "status", "--prd", "PRD-001")

    assert status.code == 0, status
    assert status.json["data"]["baseline_valid"] is False
    again = run_cli(approved, "dev", "start", "--prd", "PRD-001", "--actor", "carol",
                    "--confirm", env=ENV)
    assert again.code == 1
    assert "BASELINE_INVALID" in error_codes(again)


def test_reapproved_cr_lets_development_resume(approved: Path) -> None:
    start_dev(approved)
    cr_create(approved)
    edited = prd_text(approved).replace("5 分钟内", "8 分钟内")
    write(approved / "prd-v2.md", edited)
    assert run_cli(approved, "prd", "update", "--id", "PRD-001", "--body-file",
                   str(approved / "prd-v2.md"), env=ENV).code == 0

    approved_again = run_cli(approved, "prd", "cr", "approve", "--id", "PRD-001",
                             "--cr", "CR-001", "--actor", "alice", "--confirm", env=ENV)
    resumed = run_cli(approved, "dev", "start", "--prd", "PRD-001", "--actor", "carol",
                      "--confirm", env=ENV)

    assert approved_again.code == 0, approved_again
    assert resumed.code == 0, resumed
    assert resumed.json["data"]["prd"]["status"] == "DEVELOPING"
    marker = yaml.safe_load(read(
        approved / "06-specs/resume/PRD-001-batch-parse/.baseline.yaml"))
    assert marker["baseline_valid"] is True
    assert marker["baseline_version"] == 2


def test_baseline_invalid_after_cr_and_old_approval_not_reusable(approved: Path) -> None:
    hash_before = prd_meta(approved)["content_hash"]
    with_cr(approved)

    status = baseline_status(approved)

    assert status.code == 1
    data = status.json["data"]
    assert data["valid"] is False
    assert data["expected"] == hash_before
    assert data["invalidated_by"]["cr"] == "CR-001"
    assert data["invalidated_by"]["active"] is True
    assert "BASELINE_INVALIDATED" in data["reasons"]


def test_cr_create_from_draft_does_not_invalidate(inited_project: Path) -> None:
    planned_req(inited_project, title="简历批量解析", slug="batch-parse")
    create_prd(inited_project, scenarios="SC-RES-01")
    update_body(inited_project, VALID_PRD)

    result = cr_create(inited_project)

    assert result.code == 0, result
    meta = prd_meta(inited_project)
    assert meta["status"] == "DRAFT"
    assert meta.get("baseline_invalidated") in (None, {})
    assert cr_front_matter(cr_file(inited_project))["status"] == "OPEN"


def test_cr_create_requires_confirmation(approved: Path) -> None:
    before = read(meta_path(approved))

    missing_actor = run_cli(approved, "prd", "cr", "create", "--id", "PRD-001",
                            "--confirm", "--reason", CR_REASON, env=ENV)
    missing_confirm = run_cli(approved, "prd", "cr", "create", "--id", "PRD-001",
                              "--actor", "alice", "--reason", CR_REASON, env=ENV)

    for result in (missing_actor, missing_confirm):
        assert result.code == 1
        assert error_codes(result) == {"CONFIRMATION_REQUIRED"}
    assert read(meta_path(approved)) == before
    assert cr_files(approved) == []


def test_cr_create_requires_role(approved: Path) -> None:
    before = read(meta_path(approved))

    result = cr_create(approved, actor="bob", expect=1)

    assert error_codes(result) == {"ROLE_MISMATCH"}
    assert read(meta_path(approved)) == before


def test_cr_create_requires_reason_and_leaves_no_trace(approved: Path) -> None:
    before = read(meta_path(approved))
    counters_before = read(approved / ".neo-skill/counters.yaml")

    result = run_cli(approved, "prd", "cr", "create", "--id", "PRD-001", "--actor", "alice",
                     "--confirm", env=ENV)

    assert result.code == 1
    assert error_codes(result) == {"REASON_REQUIRED"}
    assert read(meta_path(approved)) == before
    assert read(approved / ".neo-skill/counters.yaml") == counters_before
    assert not list((prd_dir(approved) / "changes").glob("CR-*.md"))


def test_cr_create_rejects_unknown_impact_key(approved: Path) -> None:
    before = read(meta_path(approved))

    result = cr_create(approved, "--impact", "budget=100", expect=1)

    assert error_codes(result) == {"INVALID_IMPACT"}
    assert read(meta_path(approved)) == before
    assert cr_files(approved) == []


def test_cr_create_rejects_invalid_impact_version(approved: Path) -> None:
    before = read(meta_path(approved))

    result = cr_create(approved, "--impact", "version=0.2", expect=1)

    assert error_codes(result) == {"INVALID_RELEASE"}
    assert read(meta_path(approved)) == before
    assert cr_files(approved) == []


def test_cr_create_uses_body_file(approved: Path) -> None:
    body = "# 变更内容\n\n新增「导出 CSV」场景，场景数保持 2 个。\n"
    write(approved / "cr-body.md", body)

    result = cr_create(approved, "--body-file", str(approved / "cr-body.md"))

    assert result.code == 0, result
    text = read(cr_file(approved))
    assert "导出 CSV" in text
    assert result.json["data"]["cr"]["content"].startswith("新增「导出 CSV」场景")


def test_cr_create_with_missing_body_file_leaves_no_trace(approved: Path) -> None:
    before = read(meta_path(approved))

    result = cr_create(approved, "--body-file", str(approved / "nope.md"), expect=1)

    assert error_codes(result) == {"BODY_FILE_NOT_FOUND"}
    assert read(meta_path(approved)) == before
    assert cr_files(approved) == []


def test_cr_numbers_are_monotonic(approved: Path) -> None:
    first = cr_create(approved, reason="范围调整")
    second = cr_create(approved, reason="验收标准补充")

    assert first.json["data"]["cr"]["id"] == "CR-001"
    assert second.json["data"]["cr"]["id"] == "CR-002"


# ------------------------------------------------------------------ 下游基线失效标记
def test_existing_spec_is_marked_baseline_invalid(approved: Path) -> None:
    spec_dir = approved / "06-specs/resume/PRD-001-batch-parse"
    write(spec_dir / "spec.md", "# Spec：PRD-001 简历批量解析\n\n- PRD 版本: 1\n")
    hash_before = prd_meta(approved)["content_hash"]

    cr_create(approved)

    marker_file = spec_dir / ".baseline.yaml"
    assert marker_file.is_file(), "已存在的 Spec 目录必须被标记基线失效"
    marker = yaml.safe_load(read(marker_file))
    assert marker["baseline_valid"] is False
    assert marker["invalidated_by"] == "CR-001"
    assert marker["prd"] == "PRD-001"
    assert marker["baseline_hash"] == hash_before
    assert marker["invalidated_at"]


def test_cr_from_terminal_state_invalidates_without_changing_state(approved: Path) -> None:
    # ACCEPTED 是终态（规则表无回到 IN_REVIEW 的出边）：只登记 CR + 标记失效，状态不变。
    patch_meta(approved, status="ACCEPTED")

    result = cr_create(approved)

    assert result.code == 0, result
    assert result.json["data"]["invalidated"] is True
    assert result.json["data"]["status_changed"] is False
    meta = prd_meta(approved)
    assert meta["status"] == "ACCEPTED"
    assert meta["baseline_invalidated"]["active"] is True
    assert baseline_status(approved).json["data"]["valid"] is False


def test_second_cr_does_not_duplicate_downstream_annotation(approved: Path) -> None:
    report = approved / "07-acceptance/resume/PRD-001-batch-parse/report.md"
    write(report, "# 验收报告\n\n- 结论：通过\n")
    cr_create(approved, reason="范围调整")

    second = cr_create(approved, reason="验收标准补充")

    # 第二个 CR 时 PRD 已在 IN_REVIEW 且基线已失效：不重复追加注解，只登记新 CR
    assert second.json["data"]["invalidated"] is False
    text = read(report)
    assert text.count("基线失效") == 1, text
    listed = run_cli(approved, "prd", "cr", "list", "--id", "PRD-001", "--status", "OPEN")
    assert [item["id"] for item in listed.json["data"]["crs"]] == ["CR-001", "CR-002"]
    assert baseline_status(approved).json["data"]["invalidated_by"]["cr"] == "CR-001"


def test_cr_does_not_create_missing_spec_or_acceptance_dirs(approved: Path) -> None:
    assert not (approved / "06-specs/resume").exists()

    cr_create(approved)

    assert not (approved / "06-specs/resume/PRD-001-batch-parse").exists()
    assert not (approved / "07-acceptance/resume/PRD-001-batch-parse").exists()


def test_existing_acceptance_report_is_annotated_not_rewritten(approved: Path) -> None:
    report = approved / "07-acceptance/resume/PRD-001-batch-parse/report.md"
    original = "# 验收报告：PRD-001\n\n- 验收结论：通过\n"
    write(report, original)

    cr_create(approved)

    text = read(report)
    assert text.startswith(original.rstrip("\n"))
    assert "CR-001" in text
    assert "基线失效" in text


def test_existing_acceptance_conclusion_is_marked_superseded(approved: Path) -> None:
    # 验收结论字段由 ticket 07 写入；这里直接构造既有结论以验证 CR 的失效标记。
    old_hash = prd_meta(approved)["content_hash"]
    patch_meta(approved, acceptance={
        "version": 1, "content_hash": old_hash, "accepted_by": "alice",
        "accepted_at": "2026-03-02T10:00:00+08:00", "result": "accepted",
    })

    cr_create(approved)

    meta = prd_meta(approved)
    acceptance = meta["acceptance"]
    assert acceptance["result"] == "accepted"
    assert acceptance["content_hash"] == old_hash
    assert acceptance["superseded_by_cr"] == "CR-001"
    assert acceptance["superseded_by_version"] == 1

    status = baseline_status(approved)
    evidence = status.json["data"]["superseded_evidence"]
    assert any(item["cr"] == "CR-001" for item in evidence)


def test_cr_invalidation_keeps_old_acceptance_queryable(approved: Path) -> None:
    old_hash = prd_meta(approved)["content_hash"]
    patch_meta(approved, acceptance={
        "version": 1, "content_hash": old_hash, "accepted_by": "alice",
        "accepted_at": "2026-03-02T10:00:00+08:00", "result": "accepted",
    })

    cr_create(approved)
    shown = run_cli(approved, "prd", "show", "--id", "PRD-001")

    assert shown.code == 0, shown
    body = shown.json["data"]["prd"]
    assert body["status"] == "IN_REVIEW"
    assert body["version"] == 1
    # 旧批准记录仍可查询
    assert body["approved_by"] == "alice"
    assert any(item["decision"] == "approved" for item in body["confirmations"])


# ------------------------------------------------------------------ 重新批准
def test_cr_approve_bumps_version_and_new_hash(approved: Path) -> None:
    hash_before = prd_meta(approved)["content_hash"]
    with_cr(approved)
    new_body = prd_text(approved).replace(
        "- 本期做：批量上传、异步解析、结果清单导出。",
        "- 本期做：批量上传、异步解析、结果清单导出、CSV 导出。",
    )
    edited = run_cli(approved, "prd", "update", "--id", "PRD-001", "--body-file",
                     str(write(approved / "prd-v2.md", new_body)), env=ENV)
    assert edited.code == 0, edited

    result = run_cli(approved, "prd", "cr", "approve", "--id", "PRD-001", "--cr", "CR-001",
                     "--actor", "alice", "--confirm", env=ENV)

    assert result.code == 0, result
    meta = prd_meta(approved)
    assert meta["version"] == 2
    assert meta["status"] == "APPROVED"
    assert meta["content_hash"] != hash_before
    assert meta["approved_by"] == "alice"
    assert meta["baseline_invalidated"]["active"] is False
    assert meta["baseline_invalidated"]["resolved_cr"] == "CR-001"

    cr = result.json["data"]["cr"]
    assert cr["status"] == "APPROVED"
    assert cr["version_after"] == 2
    assert cr["approved_by"] == "alice"
    assert cr["approved_at"]

    status = baseline_status(approved)
    assert status.code == 0, status
    assert status.json["data"]["valid"] is True
    assert status.json["data"]["current"] == meta["content_hash"]

    verified = run_cli(approved, "prd", "verify-hash", "--id", "PRD-001")
    assert verified.code == 0, verified


def test_cr_approve_keeps_old_approval_and_history(approved: Path) -> None:
    old_hash = prd_meta(approved)["content_hash"]
    with_cr(approved)
    run_cli(approved, "prd", "cr", "approve", "--id", "PRD-001", "--cr", "CR-001",
            "--actor", "alice", "--confirm", env=ENV)

    shown = run_cli(approved, "prd", "show", "--id", "PRD-001")

    assert shown.code == 0, shown
    prd = shown.json["data"]["prd"]
    approvals = prd["approvals"]
    assert [item["version"] for item in approvals] == [1, 2]
    assert approvals[0]["content_hash"] == old_hash
    assert approvals[0]["superseded_by_cr"] == "CR-001"
    assert approvals[1]["content_hash"] == prd["content_hash"]
    transitions = [(item["from"], item["to"]) for item in prd["history"]]
    assert ("APPROVED", "IN_REVIEW") in transitions
    assert ("IN_REVIEW", "APPROVED") in transitions
    assert sum(1 for item in prd["confirmations"] if item["decision"] == "approved") == 2


def test_cr_approve_blocked_by_validation_errors(approved: Path) -> None:
    with_cr(approved)
    broken = prd_text(approved).replace("## 数据需求", "## 数据")
    write(approved / "prd-broken.md", broken)
    edited = run_cli(approved, "prd", "update", "--id", "PRD-001", "--body-file",
                     str(approved / "prd-broken.md"), env=ENV)
    assert edited.code == 0, edited
    before = read(meta_path(approved))

    result = run_cli(approved, "prd", "cr", "approve", "--id", "PRD-001", "--cr", "CR-001",
                     "--actor", "alice", "--confirm", env=ENV)

    assert result.code == 1
    assert error_codes(result) == {"VALIDATION_FAILED"}
    assert read(meta_path(approved)) == before
    assert cr_front_matter(cr_file(approved))["status"] == "OPEN"
    assert prd_meta(approved)["status"] == "IN_REVIEW"


def test_cr_approve_requires_requirement_owner(approved: Path) -> None:
    with_cr(approved)
    before = read(meta_path(approved))

    result = run_cli(approved, "prd", "cr", "approve", "--id", "PRD-001", "--cr", "CR-001",
                     "--actor", "bob", "--confirm", env=ENV)

    assert result.code == 1
    assert error_codes(result) == {"ROLE_MISMATCH"}
    assert read(meta_path(approved)) == before


def test_cr_approve_requires_confirmation(approved: Path) -> None:
    with_cr(approved)

    result = run_cli(approved, "prd", "cr", "approve", "--id", "PRD-001", "--cr", "CR-001",
                     env=ENV)

    assert result.code == 1
    assert error_codes(result) == {"CONFIRMATION_REQUIRED"}


def test_cr_cannot_be_approved_twice(approved: Path) -> None:
    with_cr(approved)
    run_cli(approved, "prd", "cr", "approve", "--id", "PRD-001", "--cr", "CR-001",
            "--actor", "alice", "--confirm", env=ENV)
    version_after_first = prd_meta(approved)["version"]

    again = run_cli(approved, "prd", "cr", "approve", "--id", "PRD-001", "--cr", "CR-001",
                    "--actor", "alice", "--confirm", env=ENV)

    assert again.code == 1
    assert error_codes(again) == {"CR_NOT_OPEN"}
    assert prd_meta(approved)["version"] == version_after_first


def test_cr_approve_from_draft_is_invalid_transition(inited_project: Path) -> None:
    planned_req(inited_project, title="简历批量解析", slug="batch-parse")
    create_prd(inited_project, scenarios="SC-RES-01")
    update_body(inited_project, VALID_PRD)
    cr_create(inited_project)
    before = read(meta_path(inited_project))

    result = run_cli(inited_project, "prd", "cr", "approve", "--id", "PRD-001",
                     "--cr", "CR-001", "--actor", "alice", "--confirm", env=ENV)

    assert result.code == 1
    assert error_codes(result) == {"INVALID_TRANSITION"}
    assert read(meta_path(inited_project)) == before


def test_plain_approve_is_blocked_while_cr_is_open(approved: Path) -> None:
    with_cr(approved)
    before = read(meta_path(approved))

    result = run_cli(approved, "prd", "approve", "--id", "PRD-001", "--actor", "alice",
                     "--confirm", env=ENV)

    assert result.code == 1
    assert error_codes(result) == {"CR_OPEN"}
    assert read(meta_path(approved)) == before


# ------------------------------------------------------------------ CR 拒绝
def test_cr_reject_restores_baseline_when_content_unchanged(approved: Path) -> None:
    hash_before = prd_meta(approved)["content_hash"]
    with_cr(approved)

    result = run_cli(approved, "prd", "cr", "reject", "--id", "PRD-001", "--cr", "CR-001",
                     "--actor", "alice", "--confirm", "--reason", "本期不做导出", env=ENV)

    assert result.code == 0, result
    assert result.json["data"]["cr"]["status"] == "REJECTED"
    assert result.json["data"]["cr"]["rejected_by"] == "alice"
    meta = prd_meta(approved)
    assert meta["status"] == "APPROVED"
    assert meta["content_hash"] == hash_before
    assert meta["baseline_invalidated"]["active"] is False
    assert meta["baseline_invalidated"]["resolved_cr"] == "CR-001"

    status = baseline_status(approved)
    assert status.code == 0, status
    assert status.json["data"]["valid"] is True


def test_cr_reject_requires_reason(approved: Path) -> None:
    with_cr(approved)
    before = read(meta_path(approved))

    result = run_cli(approved, "prd", "cr", "reject", "--id", "PRD-001", "--cr", "CR-001",
                     "--actor", "alice", "--confirm", env=ENV)

    assert result.code == 1
    assert error_codes(result) == {"REASON_REQUIRED"}
    assert read(meta_path(approved)) == before


def test_cr_reject_keeps_baseline_invalid_when_content_changed(approved: Path) -> None:
    with_cr(approved)
    edited = prd_text(approved).replace("5 分钟内", "15 分钟内")
    write(approved / "prd-edit.md", edited)
    assert run_cli(approved, "prd", "update", "--id", "PRD-001", "--body-file",
                   str(approved / "prd-edit.md"), env=ENV).code == 0

    result = run_cli(approved, "prd", "cr", "reject", "--id", "PRD-001", "--cr", "CR-001",
                     "--actor", "alice", "--confirm", "--reason", "先不改", env=ENV)

    assert result.code == 0, result
    assert prd_meta(approved)["status"] == "IN_REVIEW"
    assert baseline_status(approved).json["data"]["valid"] is False


def test_rejected_cr_with_changed_content_needs_a_new_cr(approved: Path) -> None:
    with_cr(approved)
    edited = prd_text(approved).replace("5 分钟内", "20 分钟内")
    write(approved / "prd-edit.md", edited)
    assert run_cli(approved, "prd", "update", "--id", "PRD-001", "--body-file",
                   str(approved / "prd-edit.md"), env=ENV).code == 0
    rejected = run_cli(approved, "prd", "cr", "reject", "--id", "PRD-001", "--cr", "CR-001",
                       "--actor", "alice", "--confirm", "--reason", "先不改", env=ENV)
    assert rejected.code == 0, rejected

    # 正文已偏离批准基线：普通批准不行，必须由新的 CR 承载这次改动
    plain = run_cli(approved, "prd", "approve", "--id", "PRD-001", "--actor", "alice",
                    "--confirm", env=ENV)
    assert plain.code == 1
    assert error_codes(plain) == {"BASELINE_INVALIDATED"}

    second = cr_create(approved, reason="确认把时延改成 20 分钟")
    assert second.json["data"]["cr"]["id"] == "CR-002"
    approved_again = run_cli(approved, "prd", "cr", "approve", "--id", "PRD-001",
                             "--cr", "CR-002", "--actor", "alice", "--confirm", env=ENV)

    assert approved_again.code == 0, approved_again
    assert prd_meta(approved)["version"] == 2
    assert baseline_status(approved).json["data"]["valid"] is True


def test_cr_marker_keeps_dev_baseline_fields(approved: Path) -> None:
    # ticket 05 的 `dev start` 会写 .baseline.yaml（baseline_hash/baseline_version/...）；
    # CR 打失效标记时必须保留这些字段，否则 05 无法再判断「Spec 基线是否陈旧」。
    spec_dir = approved / "06-specs/resume/PRD-001-batch-parse"
    existing = {
        "prd": "PRD-001", "module": "resume",
        "spec_dir": "06-specs/resume/PRD-001-batch-parse",
        "baseline_hash": prd_meta(approved)["content_hash"],
        "baseline_version": 1, "created_at": "2026-03-02T09:00:00+08:00",
        "baseline_valid": True,
    }
    write(spec_dir / ".baseline.yaml", yaml.safe_dump(existing, sort_keys=False,
                                                     allow_unicode=True))

    cr_create(approved)

    marker = yaml.safe_load(read(spec_dir / ".baseline.yaml"))
    assert marker["baseline_valid"] is False
    assert marker["invalidated_by"] == "CR-001"
    for key, value in existing.items():
        if key == "baseline_valid":
            continue
        assert marker[key] == value, key


# ------------------------------------------------------------------ 查询
def test_cr_list_and_show(approved: Path) -> None:
    cr_create(approved, reason="范围调整")
    cr_create(approved, reason="验收标准补充")

    listed = run_cli(approved, "prd", "cr", "list", "--id", "PRD-001")
    shown = run_cli(approved, "prd", "cr", "show", "--id", "PRD-001", "--cr", "CR-002")

    assert listed.code == 0, listed
    assert [item["id"] for item in listed.json["data"]["crs"]] == ["CR-001", "CR-002"]
    assert listed.json["data"]["count"] == 2
    assert shown.code == 0, shown
    assert shown.json["data"]["cr"]["id"] == "CR-002"
    assert "变更原因" in shown.json["data"]["body"]


def test_cr_show_unknown_cr_is_rejected(approved: Path) -> None:
    result = run_cli(approved, "prd", "cr", "show", "--id", "PRD-001", "--cr", "CR-404")

    assert result.code == 1
    assert error_codes(result) == {"CR_NOT_FOUND"}


def test_cr_without_subcommand_is_usage_error(approved: Path) -> None:
    result = run_cli(approved, "prd", "cr", "--id", "PRD-001")

    assert result.code == 2


# ------------------------------------------------------------------ 追加记录不计入 hash
def test_appending_records_does_not_change_body_hash(approved: Path) -> None:
    with_cr(approved)
    run_cli(approved, "prd", "cr", "approve", "--id", "PRD-001", "--cr", "CR-001",
            "--actor", "alice", "--confirm", env=ENV)
    hash_after = prd_meta(approved)["content_hash"]

    write(prd_dir(approved) / "reviews/2026-03-06-extra-review.md", "# 追加评审\n\n再确认一次。\n")
    write(prd_dir(approved) / "changes/CR-009-manual-note.md",
          "# CR-009 追加记录\n\n只追加。\n")

    status = baseline_status(approved)
    verified = run_cli(approved, "prd", "verify-hash", "--id", "PRD-001")

    assert status.code == 0, status
    assert status.json["data"]["current"] == hash_after
    assert verified.code == 0, verified
    assert verified.json["data"]["actual"] == hash_after


def test_new_cr_keeps_body_hash_but_invalidates_baseline(approved: Path) -> None:
    with_cr(approved)
    run_cli(approved, "prd", "cr", "approve", "--id", "PRD-001", "--cr", "CR-001",
            "--actor", "alice", "--confirm", env=ENV)
    hash_after = prd_meta(approved)["content_hash"]

    cr_create(approved, reason="补充一条说明")

    status = baseline_status(approved)
    verified = run_cli(approved, "prd", "verify-hash", "--id", "PRD-001")

    # CR 文件与 history 不参与正文 hash；失效只由 metadata 的标记驱动
    assert status.json["data"]["current"] == hash_after
    assert status.json["data"]["reasons"] == ["BASELINE_INVALIDATED"]
    assert verified.code == 1
    assert error_codes(verified) == {"BASELINE_INVALIDATED"}
    assert verified.json["data"]["actual"] == hash_after


def test_cr_create_on_unknown_prd_is_rejected(inited_project: Path) -> None:
    result = run_cli(inited_project, "prd", "cr", "create", "--id", "PRD-404", "--actor",
                     "alice", "--confirm", "--reason", CR_REASON, env=ENV)

    assert result.code == 1
    assert error_codes(result) == {"PRD_NOT_FOUND"}


def test_cr_create_lock_conflict_leaves_no_trace(approved: Path) -> None:
    before = read(meta_path(approved))
    write(approved / ".neo-skill/locks/PRD-001.lock",
          '{"object": "PRD-001", "pid": 999999, "created_at": "2026-03-01T09:00:00+08:00",'
          ' "command": "prd.cr.create", "rules_version": 1}')

    result = cr_create(approved, expect=1)

    assert error_codes(result) == {"LOCK_CONFLICT"}
    assert read(meta_path(approved)) == before
    assert cr_files(approved) == []


def test_failed_cr_approve_leaves_no_lock(approved: Path) -> None:
    with_cr(approved)
    broken = prd_text(approved).replace("## 依赖与风险", "## 依赖")
    write(approved / "prd-broken2.md", broken)
    run_cli(approved, "prd", "update", "--id", "PRD-001", "--body-file",
            str(approved / "prd-broken2.md"), env=ENV)

    run_cli(approved, "prd", "cr", "approve", "--id", "PRD-001", "--cr", "CR-001",
            "--actor", "alice", "--confirm", env=ENV)

    assert not list((approved / ".neo-skill/locks").glob("*.lock"))
    assert not [p for p in prd_dir(approved).rglob("*.tmp")]


def test_cr_lifecycle_leaves_no_lock_behind(approved: Path) -> None:
    with_cr(approved)
    run_cli(approved, "prd", "cr", "approve", "--id", "PRD-001", "--cr", "CR-001",
            "--actor", "alice", "--confirm", env=ENV)

    assert not list((approved / ".neo-skill/locks").glob("*.lock"))
    assert not [p for p in prd_dir(approved).rglob("*.tmp")]
