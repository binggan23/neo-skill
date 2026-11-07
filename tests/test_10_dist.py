"""ticket 10：分发产物打包（`scripts/build-dist.sh`）与安装模拟（`scripts/install-sim.sh`）。

黑盒契约（共享设计 §10）：测试只通过进程边界观察——脚本退出码、磁盘上的产物字节、
解压后的文件权限位与 `flow-core --version` 输出。不导入被测包的内部模块。
"""

from __future__ import annotations

import hashlib
import re
import os
import shutil
import subprocess
import sys
import tarfile
import zipfile
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[1]
BUILD_DIST = REPO_ROOT / "scripts" / "build-dist.sh"
SKILLS_DIR = REPO_ROOT / "skills"
RUNTIME_DIR = SKILLS_DIR / "neo-skill" / "runtime"
FLOW_CORE = RUNTIME_DIR / "flow-core"


def shipped_skills() -> list[str]:
    """随包的 Skill 集合：`skills/` 下含 SKILL.md 的目录（自动派生，不写死名单）。"""
    return sorted(path.parent.name for path in SKILLS_DIR.glob("*/SKILL.md"))


def _version() -> str:
    """从 `flow-core --version` 读版本号（唯一事实来源不走 pyproject 硬编码）。"""
    proc = subprocess.run(
        [sys.executable, str(FLOW_CORE), "--version"],
        capture_output=True, text=True, encoding="utf-8", timeout=60,
    )
    assert proc.returncode == 0, proc.stderr
    return proc.stdout.strip().split()[-1]


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _build(out_dir: Path) -> subprocess.CompletedProcess[str]:
    env = dict(os.environ)
    env.pop("SOURCE_DATE_EPOCH", None)
    env["PYTHONIOENCODING"] = "utf-8"
    return subprocess.run(
        ["bash", str(BUILD_DIST), "--out", str(out_dir)],
        capture_output=True, text=True, encoding="utf-8", env=env, timeout=300,
    )


@pytest.fixture(scope="module")
def dist(tmp_path_factory: pytest.TempPathFactory) -> Path:
    """构建一次产物，多用例共享（构建是纯函数，可复用）。"""
    out = tmp_path_factory.mktemp("dist")
    proc = _build(out)
    assert proc.returncode == 0, proc.stderr
    return out


def test_dist_产物齐全(dist: Path) -> None:
    version = _version()
    top = f"neo-skill-{version}"
    for suffix in (".zip", ".tar.gz"):
        assert (dist / f"{top}{suffix}").is_file(), f"缺少 {top}{suffix}"
        assert (dist / f"{top}{suffix}.sha256").is_file(), f"缺少 {top}{suffix}.sha256"


def test_dist_顶层目录唯一(dist: Path) -> None:
    version = _version()
    top = f"neo-skill-{version}"
    with zipfile.ZipFile(dist / f"{top}.zip") as zf:
        tops = {name.split("/", 1)[0] for name in zf.namelist()}
    assert tops == {top}
    with tarfile.open(dist / f"{top}.tar.gz") as tf:
        tops = {m.name.split("/", 1)[0] for m in tf.getmembers()}
    assert tops == {top}


def test_dist_打包内容包含全部_skill_与运行时(dist: Path) -> None:
    version = _version()
    top = f"neo-skill-{version}"
    with zipfile.ZipFile(dist / f"{top}.zip") as zf:
        names = set(zf.namelist())
    expected = shipped_skills()
    assert expected, "仓库 skills/ 下一个 SKILL.md 都没有"
    for skill in expected:
        assert f"{top}/skills/{skill}/SKILL.md" in names, f"缺少 {skill} Skill"
    # 分发包里的 SKILL.md 集合必须与仓库一致（不多不少，防止漏打新 Skill）
    packed = {
        name.split("/")[2]
        for name in names
        if name.startswith(f"{top}/skills/") and name.count("/") == 3 and name.endswith("/SKILL.md")
    }
    assert packed == set(expected), f"分发包的 Skill 集合与仓库不一致：{sorted(packed)} != {expected}"
    # v0.1.1 新增的只读进度 Skill 必须在包里（它不在旧硬编码清单里）
    assert "upstream-progress" in expected, "仓库里没有 upstream-progress Skill"
    assert f"{top}/skills/upstream-progress/SKILL.md" in names
    # CLI、声明式规则、模板必须随包分发（FR-011）
    assert f"{top}/skills/neo-skill/runtime/flow-core" in names
    assert f"{top}/skills/neo-skill/runtime/rules/flow.yaml" in names
    assert any(n.startswith(f"{top}/skills/neo-skill/runtime/templates/") for n in names)
    assert f"{top}/skills/neo-skill/runtime/flow_core/cli.py" in names


