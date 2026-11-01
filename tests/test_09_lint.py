"""ticket 09：结构检查（`flow-core lint`）黑盒测试。

约定（共享设计 §10）：全部通过 CLI 进程边界断言——退出码、`--json` 信封、
`data.findings` 的 `code` 与 `object`。每类违规至少一条用例。

「构造违规状态」的用例才直接改文件（真实故障就是这样产生的：有人绕过了 CLI）；
其余链路一律用 CLI 命令搭建。
"""

from __future__ import annotations

from pathlib import Path

import yaml

import helpers_07 as h7
import helpers_08 as h8
from conftest import read, run_cli, write
from helpers_03 import ENV, TEAM, create_prd, planned_req, prd_dir, update_body

PRD_ID = "PRD-001"
REQ_ID = "REQ-001"


# --------------------------------------------------------------------------
# 调用与断言辅助
# --------------------------------------------------------------------------
def lint(project: Path, *args: str, env: dict | None = None):
    return run_cli(project, "lint", *args, env=env if env is not None else ENV)


def findings(result) -> list[dict]:
    assert result.json is not None, result
    return result.json["data"]["findings"]


def codes(result) -> set[str]:
    return {item["code"] for item in findings(result)}


def for_code(result, code: str) -> list[dict]:
    return [item for item in findings(result) if item["code"] == code]


def counts(result) -> dict:
    return result.json["data"]["counts"]


def error_codes(result) -> set[str]:
    return {item["code"] for item in (result.json or {}).get("errors", [])}


# --------------------------------------------------------------------------
# 夹具
# --------------------------------------------------------------------------
def initialized(project: Path) -> Path:
    """`init` + 已确认团队 + 已确认 constitution。"""
    inited = run_cli(project, "init")
    assert inited.code == 0, inited
    write(project / "00-management/team.md", TEAM)
    write(project / ".specify/constitution.md", h7.CONSTITUTION)
    return project


def snapshot(project: Path) -> dict[str, bytes]:
    return {
        path.relative_to(project).as_posix(): path.read_bytes()
        for path in sorted(project.rglob("*"))
        if path.is_file()
    }


def req_path(project: Path, req_id: str = REQ_ID) -> Path:
    return sorted((project / "03-requirements/items").glob(f"{req_id}-*.md"))[0]


def patch_req(project: Path, req_id: str = REQ_ID, **fields) -> Path:
    path = req_path(project, req_id)
    text = read(path)
    parts = text.split("---\n", 2)
    meta = yaml.safe_load(parts[1])
    meta.update(fields)
    write(path, "---\n" + yaml.safe_dump(meta, sort_keys=False, allow_unicode=True)
          + "---\n" + parts[2])
    return path


def patch_prd_meta(project: Path, **fields) -> Path:
    path = prd_dir(project) / "metadata.yaml"
    meta = yaml.safe_load(read(path))
    meta.update(fields)
    write(path, yaml.safe_dump(meta, sort_keys=False, allow_unicode=True))
    return path


# --------------------------------------------------------------------------
# 基线：干净项目与未初始化项目
# --------------------------------------------------------------------------
def test_clean_project_has_no_errors(project: Path):
    initialized(project)
    result = lint(project)
    assert result.code == 0, result
    assert counts(result)["error"] == 0, findings(result)
    assert result.json["success"] is True


def test_warnings_alone_exit_zero(project: Path):
    """`init` 生成的 backlog.md 还没有受管理区块 → 只应是 warning，退出码仍是 0。"""
    initialized(project)
    result = lint(project)
    assert result.code == 0, result
    assert "INDEX_STALE" in codes(result)
    assert all(item["severity"] == "warning" for item in for_code(result, "INDEX_STALE"))


def test_uninitialized_project_reports_not_initialized(project: Path):
    result = lint(project)
    assert result.code == 1, result
    assert "PROJECT_NOT_INITIALIZED" in codes(result)
    assert "PROJECT_NOT_INITIALIZED" in error_codes(result)


