"""ticket 10：安装模拟（`scripts/install-sim.sh`）—— 模拟 skills.sh 复制到 `.claude/skills/`。

验证 FR-010～FR-012 的本地可验证部分：
  * 六个 SKILL.md（五个阶段 + 路由层）都被识别；
  * `runtime/`（flow-core / 声明式规则 / 模板）随 skill 目录一起走；
  * 安装后的运行时只靠**相对位置**解析资源，**不依赖源码仓库路径**；
  * 隔离目录整体搬走后仍可运行（含 `init`），证明没有写死任何绝对路径。
"""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
INSTALL_SIM = REPO_ROOT / "scripts" / "install-sim.sh"
SKILL_NAMES = ["neo-skill", "project", "requirements", "prd", "dev", "acceptance"]
STAGE_SKILLS = ["project", "requirements", "prd", "dev", "acceptance"]


def _run(*args: str, env: dict | None = None, cwd: Path | None = None) -> subprocess.CompletedProcess[str]:
    child_env = dict(os.environ)
    child_env.pop("NEO_SKILL_HOME", None)
    child_env.pop("PYTHONPATH", None)
    child_env["PYTHONIOENCODING"] = "utf-8"
    if env:
        child_env.update(env)
        for key, value in env.items():
            if value is None:
                child_env.pop(key, None)
    return subprocess.run(
        ["bash", str(INSTALL_SIM), *args],
        capture_output=True, text=True, encoding="utf-8", env=child_env,
        cwd=str(cwd or REPO_ROOT), timeout=180,
    )


def _install(target: Path, *extra: str, env: dict | None = None) -> subprocess.CompletedProcess[str]:
    return _run("--target", str(target), *extra, env=env)


def _cli(skills_root: Path) -> Path:
    return skills_root / "neo-skill" / "runtime" / "flow-core"


def _doctor(cli: Path, root: Path, env: dict | None = None) -> dict:
    child_env = dict(os.environ)
    child_env.pop("NEO_SKILL_HOME", None)
    child_env.pop("PYTHONPATH", None)
    child_env["PYTHONIOENCODING"] = "utf-8"
    if env:
        child_env.update(env)
        for key, value in env.items():
            if value is None:
                child_env.pop(key, None)
    proc = subprocess.run(
        [sys.executable, str(cli), "--root", str(root), "doctor", "--json"],
        capture_output=True, text=True, encoding="utf-8", env=child_env,
        cwd=str(root), timeout=120,
    )
    assert proc.returncode == 0, proc.stderr + proc.stdout
    return json.loads(proc.stdout)


def test_install_五个阶段_skill_全部被识别(tmp_path: Path) -> None:
    target = tmp_path / "proj"
    proc = _install(target)
    assert proc.returncode == 0, proc.stdout + proc.stderr
    skills_dir = target / ".claude" / "skills"
    for name in SKILL_NAMES:
        f = skills_dir / name / "SKILL.md"
        assert f.is_file(), f"缺少 {name}/SKILL.md"
        text = f.read_text(encoding="utf-8")
        # skills.sh 的识别条件：frontmatter 里有 name 与 description
        assert text.startswith("---\n"), f"{name} 缺 frontmatter"
        assert "\nname:" in text.split("---")[1]
        assert "\ndescription:" in text.split("---")[1]
    # 五个阶段 Skill 必须齐（HLD §3.1 / ticket 10 验收第 4 条）
    for name in STAGE_SKILLS:
        assert (skills_dir / name).is_dir()
    assert "OK project" in proc.stdout


def test_install_随包资源齐全(tmp_path: Path) -> None:
    target = tmp_path / "proj"
    assert _install(target).returncode == 0
    runtime = target / ".claude" / "skills" / "neo-skill" / "runtime"
    assert (runtime / "flow-core").is_file()
    assert (runtime / "rules" / "flow.yaml").is_file()
    assert len(list((runtime / "templates").glob("*.md"))) >= 10
    assert (runtime / "flow_core" / "cli.py").is_file()


def test_install_flow_core_从相对路径可执行(tmp_path: Path) -> None:
    target = tmp_path / "proj"
    assert _install(target).returncode == 0
    cli = _cli(target / ".claude" / "skills")
    proc = subprocess.run(
        [sys.executable, str(cli), "--version"], capture_output=True, text=True,
        encoding="utf-8", timeout=60, cwd=str(target),
        env={k: v for k, v in os.environ.items() if k not in ("NEO_SKILL_HOME", "PYTHONPATH")},
    )
    assert proc.returncode == 0, proc.stderr
    assert "flow-core" in proc.stdout


