"""ticket 05：开发门禁、开发启动、状态报告与范围检查（黑盒 CLI）。

覆盖 ticket 05 验收项：未绑定、PRD 不存在、DRAFT 被拒、hash 失效、constitution 未确认、
团队未配置、引用关系不匹配、模块目录不一致、门禁失败零写入、合法启动、幂等重启、
`dev status` 报告基线、`scope-check` 越界被拒。

全部通过 `run_cli` 进程边界观察行为；只有「构造违规状态」的用例直接改文件
（共享设计 §10 允许）。
"""

from __future__ import annotations

import shutil
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

SPEC_FILES = ("spec.md", "plan.md", "tasks.md")
BASELINE_FILE = ".baseline.yaml"

CONSTITUTION_CONFIRMED = """---
confirmed: true
confirmed_by: alice
confirmed_at: 2026-03-01T09:00:00+08:00
---

# 项目约束（constitution）

## 技术栈

Python 3.11 + pytest；仅新增必要依赖。

## 测试要求

- 每个改动必须补测试；失败修复上限 3 轮。
"""

CONSTITUTION_UNCONFIRMED = CONSTITUTION_CONFIRMED.replace("confirmed: true", "confirmed: false")


def error_codes(result) -> set[str]:
    return {item["code"] for item in (result.json or {}).get("errors", [])}


def confirm_constitution(project: Path, text: str = CONSTITUTION_CONFIRMED) -> None:
    write(project / ".specify/constitution.md", text)


def spec_dir(project: Path, prd_id: str = "PRD-001", module: str = "resume",
             slug: str = "batch-parse") -> Path:
    return project / "06-specs" / module / f"{prd_id}-{slug}"


def baseline(project: Path) -> dict:
    path = spec_dir(project) / BASELINE_FILE
    data = yaml.safe_load(read(path))
    assert isinstance(data, dict)
    return data


def front_matter(path: Path) -> dict:
    head = read(path).split("---\n", 2)[1]
    data = yaml.safe_load(head)
    assert isinstance(data, dict)
    return data


def start(project: Path, *extra: str, actor: str = "carol", expect: int = 0):
    result = run_cli(project, "dev", "start", "--prd", "PRD-001", "--actor", actor,
                     "--confirm", *extra, env=ENV)
    assert result.code == expect, result
    return result


@pytest.fixture()
def draft(inited_project: Path) -> Path:
    """DRAFT PRD（主需求已 IN_PRD）。"""
    planned_req(inited_project, title="简历批量解析", slug="batch-parse")
    create_prd(inited_project, scenarios="SC-RES-01,SC-RES-02")
    update_body(inited_project, VALID_PRD)
    return inited_project


@pytest.fixture()
def approved(inited_project: Path) -> Path:
    """APPROVED PRD + 已确认 constitution + 有效团队。"""
    planned_req(inited_project, title="简历批量解析", slug="batch-parse")
    create_prd(inited_project, scenarios="SC-RES-01,SC-RES-02")
    update_body(inited_project, VALID_PRD)
    submitted = run_cli(inited_project, "prd", "submit-review", "--id", "PRD-001",
                        "--actor", "bob", "--confirm", env=ENV)
    assert submitted.code == 0, submitted
    approved_result = run_cli(inited_project, "prd", "approve", "--id", "PRD-001",
                              "--actor", "alice", "--confirm", env=ENV)
    assert approved_result.code == 0, approved_result
    confirm_constitution(inited_project)
    return inited_project


@pytest.fixture()
def started(approved: Path) -> Path:
    start(approved)
    return approved


# --------------------------------------------------------------------------
# dev gate：绑定与存在性
# --------------------------------------------------------------------------
def test_gate_requires_explicit_prd_binding(inited_project: Path) -> None:
    result = run_cli(inited_project, "dev", "gate")

    assert result.code == 1
    assert "PRD_NOT_BOUND" in error_codes(result)
    message = result.json["errors"][0]["message"]
    assert "必须显式绑定" in message
    assert result.check("binding")["ok"] is False


def test_gate_rejects_unknown_prd(inited_project: Path) -> None:
    result = run_cli(inited_project, "dev", "gate", "--prd", "PRD-404")

    assert result.code == 1
    assert "PRD_NOT_FOUND" in error_codes(result)
    assert result.check("prd_exists")["ok"] is False


