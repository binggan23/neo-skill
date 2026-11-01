"""ticket 07：人工验收（Demo/checklist、逐条记录、建议与确认分离、条件通过、结论失效）。

覆盖 ticket 07 验收项：prepare 生成 Demo 与逐条 AC checklist、AC 条数与 PRD 一致、
建议与人工确认分离（只有 suggestion 时 approve 失败）、错误身份、
全部 pass 才 approve、不通过退回 DEVELOPING、阻塞/有条件通过不能 ACCEPTED、
验收报告字段完整、验收版本变化使结论失效、prepare 前置状态、失败零副作用。

全部通过 `run_cli` 进程边界观察行为；验证命令一律极轻量（`python3 -c`）。
"""

from __future__ import annotations

from pathlib import Path

import pytest

from conftest import read, run_cli, write
from helpers_03 import ENV, req_meta
from helpers_07 import (
    AC_1,
    AC_2,
    AC_3,
    ALL_ACS,
    PRD_ID,
    acceptance_dir,
    approve,
    approved,
    checklist_md,
    checklist_yaml,
    demo_md,
    developing,
    error_codes,
    items_by_ac,
    prepared as prepare_project,
    prd_metadata_text,
    raw_logs,
    ready,
    record,
    record_all_pass,
    report_md,
    run,
)

CONFIRM_ENV = ENV

# --------------------------------------------------------------------------
# 夹具：把项目逐层推进到 READY_FOR_ACCEPTANCE（逻辑在 tests/helpers_07.py）
# --------------------------------------------------------------------------
@pytest.fixture()
def prepared(inited_project: Path) -> Path:
    return prepare_project(inited_project)


@pytest.fixture()
def ready_project(inited_project: Path) -> Path:
    return ready(inited_project)


# --------------------------------------------------------------------------
# prepare：Demo 材料 + 逐条 AC checklist
# --------------------------------------------------------------------------
def test_prepare_generates_demo_and_checklist(prepared: Path) -> None:
    directory = acceptance_dir(prepared)
    assert (directory / "demo.md").is_file()
    assert (directory / "checklist.md").is_file()
    assert (directory / "checklist.yaml").is_file()

    demo = demo_md(prepared)
    assert "PRD-001" in demo
    assert "## 访问方式" in demo
    assert "## 操作步骤" in demo
    # 场景与 AC 都从 PRD 与验证证据来；缺信息的地方保留 TODO，不伪造访问地址/步骤
    for scenario in ("SC-RES-01", "SC-RES-02"):
        assert scenario in demo
    for ac in ALL_ACS:
        assert ac in demo
    assert "TODO" in demo


def test_prepare_checklist_rows_match_prd_acs(prepared: Path) -> None:
    data = checklist_yaml(prepared)
    assert [item["ac"] for item in data["items"]] == list(ALL_ACS)
    assert all(item["conclusion"] == "pending" for item in data["items"])
    # 每条都带上验证证据（机器可读），供人工核对
    assert all(item["verification"] == "pass" for item in data["items"])
    assert all(item["verification_evidence"] for item in data["items"])

    table = checklist_md(prepared)
    assert "| AC | 场景 | 结论 | 证据 | 记录人 |" in table
    for ac in ALL_ACS:
        assert f"| {ac} |" in table
    assert "pending" in table


def test_prepare_records_acceptance_baseline(prepared: Path) -> None:
    data = checklist_yaml(prepared)
    assert data["prd"] == PRD_ID
    assert data["prd_hash"].startswith("sha256:")
    assert data["code_version"]
    assert data["verification_digest"].startswith("sha256:")
    assert data["prepared_at"] == "2026-03-01T10:00:00+08:00"


@pytest.mark.parametrize("stage", ["developing", "approved", "accepted"])
def test_prepare_requires_ready_for_acceptance(stage: str, inited_project: Path) -> None:
    project = inited_project
    if stage == "developing":
        developing(project)
    elif stage == "approved":
        approved(project)
    else:
        prepare_project(project)
        record_all_pass(project)
        approve(project)

    if stage == "accepted":
        # 已 ACCEPTED 仍允许 prepare（用于基线变化后重新生成清单）
        assert run(project, "acceptance", "prepare", "--prd", PRD_ID).code == 0
        return

    result = run(project, "acceptance", "prepare", "--prd", PRD_ID, expect=1)

    assert "PRD_STATE_NOT_ALLOWED" in error_codes(result)
    assert not acceptance_dir(project).exists()
    assert "status:" in prd_metadata_text(project)  # 未破坏 metadata


