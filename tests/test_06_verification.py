"""ticket 06：验证证据、防伪造检查与提交人工验收（黑盒 CLI）。

覆盖 ticket 06 验收项：成功验证（AC 关联、原始输出、退出码、环境）、失败验证不算通过、
未执行/阻塞不能标通过、不适用必须有理由、修复轮次上限与修复后继续、
防伪造（删用例/弱化断言/跳过测试/改 AC）、代码版本（Git 与非 Git）、
PRD/代码/证据变化使提交基线失效、全部通过才 READY_FOR_ACCEPTANCE、
验证不完整被拒、失败零副作用。

全部通过 `run_cli` 进程边界观察行为。**验证命令一律极轻量**
（`python3 -c`、`sh -c 'exit N'`、写临时文件），不嵌套 pytest、不跑重量级命令。
"""

from __future__ import annotations

import json
import subprocess
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
    update_body,
)

MODULE = "resume"
SLUG = "batch-parse"
PRD_ID = "PRD-001"

AC_1 = "AC-RES-01-01"
AC_2 = "AC-RES-01-02"
AC_3 = "AC-RES-02-01"
ALL_ACS = (AC_1, AC_2, AC_3)

CONSTITUTION = """---
confirmed: true
confirmed_by: alice
confirmed_at: 2026-03-01T09:00:00+08:00
---

# 项目约束（constitution）

## 技术栈

Python 3.11 + pytest；仅使用标准库。

## 测试要求

- 每个改动必须补测试；失败修复上限 3 轮。
"""

# 极轻量的「项目验证命令」
PASS_CMD = 'python3 -c "print(1)"'
FAIL_CMD = "sh -c 'exit 3'"
TOUCH_CMD = "sh -c 'printf hello > verify-cwd.txt'"

SAMPLE_TESTS = '''"""样本测试：给防伪造检查用。"""


def test_upload():
    total = 2 + 2
    assert total == 4


def test_retry():
    value = 1
    assert value == 1
    assert value > 0
'''


# --------------------------------------------------------------------------
# 夹具与工具
# --------------------------------------------------------------------------
def spec_dir(project: Path, prd_id: str = PRD_ID) -> Path:
    return project / "06-specs" / MODULE / f"{prd_id}-{SLUG}"


def ver_dir(project: Path, prd_id: str = PRD_ID) -> Path:
    return spec_dir(project, prd_id) / "verification"


def ver_json(project: Path, prd_id: str = PRD_ID) -> dict:
    path = spec_dir(project, prd_id) / "verification.json"
    assert path.is_file(), f"缺少 {path}"
    data = json.loads(read(path))
    assert isinstance(data, dict)
    return data


def ver_md(project: Path, prd_id: str = PRD_ID) -> str:
    return read(spec_dir(project, prd_id) / "verification.md")


def raw_logs(project: Path, prd_id: str = PRD_ID) -> list[Path]:
    directory = ver_dir(project, prd_id)
    return sorted(directory.glob("raw-*.log")) if directory.is_dir() else []


def run(project: Path, *args: str, expect: int = 0, env: dict | None = None):
    result = run_cli(project, *args, env=env if env is not None else ENV)
    assert result.code == expect, result
    return result


def error_codes(result) -> set[str]:
    return {item["code"] for item in (result.json or {}).get("errors", [])}


def acs_of(project: Path) -> dict:
    return {item["ac"]: item for item in ver_json(project)["acs"]}


@pytest.fixture()
def approved(inited_project: Path) -> Path:
    """APPROVED PRD + 已确认 constitution + 有效团队。"""
    planned_req(inited_project, title="简历批量解析", slug=SLUG)
    create_prd(inited_project, scenarios="SC-RES-01,SC-RES-02")
    update_body(inited_project, VALID_PRD)
    run(inited_project, "prd", "submit-review", "--id", PRD_ID, "--actor", "bob", "--confirm")
    run(inited_project, "prd", "approve", "--id", PRD_ID, "--actor", "alice", "--confirm")
    write(inited_project / ".specify/constitution.md", CONSTITUTION)
    return inited_project