def test_dist_排除缓存与开发机痕迹(dist: Path) -> None:
    version = _version()
    top = f"neo-skill-{version}"
    with zipfile.ZipFile(dist / f"{top}.zip") as zf:
        names = zf.namelist()
    for bad in ("__pycache__/", ".pytest_cache/", ".git/", ".codegraph/", ".pi/", "dist/"):
        assert not [n for n in names if bad in n], f"产物里混入了 {bad}"
    assert not [n for n in names if n.endswith((".pyc", ".pyo"))]


def test_dist_zip_保留_flow_core_755(dist: Path) -> None:
    """zip 的 external_attr 必须携带 0755：`unzip` 按它还原权限位。

    注意 Python 的 `zipfile.extractall` **不会**应用权限位（它只写 0666 & ~umask），
    所以权限位断言直接读 external_attr，跨解压器行为的验证交给下面用 `unzip` 的用例。
    """
    version = _version()
    top = f"neo-skill-{version}"
    with zipfile.ZipFile(dist / f"{top}.zip") as zf:
        info = zf.getinfo(f"{top}/skills/neo-skill/runtime/flow-core")
        readme = zf.getinfo(f"{top}/README.md")
    assert info.create_system == 3, "zip 必须标记为 unix（create_system=3）才保留权限"
    assert (info.external_attr >> 16) & 0o777 == 0o755
    assert info.external_attr >> 16 & 0o170000 == 0o100000  # S_IFREG
    assert (readme.external_attr >> 16) & 0o777 == 0o644


@pytest.mark.skipif(shutil.which("unzip") is None, reason="需要 unzip CLI 验证真实解压权限行为")
def test_dist_unzip_解压后_flow_core_可执行且能跑(dist: Path, tmp_path: Path) -> None:
    """真实解压器（`unzip`）还原后，`./flow-core --version` 直接可跑。"""
    version = _version()
    top = f"neo-skill-{version}"
    target = tmp_path / "unzip"
    target.mkdir()
    proc = subprocess.run(
        ["unzip", "-q", str(dist / f"{top}.zip"), "-d", str(target)],
        capture_output=True, text=True, encoding="utf-8", timeout=120,
    )
    assert proc.returncode == 0, proc.stderr
    entry = target / top / "skills/neo-skill/runtime/flow-core"
    assert entry.stat().st_mode & 0o111, "unzip 后 flow-core 必须带可执行位"
    run = subprocess.run(
        [str(entry), "--version"], capture_output=True, text=True,
        encoding="utf-8", timeout=60, cwd=str(target),
    )
    assert run.returncode == 0, run.stderr
    assert version in run.stdout


def test_dist_targz_解压后_flow_core_version_可跑(dist: Path, tmp_path: Path) -> None:
    """tar.gz 解压后权限位由 tarfile 还原，直接以可执行文件运行。"""
    version = _version()
    top = f"neo-skill-{version}"
    target = tmp_path / "targz"
    target.mkdir()
    with tarfile.open(dist / f"{top}.tar.gz") as tf:
        tf.extractall(target)
    entry = target / top / "skills/neo-skill/runtime/flow-core"
    assert entry.stat().st_mode & 0o111, "tar.gz 解压后 flow-core 必须带可执行位"
    proc = subprocess.run(
        [str(entry), "--version"], capture_output=True, text=True,
        encoding="utf-8", timeout=60, cwd=str(target),
    )
    assert proc.returncode == 0, proc.stderr
    assert version in proc.stdout

    # 解压后 doctor 也能跑：说明随包规则与模板真的齐（不只入口脚本在）
    proj = tmp_path / "proj"
    proj.mkdir()
    doc = subprocess.run(
        [str(entry), "--root", str(proj), "doctor", "--json"],
        capture_output=True, text=True, encoding="utf-8", timeout=120, cwd=str(proj),
    )
    assert "flow-core" in doc.stdout