def test_prepare_is_idempotent_and_keeps_human_conclusions(prepared: Path) -> None:
    record(prepared, AC_1, "pass", evidence="人工演示通过")
    before = read(acceptance_dir(prepared) / "checklist.yaml")

    result = run(prepared, "acceptance", "prepare", "--prd", PRD_ID)

    assert result.code == 0
    actions = result.actions()
    assert actions["07-acceptance/resume/PRD-001-batch-parse/checklist.md"] == "unchanged"
    assert read(acceptance_dir(prepared) / "checklist.yaml") == before
    assert items_by_ac(prepared)[AC_1]["conclusion"] == "pass"


def test_prepare_resets_conclusions_when_baseline_changes(prepared: Path) -> None:
    record_all_pass(prepared)
    assert all(item["conclusion"] == "pass" for item in items_by_ac(prepared).values())

    write(prepared / "src/app.py", "print('late change')\n")
    result = run(prepared, "acceptance", "prepare", "--prd", PRD_ID)

    items = items_by_ac(prepared)
    assert all(item["conclusion"] == "pending" for item in items.values())
    # 旧结论保留在 superseded 里，便于审计
    assert items[AC_1]["superseded"]
    assert items[AC_1]["superseded"][-1]["conclusion"] == "pass"
    assert items[AC_1]["superseded"][-1]["recorded_by"] == "alice"
    # 重新 prepare 后清单跟到新基线（因此不再 stale），旧结论已被归档重置
    assert result.json["data"]["checklist"]["baseline_stale"] is False
    # 基线已变：旧证据不得沿用，approve 必须被拦下
    blocked = approve(prepared, expect=1)
    assert "BASELINE_STALE" in error_codes(blocked)
    assert "ACCEPTED" not in prd_metadata_text(prepared)


# --------------------------------------------------------------------------
# record：人工逐条结论
# --------------------------------------------------------------------------
def test_record_writes_human_conclusion_and_evidence(prepared: Path) -> None:
    result = record(prepared, AC_1, "pass", evidence="现场演示：100 份 3 分钟完成",
                    note="网络抖动一次")

    data = checklist_yaml(prepared)
    item = items_by_ac(prepared)[AC_1]
    assert item["conclusion"] == "pass"
    assert item["evidence"] == "现场演示：100 份 3 分钟完成"
    assert item["note"] == "网络抖动一次"
    assert item["recorded_by"] == "alice"
    assert item["recorded_at"] == "2026-03-01T10:00:00+08:00"
    assert data["updated_at"] == "2026-03-01T10:00:00+08:00"
    assert result.json["data"]["all_pass"] is False
    assert f"| {AC_1} |" in checklist_md(prepared)
    assert "现场演示：100 份 3 分钟完成" in checklist_md(prepared)


def test_record_accepts_any_team_member(prepared: Path) -> None:
    """验收人不必是需求负责人：任何有职责的团队成员都能记录，但必须登记身份。"""
    result = record(prepared, AC_2, "pass", actor="carol", evidence="开发自测演示")

    assert result.code == 0
    assert items_by_ac(prepared)[AC_2]["recorded_by"] == "carol"


def test_record_rejects_unknown_actor_without_side_effects(prepared: Path) -> None:
    before = read(acceptance_dir(prepared) / "checklist.yaml")

    result = record(prepared, AC_1, "pass", actor="nobody", expect=1)

    assert "ACTOR_UNKNOWN" in error_codes(result)
    assert read(acceptance_dir(prepared) / "checklist.yaml") == before
    assert not list((prepared / ".neo-skill/locks").glob("*.lock"))


def test_record_rejects_unknown_ac(prepared: Path) -> None:
    result = record(prepared, "AC-RES-09-09", "pass", expect=1)

    assert "UNKNOWN_AC" in error_codes(result)