@pytest.fixture()
def developing(approved: Path) -> Path:
    """已 dev start 的 PRD（DEVELOPING）+ 一份样本测试。"""
    write(approved / "tests/test_sample.py", SAMPLE_TESTS)
    run(approved, "dev", "start", "--prd", PRD_ID, "--actor", "carol", "--confirm")
    return approved


# --------------------------------------------------------------------------
# 成功验证
# --------------------------------------------------------------------------
def test_verify_pass_records_ac_evidence_and_environment(developing: Path) -> None:
    result = run(developing, "dev", "verify", "--prd", PRD_ID, "--suite", "unit",
                 "--command", PASS_CMD, "--ac", f"{AC_1},{AC_2}")

    assert result.json["data"]["result"] == "pass"
    assert result.json["data"]["exit_code"] == 0
    paths = result.actions()
    assert "06-specs/resume/PRD-001-batch-parse/verification.md" in paths
    assert "06-specs/resume/PRD-001-batch-parse/verification.json" in paths

    logs = raw_logs(developing)
    assert len(logs) == 1
    assert logs[0].name.startswith("raw-unit-")
    assert logs[0].name.endswith(".log")
    assert "1" in read(logs[0])

    data = ver_json(developing)
    assert data["prd"] == PRD_ID
    run_entry = data["runs"][0]
    assert run_entry["suite"] == "unit"
    assert run_entry["command"] == PASS_CMD
    assert run_entry["exit_code"] == 0
    assert run_entry["result"] == "pass"
    assert run_entry["acs"] == [AC_1, AC_2]
    assert run_entry["raw_log"] == f"06-specs/resume/PRD-001-batch-parse/verification/{logs[0].name}"
    assert run_entry["environment"]["python"]
    assert run_entry["environment"]["platform"]
    assert run_entry["started_at"]
    assert run_entry["duration_ms"] >= 0

    statuses = acs_of(developing)
    assert statuses[AC_1]["result"] == "pass"
    assert statuses[AC_2]["result"] == "pass"
    assert statuses[AC_3]["result"] == "not-run"

    report = ver_md(developing)
    assert f"| {AC_1} | pass |" in report
    assert logs[0].name in report
    assert "pass" in report


def test_verify_command_runs_in_project_root(developing: Path) -> None:
    run(developing, "dev", "verify", "--prd", PRD_ID, "--suite", "static",
        "--command", TOUCH_CMD, "--ac", AC_1)

    assert (developing / "verify-cwd.txt").is_file()
    assert read(developing / "verify-cwd.txt") == "hello"


# --------------------------------------------------------------------------
# 失败验证 / 未执行 / 阻塞
# --------------------------------------------------------------------------
def test_verify_failure_is_recorded_as_fail_not_pass(developing: Path) -> None:
    result = run(developing, "dev", "verify", "--prd", PRD_ID, "--suite", "unit",
                 "--command", FAIL_CMD, "--ac", AC_1, expect=1)

    assert "VERIFICATION_FAILED" in error_codes(result)
    assert result.json["data"]["result"] == "fail"

    data = ver_json(developing)
    assert data["runs"][0]["result"] == "fail"
    assert data["runs"][0]["exit_code"] == 3
    assert acs_of(developing)[AC_1]["result"] == "fail"
    # 原始输出仍被保留（NFR-008：不只保存摘要）
    assert len(raw_logs(developing)) == 1


def test_blocked_record_does_not_become_pass(developing: Path) -> None:
    output = write(developing / "external.log", "integration blocked: 无外部环境\n")
    run(developing, "dev", "verify", "verify_placeholder", expect=2) if False else None
    result = run(developing, "dev", "verify", "record", "--prd", PRD_ID, "--suite",
                 "integration", "--result", "blocked", "--ac", AC_1,
                 "--exit-code", "0", "--output-file", str(output))

    assert result.json["data"]["result"] == "blocked"
    assert acs_of(developing)[AC_1]["result"] == "blocked"
    logs = raw_logs(developing)
    assert len(logs) == 1
    assert "integration blocked" in read(logs[0])
    # 阻塞不算通过 → 不能提交验收
    submitted = run(developing, "dev", "submit-acceptance", "--prd", PRD_ID, expect=1)
    assert "VERIFICATION_INCOMPLETE" in error_codes(submitted)
    assert submitted.json["data"]["pending"]