def test_gate_rejects_draft_prd_and_writes_nothing(draft: Path) -> None:
    result = run_cli(draft, "dev", "gate", "--prd", "PRD-001")

    assert result.code == 1
    assert "PRD_STATE_NOT_ALLOWED" in error_codes(result)
    assert result.check("prd_state")["ok"] is False
    assert prd_meta(draft)["status"] == "DRAFT"
    assert not spec_dir(draft).exists()


# --------------------------------------------------------------------------
# dev gate：批准记录、hash、constitution、团队
# --------------------------------------------------------------------------
def test_gate_reports_not_approved_and_missing_baseline(draft: Path) -> None:
    result = run_cli(draft, "dev", "gate", "--prd", "PRD-001")

    assert result.code == 1
    codes = error_codes(result)
    assert "NOT_APPROVED" in codes
    assert "BASELINE_MISSING" in codes
    assert result.check("approval")["ok"] is False
    assert result.check("baseline_hash")["ok"] is False


def test_gate_rejects_hash_mismatch_and_suggests_cr(approved: Path) -> None:
    body = prd_dir(approved) / "prd.md"
    write(body, read(body) + "\n## 追加的范围\n\n直接改了已批准内容。\n")

    result = run_cli(approved, "dev", "gate", "--prd", "PRD-001")

    assert result.code == 1
    assert "HASH_MISMATCH" in error_codes(result)
    assert result.check("baseline_hash")["ok"] is False
    joined = " ".join(result.json.get("next_actions", []))
    assert "prd cr" in joined


def test_gate_rejects_unconfirmed_constitution(approved: Path) -> None:
    confirm_constitution(approved, CONSTITUTION_UNCONFIRMED)

    result = run_cli(approved, "dev", "gate", "--prd", "PRD-001")

    assert result.code == 1
    assert "CONSTITUTION_NOT_CONFIRMED" in error_codes(result)
    assert result.check("constitution")["ok"] is False


def test_gate_rejects_missing_constitution(approved: Path) -> None:
    (approved / ".specify/constitution.md").unlink()

    result = run_cli(approved, "dev", "gate", "--prd", "PRD-001")

    assert result.code == 1
    assert "CONSTITUTION_MISSING" in error_codes(result)


def test_gate_rejects_unconfigured_team(approved: Path) -> None:
    write(approved / "00-management/team.md", """---
confirmed: false
members: []
---

# 团队与职责
""")

    result = run_cli(approved, "dev", "gate", "--prd", "PRD-001")

    assert result.code == 1
    assert "TEAM_NOT_CONFIGURED" in error_codes(result)
    assert result.check("team")["ok"] is False


# --------------------------------------------------------------------------
# dev gate：引用关系与模块一致性
# --------------------------------------------------------------------------
def test_gate_rejects_primary_req_not_in_prd(approved: Path) -> None:
    """source_reqs 指向的 REQ 必须处于 IN_PRD/DONE 且 prd 字段指回本 PRD。"""
    item = sorted((approved / "03-requirements/items").glob("REQ-001-*.md"))[0]
    write(item, read(item).replace("status: IN_PRD", "status: TRIAGED"))

    result = run_cli(approved, "dev", "gate", "--prd", "PRD-001")

    assert result.code == 1
    assert "REQ_REFERENCE_INVALID" in error_codes(result)
    assert result.check("references")["ok"] is False


def test_gate_rejects_multiple_or_missing_source_reqs(approved: Path) -> None:
    meta_path = prd_dir(approved) / "metadata.yaml"
    meta = yaml.safe_load(read(meta_path))
    meta["source_reqs"] = []
    write(meta_path, yaml.safe_dump(meta, sort_keys=False, allow_unicode=True))

    result = run_cli(approved, "dev", "gate", "--prd", "PRD-001")

    assert result.code == 1
    assert "REQ_REFERENCE_INVALID" in error_codes(result)


def test_gate_rejects_module_directory_mismatch(approved: Path) -> None:
    source = prd_dir(approved)
    target = approved / "04-prds" / "billing" / source.name
    target.parent.mkdir(parents=True, exist_ok=True)
    shutil.move(str(source), str(target))

    result = run_cli(approved, "dev", "gate", "--prd", "PRD-001")

    assert result.code == 1
    assert "MODULE_MISMATCH" in error_codes(result)
    assert result.check("module")["ok"] is False


