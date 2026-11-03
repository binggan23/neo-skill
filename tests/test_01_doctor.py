"""ticket 01：`doctor` 环境诊断与修复建议（黑盒 CLI）。"""

from __future__ import annotations

from pathlib import Path

from conftest import read, run_cli, write


def test_doctor_reports_all_checks(project: Path) -> None:
    assert run_cli(project, "init").code == 0

    result = run_cli(project, "doctor")

    assert result.code == 0
    assert result.json["success"] is True
    assert result.json["command"] == "doctor"
    names = {check["name"] for check in result.json["checks"]}
    assert {"python", "node", "flow-core", "rules", "templates", "project", "locks"} <= names
    for check in result.json["checks"]:
        assert check["status"] in {"ok", "warn", "fail"}
        assert check["detail"]


def test_doctor_reports_its_own_runtime_paths(project: Path) -> None:
    assert run_cli(project, "init").code == 0

    result = run_cli(project, "doctor")

    runtime = result.json["data"]["runtime"]
    assert Path(runtime["flow_core"]).is_file()
    assert Path(runtime["runtime_dir"]).is_dir()
    assert runtime["root"] == str(project.resolve())
    assert runtime["rules_origin"] == "project"


def test_doctor_warns_uninitialized_project_and_suggests_init(project: Path) -> None:
    result = run_cli(project, "doctor")

    assert result.code == 0
    assert result.check("project")["status"] == "warn"
    assert "flow-core init" in result.json["next_actions"]
    # 未初始化时 rules 与 project 两项都会建议 init —— 同一条建议只能出现一次，
    # 否则用户看到重复的下一步，误以为有两件事要做。
    assert result.json["next_actions"].count("flow-core init") == 1


def test_doctor_fails_with_nonzero_exit_when_root_missing(tmp_path: Path) -> None:
    missing = tmp_path / "nope"

    result = run_cli(missing, "doctor", cwd=tmp_path)

    assert result.code == 1
    assert result.json["success"] is False
    assert "DOCTOR_CHECK_FAILED" in result.error_codes()
    assert result.check("project")["status"] == "fail"


def test_doctor_reports_stale_lock_without_deleting_it(inited_project: Path) -> None:
    lock = inited_project / ".neo-skill/locks/PRD-001.lock"
    write(
        lock,
        '{"object": "PRD-001", "pid": 999999, "created_at": "2026-01-01T10:00:00+08:00",'
        ' "command": "prd.approve", "rules_version": 1}',
    )

    result = run_cli(inited_project, "doctor")

    assert result.code == 0
    locks_check = result.check("locks")
    assert locks_check["status"] == "warn"
    assert "PRD-001" in locks_check["detail"]
    assert "recover --release-lock" in locks_check["suggestion"]
    # 不自动删除
    assert lock.is_file()


def test_doctor_reports_live_lock_as_ok(inited_project: Path) -> None:
    import os

    write(
        inited_project / ".neo-skill/locks/REQ-001.lock",
        f'{{"object": "REQ-001", "pid": {os.getpid()}, "created_at": "2026-01-01T10:00:00+08:00",'
        ' "command": "req.plan", "rules_version": 1}',
    )

    result = run_cli(inited_project, "doctor")

    assert result.code == 0
    assert result.check("locks")["status"] == "ok"


def test_doctor_warns_when_project_rules_baseline_missing(inited_project: Path) -> None:
    (inited_project / ".specify/rules/flow.yaml").unlink()

    result = run_cli(inited_project, "doctor")

    assert result.code == 0
    rules_check = result.check("rules")
    assert rules_check["status"] == "warn"
    assert rules_check["suggestion"] == "flow-core init"


def test_doctor_warns_missing_constitution(inited_project: Path) -> None:
    (inited_project / ".specify/constitution.md").unlink()

    result = run_cli(inited_project, "doctor")

    assert result.code == 0
    assert result.check("project")["status"] == "warn"
    assert "constitution" in result.check("project")["detail"]


def test_doctor_strict_turns_warnings_into_failure(project: Path) -> None:
    result = run_cli(project, "doctor", "--strict")

    assert result.code == 1
    assert result.json["success"] is False


def test_doctor_never_modifies_project(inited_project: Path) -> None:
    before = {p: read(p) for p in sorted(inited_project.rglob("*")) if p.is_file()}

    run_cli(inited_project, "doctor")

    after = {p: read(p) for p in sorted(inited_project.rglob("*")) if p.is_file()}
    assert after == before


def test_doctor_human_output_lists_checks(project: Path) -> None:
    result = run_cli(project, "doctor", json_output=False)

    assert result.code == 0
    assert "OK:" in result.stdout
    assert "python" in result.stdout
    assert "下一步" in result.stdout
