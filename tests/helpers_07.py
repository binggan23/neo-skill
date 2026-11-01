"""ticket 07 测试共用夹具：把项目推进到 `READY_FOR_ACCEPTANCE`（含验证证据）。

全部通过 CLI 命令搭建对象（共享设计 §10：不手写 metadata 绕过 CLI）。
用到的验证命令一律极轻量（`python3 -c`），不嵌套 pytest、不跑重量级命令。
"""

from __future__ import annotations

import json
from pathlib import Path

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
"""

PASS_CMD = 'python3 -c "print(1)"'


def run(project: Path, *args: str, expect: int = 0, env: dict | None = None):
    result = run_cli(project, *args, env=env if env is not None else ENV)
    assert result.code == expect, result
    return result


def acceptance_dir(project: Path, prd_id: str = PRD_ID) -> Path:
    return project / "07-acceptance" / MODULE / f"{prd_id}-{SLUG}"


def checklist_yaml(project: Path, prd_id: str = PRD_ID) -> dict:
    path = acceptance_dir(project, prd_id) / "checklist.yaml"
    assert path.is_file(), f"缺少 {path}"
    data = yaml.safe_load(read(path))
    assert isinstance(data, dict)
    return data


def items_by_ac(project: Path, prd_id: str = PRD_ID) -> dict:
    return {item["ac"]: item for item in checklist_yaml(project, prd_id)["items"]}


def checklist_md(project: Path, prd_id: str = PRD_ID) -> str:
    return read(acceptance_dir(project, prd_id) / "checklist.md")


def demo_md(project: Path, prd_id: str = PRD_ID) -> str:
    return read(acceptance_dir(project, prd_id) / "demo.md")


def report_md(project: Path, prd_id: str = PRD_ID) -> str:
    return read(acceptance_dir(project, prd_id) / "report.md")


def ver_json(project: Path, prd_id: str = PRD_ID) -> dict:
    path = project / "06-specs" / MODULE / f"{prd_id}-{SLUG}" / "verification.json"
    assert path.is_file(), f"缺少 {path}"
    return json.loads(read(path))


def raw_logs(project: Path, prd_id: str = PRD_ID) -> list[Path]:
    directory = project / "06-specs" / MODULE / f"{prd_id}-{SLUG}" / "verification"
    return sorted(directory.glob("raw-*.log")) if directory.is_dir() else []


def approved(project: Path) -> Path:
    """APPROVED PRD + 已确认 constitution + 有效团队。"""
    planned_req(project, title="简历批量解析", slug=SLUG)
    create_prd(project, scenarios="SC-RES-01,SC-RES-02")
    update_body(project, VALID_PRD)
    run(project, "prd", "submit-review", "--id", PRD_ID, "--actor", "bob", "--confirm")
    run(project, "prd", "approve", "--id", PRD_ID, "--actor", "alice", "--confirm")
    write(project / ".specify/constitution.md", CONSTITUTION)
    return project


def developing(project: Path) -> Path:
    approved(project)
    run(project, "dev", "start", "--prd", PRD_ID, "--actor", "carol", "--confirm")
    return project


def verify_all_pass(project: Path) -> Path:
    run(project, "dev", "verify", "--prd", PRD_ID, "--suite", "unit",
        "--command", PASS_CMD, "--ac", f"{AC_1},{AC_2}")
    run(project, "dev", "verify", "--prd", PRD_ID, "--suite", "integration",
        "--command", PASS_CMD, "--ac", AC_3)
    return project


def ready(project: Path) -> Path:
    """完整链路：DEVELOPING → 全部验证通过 → READY_FOR_ACCEPTANCE。"""
    developing(project)
    verify_all_pass(project)
    run(project, "dev", "submit-acceptance", "--prd", PRD_ID, "--actor", "carol")
    assert prd_meta(project)["status"] == "READY_FOR_ACCEPTANCE"
    return project


def prepared(project: Path) -> Path:
    """`ready` + `acceptance prepare`（Demo 与 checklist 已生成）。"""
    ready(project)
    run(project, "acceptance", "prepare", "--prd", PRD_ID)
    return project


def record(project: Path, ac: str, result: str, *, actor: str = "alice",
           evidence: str | None = None, note: str | None = None,
           expect: int = 0):
    args = ["acceptance", "record", "--prd", PRD_ID, "--ac", ac, "--result", result,
            "--actor", actor]
    if evidence is not None:
        args += ["--evidence", evidence]
    if note is not None:
        args += ["--note", note]
    return run(project, *args, expect=expect)


def record_all_pass(project: Path) -> Path:
    for ac in ALL_ACS:
        record(project, ac, "pass", evidence=f"{ac} 人工演示通过")
    return project


def approve(project: Path, *, actor: str = "alice", expect: int = 0,
            extra: tuple[str, ...] = ()):
    return run(project, "acceptance", "approve", "--prd", PRD_ID, "--actor", actor,
               "--confirm", *extra, expect=expect)


def error_codes(result) -> set[str]:
    return {item["code"] for item in (result.json or {}).get("errors", [])}


def prd_metadata_text(project: Path) -> str:
    return read(prd_dir(project) / "metadata.yaml")