# --------------------------------------------------------------------------
# dev gate 通过 / dev start 零副作用
# --------------------------------------------------------------------------
def test_gate_passes_on_approved_prd_without_writing(approved: Path) -> None:
    before = read(prd_dir(approved) / "metadata.yaml")

    result = run_cli(approved, "dev", "gate", "--prd", "PRD-001")

    assert result.code == 0, result
    binding = result.json["data"]["binding"]
    assert binding["prd"] == "PRD-001"
    assert binding["module"] == "resume"
    assert binding["version"] == 1
    assert binding["content_hash"].startswith("sha256:")
    checks = {item["name"]: item for item in result.json["data"]["checks"]}
    assert set(checks) >= {"binding", "prd_exists", "prd_state", "approval",
                           "baseline_hash", "constitution", "team", "references", "module"}
    assert all(item["ok"] for item in checks.values())
    # 只检查不写：状态不变、不生成 Spec、不留锁
    assert read(prd_dir(approved) / "metadata.yaml") == before
    assert not spec_dir(approved).exists()
    assert not list((approved / ".neo-skill/locks").glob("*.lock"))


def test_start_failure_has_no_side_effects(approved: Path) -> None:
    confirm_constitution(approved, CONSTITUTION_UNCONFIRMED)
    before = read(prd_dir(approved) / "metadata.yaml")

    result = start(approved, expect=1)

    assert "CONSTITUTION_NOT_CONFIRMED" in error_codes(result)
    assert not spec_dir(approved).exists()
    assert read(prd_dir(approved) / "metadata.yaml") == before
    assert prd_meta(approved)["status"] == "APPROVED"
    assert not list((approved / "06-specs").glob("**/PRD-001-*"))


def test_start_requires_actor_and_confirm(approved: Path) -> None:
    missing_confirm = run_cli(approved, "dev", "start", "--prd", "PRD-001",
                              "--actor", "carol", env=ENV)
    missing_actor = run_cli(approved, "dev", "start", "--prd", "PRD-001", "--confirm", env=ENV)

    assert missing_confirm.code == 1
    assert "CONFIRMATION_REQUIRED" in error_codes(missing_confirm)
    assert missing_actor.code == 1
    assert "CONFIRMATION_REQUIRED" in error_codes(missing_actor)
    assert not spec_dir(approved).exists()


def test_start_rejects_actor_without_developer_role(approved: Path) -> None:
    result = start(approved, actor="bob", expect=1)

    assert "ROLE_MISMATCH" in error_codes(result)
    assert not spec_dir(approved).exists()
    assert prd_meta(approved)["status"] == "APPROVED"


# --------------------------------------------------------------------------
# 合法启动
# --------------------------------------------------------------------------
def test_start_creates_spec_plan_tasks_and_baseline(started: Path) -> None:
    directory = spec_dir(started)
    for name in SPEC_FILES:
        assert (directory / name).is_file(), name
    assert (directory / BASELINE_FILE).is_file()

    meta = prd_meta(started)
    assert meta["status"] == "DEVELOPING"
    assert meta["history"][-1]["to"] == "DEVELOPING"
    assert meta["history"][-1]["by"] == "carol"
    assert meta["history"][-1]["action"] == "dev.start"

    data = baseline(started)
    assert data["prd"] == "PRD-001"
    assert data["baseline_hash"] == meta["content_hash"]
    assert data["baseline_version"] == meta["version"]
    assert data["baseline_valid"] is True

    for name in SPEC_FILES:
        head = front_matter(directory / name)
        assert head["prd"] == "PRD-001"
        assert head["baseline_hash"] == meta["content_hash"]
        assert head["baseline_version"] == meta["version"]
        assert head["module"] == "resume"
        assert head["status"] == "DRAFT"


def test_start_records_created_artifacts(approved: Path) -> None:
    result = start(approved)

    actions = result.actions()
    prefix = "06-specs/resume/PRD-001-batch-parse/"
    for name in SPEC_FILES:
        assert actions.get(prefix + name) == "created", actions
    assert actions.get(prefix + BASELINE_FILE) == "created"
    binding = result.json["data"]["binding"]
    assert binding["prd"] == "PRD-001"
    assert binding["spec_dir"] == "06-specs/resume/PRD-001-batch-parse"