def test_missing_required_path(project: Path):
    initialized(project)
    (project / "02-definition/mvp-scope.md").unlink()
    result = lint(project)
    assert result.code == 1, result
    hits = for_code(result, "MISSING_REQUIRED_PATH")
    assert hits, findings(result)
    assert any("02-definition/mvp-scope.md" in item["path"] for item in hits)


def test_finding_shape(project: Path):
    initialized(project)
    (project / "00-management/charter.md").unlink()
    result = lint(project)
    hit = for_code(result, "MISSING_REQUIRED_PATH")[0]
    assert set(hit) >= {"code", "severity", "object", "message", "suggestion", "path"}
    assert hit["severity"] == "error"
    assert hit["suggestion"]


# --------------------------------------------------------------------------
# 治理前置：constitution / team
# --------------------------------------------------------------------------
def test_constitution_missing(project: Path):
    initialized(project)
    (project / ".specify/constitution.md").unlink()
    result = lint(project)
    assert result.code == 1, result
    assert "CONSTITUTION_MISSING" in codes(result)


def test_constitution_not_confirmed(project: Path):
    initialized(project)
    write(project / ".specify/constitution.md", "---\nconfirmed: false\n---\n\n# 约束\n")
    result = lint(project)
    assert result.code == 1, result
    assert "CONSTITUTION_NOT_CONFIRMED" in codes(result)


def test_team_not_configured(project: Path):
    initialized(project)
    write(project / "00-management/team.md", "---\nconfirmed: true\nmembers: []\n---\n\n# 团队\n")
    result = lint(project)
    assert result.code == 1, result
    assert "TEAM_NOT_CONFIGURED" in codes(result)


# --------------------------------------------------------------------------
# ID / 目录 / 编号
# --------------------------------------------------------------------------
def test_id_mismatch(project: Path):
    initialized(project)
    planned_req(project)
    patch_req(project, id="REQ-099")
    result = lint(project)
    assert result.code == 1, result
    assert "ID_MISMATCH" in codes(result)


def test_module_mismatch(project: Path):
    initialized(project)
    h7.approved(project)
    source = prd_dir(project)
    target = project / "04-prds" / "moved-module" / source.name
    target.parent.mkdir(parents=True, exist_ok=True)
    source.rename(target)
    result = lint(project)
    assert result.code == 1, result
    assert "MODULE_MISMATCH" in codes(result)


def test_duplicate_id(project: Path):
    initialized(project)
    planned_req(project)
    original = req_path(project)
    copy = original.with_name("REQ-001-duplicate.md")
    write(copy, read(original))
    result = lint(project)
    assert result.code == 1, result
    assert "DUPLICATE_ID" in codes(result)


def test_counter_regression(project: Path):
    initialized(project)
    planned_req(project)
    write(project / ".neo-skill/counters.yaml",
          yaml.safe_dump({"REQ": 0, "PRD": 0, "CR": 0, "SC": 0, "ADR": 0, "DEC": 0}))
    result = lint(project)
    assert result.code == 1, result
    assert "COUNTER_REGRESSION" in codes(result)


# --------------------------------------------------------------------------
# 孤儿 Spec / 失效引用
# --------------------------------------------------------------------------
def test_orphan_spec_without_prd(project: Path):
    initialized(project)
    write(project / "06-specs/resume/PRD-999-phantom/spec.md",
          "---\nprd: PRD-999\nbaseline_version: 1\nmodule: resume\nstatus: DRAFT\n---\n\n# spec\n")
    result = lint(project)
    assert result.code == 1, result
    hits = for_code(result, "ORPHAN_SPEC")
    assert hits, findings(result)
    assert any("PRD-999" in (item["object"] + item["path"]) for item in hits)


def test_broken_reference_from_prd_source_req(project: Path):
    initialized(project)
    h7.approved(project)
    patch_prd_meta(project, source_reqs=["REQ-999"])
    result = lint(project)
    assert result.code == 1, result
    assert "BROKEN_REFERENCE" in codes(result)


