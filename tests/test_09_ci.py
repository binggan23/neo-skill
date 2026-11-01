"""ticket 09：CI 治理黑盒测试。

两条线：

1. **工作流内容**（契约检查）：`.github/workflows/ci.yml` 必须是 Linux + Python
   3.11/3.12/3.13 矩阵，装 `pyyaml pytest`，跑 `compileall`，**按文件循环**跑 pytest
   （不能出现 `pytest tests/` 全量调用），并在临时初始化项目上跑 `doctor` 与 `lint`；
   注释里必须写明「非零退出码阻止违规结果」与「lint/CI 是流程治理，不是不可绕过的安全边界」。
2. **失败传播**（行为检查）：在本机按工作流的同样步骤跑一遍——干净项目 `doctor`/`lint`
   退出 0；把必需产物删掉后 `lint` 必须非零退出并带 `MISSING_REQUIRED_PATH`。
   CI 之所以能拦住违规结果，靠的就是这个非零退出码。
"""

from __future__ import annotations

import re
from pathlib import Path

import yaml

from conftest import REPO_ROOT, run_cli, write
from helpers_03 import TEAM
from helpers_07 import CONSTITUTION

WORKFLOW = REPO_ROOT / ".github" / "workflows" / "ci.yml"


def workflow_text() -> str:
    assert WORKFLOW.is_file(), f"缺少 CI 工作流：{WORKFLOW}"
    return WORKFLOW.read_text(encoding="utf-8")


def workflow_data() -> dict:
    return yaml.safe_load(workflow_text())


def run_steps() -> str:
    """所有 job 的 run 脚本拼成一段文本，便于断言工作流到底执行了什么。"""
    data = workflow_data()
    chunks: list[str] = []
    for job in (data.get("jobs") or {}).values():
        for step in job.get("steps") or []:
            if "run" in step:
                chunks.append(str(step["run"]))
    return "\n".join(chunks)


# --------------------------------------------------------------------------
# 工作流内容
# --------------------------------------------------------------------------
def test_workflow_exists_and_is_valid_yaml():
    data = workflow_data()
    assert isinstance(data, dict)
    assert data.get("jobs"), "工作流没有任何 job"


def test_matrix_covers_linux_and_python_311_312_313():
    data = workflow_data()
    job = next(iter(data["jobs"].values()))
    matrix = job["strategy"]["matrix"]
    versions = [str(item) for item in matrix["python"]]
    assert versions == ["3.11", "3.12", "3.13"], versions
    assert any("ubuntu" in str(item) for item in matrix["os"]), matrix["os"]
    assert str(job.get("runs-on", "")).startswith("${{ matrix")


def test_workflow_installs_only_declared_dependencies():
    steps = run_steps()
    assert "pip install" in steps
    assert re.search(r"pip install[^\n]*pyyaml", steps, re.IGNORECASE)
    assert re.search(r"pip install[^\n]*pytest", steps, re.IGNORECASE)


def test_workflow_compiles_runtime():
    steps = run_steps()
    assert "compileall" in steps
    assert "skills/neo-skill/runtime" in steps


def test_workflow_runs_tests_file_by_file():
    """禁止 `pytest tests/` 全量：按文件循环，单文件失败即中断。"""
    steps = run_steps()
    assert "for f in tests/" in steps, "没有按文件循环跑测试"
    assert "-m pytest \"$f\"" in steps or "-m pytest $f" in steps
    forbidden = re.compile(r"pytest\s+tests/?(\s|$)", re.IGNORECASE)
    assert not forbidden.search(steps), "工作流里出现了全量 `pytest tests/`"
    assert "set -euo pipefail" in steps


def test_workflow_runs_doctor_and_lint_on_temp_project():
    steps = run_steps()
    assert "init" in steps
    assert "doctor" in steps
    assert "lint" in steps
    assert "mktemp -d" in steps, "没有在临时初始化项目上验证"
    # 刚 init 的项目团队/约束未确认，必须先在 CI 里把它们确认掉才能期望 lint 通过
    assert "team.md" in steps
    assert "constitution.md" in steps


def test_workflow_asserts_failure_propagation():
    """CI 必须显式验证「违规 → 非零退出码」，否则会静默放行。"""
    steps = run_steps()
    assert "MISSING_REQUIRED_PATH" in steps
    assert "exit 1" in steps


def test_workflow_documents_governance_boundary():
    text = workflow_text()
    assert "非零退出码" in text
    # 「流程治理，不是不可绕过的安全边界」必须写在文件里，避免被当成安全承诺
    assert "流程治理" in text
    assert "不是不可绕过的安全边界" in text


# --------------------------------------------------------------------------
# 失败传播（本机复现工作流的关键步骤）
# --------------------------------------------------------------------------
def governed(project: Path) -> Path:
    """按 CI 的同样步骤：init → 确认团队与约束 →（可选）生成索引。"""
    assert run_cli(project, "init").code == 0
    write(project / "00-management/team.md", TEAM)
    write(project / ".specify/constitution.md", CONSTITUTION)
    return project


def test_fresh_init_project_reports_unconfirmed_governance(project: Path):
    """刚 init 的项目必然报「团队/约束未确认」——这是正确的阻断，测试把它钉住。"""
    assert run_cli(project, "init").code == 0
    linted = run_cli(project, "lint")
    assert linted.code == 1, linted.stdout[-2000:]
    found = {item["code"] for item in linted.json["data"]["findings"]}
    assert {"TEAM_NOT_CONFIGURED", "CONSTITUTION_NOT_CONFIRMED"} <= found, found


def test_governed_project_passes_lint_and_doctor(project: Path):
    governed(project)
    doctor = run_cli(project, "doctor")
    assert doctor.code == 0, doctor.stdout[-2000:]
    linted = run_cli(project, "lint")
    assert linted.code == 0, linted.stdout[-2000:]
    assert linted.json["data"]["counts"]["error"] == 0


def test_governed_project_passes_after_index_generate(project: Path):
    governed(project)
    assert run_cli(project, "index", "generate").code == 0
    linted = run_cli(project, "lint")
    assert linted.code == 0, linted.stdout[-2000:]
    assert linted.json["data"]["counts"]["total"] == 0, linted.json["data"]["findings"]


def test_missing_artifact_makes_lint_fail(project: Path):
    governed(project)
    (project / "02-definition/mvp-scope.md").unlink()
    linted = run_cli(project, "lint")
    assert linted.code == 1, linted.stdout[-2000:]
    assert linted.json["success"] is False
    assert "MISSING_REQUIRED_PATH" in {item["code"] for item in linted.json["errors"]}


def test_workflow_paths_exist_in_repo():
    """工作流里写到的路径必须在仓库里真实存在（避免 CI 配置漂移）。"""
    steps = run_steps()
    assert (REPO_ROOT / "skills/neo-skill/runtime/flow-core").is_file()
    assert "skills/neo-skill/runtime" in steps
    assert (REPO_ROOT / "tests").is_dir()
    for name in ("test_01_cli.py", "test_08_closeout.py", "test_09_lint.py"):
        assert (REPO_ROOT / "tests" / name).is_file(), f"缺少测试文件 {name}"
