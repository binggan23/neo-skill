"""ticket 01：并发锁、失败无副作用与写入原子性（黑盒 CLI）。

ticket 01 只有 `init` / `doctor` 两个命令，因此项目级写入用项目锁（对象 `PROJECT`）；
需求/PRD 对象锁的语义在 `tests/test_01_kernel.py` 覆盖，后续 ticket 的命令沿用同一实现。
"""

from __future__ import annotations

import json
import os
from pathlib import Path

from conftest import read, run_cli, write


def _seed_lock(root: Path, object_id: str, pid: int, command: str) -> Path:
    path = root / f".neo-skill/locks/{object_id}.lock"
    write(
        path,
        json.dumps(
            {
                "object": object_id,
                "pid": pid,
                "created_at": "2026-01-01T10:00:00+08:00",
                "command": command,
                "rules_version": 1,
            },
            ensure_ascii=False,
        ),
    )
    return path


def test_init_rejects_concurrent_write_with_lock_conflict(project: Path) -> None:
    _seed_lock(project, "PROJECT", os.getpid(), "init")

    result = run_cli(project, "init")

    assert result.code == 1
    assert result.json["success"] is False
    assert "LOCK_CONFLICT" in result.error_codes()
    assert result.json["errors"][0]["object"] == "PROJECT"
    assert "init" in result.json["errors"][0]["message"]
    # 失败无副作用：没有创建任何骨架
    assert not (project / "README.md").exists()
    assert not (project / "03-requirements").exists()


def test_lock_conflict_suggests_recover(project: Path) -> None:
    _seed_lock(project, "PROJECT", 999999, "init")

    result = run_cli(project, "init")

    assert result.code == 1
    suggestion = result.json["errors"][0]["suggestion"]
    assert "recover --release-lock" in suggestion


def test_successful_init_releases_project_lock(project: Path) -> None:
    assert run_cli(project, "init").code == 0

    assert not (project / ".neo-skill/locks/PROJECT.lock").exists()
    assert list((project / ".neo-skill/locks").glob("*.lock")) == []


def test_init_is_rerunnable_after_lock_released(project: Path) -> None:
    assert run_cli(project, "init").code == 0

    assert run_cli(project, "init").code == 0
    assert not (project / ".neo-skill/locks/PROJECT.lock").exists()


def test_failed_init_does_not_modify_existing_project(project: Path) -> None:
    assert run_cli(project, "init").code == 0
    before = {
        p.relative_to(project).as_posix(): read(p)
        for p in sorted(project.rglob("*"))
        if p.is_file()
    }

    failed = run_cli(project, "init", "--prune", "04-prds")

    assert failed.code == 1
    after = {
        p.relative_to(project).as_posix(): read(p)
        for p in sorted(project.rglob("*"))
        if p.is_file()
    }
    assert after == before


def test_planned_conflict_does_not_modify_user_file(project: Path) -> None:
    original = "# 团队 README\n\n保留这段。\n"
    write(project / "README.md", original)

    first = run_cli(project, "init")
    second = run_cli(project, "init")

    assert first.code == second.code == 0
    assert read(project / "README.md") == original
    assert second.actions().get("README.md") == "planned"


def test_no_partial_files_left_after_conflict_plan(project: Path) -> None:
    write(project / "README.md", "# 已有\n")

    assert run_cli(project, "init").code == 0

    temp_files = [p for p in project.rglob("*") if p.name.endswith(".tmp")]
    assert temp_files == []


def test_doctor_does_not_clear_stale_lock(project: Path) -> None:
    assert run_cli(project, "init").code == 0
    lock = _seed_lock(project, "REQ-001", 999999, "req.plan")

    run_cli(project, "doctor")

    assert lock.is_file()