def test_record_requires_ready_state(inited_project: Path) -> None:
    """非 READY_FOR_ACCEPTANCE 时不能记录验收结论。"""
    developing(inited_project)

    result = record(inited_project, AC_1, "pass", expect=1)

    assert "PRD_STATE_NOT_ALLOWED" in error_codes(result)
    assert not acceptance_dir(inited_project).exists()


# --------------------------------------------------------------------------
# suggest：AI 建议 ≠ 人工确认
# --------------------------------------------------------------------------
def test_suggestion_alone_never_approves(prepared: Path) -> None:
    for ac in ALL_ACS:
        result = run(prepared, "acceptance", "suggest", "--prd", PRD_ID, "--ac", ac,
                     "--result", "pass", "--reason", "验证证据显示通过")
        assert result.code == 0, result

    items = items_by_ac(prepared)
    assert all(item["suggestion"] == "pass" for item in items.values())
    assert all(item["conclusion"] == "pending" for item in items.values())

    result = approve(prepared, expect=1)

    assert "AC_NOT_ALL_PASSED" in error_codes(result)
    assert prd_metadata_text(prepared).count("status: ACCEPTED") == 0
    assert not (acceptance_dir(prepared) / "report.md").exists()


def test_suggestion_does_not_overwrite_human_conclusion(prepared: Path) -> None:
    record(prepared, AC_1, "pass", evidence="人工确认通过")

    result = run(prepared, "acceptance", "suggest", "--prd", PRD_ID, "--ac", AC_1,
                 "--result", "fail", "--reason", "验证证据与我看到的不一致")

    assert result.code == 0, result
    item = items_by_ac(prepared)[AC_1]
    assert item["conclusion"] == "pass"
    assert item["evidence"] == "人工确认通过"
    assert item["suggestion"] == "fail"
    assert item["suggestion_reason"] == "验证证据与我看到的不一致"
    # 建议与人工结论冲突只提示，不改结论
    assert result.json["data"]["suggestion_conflict"] is True
    assert item["suggestion_conflict"] is True


# --------------------------------------------------------------------------
# approve：全部通过 + 需求负责人显式确认
# --------------------------------------------------------------------------
def test_approve_requires_all_ac_pass(prepared: Path) -> None:
    record(prepared, AC_1, "pass")
    record(prepared, AC_2, "fail", evidence="上传中断")
    # AC_3 未记录，仍是 pending

    result = approve(prepared, expect=1)

    assert "AC_NOT_ALL_PASSED" in error_codes(result)
    pending = {item["ac"] for item in result.json["data"]["pending"]}
    assert AC_2 in pending
    assert AC_3 in pending
    assert result.json["next_actions"]
    assert "READY_FOR_ACCEPTANCE" in prd_metadata_text(prepared)


def test_approve_rejects_non_requirement_owner(prepared: Path) -> None:
    record_all_pass(prepared)
    before = prd_metadata_text(prepared)

    result = approve(prepared, actor="bob", expect=1)

    assert "ROLE_MISMATCH" in error_codes(result)
    assert prd_metadata_text(prepared) == before
    assert not (acceptance_dir(prepared) / "report.md").exists()
    assert not list((prepared / ".neo-skill/locks").glob("*.lock"))


def test_approve_requires_actor_and_confirm(prepared: Path) -> None:
    record_all_pass(prepared)
    before = prd_metadata_text(prepared)

    result = run_cli(prepared, "acceptance", "approve", "--prd", PRD_ID, env=CONFIRM_ENV)

    assert result.code == 1
    assert "CONFIRMATION_REQUIRED" in error_codes(result)
    assert prd_metadata_text(prepared) == before


