"""ticket 10 + v0.1.1 ticket 04：安装模拟（`scripts/install-sim.sh`）—— 模拟 skills.sh 复制到 `.claude/skills/`。

验证 FR-010～FR-012 与 SRS-FR-090 的本地可验证部分：
  * **完整的独立 Skill 集合**都被识别：集合从仓库 `skills/` 自动派生（只算含 `SKILL.md` 的目录），
    测试同样自动派生、不写死名单，且每个 Skill 的 frontmatter 有非空 `name`（等于目录名）与
    `description`——这是 skills.sh 的识别条件；
  * `runtime/`（flow-core / 声明式规则 / 模板）随 skill 目录一起走；
  * 安装后的运行时只靠**相对位置**解析资源，**不依赖源码仓库路径**；
  * 隔离目录整体搬走后仍可运行（含 `init`），证明没有写死任何绝对路径。

缺陷注入（ticket 04 验收第 5 条「缺失 Skill 必须非零退出、不静默放过」）：
脚本新增**可选**参数 `--skills-root DIR`（缺省仍是 `<repo>/skills`，默认行为不变）。
测试把 `skills/` 以软链方式复制到 tmp、再删掉某个 Skill 或伪造一个 Skill 目录，
用 `--skills-root` 指过去断言脚本非零退出。**真实仓库从不被改动**。
另有一条「装完再把目标里的 Skill 删掉、`--verify-only` 必须红」的路径。
"""

from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
INSTALL_SIM = REPO_ROOT / "scripts" / "install-sim.sh"
SKILLS_DIR_SRC = REPO_ROOT / "skills"
SKILL_CATALOG = REPO_ROOT / "docs" / "agents" / "p0-workflow" / "07-skill-catalog.md"
# 阶段 Skill：进度查询 Skill（upstream-progress）**不是**阶段 Skill，不进这个集合。
STAGE_SKILLS = ["project", "requirements", "prd", "dev", "acceptance"]


def shipped_skills() -> list[str]:
    """仓库随包的 Skill 集合：`skills/` 下含 SKILL.md 的目录（自动派生，不写死名单）。"""
    return sorted(path.parent.name for path in SKILLS_DIR_SRC.glob("*/SKILL.md"))


def catalogued_skills() -> list[str]:
    """catalog 表里列出的 Skill（安装模拟的必需底线的唯一事实来源）。"""
    text = SKILL_CATALOG.read_text(encoding="utf-8")
    return sorted(set(re.findall(r"\]\(\.\./\.\./\.\./skills/([a-z0-9-]+)/SKILL\.md\)", text)))


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


def _frontmatter(path: Path) -> dict[str, str]:
    text = path.read_text(encoding="utf-8").replace("\r\n", "\n")
    assert text.startswith("---\n"), f"{path} 缺 frontmatter"
    head = text[4:].split("\n---\n", 1)[0]
    fields: dict[str, str] = {}
    for line in head.splitlines():
        key, sep, value = line.partition(":")
        if sep:
            fields[key.strip()] = value.strip()
    return fields


def _assert_valid_skill(skill_md: Path, expected_name: str) -> None:
    """skills.sh 的识别条件：frontmatter 里 name（等于目录名）与 description 都非空。"""
    fields = _frontmatter(skill_md)
    assert fields.get("name", "") == expected_name, (
        f"{expected_name}/SKILL.md 的 name={fields.get('name')!r} 与目录名不一致"
    )
    assert fields.get("description", "").strip(), f"{expected_name}/SKILL.md 的 description 为空"


def _fake_skills_root(tmp_path: Path, *, drop: str | None = None) -> Path:
    """造一个 `skills/` 的软链副本（成本极低，且不碰真实仓库）。drop = 要删掉的 Skill 名。"""
    root = tmp_path / "skills-root"
    root.mkdir()
    for name in shipped_skills():
        if name == drop:
            continue
        (root / name).symlink_to(SKILLS_DIR_SRC / name, target_is_directory=True)
    return root


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


def test_install_全部_skill_被发现且_frontmatter_有效(tmp_path: Path) -> None:
    target = tmp_path / "proj"
    proc = _install(target)
    assert proc.returncode == 0, proc.stdout + proc.stderr

    expected = shipped_skills()
    assert expected, "仓库 skills/ 下一个 SKILL.md 都没有"
    skills_dir = target / ".claude" / "skills"
    installed = sorted(path.parent.name for path in skills_dir.glob("*/SKILL.md"))
    assert installed == expected, f"安装结果与仓库 Skill 集合不一致：{installed} != {expected}"

    for name in expected:
        _assert_valid_skill(skills_dir / name / "SKILL.md", name)
        # 每个被发现的 Skill 都要在输出里可读（不是只报个数）
        assert f"OK {name}" in proc.stdout, f"安装输出里没有 OK {name}"
    assert f"发现 {len(expected)} 个 Skill" in proc.stdout, proc.stdout

    # 五个阶段 Skill 必须齐（HLD §3.1 / ticket 10 验收第 4 条）
    for name in STAGE_SKILLS:
        assert (skills_dir / name).is_dir()


def test_install_进度_skill_被安装且有有效_frontmatter(tmp_path: Path) -> None:
    """v0.1.1 新增的只读 Skill 必须随安装走（它不在旧硬编码清单里，靠自动派生才被发现）。"""
    target = tmp_path / "proj"
    proc = _install(target)
    assert proc.returncode == 0, proc.stdout + proc.stderr

    assert "upstream-progress" in shipped_skills(), "仓库里没有 upstream-progress Skill"
    skill_md = target / ".claude" / "skills" / "upstream-progress" / "SKILL.md"
    assert skill_md.is_file(), "安装结果里缺少 upstream-progress/SKILL.md"
    _assert_valid_skill(skill_md, "upstream-progress")
    assert "OK upstream-progress" in proc.stdout
    # 它不是阶段 Skill：不得被安装模拟当成阶段从属物
    assert "upstream-progress" not in STAGE_SKILLS


