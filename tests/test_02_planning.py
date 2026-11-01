"""ticket 02：共识确认与人工排期（`req confirm` / `req plan` / `req unplan` / 旁支）。

全部走 `run_cli` 黑盒边界。人工决策命令的顺序按共享设计 §2.3：
解析对象 → 团队/身份/职责 → 状态跳转 → 锁 → 追加记录 → 原子写 → 释放锁。
任何一步失败都不改变 metadata、不留下锁文件。
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

ITEM = "03-requirements/items/REQ-001-batch-parse-resume.md"

COMPLETE_BODY = """# REQ-001 batch parse resume

## 背景

客户每月人工解析 2000 份简历，招聘旺季堆积。

## 用户

招聘专员。

## 问题

单份上传耗时，旺季需要 3 人全职处理。

## 场景

1. 专员一次上传 100 份简历；
2. 系统异步解析并给出结果清单。

## 期望结果

单批 100 份在 5 分钟内完成解析。

## 来源

客户访谈 2026-02。
"""


def front_matter(path: Path) -> dict:
    head = read(path).split("---\n", 2)[1]
    data = yaml.safe_load(head)
    assert isinstance(data, dict)
    return data


def write_item(project: Path, *, status: str = "NEW", extra: dict | None = None) -> Path:
    """直接构造需求文件（测试目的：绕过 CLI 制造违规/后续状态）。"""
    meta = {
        "id": "REQ-001",
        "slug": "batch-parse-resume",
        "title": "batch parse resume",
        "module": "resume",
        "category": "new-feature",
        "source": "客户访谈",
        "status": status,
        "release": None,
        "prd": None,
        "priority": None,
        "related": [],
        "grilling": {
            "frontier": [],
            "assumptions": [],
            "open_questions": [],
            "consensus": "支持一次上传多份并异步解析",
            "consensus_confirmed": status != "NEW",
        },
        "history": [],
        "confirmations": [],
    }
    meta.update(extra or {})
    text = "---\n" + yaml.safe_dump(meta, sort_keys=False, allow_unicode=True) + "---\n\n" + COMPLETE_BODY
    path = project / ITEM
    write(path, text)
    return path


@pytest.fixture()
def ready(inited_project: Path) -> Path:
    write(inited_project / "00-management/team.md", TEAM_CONFIRMED)
    return inited_project


@pytest.fixture()
def triaged(ready: Path) -> Path:
    write_item(ready, status="TRIAGED")
    return ready


# ------------------------------------------------------------------ 共识确认
def test_confirm_moves_new_to_triaged_and_records_decision(ready: Path) -> None:
    write_item(ready, status="NEW")

    result = run_cli(ready, "req", "confirm", "--id", "REQ-001", "--actor", "bob",
                     "--confirm", env=ENV)

    assert result.code == 0, result
    assert result.json["command"] == "req.confirm"
    meta = front_matter(ready / ITEM)
    assert meta["status"] == "TRIAGED"
    assert meta["grilling"]["consensus_confirmed"] is True
    assert len(meta["history"]) == 1
    entry = meta["history"][0]
    assert entry["from"] == "NEW"
    assert entry["to"] == "TRIAGED"
    assert entry["by"] == "bob"
    assert entry["action"] == "req.confirm"
    assert entry["at"] == NOW
    assert entry["rules_version"] == 1
    assert len(meta["confirmations"]) == 1
    confirmation = meta["confirmations"][0]
    assert confirmation["by"] == "bob"
    assert confirmation["object"] == "REQ-001"
    assert confirmation["at"] == NOW
    assert confirmation["decision"] == "approved"
    assert confirmation["role"] == "stakeholder"


def test_confirm_asks_whether_to_schedule(ready: Path) -> None:
    write_item(ready, status="NEW")

    result = run_cli(ready, "req", "confirm", "--id", "REQ-001", "--actor", "bob", "--confirm")

    assert result.code == 0, result
    prompt = result.json["data"]["prompt"]
    assert "是否设置 batch parse resume 需求排期" in prompt
    joined = " ".join(result.json["next_actions"])
    assert "req plan --id REQ-001 --actor alice --confirm" in joined
    assert "--release" in joined


def test_confirm_without_confirmation_keeps_new(ready: Path) -> None:
    write_item(ready, status="NEW")
    before = read(ready / ITEM)

    missing_flag = run_cli(ready, "req", "confirm", "--id", "REQ-001", "--actor", "bob")
    missing_actor = run_cli(ready, "req", "confirm", "--id", "REQ-001", "--confirm")

    assert missing_flag.code == 1
    assert missing_flag.error_codes() == ["CONFIRMATION_REQUIRED"]
    assert missing_actor.code == 1
    assert missing_actor.error_codes() == ["CONFIRMATION_REQUIRED"]
    assert read(ready / ITEM) == before


def test_confirm_requires_stakeholder_role(ready: Path) -> None:
    write_item(ready, status="NEW")

    result = run_cli(ready, "req", "confirm", "--id", "REQ-001", "--actor", "alice", "--confirm")

    assert result.code == 1
    assert result.error_codes() == ["ROLE_MISMATCH"]
    assert front_matter(ready / ITEM)["status"] == "NEW"


def test_confirm_with_unknown_actor_changes_nothing(ready: Path) -> None:
    write_item(ready, status="NEW")
    before = read(ready / ITEM)

    result = run_cli(ready, "req", "confirm", "--id", "REQ-001", "--actor", "dave", "--confirm")

    assert result.code == 1
    assert result.error_codes() == ["ACTOR_UNKNOWN"]
    assert read(ready / ITEM) == before


@pytest.mark.parametrize("team", [None, TEAM_UNCONFIRMED])
def test_confirm_requires_configured_team(ready: Path, team: str | None) -> None:
    write_item(ready, status="NEW")
    if team is None:
        (ready / "00-management/team.md").unlink()
    else:
        write(ready / "00-management/team.md", team)

    result = run_cli(ready, "req", "confirm", "--id", "REQ-001", "--actor", "bob", "--confirm")

    assert result.code == 1
    assert result.error_codes() == ["TEAM_NOT_CONFIGURED"]
    assert front_matter(ready / ITEM)["status"] == "NEW"


def test_confirm_on_triaged_requirement_is_invalid_transition(triaged: Path) -> None:
    result = run_cli(triaged, "req", "confirm", "--id", "REQ-001", "--actor", "bob", "--confirm")

    assert result.code == 1
    assert result.error_codes() == ["INVALID_TRANSITION"]
    assert front_matter(triaged / ITEM)["status"] == "TRIAGED"


# ------------------------------------------------------------------ 排期询问
def test_triage_prompt_is_read_only_and_gives_exact_commands(triaged: Path) -> None:
    before = read(triaged / ITEM)

    result = run_cli(triaged, "req", "triage-prompt", "--id", "REQ-001")

    assert result.code == 0, result
    assert "是否设置 batch parse resume 需求排期" in result.json["data"]["prompt"]
    assert result.json["data"]["status"] == "TRIAGED"
    actions = result.json["next_actions"]
    assert any("req plan --id REQ-001 --actor alice --confirm --release" in a for a in actions)
    assert any("--decline" in a for a in actions)
    assert result.json["artifacts"] == []
    assert read(triaged / ITEM) == before


def test_triage_prompt_on_planned_requirement_reports_no_scheduling_question(
    triaged: Path,
) -> None:
    run_cli(triaged, "req", "plan", "--id", "REQ-001", "--actor", "alice", "--confirm",
            "--release", "v0.1.0")

    result = run_cli(triaged, "req", "triage-prompt", "--id", "REQ-001")

    assert result.code == 0, result
    assert result.json["data"]["prompt"] is None
    assert result.json["data"]["status"] == "PLANNED"


# ------------------------------------------------------------------ 合法排期
def test_plan_moves_triaged_to_planned_with_release(triaged: Path) -> None:
    result = run_cli(triaged, "req", "plan", "--id", "REQ-001", "--actor", "alice",
                     "--confirm", "--release", "v0.1.0", env=ENV)

    assert result.code == 0, result
    assert result.json["command"] == "req.plan"
    meta = front_matter(triaged / ITEM)
    assert meta["status"] == "PLANNED"
    assert meta["release"] == "v0.1.0"
    assert meta["planned_by"] == "alice"
    assert meta["planned_at"] == NOW
    history = meta["history"][-1]
    assert (history["from"], history["to"]) == ("TRIAGED", "PLANNED")
    assert history["by"] == "alice"
    assert history["action"] == "req.plan"
    assert history["at"] == NOW
    confirmation = meta["confirmations"][-1]
    assert confirmation["by"] == "alice"
    assert confirmation["object"] == "REQ-001"
    assert confirmation["decision"] == "approved"
    assert confirmation["role"] == "requirement_owner"
    assert confirmation["at"] == NOW
    assert confirmation["release"] == "v0.1.0"


def test_plan_keeps_history_append_only(triaged: Path) -> None:
    run_cli(triaged, "req", "plan", "--id", "REQ-001", "--actor", "alice", "--confirm",
            "--release", "v0.1.0")
    first = front_matter(triaged / ITEM)["history"]

    run_cli(triaged, "req", "unplan", "--id", "REQ-001", "--actor", "alice", "--confirm",
            "--reason", "版本改到 v0.2.0")
    run_cli(triaged, "req", "plan", "--id", "REQ-001", "--actor", "alice", "--confirm",
            "--release", "v0.2.0")

    meta = front_matter(triaged / ITEM)
    assert meta["release"] == "v0.2.0"
    assert meta["history"][: len(first)] == first
    assert [(h["from"], h["to"]) for h in meta["history"]] == [
        ("TRIAGED", "PLANNED"),
        ("PLANNED", "TRIAGED"),
        ("TRIAGED", "PLANNED"),
    ]
    assert meta["history"][1]["reason"] == "版本改到 v0.2.0"


# ------------------------------------------------------------------ 拒绝排期
def test_plan_declined_keeps_triaged_and_records_reason(triaged: Path) -> None:
    result = run_cli(triaged, "req", "plan", "--id", "REQ-001", "--actor", "alice",
                     "--confirm", "--decline", "--reason", "本季度资源不足", env=ENV)

    assert result.code == 0, result
    meta = front_matter(triaged / ITEM)
    assert meta["status"] == "TRIAGED"
    assert meta["release"] is None
    assert meta["history"] == []
    confirmation = meta["confirmations"][-1]
    assert confirmation["decision"] == "declined"
    assert confirmation["by"] == "alice"
    assert confirmation["reason"] == "本季度资源不足"
    assert confirmation["at"] == NOW
    assert "TRIAGED" in " ".join(result.json["next_actions"])


def test_plan_decline_still_requires_role_and_confirmation(triaged: Path) -> None:
    no_role = run_cli(triaged, "req", "plan", "--id", "REQ-001", "--actor", "bob",
                      "--confirm", "--decline")
    no_flag = run_cli(triaged, "req", "plan", "--id", "REQ-001", "--actor", "alice", "--decline")

    assert no_role.error_codes() == ["ROLE_MISMATCH"]
    assert no_flag.error_codes() == ["CONFIRMATION_REQUIRED"]
    assert front_matter(triaged / ITEM)["confirmations"] == []


# ------------------------------------------------------------------ 缺版本
def test_plan_without_release_keeps_triaged_and_asks_again(triaged: Path) -> None:
    before = read(triaged / ITEM)

    result = run_cli(triaged, "req", "plan", "--id", "REQ-001", "--actor", "alice", "--confirm")

    assert result.code == 1
    assert result.error_codes() == ["RELEASE_REQUIRED"]
    assert read(triaged / ITEM) == before
    assert any("--release" in action for action in result.json["next_actions"])


def test_plan_rejects_malformed_release(triaged: Path) -> None:
    result = run_cli(triaged, "req", "plan", "--id", "REQ-001", "--actor", "alice",
                     "--confirm", "--release", "下一版")

    assert result.code == 1
    assert result.error_codes() == ["INVALID_RELEASE"]
    assert front_matter(triaged / ITEM)["status"] == "TRIAGED"


# ------------------------------------------------------------------ 身份与状态门禁
def test_plan_requires_requirement_owner_role(triaged: Path) -> None:
    result = run_cli(triaged, "req", "plan", "--id", "REQ-001", "--actor", "bob",
                     "--confirm", "--release", "v0.1.0")

    assert result.code == 1
    assert result.error_codes() == ["ROLE_MISMATCH"]
    assert front_matter(triaged / ITEM)["status"] == "TRIAGED"


def test_plan_with_unknown_actor_or_team_changes_nothing(triaged: Path) -> None:
    unknown = run_cli(triaged, "req", "plan", "--id", "REQ-001", "--actor", "dave",
                      "--confirm", "--release", "v0.1.0")
    (triaged / "00-management/team.md").unlink()
    no_team = run_cli(triaged, "req", "plan", "--id", "REQ-001", "--actor", "alice",
                      "--confirm", "--release", "v0.1.0")

    assert unknown.error_codes() == ["ACTOR_UNKNOWN"]
    assert no_team.error_codes() == ["TEAM_NOT_CONFIGURED"]
    assert front_matter(triaged / ITEM)["status"] == "TRIAGED"


@pytest.mark.parametrize("status", ["NEW", "DONE", "DEFERRED", "REJECTED"])
def test_plan_from_wrong_state_is_invalid_transition(ready: Path, status: str) -> None:
    write_item(ready, status=status)

    result = run_cli(ready, "req", "plan", "--id", "REQ-001", "--actor", "alice",
                     "--confirm", "--release", "v0.1.0")

    assert result.code == 1
    assert result.error_codes() == ["INVALID_TRANSITION"]
    assert front_matter(ready / ITEM)["status"] == status


def test_plan_rejects_incomplete_requirement(ready: Path) -> None:
    created = run_cli(ready, "req", "create", "--title", "batch parse resume",
                      "--module", "resume")
    assert created.code == 0, created
    assert run_cli(ready, "req", "confirm", "--id", "REQ-001", "--actor", "bob",
                   "--confirm").code == 0

    result = run_cli(ready, "req", "plan", "--id", "REQ-001", "--actor", "alice",
                     "--confirm", "--release", "v0.1.0")

    assert result.code == 1
    assert result.error_codes() == ["INCOMPLETE_REQUIREMENT"]
    assert front_matter(ready / ITEM)["status"] == "TRIAGED"


def test_plan_unknown_requirement_reports_not_found(ready: Path) -> None:
    result = run_cli(ready, "req", "plan", "--id", "REQ-404", "--actor", "alice",
                     "--confirm", "--release", "v0.1.0")

    assert result.code == 1
    assert result.error_codes() == ["REQ_NOT_FOUND"]


# ------------------------------------------------------------------ 取消排期
def test_unplan_returns_to_triaged_before_prd(triaged: Path) -> None:
    run_cli(triaged, "req", "plan", "--id", "REQ-001", "--actor", "alice", "--confirm",
            "--release", "v0.1.0")

    result = run_cli(triaged, "req", "unplan", "--id", "REQ-001", "--actor", "alice",
                     "--confirm", "--reason", "让位给更紧急的需求", env=ENV)

    assert result.code == 0, result
    meta = front_matter(triaged / ITEM)
    assert meta["status"] == "TRIAGED"
    assert meta["release"] is None
    assert meta["history"][-1]["action"] == "req.unplan"
    assert meta["history"][-1]["reason"] == "让位给更紧急的需求"
    assert meta["confirmations"][-1]["decision"] == "approved"


def test_unplan_after_in_prd_is_locked(triaged: Path) -> None:
    write_item(triaged, status="IN_PRD", extra={"release": "v0.1.0", "prd": "PRD-001"})

    result = run_cli(triaged, "req", "unplan", "--id", "REQ-001", "--actor", "alice", "--confirm")

    assert result.code == 1
    assert result.error_codes() == ["REQ_LOCKED"]
    assert front_matter(triaged / ITEM)["status"] == "IN_PRD"


def test_unplan_from_triaged_is_invalid_transition(triaged: Path) -> None:
    result = run_cli(triaged, "req", "unplan", "--id", "REQ-001", "--actor", "alice", "--confirm")

    assert result.code == 1
    assert result.error_codes() == ["INVALID_TRANSITION"]


def test_planned_requirement_is_read_only(triaged: Path) -> None:
    run_cli(triaged, "req", "plan", "--id", "REQ-001", "--actor", "alice", "--confirm",
            "--release", "v0.1.0")
    before = read(triaged / ITEM)

    result = run_cli(triaged, "req", "update", "--id", "REQ-001", "--set", "source=反馈")

    assert result.code == 1
    assert result.error_codes() == ["REQ_LOCKED"]
    assert read(triaged / ITEM) == before


# ------------------------------------------------------------------ 旁支
def test_defer_and_reject_record_reason(triaged: Path) -> None:
    deferred = run_cli(triaged, "req", "defer", "--id", "REQ-001", "--actor", "alice",
                       "--confirm", "--reason", "等客户确认", env=ENV)

    assert deferred.code == 0, deferred
    meta = front_matter(triaged / ITEM)
    assert meta["status"] == "DEFERRED"
    assert meta["status_reason"] == "等客户确认"
    assert meta["history"][-1]["action"] == "req.defer"
    assert meta["history"][-1]["reason"] == "等客户确认"

    rejected = run_cli(triaged, "req", "reject", "--id", "REQ-001", "--actor", "alice",
                       "--confirm", "--reason", "不在 P0 范围")

    assert rejected.code == 0, rejected
    meta = front_matter(triaged / ITEM)
    assert meta["status"] == "REJECTED"
    assert meta["status_reason"] == "不在 P0 范围"
    assert [h["to"] for h in meta["history"]] == ["DEFERRED", "REJECTED"]


def test_defer_requires_confirmation(triaged: Path) -> None:
    result = run_cli(triaged, "req", "defer", "--id", "REQ-001", "--actor", "alice")

    assert result.code == 1
    assert result.error_codes() == ["CONFIRMATION_REQUIRED"]
    assert front_matter(triaged / ITEM)["status"] == "TRIAGED"


def test_reject_from_planned_is_allowed(triaged: Path) -> None:
    run_cli(triaged, "req", "plan", "--id", "REQ-001", "--actor", "alice", "--confirm",
            "--release", "v0.1.0")

    result = run_cli(triaged, "req", "reject", "--id", "REQ-001", "--actor", "alice",
                     "--confirm", "--reason", "方向调整")

    assert result.code == 0, result
    assert front_matter(triaged / ITEM)["status"] == "REJECTED"


# ------------------------------------------------------------------ 失败无副作用
def test_failed_commands_leave_no_lock_or_temp_files(triaged: Path) -> None:
    run_cli(triaged, "req", "plan", "--id", "REQ-001", "--actor", "bob", "--confirm",
            "--release", "v0.1.0")
    run_cli(triaged, "req", "plan", "--id", "REQ-001", "--actor", "alice", "--confirm")

    locks = sorted(p.name for p in (triaged / ".neo-skill/locks").iterdir())
    leftovers = [p.name for p in (triaged / "03-requirements/items").iterdir()
                 if p.name.startswith(".")]
    assert locks == []
    assert leftovers == []
    assert front_matter(triaged / ITEM)["status"] == "TRIAGED"


def test_successful_plan_releases_lock(triaged: Path) -> None:
    run_cli(triaged, "req", "plan", "--id", "REQ-001", "--actor", "alice", "--confirm",
            "--release", "v0.1.0")

    assert sorted(p.name for p in (triaged / ".neo-skill/locks").iterdir()) == []
    # 能立即再执行一次人工决策命令（锁已释放）
    again = run_cli(triaged, "req", "reject", "--id", "REQ-001", "--actor", "alice",
                    "--confirm", "--reason", "方向调整")

    assert again.code == 0, again


def test_concurrent_lock_blocks_plan(triaged: Path) -> None:
    write(triaged / ".neo-skill/locks/REQ-001.lock",
          '{"object": "REQ-001", "pid": 999999, "created_at": "2026-03-01T09:59:00+08:00",'
          ' "command": "req.plan", "rules_version": 1}')

    result = run_cli(triaged, "req", "plan", "--id", "REQ-001", "--actor", "alice",
                     "--confirm", "--release", "v0.1.0")

    assert result.code == 1
    assert result.error_codes() == ["LOCK_CONFLICT"]
    assert front_matter(triaged / ITEM)["status"] == "TRIAGED"