def test_record_result_requires_existing_output_file(developing: Path) -> None:
    result = run(developing, "dev", "verify", "record", "--prd", PRD_ID, "--suite", "unit",
                 "--result", "pass", "--ac", AC_1, "--exit-code", "0",
                 "--output-file", str(developing / "nope.log"), expect=1)

    assert "OUTPUT_FILE_NOT_FOUND" in error_codes(result)
    assert not (spec_dir(developing) / "verification.json").exists()


def test_not_applicable_requires_reason(developing: Path) -> None:
    result = run(developing, "dev", "verify", "--prd", PRD_ID, "--suite", "e2e",
                 "--ac", AC_3, "--not-applicable", expect=1)

    assert "NOT_APPLICABLE_REASON_REQUIRED" in error_codes(result)
    assert not (spec_dir(developing) / "verification.json").exists()


def test_not_applicable_with_reason_is_allowed_and_recorded(developing: Path) -> None:
    result = run(developing, "dev", "verify", "--prd", PRD_ID, "--suite", "e2e",
                 "--ac", AC_3, "--not-applicable", "--reason", "本环境无外部存储，由人工验收确认")

    assert result.json["data"]["result"] == "not-applicable"
    entry = ver_json(developing)["runs"][0]
    assert entry["result"] == "not-applicable"
    assert entry["reason"] == "本环境无外部存储，由人工验收确认"
    assert acs_of(developing)[AC_3]["result"] == "not-applicable"


def test_unknown_ac_is_rejected(developing: Path) -> None:
    result = run(developing, "dev", "verify", "--prd", PRD_ID, "--suite", "unit",
                 "--command", PASS_CMD, "--ac", "AC-RES-09-09", expect=1)

    assert "UNKNOWN_AC" in error_codes(result)
    assert not (spec_dir(developing) / "verification.json").exists()


def test_verify_requires_command_unless_not_applicable(developing: Path) -> None:
    result = run(developing, "dev", "verify", "--prd", PRD_ID, "--suite", "unit",
                 "--ac", AC_1, expect=1)

    assert "COMMAND_REQUIRED" in error_codes(result)


def test_each_suite_keeps_its_own_raw_log(developing: Path) -> None:
    run(developing, "dev", "verify", "--prd", PRD_ID, "--suite", "unit",
        "--command", PASS_CMD, "--ac", AC_1)
    run(developing, "dev", "verify", "--prd", PRD_ID, "--suite", "static",
        "--command", FAIL_CMD, "--ac", AC_2, expect=1)

    names = [path.name for path in raw_logs(developing)]
    assert len(names) == 2
    assert any(name.startswith("raw-unit-") for name in names)
    assert any(name.startswith("raw-static-") for name in names)
    runs = ver_json(developing)["runs"]
    assert [run_["suite"] for run_ in runs] == ["unit", "static"]
    assert runs[0]["id"] != runs[1]["id"]


def test_guard_detects_skip_flag_in_recorded_command(developing: Path) -> None:
    output = write(developing / "external.log", "1 passed, 3 deselected\n")
    run(developing, "dev", "verify", "record", "--prd", PRD_ID, "--suite", "unit",
        "--result", "pass", "--ac", AC_1, "--exit-code", "0",
        "--output-file", str(output), "--command", "pytest -q -k 'not slow'")

    result = run(developing, "dev", "verify", "guard", "--prd", PRD_ID, expect=1)

    codes = {item["code"] for item in result.json["data"]["findings"]}
    assert "TEST_SKIPPED" in codes


