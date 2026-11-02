"""ticket 03：PRD 基线 hash 的确定性与稳定性，以及失败零副作用。

hash 契约见共享设计 §6：`prd.md` + 存在的 `test-cases.md`（按相对路径 + UTF-8 字节）
+ `locked_fields` 指定的 metadata 字段；`reviews/`、`changes/`、`history`、
`confirmations`、`status`、`content_hash` 不参与。
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


def error_codes(result) -> set[str]:
    return {item["code"] for item in (result.json or {}).get("errors", [])}


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


# ------------------------------------------------------------------ hash 生成与稳定性
def test_approval_records_deterministic_hash(approved: Path) -> None:
    first = prd_meta(approved)["content_hash"]

    verified = run_cli(approved, "prd", "verify-hash", "--id", "PRD-001")

    assert verified.code == 0, verified
    assert verified.json["success"] is True
    assert verified.json["data"]["expected"] == first
    assert verified.json["data"]["actual"] == first
    assert verified.json["data"]["match"] is True


def test_hash_ignores_reviews_and_history_and_status(approved: Path) -> None:
    before = prd_meta(approved)["content_hash"]

    write(prd_dir(approved) / "reviews/2026-03-05-extra-review.md", "# 追加评审\n\n又看了一遍。\n")
    write(prd_dir(approved) / "changes/CR-001-scope.md", "# CR-001 范围调整\n\n追加记录。\n")

    verified = run_cli(approved, "prd", "verify-hash", "--id", "PRD-001")

    assert verified.code == 0, verified
    assert verified.json["data"]["actual"] == before


def test_repeated_verify_hash_is_stable(approved: Path) -> None:
    first = run_cli(approved, "prd", "verify-hash", "--id", "PRD-001")
    second = run_cli(approved, "prd", "verify-hash", "--id", "PRD-001")

    assert first.json["data"]["expected"] == second.json["data"]["expected"] == \
        first.json["data"]["actual"] == second.json["data"]["actual"]


def test_hash_changes_when_body_changes(approved: Path) -> None:
    expected = prd_meta(approved)["content_hash"]
    body = prd_text(approved)
    write(prd_dir(approved) / "prd.md", body.replace("5 分钟内", "10 分钟内"))

    verified = run_cli(approved, "prd", "verify-hash", "--id", "PRD-001")

    assert verified.code == 1
    assert error_codes(verified) == {"HASH_MISMATCH"}
    assert verified.json["data"]["expected"] == expected
    assert verified.json["data"]["actual"] != expected
    assert verified.json["data"]["match"] is False


def test_hash_changes_when_locked_field_changes(approved: Path) -> None:
    expected = prd_meta(approved)["content_hash"]
    path = prd_dir(approved) / "metadata.yaml"
    meta = yaml.safe_load(read(path))
    meta["release"] = "v0.2.0"
    write(path, yaml.safe_dump(meta, sort_keys=False, allow_unicode=True))

    verified = run_cli(approved, "prd", "verify-hash", "--id", "PRD-001")

    assert verified.code == 1
    assert error_codes(verified) == {"HASH_MISMATCH"}
    assert verified.json["data"]["actual"] != expected


def test_hash_changes_when_test_cases_added(approved: Path) -> None:
    expected = prd_meta(approved)["content_hash"]

    write(prd_dir(approved) / "test-cases.md", "# 测试用例\n\n- 正常：批量上传 100 份。\n")

    verified = run_cli(approved, "prd", "verify-hash", "--id", "PRD-001")

    assert verified.code == 1
    assert error_codes(verified) == {"HASH_MISMATCH"}
    assert verified.json["data"]["actual"] != expected


def test_hash_unaffected_by_non_locked_metadata_fields(approved: Path) -> None:
    expected = prd_meta(approved)["content_hash"]
    path = prd_dir(approved) / "metadata.yaml"
    meta = yaml.safe_load(read(path))
    meta["notes"] = "补充说明"
    meta["approval_note"] = "口头也确认过了"
    write(path, yaml.safe_dump(meta, sort_keys=False, allow_unicode=True))

    verified = run_cli(approved, "prd", "verify-hash", "--id", "PRD-001")

    assert verified.code == 0, verified
    assert verified.json["data"]["actual"] == expected


def test_verify_hash_requires_existing_prd(inited_project: Path) -> None:
    result = run_cli(inited_project, "prd", "verify-hash", "--id", "PRD-404")

    assert result.code == 1
    assert error_codes(result) == {"PRD_NOT_FOUND"}


def test_verify_hash_before_approval_reports_missing_baseline(inited_project: Path) -> None:
    planned_req(inited_project, title="简历批量解析", slug="batch-parse")
    create_prd(inited_project, scenarios="SC-RES-01")

    result = run_cli(inited_project, "prd", "verify-hash", "--id", "PRD-001")

    assert result.code == 1
    assert error_codes(result) == {"BASELINE_MISSING"}


# ------------------------------------------------------------------ 失败零副作用
def test_failed_approve_leaves_no_lock_and_no_partial_file(inited_project: Path) -> None:
    planned_req(inited_project, title="简历批量解析", slug="batch-parse")
    create_prd(inited_project, scenarios="SC-RES-01")
    run_cli(inited_project, "prd", "submit-review", "--id", "PRD-001", "--actor", "bob",
            "--confirm", env=ENV)
    before = read(prd_dir(inited_project) / "metadata.yaml")
    reviews_before = sorted((prd_dir(inited_project) / "reviews").glob("*.md"))

    result = run_cli(inited_project, "prd", "approve", "--id", "PRD-001", "--actor", "alice",
                     "--confirm", env=ENV)

    assert result.code == 1
    assert read(prd_dir(inited_project) / "metadata.yaml") == before
    assert sorted((prd_dir(inited_project) / "reviews").glob("*.md")) == reviews_before
    assert not list((inited_project / ".neo-skill/locks").glob("*.lock"))
    assert not [p for p in prd_dir(inited_project).rglob("*.tmp")]


def test_lock_conflict_does_not_change_state(inited_project: Path) -> None:
    planned_req(inited_project, title="简历批量解析", slug="batch-parse")
    create_prd(inited_project, scenarios="SC-RES-01")
    write(inited_project / ".neo-skill/locks/PRD-001.lock",
          '{"object": "PRD-001", "pid": 999999, "created_at": "2026-03-01T09:00:00+08:00",'
          ' "command": "prd.approve", "rules_version": 1}')
    before = read(prd_dir(inited_project) / "metadata.yaml")

    result = run_cli(inited_project, "prd", "submit-review", "--id", "PRD-001",
                     "--actor", "bob", "--confirm", env=ENV)

    assert result.code == 1
    assert error_codes(result) == {"LOCK_CONFLICT"}
    assert read(prd_dir(inited_project) / "metadata.yaml") == before


def test_create_lock_conflict_does_not_consume_prd_id(inited_project: Path) -> None:
    planned_req(inited_project, title="简历批量解析", slug="batch-parse")
    counters_before = yaml.safe_load(read(inited_project / ".neo-skill/counters.yaml"))
    write(inited_project / ".neo-skill/locks/REQ-001.lock",
          '{"object": "REQ-001", "pid": 999999, "created_at": "2026-03-01T09:00:00+08:00",'
          ' "command": "req.plan", "rules_version": 1}')

    result = run_cli(inited_project, "prd", "create", "--req", "REQ-001",
                     "--scenarios", "SC-RES-01")

    assert result.code == 1
    assert error_codes(result) == {"LOCK_CONFLICT"}
    assert yaml.safe_load(read(inited_project / ".neo-skill/counters.yaml")) == counters_before
    assert not (inited_project / "04-prds/resume").exists()