def test_approve_moves_prd_to_accepted_with_full_report(prepared: Path) -> None:
    record_all_pass(prepared)

    result = approve(prepared, extra=("--leftover", "导出文件名格式待统一下一版"))

    assert result.code == 0, result
    data = result.json["data"]
    assert data["prd"]["status"] == "ACCEPTED"
    assert data["accepted"] is True
    assert data["next_step"] == "closeout"
    assert any("closeout" in item for item in result.json["next_actions"])
    assert any("未" in item and "DONE" in item for item in result.json["next_actions"])

    # metadata：acceptance 记录 + 历史 + 人工确认
    meta = checklist_yaml(prepared)
    acceptance = result.json["data"]["acceptance"]
    assert acceptance["approved_by"] == "alice"
    assert acceptance["approved_at"] == "2026-03-01T10:00:00+08:00"
    assert acceptance["prd_hash"].startswith("sha256:")
    assert acceptance["code_version"]
    assert acceptance["checklist_digest"].startswith("sha256:")
    assert acceptance["verification_digest"].startswith("sha256:")
    assert acceptance["leftovers"] == ["导出文件名格式待统一下一版"]
    assert data["closeout"] is None
    assert meta["decisions"][-1]["decision"] == "approved"

    text = prd_metadata_text(prepared)
    assert "status: ACCEPTED" in text
    assert "approved_by: alice" in text
    assert "closeout: null" in text
    # 主需求不得被置 DONE（那是收尾的事）
    assert req_meta(prepared)["status"] == "IN_PRD"

    report = report_md(prepared)
    assert "alice" in report
    assert "2026-03-01T10:00:00+08:00" in report
    assert acceptance["prd_hash"] in report
    assert acceptance["code_version"] in report
    assert acceptance["verification_digest"] in report
    for ac in ALL_ACS:
        assert f"| {ac} |" in report
    assert "导出文件名格式待统一下一版" in report
    assert "收尾" in report
    # 报告明确「已确认通过但收尾未完成」，不得暗示需求已完成
    assert "已确认通过，收尾未完成" in report
    assert "主需求尚未 DONE" in report


def test_approve_records_history_and_confirmation(prepared: Path) -> None:
    record_all_pass(prepared)
    approve(prepared)

    import yaml

    meta = yaml.safe_load(prd_metadata_text(prepared))
    assert meta["status"] == "ACCEPTED"
    assert meta["history"][-1]["action"] == "acceptance.approve"
    assert meta["history"][-1]["from"] == "READY_FOR_ACCEPTANCE"
    assert meta["history"][-1]["to"] == "ACCEPTED"
    assert meta["confirmations"][-1]["by"] == "alice"
    assert meta["confirmations"][-1]["role"] == "requirement_owner"
    assert meta["confirmations"][-1]["decision"] == "acceptance.approve"


def test_approve_failure_has_no_side_effects(prepared: Path) -> None:
    record(prepared, AC_1, "pass")
    before_meta = prd_metadata_text(prepared)
    before_checklist = read(acceptance_dir(prepared) / "checklist.yaml")

    approve(prepared, expect=1)

    assert prd_metadata_text(prepared) == before_meta
    assert read(acceptance_dir(prepared) / "checklist.yaml") == before_checklist
    assert not (acceptance_dir(prepared) / "report.md").exists()
    assert not list((prepared / ".neo-skill/locks").glob("*.lock"))


# --------------------------------------------------------------------------
# 不通过 / 阻塞 / 有条件通过
# --------------------------------------------------------------------------
def test_reject_returns_prd_to_developing(prepared: Path) -> None:
    record(prepared, AC_1, "pass")
    record(prepared, AC_2, "fail", evidence="上传 100 份时中断")

    result = run(prepared, "acceptance", "reject", "--prd", PRD_ID, "--actor", "alice",
                 "--confirm", "--reason", "批量上传在 100 份时中断",
                 "--leftover", "需要补断点续传")

    assert result.code == 0, result
    assert result.json["data"]["prd"]["status"] == "DEVELOPING"
    import yaml

    meta = yaml.safe_load(prd_metadata_text(prepared))
    assert meta["status"] == "DEVELOPING"
    assert meta["acceptance"] is None
    assert meta["history"][-1]["action"] == "acceptance.reject"
    assert meta["confirmations"][-1]["by"] == "alice"
    report = report_md(prepared)
    assert "不通过" in report
    assert "批量上传在 100 份时中断" in report
    assert "需要补断点续传" in report
    assert any("dev verify" in item or "dev start" in item
               for item in result.json["next_actions"])