def test_broken_reference_from_req_prd(project: Path):
    initialized(project)
    planned_req(project)
    patch_req(project, prd="PRD-999")
    result = lint(project)
    assert result.code == 1, result
    assert "BROKEN_REFERENCE" in codes(result)


# --------------------------------------------------------------------------
# 非法状态关系
# --------------------------------------------------------------------------
def test_invalid_state_req_done_without_accepted_prd(project: Path):
    initialized(project)
    h7.ready(project)
    patch_req(project, status="DONE")
    result = lint(project)
    assert result.code == 1, result
    assert "INVALID_STATE" in codes(result)


# --------------------------------------------------------------------------
# 基线失效
# --------------------------------------------------------------------------
def test_hash_mismatch(project: Path):
    initialized(project)
    h7.approved(project)
    body = prd_dir(project) / "prd.md"
    write(body, read(body) + "\n<!-- 绕过 CLI 直接改正文 -->\n")
    result = lint(project)
    assert result.code == 1, result
    assert "HASH_MISMATCH" in codes(result)


def test_baseline_invalidated_by_open_cr(project: Path):
    initialized(project)
    h7.approved(project)
    created = run_cli(project, "prd", "cr", "create", "--id", PRD_ID, "--actor", "alice",
                      "--confirm", "--reason", "客户要求增加 CSV 导出", env=ENV)
    assert created.code == 0, created
    result = lint(project)
    assert result.code == 1, result
    assert "BASELINE_INVALIDATED" in codes(result)


def test_baseline_invalidated_even_without_marker(project: Path):
    """§13 待办 3：有未决 CR 就算失效，不能只看 `baseline_invalidated.active`。"""
    initialized(project)
    h7.approved(project)
    created = run_cli(project, "prd", "cr", "create", "--id", PRD_ID, "--actor", "alice",
                      "--confirm", "--reason", "客户要求增加 CSV 导出", env=ENV)
    assert created.code == 0, created
    meta = yaml.safe_load(read(prd_dir(project) / "metadata.yaml"))
    meta.pop("baseline_invalidated", None)          # 抹掉标记，只剩未决 CR
    write(prd_dir(project) / "metadata.yaml",
          yaml.safe_dump(meta, sort_keys=False, allow_unicode=True))
    result = lint(project)
    assert result.code == 1, result
    assert "BASELINE_INVALIDATED" in codes(result)


# --------------------------------------------------------------------------
# 验证 / 验收失效
# --------------------------------------------------------------------------
def test_verification_missing_is_warning(project: Path):
    initialized(project)
    h7.developing(project)
    result = lint(project)
    assert result.code == 0, result
    assert "VERIFICATION_MISSING" in codes(result)
    assert all(item["severity"] == "warning"
               for item in for_code(result, "VERIFICATION_MISSING"))


def test_verification_stale(project: Path):
    initialized(project)
    h7.ready(project)
    log = h7.raw_logs(project)[0]
    write(log, read(log) + "\n<!-- 篡改证据 -->\n")
    result = lint(project)
    assert result.code == 1, result
    assert "VERIFICATION_STALE" in codes(result)


def test_acceptance_stale(project: Path):
    initialized(project)
    h8.accepted(project)
    src = project / "src" / "app.py"
    write(src, "print('changed after acceptance')\n")
    result = lint(project)
    assert result.code == 1, result
    assert "ACCEPTANCE_STALE" in codes(result)


# --------------------------------------------------------------------------
# 收尾
# --------------------------------------------------------------------------
def test_closeout_incomplete(project: Path):
    initialized(project)
    h8.accepted(project)
    result = lint(project)
    assert result.code == 1, result
    assert "CLOSEOUT_INCOMPLETE" in codes(result)


