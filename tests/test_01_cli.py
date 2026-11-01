"""ticket 01：CLI 公共契约（信封、全局参数、退出码、人类输出）。"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from conftest import FLOW_CORE, run_cli, write


@pytest.mark.parametrize("args", [("init",), ("doctor",)])
def test_envelope_always_has_contract_keys(project: Path, args: tuple[str, ...]) -> None:
    result = run_cli(project, *args)

    payload = result.json
    assert set(payload) >= {"success", "command", "artifacts", "errors", "next_actions"}
    assert isinstance(payload["artifacts"], list)
    assert isinstance(payload["errors"], list)
    assert isinstance(payload["next_actions"], list)
    assert isinstance(payload["success"], bool)
    assert payload["command"] == args[0]


def test_artifacts_use_contract_actions(project: Path) -> None:
    result = run_cli(project, "init")

    allowed = {"created", "updated", "unchanged", "planned", "staged"}
    assert {item["action"] for item in result.json["artifacts"]} <= allowed
    for item in result.json["artifacts"]:
        assert set(item) == {"path", "kind", "action"}
        assert item["path"]
        assert item["kind"]


def test_error_entries_carry_code_and_message(project: Path) -> None:
    result = run_cli(project, "init", "--prune", "04-prds")

    assert result.code == 1
    error = result.json["errors"][0]
    assert error["code"] == "NOT_PRUNABLE"
    assert error["message"]
    assert error["object"] == "04-prds"
    assert error["suggestion"]
    assert result.json["success"] is False


def test_global_flags_accepted_before_and_after_subcommand(project: Path) -> None:
    import subprocess
    import sys

    def invoke(*argv: str) -> subprocess.CompletedProcess[str]:
        return subprocess.run(
            [sys.executable, str(FLOW_CORE), *argv],
            capture_output=True,
            text=True,
            cwd=str(project),
            timeout=60,
        )

    # --json 在子命令前 / 后都应得到同一信封
    before = invoke("--root", str(project), "--json", "doctor")
    after = invoke("--root", str(project), "doctor", "--json")

    assert before.returncode == after.returncode == 0
    assert json.loads(before.stdout)["command"] == json.loads(after.stdout)["command"] == "doctor"

    # 全局 --actor 在任何命令上都不应触发用法错误
    with_actor = invoke("--root", str(project), "--actor", "alice", "--json", "doctor")
    assert with_actor.returncode == 0


def test_root_flag_omitted_uses_cwd_discovery(inited_project: Path) -> None:
    nested = inited_project / "src"
    nested.mkdir(exist_ok=True)

    result = run_cli(inited_project, "doctor", root_flag=False, cwd=nested)

    assert result.code == 0
    assert result.json["data"]["runtime"]["root"] == str(inited_project.resolve())


def test_version_flag_exits_zero(project: Path) -> None:
    import subprocess
    import sys

    proc = subprocess.run(
        [sys.executable, str(FLOW_CORE), "--version"],
        capture_output=True,
        text=True,
        cwd=str(project),
        timeout=60,
    )

    assert proc.returncode == 0
    assert "flow-core" in proc.stdout


def test_missing_required_argument_is_usage_error(project: Path) -> None:
    import subprocess
    import sys

    proc = subprocess.run(
        [sys.executable, str(FLOW_CORE), "init", "--prune"],
        capture_output=True,
        text=True,
        cwd=str(project),
        timeout=60,
    )

    assert proc.returncode == 2


def test_human_failure_output_uses_error_prefix(project: Path) -> None:
    result = run_cli(project, "init", "--prune", "nonsense", json_output=False)

    assert result.code == 1
    assert "ERROR:" in result.stdout
    assert "UNKNOWN_PRUNE_GROUP" in result.stdout
    assert "建议：" in result.stdout
    assert '"errors"' not in result.stdout


def test_flow_core_entry_is_executable() -> None:
    import os

    assert FLOW_CORE.is_file()
    assert os.access(FLOW_CORE, os.X_OK)


def test_init_conflict_plan_keeps_exit_zero_and_lists_next_action(project: Path) -> None:
    write(project / "README.md", "# 已有说明\n")

    result = run_cli(project, "init")

    assert result.code == 0
    assert result.json["success"] is True
    assert result.json["data"]["pending_conflicts"] == ["README.md"]
    assert result.json["next_actions"][0] == "flow-core init --apply-conflicts"


def test_bare_invocation_is_usage_error(project: Path) -> None:
    import subprocess
    import sys

    proc = subprocess.run(
        [sys.executable, str(FLOW_CORE)],
        capture_output=True,
        text=True,
        cwd=str(project),
        timeout=60,
    )

    assert proc.returncode == 2
    assert "init" in proc.stderr


@pytest.mark.parametrize("group", ["acceptance", "lint", "index", "recover"])
def test_future_command_groups_are_not_registered_as_empty_shells(
    project: Path, group: str
) -> None:
    """未实现的命令组不得注册：调用应是用法错误，而不是伪装成功。

    `req`（ticket 02）、`prd`（ticket 03）、`dev`（ticket 05）实现后已从本参数化列表移除（命令组按 ticket 逐个上线）。
    """
    result = run_cli(project, group, "list")

    assert result.code == 2
    assert result.json is None


def test_doctor_reports_corrupt_lock_file_without_crashing(project: Path) -> None:
    assert run_cli(project, "init").code == 0
    write(project / ".neo-skill/locks/BROKEN.lock", "not json at all")

    result = run_cli(project, "doctor")

    assert result.code == 0
    locks_check = result.check("locks")
    assert locks_check["status"] == "warn"
    assert "BROKEN" in locks_check["detail"]
    # 不自动删除，也不因损坏文件崩溃
    assert (project / ".neo-skill/locks/BROKEN.lock").is_file()