def test_blocked_ac_cannot_be_accepted(prepared: Path) -> None:
    record(prepared, AC_1, "pass")
    record(prepared, AC_2, "pass")
    record(prepared, AC_3, "blocked", evidence="外部存储环境不可用")

    result = approve(prepared, expect=1)

    assert "AC_NOT_ALL_PASSED" in error_codes(result)
    assert [item["ac"] for item in result.json["data"]["pending"]] == [AC_3]
    assert "READY_FOR_ACCEPTANCE" in prd_metadata_text(prepared)


def test_conditional_pass_does_not_reach_accepted(prepared: Path) -> None:
    record_all_pass(prepared)

    result = run(prepared, "acceptance", "conditional", "--prd", PRD_ID, "--actor", "alice",
                 "--confirm", "--reason", "核心场景通过，导出文件名格式待统一",
                 "--leftover", "导出文件名格式下一版统一")

    assert result.code == 0, result
    data = result.json["data"]
    assert data["conditional"] is True
    assert data["accepted"] is False
    assert data["prd"]["status"] == "READY_FOR_ACCEPTANCE"
    assert "ACCEPTED" not in prd_metadata_text(prepared)
    assert data["leftovers"] == ["导出文件名格式下一版统一"]
    assert any("reject" in item for item in result.json["next_actions"])
    assert any("record" in item or "prepare" in item for item in result.json["next_actions"])

    report = report_md(prepared)
    assert "有条件通过" in report
    assert "导出文件名格式下一版统一" in report

    # 有条件通过之后不能靠 approve 直接进 ACCEPTED（遗留问题未处理）
    blocked = approve(prepared, expect=1)
    assert "ACCEPTANCE_CONDITIONAL" in error_codes(blocked)
    # 全部 AC 已 pass，因此不应再报「AC 未通过」
    assert "AC_NOT_ALL_PASSED" not in error_codes(blocked)
    assert "ACCEPTED" not in prd_metadata_text(prepared)


def test_conditional_can_be_resolved_by_recording_again(prepared: Path) -> None:
    record_all_pass(prepared)
    run(prepared, "acceptance", "conditional", "--prd", PRD_ID, "--actor", "alice",
        "--confirm", "--reason", "导出格式待统一", "--leftover", "导出文件名格式下一版统一")
    # 遗留问题处理完、人重新记录结论 → 条件决定失效（遗留问题仍可查）
    record(prepared, AC_1, "pass", evidence="导出格式已按约定调整并复核")

    result = approve(prepared)

    assert result.code == 0, result
    assert result.json["data"]["prd"]["status"] == "ACCEPTED"
    assert result.json["data"]["conditional"] is False


def test_status_exposes_closeout_gate_for_ticket08(prepared: Path) -> None:
    result = run(prepared, "acceptance", "status", "--prd", PRD_ID)
    assert result.json["data"]["closeout"]["ok"] is False
    assert "ACCEPTANCE_MISSING" in result.json["data"]["closeout"]["problems"]

    record_all_pass(prepared)
    approve(prepared, extra=("--leftover", "导出格式下一版统一"))

    done = run(prepared, "acceptance", "status", "--prd", PRD_ID)
    closeout = done.json["data"]["closeout"]
    assert closeout["ok"] is True
    assert closeout["acceptance"]["approved_by"] == "alice"
    assert closeout["leftovers"] == ["导出格式下一版统一"]
    assert closeout["checklist_digest"].startswith("sha256:")


def test_conditional_requires_reason_and_leftover(prepared: Path) -> None:
    record_all_pass(prepared)

    result = run(prepared, "acceptance", "conditional", "--prd", PRD_ID, "--actor", "alice",
                 "--confirm", expect=1)

    assert "REASON_REQUIRED" in error_codes(result) or "LEFTOVER_REQUIRED" in error_codes(result)
    assert "ACCEPTED" not in prd_metadata_text(prepared)


# --------------------------------------------------------------------------
# status：逐条状态 + 结论是否失效
# --------------------------------------------------------------------------
def test_status_reports_stale_baseline_before_reprepare(prepared: Path) -> None:
    record_all_pass(prepared)
    write(prepared / "src/app.py", "print('late change')\n")

    result = run(prepared, "acceptance", "status", "--prd", PRD_ID)

    assert result.json["data"]["baseline_stale"] is True
    assert "CODE_CHANGED" in result.json["data"]["baseline_reasons"]