def test_dist_targz_权限位与属主归一(dist: Path) -> None:
    version = _version()
    top = f"neo-skill-{version}"
    with tarfile.open(dist / f"{top}.tar.gz") as tf:
        members = {m.name: m for m in tf.getmembers()}
    entry = members[f"{top}/skills/neo-skill/runtime/flow-core"]
    assert entry.mode == 0o755
    assert entry.uid == 0 and entry.gid == 0
    assert entry.uname == "" and entry.gname == ""
    readme = members[f"{top}/README.md"]
    assert readme.mode == 0o644


def test_dist_可重现_两次构建字节一致(tmp_path: Path) -> None:
    a, b = tmp_path / "a", tmp_path / "b"
    assert _build(a).returncode == 0
    assert _build(b).returncode == 0
    version = _version()
    names = [f"neo-skill-{version}.zip", f"neo-skill-{version}.tar.gz"]
    names += [f"{n}.sha256" for n in names]
    for name in names:
        assert (a / name).is_file(), f"缺少产物 {name}"
        assert (a / name).read_bytes() == (b / name).read_bytes(), f"{name} 两次构建不一致"


def test_dist_可重现_自检模式通过() -> None:
    proc = subprocess.run(
        ["bash", str(BUILD_DIST), "--check"],
        capture_output=True, text=True, encoding="utf-8", timeout=300,
    )
    assert proc.returncode == 0, proc.stderr
    assert "可重现性自检通过" in proc.stdout


def test_dist_sha256_文件与产物一致(dist: Path) -> None:
    version = _version()
    for suffix in (".zip", ".tar.gz"):
        name = f"neo-skill-{version}{suffix}"
        recorded = (dist / f"{name}.sha256").read_text(encoding="utf-8").split()[0]
        assert recorded == _sha256(dist / name), f"{name} 的 sha256 不一致"
        proc = subprocess.run(
            ["sha256sum", "-c", f"{name}.sha256"],
            capture_output=True, text=True, encoding="utf-8", cwd=str(dist), timeout=60,
        )
        assert proc.returncode == 0, proc.stdout + proc.stderr


def test_dist_不含绝对仓库路径(dist: Path) -> None:
    """产物内容不得写死开发机源码仓库路径（FR-011）。"""
    version = _version()
    top = f"neo-skill-{version}"
    marker = str(REPO_ROOT).encode()
    with zipfile.ZipFile(dist / f"{top}.zip") as zf:
        for name in zf.namelist():
            data = zf.read(name)
            assert marker not in data, f"{name} 里写死了仓库路径 {REPO_ROOT}"


def test_dist_包内_pyproject_版本与_flow_core_一致(dist: Path) -> None:
    """分发包里 `pip install .` 拿到的版本必须与 `flow-core --version` 一致。

    版本号有两处（`pyproject.toml` 与 `flow_core/__init__.py`），而 `build-dist.sh` 只用后者
    命名顶层目录——两处漂移时分发包看起来是 0.1.1、装出来却是 0.1.0，且没有任何断言会发现。
    """
    version = _version()
    top = f"neo-skill-{version}"
    with zipfile.ZipFile(dist / f"{top}.zip") as zf:
        pyproject = zf.read(f"{top}/pyproject.toml").decode("utf-8")
    declared = re.search(r'^\[project\]$(?:\n(?!\[)[^\n]*)*?\nversion = "([^"]+)"', pyproject, re.MULTILINE)
    assert declared, "分发包里的 pyproject.toml 没有 version 字段"
    assert declared.group(1) == version, (
        f"pyproject.toml 声明 {declared.group(1)}，flow-core 是 {version}；"
        "解压后 pip install . 会装成错误的版本"
    )