@pytest.mark.parametrize("state", ["draft", "approved"])
def test_verify_rejected_outside_developing(state: str, approved: Path) -> None:
    project = approved
    if state == "draft":
        project = approved.parent / "draft-proj"
        project.mkdir()
        run(project, "init")
        planned_req(project, title="简历批量解析", slug=SLUG)
        create_prd(project, scenarios="SC-RES-01,SC-RES-02")
        update_body(project, VALID_PRD)

    result = run(project, "dev", "verify", "--prd", PRD_ID, "--suite", "unit",
                 "--command", PASS_CMD, "--ac", AC_1, expect=1)

    assert "PRD_STATE_NOT_ALLOWED" in error_codes(result)
    if state == "approved":
        assert any("dev start" in item for item in result.json["next_actions"] + [
            result.json["errors"][0].get("suggestion") or ""])
    assert not ver_dir(project).exists()


# --------------------------------------------------------------------------
# 修复轮次
# --------------------------------------------------------------------------
def test_repair_limit_default_three(developing: Path) -> None:
    for note in ("先修边界条件", "修空值分支", "修重试逻辑"):
        result = run(developing, "dev", "verify", "repair", "--prd", PRD_ID, "--note", note)
        assert result.json["data"]["repair_rounds"] >= 1

    assert ver_json(developing)["repair_rounds"] == 3

    exceeded = run(developing, "dev", "verify", "repair", "--prd", PRD_ID,
                   "--note", "第四轮", expect=1)
    assert "REPAIR_LIMIT_EXCEEDED" in error_codes(exceeded)
    assert "人" in exceeded.json["errors"][0]["suggestion"]
    # 失败不写：轮次仍是 3
    assert ver_json(developing)["repair_rounds"] == 3


def test_repair_limit_respects_constitution_override(developing: Path) -> None:
    write(developing / ".specify/constitution.md",
          CONSTITUTION.replace("修复上限 3 轮", "修复上限 1 轮").replace(
              "---\n\n# 项目约束",
              "max_repair_rounds: 1\n---\n\n# 项目约束"))

    run(developing, "dev", "verify", "repair", "--prd", PRD_ID, "--note", "第一轮")
    exceeded = run(developing, "dev", "verify", "repair", "--prd", PRD_ID,
                   "--note", "第二轮", expect=1)

    assert "REPAIR_LIMIT_EXCEEDED" in error_codes(exceeded)
    assert ver_json(developing)["repair_limit"] == 1


def test_verification_can_continue_after_repairs(developing: Path) -> None:
    run(developing, "dev", "verify", "--prd", PRD_ID, "--suite", "unit",
        "--command", FAIL_CMD, "--ac", AC_1, expect=1)
    run(developing, "dev", "verify", "repair", "--prd", PRD_ID, "--note", "修断言")
    # 修复后重新验证通过：轮次归零，允许继续
    result = run(developing, "dev", "verify", "--prd", PRD_ID, "--suite", "unit",
                 "--command", PASS_CMD, "--ac", AC_1)

    assert result.json["data"]["result"] == "pass"
    assert ver_json(developing)["repair_rounds"] == 0


# --------------------------------------------------------------------------
# 防伪造检查
# --------------------------------------------------------------------------
def _baseline_verified(project: Path) -> None:
    run(project, "dev", "verify", "--prd", PRD_ID, "--suite", "unit",
        "--command", PASS_CMD, "--ac", AC_1)


def test_guard_passes_when_sources_unchanged(developing: Path) -> None:
    _baseline_verified(developing)

    result = run(developing, "dev", "verify", "guard", "--prd", PRD_ID)

    assert result.json["data"]["findings"] == []
    assert result.json["data"]["ok"] is True


def test_guard_detects_removed_test_case(developing: Path) -> None:
    _baseline_verified(developing)
    write(developing / "tests/test_sample.py",
          SAMPLE_TESTS.split("def test_retry")[0])

    result = run(developing, "dev", "verify", "guard", "--prd", PRD_ID, expect=1)

    codes = {item["code"] for item in result.json["data"]["findings"]}
    assert "TEST_CASE_REMOVED" in codes
    assert any("test_retry" in item.get("object", "") for item in result.json["data"]["findings"])