def test_status_reports_checklist_and_acceptance(prepared: Path) -> None:
    record(prepared, AC_1, "pass", evidence="演示通过")

    result = run(prepared, "acceptance", "status", "--prd", PRD_ID)

    data = result.json["data"]
    assert data["prepared"] is True
    assert [item["ac"] for item in data["items"]] == list(ALL_ACS)
    assert data["all_pass"] is False
    assert data["conditional"] is False
    assert data["acceptance"] is None
    assert data["stale"] is False


def test_status_before_prepare_is_read_only(inited_project: Path) -> None:
    ready(inited_project)

    result = run(inited_project, "acceptance", "status", "--prd", PRD_ID)

    assert result.code == 0, result
    assert result.json["data"]["prepared"] is False
    assert result.json["data"]["all_pass"] is False
    assert not acceptance_dir(inited_project).exists()


def test_approved_acceptance_reports_live_baseline(prepared: Path) -> None:
    record_all_pass(prepared)
    approve(prepared)

    result = run(prepared, "acceptance", "status", "--prd", PRD_ID)

    data = result.json["data"]
    assert result.code == 0, result
    assert data["all_pass"] is True
    assert data["stale"] is False
    assert data["acceptance"]["approved_by"] == "alice"
    assert data["next_step"] == "closeout"


def test_prd_change_invalidates_acceptance(prepared: Path) -> None:
    record_all_pass(prepared)
    approve(prepared)
    text = read(prepared / "04-prds/resume/PRD-001-batch-parse/prd.md")
    write(prepared / "04-prds/resume/PRD-001-batch-parse/prd.md",
          text + "\n<!-- 变更：范围调整 -->\n")

    result = run(prepared, "acceptance", "status", "--prd", PRD_ID, expect=1)

    assert "ACCEPTANCE_STALE" in error_codes(result)
    assert result.json["data"]["stale"] is True
    assert "PRD_CHANGED" in result.json["data"]["stale_reasons"]
    assert result.json["next_actions"]


def test_code_change_invalidates_acceptance(prepared: Path) -> None:
    record_all_pass(prepared)
    approve(prepared)
    write(prepared / "src/app.py", "print('changed after acceptance')\n")

    result = run(prepared, "acceptance", "status", "--prd", PRD_ID, expect=1)

    assert "ACCEPTANCE_STALE" in error_codes(result)
    assert "CODE_CHANGED" in result.json["data"]["stale_reasons"]


def test_evidence_replacement_invalidates_acceptance(prepared: Path) -> None:
    record_all_pass(prepared)
    approve(prepared)
    log = raw_logs(prepared)[0]
    write(log, "$ 伪造输出\n--- stdout ---\n全部通过\n")

    result = run(prepared, "acceptance", "status", "--prd", PRD_ID, expect=1)

    assert "ACCEPTANCE_STALE" in error_codes(result)
    assert "EVIDENCE_CHANGED" in result.json["data"]["stale_reasons"]


def test_checklist_change_after_approval_invalidates_acceptance(prepared: Path) -> None:
    record_all_pass(prepared)
    approve(prepared)

    # 越过 CLI 直接改人工结论：报告里的 checklist 摘要必须能发现
    import yaml

    path = acceptance_dir(prepared) / "checklist.yaml"
    data = yaml.safe_load(read(path))
    for item in data["items"]:
        if item["ac"] == AC_3:
            item["conclusion"] = "fail"
    write(path, yaml.safe_dump(data, sort_keys=False, allow_unicode=True))
    assert "fail" in read(path)

    result = run(prepared, "acceptance", "status", "--prd", PRD_ID, expect=1)
    assert "ACCEPTANCE_STALE" in error_codes(result)
    assert "CHECKLIST_CHANGED" in result.json["data"]["stale_reasons"]


# --------------------------------------------------------------------------
# 基线不一致时不许验收通过
# --------------------------------------------------------------------------
def test_approve_refuses_when_verification_baseline_is_stale(prepared: Path) -> None:
    record_all_pass(prepared)
    write(prepared / "src/app.py", "print('changed before acceptance')\n")

    result = approve(prepared, expect=1)

    assert "BASELINE_STALE" in error_codes(result)
    assert "READY_FOR_ACCEPTANCE" in prd_metadata_text(prepared)