def test_closeout_complete_clears_finding(project: Path):
    initialized(project)
    h8.accepted(project)
    closed = h8.closeout(project)
    assert closed.code == 0, closed
    result = lint(project)
    assert "CLOSEOUT_INCOMPLETE" not in codes(result), findings(result)


# --------------------------------------------------------------------------
# 索引
# --------------------------------------------------------------------------
def test_index_stale_after_new_object(project: Path):
    initialized(project)
    generated = run_cli(project, "index", "generate", env=ENV)
    assert generated.code == 0, generated
    assert "INDEX_STALE" not in codes(lint(project))

    created = run_cli(project, "req", "create", "--title", "新需求", "--module", "resume",
                      env=ENV)
    assert created.code == 0, created
    result = lint(project)
    assert result.code == 0, result
    assert "INDEX_STALE" in codes(result)


def test_managed_block_modified(project: Path):
    initialized(project)
    generated = run_cli(project, "index", "generate", env=ENV)
    assert generated.code == 0, generated
    backlog = project / "03-requirements/backlog.md"
    text = read(backlog)
    target = "<!-- neo-skill:managed:end -->"
    write(backlog, text.replace(target, "| 手工加的假行 |\n" + target))
    result = lint(project)
    assert result.code == 1, result
    assert "MANAGED_BLOCK_MODIFIED" in codes(result)


# --------------------------------------------------------------------------
# 规则一致性（`lint rules`）
# --------------------------------------------------------------------------
def test_lint_rules_ok_on_inited_project(project: Path):
    initialized(project)
    result = lint(project, "rules")
    assert result.code == 0, result
    assert result.json["command"] == "lint.rules"
    assert "RULES_MISMATCH" not in codes(result)


def test_lint_rules_detects_missing_gates(project: Path):
    initialized(project)
    path = project / ".specify/rules/flow.yaml"
    data = yaml.safe_load(read(path))
    data.pop("gates", None)
    write(path, yaml.safe_dump(data, sort_keys=False, allow_unicode=True))
    result = lint(project, "rules")
    assert result.code == 1, result
    assert "RULES_MISMATCH" in codes(result)


# --------------------------------------------------------------------------
# `--only` 过滤
# --------------------------------------------------------------------------
def test_only_filters_codes(project: Path):
    initialized(project)
    h8.accepted(project)
    everything = lint(project)
    assert len(codes(everything)) > 1, findings(everything)
    narrowed = lint(project, "--only", "CLOSEOUT_INCOMPLETE")
    assert narrowed.code == 1, narrowed
    assert codes(narrowed) == {"CLOSEOUT_INCOMPLETE"}, codes(narrowed)


def test_only_unknown_code_is_rejected(project: Path):
    initialized(project)
    result = lint(project, "--only", "NO_SUCH_CODE")
    assert result.code == 1, result
    assert "UNKNOWN_CHECK_CODE" in error_codes(result)


def test_only_warning_code_exits_zero(project: Path):
    initialized(project)
    h7.developing(project)
    result = lint(project, "--only", "VERIFICATION_MISSING")
    assert result.code == 0, result
    assert codes(result) == {"VERIFICATION_MISSING"}


# --------------------------------------------------------------------------
# 只读、稳定输出、人类输出
# --------------------------------------------------------------------------
def test_lint_is_read_only(project: Path):
    initialized(project)
    h8.accepted(project)
    before = snapshot(project)
    result = lint(project, "--json")
    assert result.code == 1, result
    assert snapshot(project) == before


def test_lint_output_is_stable(project: Path):
    initialized(project)
    h8.accepted(project)
    first = lint(project)
    second = lint(project)
    assert first.json["data"] == second.json["data"]


def test_lint_human_output_mentions_code(project: Path):
    initialized(project)
    (project / "00-management/risks.md").unlink()
    result = run_cli(project, "lint", json_output=False, env=ENV)
    assert result.code == 1, result
    assert "MISSING_REQUIRED_PATH" in result.stdout
    assert not result.stdout.lstrip().startswith("{")