def test_guard_detects_weakened_assertion(developing: Path) -> None:
    _baseline_verified(developing)
    write(developing / "tests/test_sample.py",
          SAMPLE_TESTS.replace("    assert value > 0\n", ""))

    result = run(developing, "dev", "verify", "guard", "--prd", PRD_ID, expect=1)

    codes = {item["code"] for item in result.json["data"]["findings"]}
    assert "ASSERTION_WEAKENED" in codes


def test_guard_detects_skipped_test_and_allows_explicit_waiver(developing: Path) -> None:
    _baseline_verified(developing)
    write(developing / "tests/test_sample.py",
          "import pytest\n\n\n@pytest.mark.skip(reason='绕过')\n" + SAMPLE_TESTS)

    failed = run(developing, "dev", "verify", "guard", "--prd", PRD_ID, expect=1)
    codes = {item["code"] for item in failed.json["data"]["findings"]}
    assert "TEST_SKIPPED" in codes

    waived = run(developing, "dev", "verify", "guard", "--prd", PRD_ID,
                 "--allow-skip", "--reason", "该用例依赖外部设备，已登记豁免")
    assert waived.json["data"]["ok"] is True
    assert ver_json(developing)["guard"]["waivers"][0]["reason"] == "该用例依赖外部设备，已登记豁免"


def test_guard_detects_modified_ac(developing: Path) -> None:
    _baseline_verified(developing)
    text = read(prd_dir(developing) / "prd.md")
    write(prd_dir(developing) / "prd.md",
          text.replace("AC-RES-01-02", "AC-RES-01-09"))

    result = run(developing, "dev", "verify", "guard", "--prd", PRD_ID, expect=1)

    codes = {item["code"] for item in result.json["data"]["findings"]}
    assert "AC_MODIFIED" in codes


def test_guard_requires_previous_verification(developing: Path) -> None:
    result = run(developing, "dev", "verify", "guard", "--prd", PRD_ID, expect=1)

    assert "VERIFICATION_MISSING" in error_codes(result)


def test_guard_also_works_after_submission_for_human_review(developing: Path) -> None:
    _verify_all_pass(developing)
    run(developing, "dev", "submit-acceptance", "--prd", PRD_ID)

    result = run(developing, "dev", "verify", "guard", "--prd", PRD_ID)

    assert result.json["data"]["ok"] is True
    assert result.json["data"]["changed_files"] == []


# --------------------------------------------------------------------------
# 代码版本
# --------------------------------------------------------------------------
def _git(project: Path, *args: str) -> None:
    subprocess.run(["git", *args], cwd=str(project), check=True,
                   capture_output=True, text=True)


def test_code_version_uses_git_when_available(developing: Path) -> None:
    _git(developing, "init")
    _git(developing, "-c", "user.email=t@t", "-c", "user.name=t", "add", "-A")
    _git(developing, "-c", "user.email=t@t", "-c", "user.name=t", "commit", "-m", "init")
    head = subprocess.run(["git", "rev-parse", "HEAD"], cwd=str(developing),
                          capture_output=True, text=True, check=True).stdout.strip()

    _verify_all_pass(developing)
    result = run(developing, "dev", "submit-acceptance", "--prd", PRD_ID)

    assert result.json["data"]["submission"]["code_version"] == f"git:{head}"
    assert ver_json(developing)["submission"]["code_version"] == f"git:{head}"


def test_code_version_uses_content_digest_without_git(developing: Path) -> None:
    _verify_all_pass(developing)
    first = run(developing, "dev", "submit-acceptance", "--prd", PRD_ID)
    version = first.json["data"]["submission"]["code_version"]
    assert version.startswith("sha256:")

    # 同样的内容 → 同样的摘要（确定性）
    again = run(developing, "dev", "verification-status", "--prd", PRD_ID)
    assert again.json["data"]["code_version"] == version

    # 改代码内容 → 摘要变化，旧提交基线失效
    write(developing / "src/app.py", "print('changed')\n")
    stale = run(developing, "dev", "verification-status", "--prd", PRD_ID, expect=1)
    assert "BASELINE_STALE" in error_codes(stale)
    assert stale.json["data"]["code_version"] != version