def test_install_doctor_报告安装目录内的路径(tmp_path: Path) -> None:
    """doctor 的 data.runtime 必须指向安装目录，而不是源码仓库。"""
    target = tmp_path / "proj"
    assert _install(target).returncode == 0
    skills_dir = target / ".claude" / "skills"
    data = _doctor(_cli(skills_dir), target)["data"]["runtime"]
    assert Path(data["flow_core"]).resolve() == _cli(skills_dir).resolve()
    assert Path(data["runtime_dir"]).resolve() == (skills_dir / "neo-skill" / "runtime").resolve()
    assert REPO_ROOT.resolve() not in Path(data["runtime_dir"]).resolve().parents
    # 规则回退到随包资源（尚未 init → 无项目基线）
    assert data["rules_origin"] == "packaged"


def test_install_隔离目录搬走仍可跑且能_init(tmp_path: Path) -> None:
    """把整个隔离项目搬到别处（父路径完全不同），仍能 doctor + init。"""
    first = tmp_path / "a" / "proj"
    assert _install(first).returncode == 0
    moved = tmp_path / "b" / "elsewhere" / "proj"
    moved.parent.mkdir(parents=True)
    shutil.copytree(first, moved)
    shutil.rmtree(first)  # 原位置彻底消失，排除任何残留依赖

    skills_dir = moved / ".claude" / "skills"
    cli = _cli(skills_dir)
    data = _doctor(cli, moved)["data"]["runtime"]
    assert Path(data["runtime_dir"]).resolve() == (skills_dir / "neo-skill" / "runtime").resolve()

    proc = subprocess.run(
        [sys.executable, str(cli), "--root", str(moved), "init", "--json"],
        capture_output=True, text=True, encoding="utf-8", timeout=180, cwd=str(moved),
        env={k: v for k, v in os.environ.items() if k not in ("NEO_SKILL_HOME", "PYTHONPATH")},
    )
    assert proc.returncode == 0, proc.stderr + proc.stdout
    payload = json.loads(proc.stdout)
    assert payload["success"] is True
    assert (moved / "03-requirements").is_dir()
    assert (moved / ".specify" / "rules" / "flow.yaml").is_file()
    # 项目基线来源是随包规则（不是仓库）
    after = _doctor(cli, moved)["data"]["runtime"]
    assert after["rules_origin"] == "project"
    assert Path(after["rules_source"]).resolve() == (moved / ".specify" / "rules" / "flow.yaml").resolve()


def test_install_不写死源码仓库路径(tmp_path: Path) -> None:
    target = tmp_path / "proj"
    assert _install(target).returncode == 0
    marker = str(REPO_ROOT).encode("utf-8")
    offenders = []
    for path in (target / ".claude" / "skills").rglob("*"):
        if path.is_file() and path.suffix in {".md", ".py", ".yaml", ".yml"}:
            if marker in path.read_bytes():
                offenders.append(str(path.relative_to(target)))
    assert offenders == [], f"安装目录里写死了源码仓库路径：{offenders}"


def test_install_NEO_SKILL_HOME_优先(tmp_path: Path) -> None:
    """`$NEO_SKILL_HOME` 是显式覆盖，优先级高于兄弟目录。"""
    home = tmp_path / "home"
    shutil.copytree(REPO_ROOT / "skills" / "neo-skill" / "runtime", home / "runtime")
    target = tmp_path / "proj"
    proc = _install(target, env={"NEO_SKILL_HOME": str(home)})
    assert proc.returncode == 0, proc.stdout + proc.stderr
    assert f"RESOLVED {home}/runtime/flow-core" in proc.stdout
    assert "显式覆盖" in proc.stdout


def test_install_verify_only_不复制(tmp_path: Path) -> None:
    target = tmp_path / "proj"
    assert _install(target).returncode == 0
    skills_dir = target / ".claude" / "skills"
    before = sorted(p.name for p in skills_dir.iterdir())
    proc = _install(target, "--verify-only")
    assert proc.returncode == 0, proc.stdout + proc.stderr
    assert sorted(p.name for p in skills_dir.iterdir()) == before


def test_install_缺目标目录时报用法错误(tmp_path: Path) -> None:
    proc = subprocess.run(
        ["bash", str(INSTALL_SIM)], capture_output=True, text=True,
        encoding="utf-8", timeout=60,
    )
    assert proc.returncode == 2
    assert "必须给 --target" in proc.stderr