def test_install_源里缺_catalog_列出的_skill_时非零退出(tmp_path: Path) -> None:
    """源目录里直接少一个 catalog 列出的 Skill：源与目标会「一致地」都少它，
    光靠集合一致性检查抓不到，必须由 catalog 派生的必需底线报 MISSING-REQUIRED。

    这正是三个 commit 里 add → delete → re-add 的那个振荡点：必需名单只能有一个来源。
    """
    root = _fake_skills_root(tmp_path, drop="upstream-progress")
    assert not (root / "upstream-progress").exists()

    proc = _install(tmp_path / "proj", "--skills-root", str(root))

    assert proc.returncode != 0, f"源里缺 catalog 列出的 Skill 却仍返回 0：\n{proc.stdout}"
    assert "MISSING-REQUIRED upstream-progress" in proc.stdout, proc.stdout
    assert "INSTALL-SIM FAILED" in proc.stdout + proc.stderr


def test_install_必需底线来自_catalog_而不是脚本里的第二份名单(tmp_path: Path) -> None:
    """把 catalog 里只列了一个 Skill：安装模拟只能要那一个。

    证明底线是 catalog 的**投影**——如果脚本里还藏着另一份写死的名单，
    剔除 catalog 后它仍会要 project/prd/…，这个用例就会红。
    """
    sandbox = tmp_path / "repo"
    catalog = sandbox / "docs" / "agents" / "p0-workflow" / "07-skill-catalog.md"
    catalog.parent.mkdir(parents=True)
    catalog.write_text(
        "| Skill |\n| --- |\n"
        "| [`neo-skill`](../../../skills/neo-skill/SKILL.md) |\n",
        encoding="utf-8",
    )
    # 脚本要在它自己的 REPO_ROOT 下找 catalog，所以把脚本连同 skills 一起软链成一份「仓库」
    (sandbox / "scripts").mkdir()
    (sandbox / "scripts" / "install-sim.sh").symlink_to(INSTALL_SIM)
    (sandbox / "skills").symlink_to(SKILLS_DIR_SRC, target_is_directory=True)

    proc = subprocess.run(
        ["bash", str(sandbox / "scripts" / "install-sim.sh"), "--target", str(tmp_path / "proj")],
        capture_output=True, text=True, encoding="utf-8", timeout=180,
    )

    assert proc.returncode == 0, proc.stdout + proc.stderr
    required = [line for line in proc.stdout.splitlines() if line.startswith("OK-REQUIRED ")]
    assert required == ["OK-REQUIRED neo-skill"], (
        "必需底线不是 catalog 的投影（脚本里大概还留着一份写死的名单）：" + repr(required)
    )


def test_install_目标_skill_集合与源集合不一致时非零退出(tmp_path: Path) -> None:
    """安装后故意删掉一个 Skill：与 --skills-root 派生的集合不一致，必须非零退出。"""
    root = _fake_skills_root(tmp_path)
    target = tmp_path / "proj"
    assert _install(target, "--skills-root", str(root)).returncode == 0
    missing = "upstream-progress"
    shutil.rmtree(target / ".claude" / "skills" / missing)

    proc = _install(target, "--skills-root", str(root), "--verify-only")

    assert proc.returncode != 0, f"目标缺 {missing} 却仍返回 0：\n{proc.stdout}"
    assert f"MISMATCH 目标缺少 Skill：{missing}" in proc.stdout, proc.stdout
    assert "INSTALL-SIM FAILED" in proc.stdout + proc.stderr


def test_install_伪造_frontmatter_不完整的_skill_时非零退出(tmp_path: Path) -> None:
    root = _fake_skills_root(tmp_path)
    fake = root / "bogus-skill"
    fake.mkdir()
    (fake / "SKILL.md").write_text(
        "---\nname: bogus-skill\n---\n\n# 缺 description 的伪 Skill\n", encoding="utf-8"
    )

    proc = _install(tmp_path / "proj", "--skills-root", str(root))

    assert proc.returncode != 0, f"description 为空却仍返回 0：\n{proc.stdout}"
    assert "INVALID bogus-skill" in proc.stdout, proc.stdout


def test_install_name_与目录名不一致时非零退出(tmp_path: Path) -> None:
    root = _fake_skills_root(tmp_path)
    fake = root / "wrong-name"
    fake.mkdir()
    (fake / "SKILL.md").write_text(
        "---\nname: something-else\ndescription: 当用户想验证名字与目录名不一致时使用。\n---\n",
        encoding="utf-8",
    )

    proc = _install(tmp_path / "proj", "--skills-root", str(root))

    assert proc.returncode != 0, f"name 与目录名不一致却仍返回 0：\n{proc.stdout}"
    assert "INVALID wrong-name" in proc.stdout, proc.stdout


def test_install_verify_only_发现目标缺_skill_时非零退出(tmp_path: Path) -> None:
    """装完再把目标里的 Skill 删掉：--verify-only 必须报 MISSING 并非零退出。"""
    target = tmp_path / "proj"
    assert _install(target).returncode == 0
    missing = "upstream-progress"
    shutil.rmtree(target / ".claude" / "skills" / missing)

    proc = _install(target, "--verify-only")

    assert proc.returncode != 0, f"目标缺 {missing} 却仍返回 0：\n{proc.stdout}"
    assert f"MISSING {missing}" in proc.stdout, proc.stdout


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