# --------------------------------------------------------------------------
# 提交验收
# --------------------------------------------------------------------------
def _verify_all_pass(project: Path) -> None:
    run(project, "dev", "verify", "--prd", PRD_ID, "--suite", "unit",
        "--command", PASS_CMD, "--ac", f"{AC_1},{AC_2}")
    run(project, "dev", "verify", "--prd", PRD_ID, "--suite", "integration",
        "--command", PASS_CMD, "--ac", AC_3)


def test_submit_acceptance_requires_all_applicable_pass(developing: Path) -> None:
    run(developing, "dev", "verify", "--prd", PRD_ID, "--suite", "unit",
        "--command", PASS_CMD, "--ac", AC_1)

    result = run(developing, "dev", "submit-acceptance", "--prd", PRD_ID, expect=1)

    assert "VERIFICATION_INCOMPLETE" in error_codes(result)
    pending = {item["ac"] for item in result.json["data"]["pending"]}
    assert pending == {AC_2, AC_3}
    assert prd_meta(developing)["status"] == "DEVELOPING"


def test_submit_acceptance_blocks_on_failure(developing: Path) -> None:
    run(developing, "dev", "verify", "--prd", PRD_ID, "--suite", "unit",
        "--command", PASS_CMD, "--ac", f"{AC_1},{AC_2}")
    run(developing, "dev", "verify", "--prd", PRD_ID, "--suite", "integration",
        "--command", FAIL_CMD, "--ac", AC_3, expect=1)

    result = run(developing, "dev", "submit-acceptance", "--prd", PRD_ID, expect=1)

    assert "VERIFICATION_INCOMPLETE" in error_codes(result)
    assert {item["ac"] for item in result.json["data"]["pending"]} == {AC_3}


def test_submit_acceptance_moves_prd_to_ready_for_acceptance(developing: Path) -> None:
    _verify_all_pass(developing)

    result = run(developing, "dev", "submit-acceptance", "--prd", PRD_ID, "--actor", "carol")

    submission = result.json["data"]["submission"]
    assert result.json["data"]["prd"]["status"] == "READY_FOR_ACCEPTANCE"
    assert submission["code_version"]
    assert submission["prd_hash"].startswith("sha256:")
    assert submission["verification_digest"].startswith("sha256:")
    assert [item["ac"] for item in submission["checklist_preview"]] == list(ALL_ACS)
    assert all(item["result"] == "pass" for item in submission["checklist_preview"])
    assert any("验收" in item for item in result.json["next_actions"])

    meta = prd_meta(developing)
    assert meta["status"] == "READY_FOR_ACCEPTANCE"
    assert meta["history"][-1]["to"] == "READY_FOR_ACCEPTANCE"
    assert meta["history"][-1]["action"] == "dev.submit-acceptance"

    data = ver_json(developing)
    assert data["code_version"]
    assert data["prd_hash"].startswith("sha256:")
    assert data["verification_summary"]["pass"] == 3
    assert data["submission"]["checklist"][0]["ac"] == AC_1
    report = ver_md(developing)
    assert "代码版本" in report
    assert "PRD 基线" in report


def test_submit_acceptance_allows_not_applicable_with_reason(developing: Path) -> None:
    run(developing, "dev", "verify", "--prd", PRD_ID, "--suite", "unit",
        "--command", PASS_CMD, "--ac", f"{AC_1},{AC_2}")
    run(developing, "dev", "verify", "--prd", PRD_ID, "--suite", "e2e", "--ac", AC_3,
        "--not-applicable", "--reason", "本环境无外部存储")

    result = run(developing, "dev", "submit-acceptance", "--prd", PRD_ID)

    assert result.json["data"]["prd"]["status"] == "READY_FOR_ACCEPTANCE"
    preview = {item["ac"]: item for item in result.json["data"]["submission"]["checklist_preview"]}
    assert preview[AC_3]["result"] == "not-applicable"
    assert preview[AC_3]["reason"] == "本环境无外部存储"