# --------------------------------------------------------------------------
# 幂等重启
# --------------------------------------------------------------------------
def test_start_is_idempotent_for_developing_prd(started: Path) -> None:
    spec = spec_dir(started) / "spec.md"
    write(spec, read(spec) + "\n## 人工补充\n\n保留这段内容。\n")
    history_before = prd_meta(started)["history"]

    result = start(started)

    assert result.code == 0
    assert "保留这段内容" in read(spec)
    meta = prd_meta(started)
    assert meta["status"] == "DEVELOPING"
    assert meta["history"] == history_before
    assert all(item["action"] != "created" for item in result.json["artifacts"])


def test_start_fills_only_missing_files_on_restart(started: Path) -> None:
    tasks = spec_dir(started) / "tasks.md"
    tasks.unlink()

    result = start(started)

    assert result.code == 0
    assert (spec_dir(started) / "tasks.md").is_file()
    actions = result.actions()
    assert actions.get("06-specs/resume/PRD-001-batch-parse/tasks.md") == "created"


def test_start_blocks_restart_when_baseline_invalidated(started: Path) -> None:
    """ticket 04 会把 .baseline.yaml 的 baseline_valid 置 false；重启必须被拦。"""
    path = spec_dir(started) / BASELINE_FILE
    data = baseline(started)
    data["baseline_valid"] = False
    write(path, yaml.safe_dump(data, sort_keys=False, allow_unicode=True))

    result = start(started, expect=1)

    assert "BASELINE_INVALID" in error_codes(result)


# --------------------------------------------------------------------------
# dev status
# --------------------------------------------------------------------------
def test_status_reports_missing_specs_before_start(approved: Path) -> None:
    result = run_cli(approved, "dev", "status", "--prd", "PRD-001")

    assert result.code == 0, result
    data = result.json["data"]
    assert data["files"] == {"spec": False, "plan": False, "tasks": False}
    assert data["baseline_valid"] is False
    assert data["spec_dir"] == "06-specs/resume/PRD-001-batch-parse"


def test_status_reports_valid_baseline_after_start(started: Path) -> None:
    result = run_cli(started, "dev", "status", "--prd", "PRD-001")

    assert result.code == 0, result
    data = result.json["data"]
    assert data["files"] == {"spec": True, "plan": True, "tasks": True}
    assert data["hash_matches"] is True
    assert data["baseline_valid"] is True
    assert data["baseline_hash"] == prd_meta(started)["content_hash"]


def test_status_detects_prd_content_change(started: Path) -> None:
    body = prd_dir(started) / "prd.md"
    write(body, read(body) + "\n## 悄悄改动\n\n没有走 CR。\n")

    result = run_cli(started, "dev", "status", "--prd", "PRD-001")

    assert result.code == 0, result
    data = result.json["data"]
    assert data["hash_matches"] is False
    assert data["baseline_valid"] is False


def test_status_reads_baseline_valid_flag_written_by_cr(started: Path) -> None:
    path = spec_dir(started) / BASELINE_FILE
    data = baseline(started)
    data["baseline_valid"] = False
    data["invalidated_by"] = "CR-001"
    write(path, yaml.safe_dump(data, sort_keys=False, allow_unicode=True))

    result = run_cli(started, "dev", "status", "--prd", "PRD-001")

    assert result.code == 0, result
    data = result.json["data"]
    assert data["baseline_valid"] is False
    assert data["baseline"]["invalidated_by"] == "CR-001"


def test_status_rejects_unknown_prd(inited_project: Path) -> None:
    result = run_cli(inited_project, "dev", "status", "--prd", "PRD-404")

    assert result.code == 1
    assert "PRD_NOT_FOUND" in error_codes(result)


# --------------------------------------------------------------------------
# dev scope-check
# --------------------------------------------------------------------------
def test_scope_check_passes_for_generated_specs(started: Path) -> None:
    result = run_cli(started, "dev", "scope-check", "--prd", "PRD-001")

    assert result.code == 0, result
    scope = result.json["data"]["scope"]
    assert scope["unknown_scenarios"] == []
    assert sorted(scope["referenced_scenarios"]) == ["SC-RES-01", "SC-RES-02"]
    assert scope["path_violations"] == []