def test_submit_acceptance_rejected_from_wrong_state(approved: Path) -> None:
    result = run(approved, "dev", "submit-acceptance", "--prd", PRD_ID, expect=1)

    assert "PRD_STATE_NOT_ALLOWED" in error_codes(result)
    assert prd_meta(approved)["status"] == "APPROVED"


def test_submit_acceptance_failure_has_no_side_effects(developing: Path) -> None:
    run(developing, "dev", "verify", "--prd", PRD_ID, "--suite", "unit",
        "--command", PASS_CMD, "--ac", AC_1)
    before_meta = read(prd_dir(developing) / "metadata.yaml")
    before_json = read(spec_dir(developing) / "verification.json")

    run(developing, "dev", "submit-acceptance", "--prd", PRD_ID, expect=1)

    assert read(prd_dir(developing) / "metadata.yaml") == before_meta
    assert read(spec_dir(developing) / "verification.json") == before_json
    assert not list((developing / ".neo-skill/locks").glob("*.lock"))


def test_submit_acceptance_detects_code_changed_after_verification(developing: Path) -> None:
    _verify_all_pass(developing)
    write(developing / "src/app.py", "print('late change')\n")

    result = run(developing, "dev", "submit-acceptance", "--prd", PRD_ID, expect=1)

    assert "VERIFICATION_INCOMPLETE" in error_codes(result) or \
           "BASELINE_STALE" in error_codes(result)
    assert prd_meta(developing)["status"] == "DEVELOPING"


# --------------------------------------------------------------------------
# 验证状态与基线失效
# --------------------------------------------------------------------------
def test_verification_status_before_any_evidence(developing: Path) -> None:
    result = run(developing, "dev", "verification-status", "--prd", PRD_ID)

    assert result.json["data"]["complete"] is False
    assert result.json["data"]["stale"] is False
    assert {item["ac"] for item in result.json["data"]["acs"]} == set(ALL_ACS)
    assert all(item["result"] == "not-run" for item in result.json["data"]["acs"])
    assert any("dev verify" in item for item in result.json["next_actions"])
    # 只读命令：不得创建任何验证产物
    assert not (spec_dir(developing) / "verification.json").exists()
    assert not (spec_dir(developing) / "verification.md").exists()
    assert not ver_dir(developing).exists()


def test_verification_status_after_submit_is_clean(developing: Path) -> None:
    _verify_all_pass(developing)
    run(developing, "dev", "submit-acceptance", "--prd", PRD_ID)

    result = run(developing, "dev", "verification-status", "--prd", PRD_ID)

    assert result.json["data"]["complete"] is True
    assert result.json["data"]["stale"] is False
    assert result.json["data"]["submitted"] is True


def test_prd_change_invalidates_submission_baseline(developing: Path) -> None:
    _verify_all_pass(developing)
    run(developing, "dev", "submit-acceptance", "--prd", PRD_ID)
    text = read(prd_dir(developing) / "prd.md")
    write(prd_dir(developing) / "prd.md", text + "\n<!-- 变更说明：范围调整 -->\n")

    result = run(developing, "dev", "verification-status", "--prd", PRD_ID, expect=1)

    assert "BASELINE_STALE" in error_codes(result)
    assert result.json["data"]["stale"] is True
    assert "PRD_CHANGED" in result.json["data"]["stale_reasons"]
    assert result.json["next_actions"]


def test_verification_status_detects_evidence_tampering(developing: Path) -> None:
    _verify_all_pass(developing)
    run(developing, "dev", "submit-acceptance", "--prd", PRD_ID)
    log = raw_logs(developing)[0]
    write(log, "伪造输出：全部通过\n")

    result = run(developing, "dev", "verification-status", "--prd", PRD_ID, expect=1)

    assert "BASELINE_STALE" in error_codes(result)
    assert "EVIDENCE_CHANGED" in result.json["data"]["stale_reasons"]