def test_scope_check_rejects_scenario_outside_prd(started: Path) -> None:
    tasks = spec_dir(started) / "tasks.md"
    write(tasks, read(tasks) + "\n| 顺手多做一点 | SC-RES-99 | TODO | <!-- TODO --> |\n")

    result = run_cli(started, "dev", "scope-check", "--prd", "PRD-001")

    assert result.code == 1
    assert "SCOPE_EXCEEDED" in error_codes(result)
    message = " ".join(item["message"] for item in result.json["errors"])
    assert "SC-RES-99" in message
    suggestion = " ".join(filter(None, (item.get("suggestion") or ""
                                        for item in result.json["errors"])))
    assert "CR" in suggestion
    assert "不得自动修改 PRD" in suggestion


def test_scope_check_rejects_ac_of_unknown_scenario(started: Path) -> None:
    spec = spec_dir(started) / "spec.md"
    write(spec, read(spec) + "\n- AC-RES-88-01 顺手加的验收标准\n")

    result = run_cli(started, "dev", "scope-check", "--prd", "PRD-001")

    assert result.code == 1
    assert "SCOPE_EXCEEDED" in error_codes(result)
    assert "AC-RES-88-01" in " ".join(item["message"] for item in result.json["errors"])


def test_scope_check_accepts_declared_paths(started: Path) -> None:
    result = run_cli(started, "dev", "scope-check", "--prd", "PRD-001",
                     "--paths", "src/resume/parse.py,tests/test_parse.py,"
                                "06-specs/resume/PRD-001-batch-parse/spec.md")

    assert result.code == 0, result
    assert result.json["data"]["scope"]["path_violations"] == []


def test_scope_check_rejects_paths_outside_src_tests_specs(started: Path) -> None:
    result = run_cli(started, "dev", "scope-check", "--prd", "PRD-001",
                     "--paths", "src/resume/parse.py,04-prds/resume/PRD-001-batch-parse/prd.md,"
                                "../outside/hack.py")

    assert result.code == 1
    assert "SCOPE_EXCEEDED" in error_codes(result)
    violations = result.json["data"]["scope"]["path_violations"]
    assert "04-prds/resume/PRD-001-batch-parse/prd.md" in violations
    assert any("outside" in item for item in violations)
    assert "src/resume/parse.py" not in violations


def test_scope_check_requires_specs(started: Path) -> None:
    shutil.rmtree(spec_dir(started))

    result = run_cli(started, "dev", "scope-check", "--prd", "PRD-001")

    assert result.code == 1
    assert "SPEC_NOT_FOUND" in error_codes(result)


# --------------------------------------------------------------------------
# CR 重新批准后按新基线重建（ticket 04 产生的状态，ticket 05 必须能接住）
# --------------------------------------------------------------------------
def test_start_rebuilds_baseline_after_cr_reapproval(started: Path) -> None:
    """模拟 CR 重新批准：PRD 回到 APPROVED + 新版本/新 hash，旧 Spec 基线失效。"""
    body = prd_dir(started) / "prd.md"
    write(body, read(body) + "\n## 追加场景说明\n\nCR-001 之后的内容。\n")
    # CR 重新批准：版本 +1、状态回 APPROVED；hash 由 flow-core 按共享设计 §6 计算。
    meta_path = prd_dir(started) / "metadata.yaml"
    meta = yaml.safe_load(read(meta_path))
    meta.update({"status": "APPROVED", "version": 2})
    write(meta_path, yaml.safe_dump(meta, sort_keys=False, allow_unicode=True))
    new_hash = run_cli(started, "dev", "status", "--prd", "PRD-001").json["data"]["current_hash"]
    meta["content_hash"] = new_hash
    write(meta_path, yaml.safe_dump(meta, sort_keys=False, allow_unicode=True))
    base_path = spec_dir(started) / BASELINE_FILE
    data = baseline(started)
    data["baseline_valid"] = False
    data["invalidated_by"] = "CR-001"
    write(base_path, yaml.safe_dump(data, sort_keys=False, allow_unicode=True))

    result = start(started)

    assert result.code == 0, result
    assert prd_meta(started)["status"] == "DEVELOPING"
    rebuilt = baseline(started)
    assert rebuilt["baseline_hash"] == new_hash
    assert rebuilt["baseline_version"] == 2
    assert rebuilt["baseline_valid"] is True
    assert "invalidated_by" not in rebuilt
    for name in SPEC_FILES:
        head = front_matter(spec_dir(started) / name)
        assert head["baseline_hash"] == new_hash
        assert head["baseline_version"] == 2
