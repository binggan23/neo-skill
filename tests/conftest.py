"""flow-core CLI 黑盒测试夹具。

契约（共享设计 §10）：测试只通过 CLI 进程边界观察行为——退出码、`--json` 信封、
文件是否存在与内容片段。禁止导入被测包的内部模块（`tests/test_01_units.py` 例外，
它显式测试共享内核的纯函数）。
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[1]
RUNTIME_DIR = REPO_ROOT / "skills" / "neo-skill" / "runtime"
FLOW_CORE = RUNTIME_DIR / "flow-core"

# 只有 tests/test_01_kernel.py 直接导入包内模块（测共享内核契约）；
# 其余测试一律走 CLI 进程边界。
if str(RUNTIME_DIR) not in sys.path:
    sys.path.insert(0, str(RUNTIME_DIR))


class Result:
    """一次 CLI 调用的可观察结果。"""

    def __init__(self, proc: subprocess.CompletedProcess[str]) -> None:
        self.code = proc.returncode
        self.stdout = proc.stdout
        self.stderr = proc.stderr
        self.json = _parse_json(proc.stdout)

    @property
    def ok(self) -> bool:
        return self.code == 0

    def artifact(self, path: str) -> dict | None:
        if not self.json:
            return None
        for item in self.json.get("artifacts", []):
            if item.get("path") == path:
                return item
        return None

    def actions(self) -> dict[str, str]:
        if not self.json:
            return {}
        return {item["path"]: item["action"] for item in self.json.get("artifacts", [])}

    def error_codes(self) -> list[str]:
        if not self.json:
            return []
        return [item.get("code", "") for item in self.json.get("errors", [])]

    def check(self, name: str) -> dict | None:
        if not self.json:
            return None
        for item in self.json.get("checks", []):
            if item.get("name") == name:
                return item
        return None

    def __repr__(self) -> str:  # pragma: no cover - 仅调试用
        return f"<Result code={self.code} stdout={self.stdout!r} stderr={self.stderr!r}>"


def _parse_json(text: str) -> dict | None:
    stripped = text.strip()
    if not stripped.startswith("{"):
        return None
    try:
        return json.loads(stripped)
    except json.JSONDecodeError:
        return None


def run_cli(
    root: Path,
    *args: str,
    env: dict | None = None,
    json_output: bool = True,
    cwd: Path | None = None,
    executable: Path | None = None,
    root_flag: bool = True,
) -> Result:
    """在 `root` 项目上执行 `flow-core`。

    `executable` 用于模拟另一份安装（安装检查类测试）；`root_flag=False` 用于
    验证缺省根目录解析（依赖 cwd 向上查找）。
    """
    entry = executable or FLOW_CORE
    argv = [sys.executable, str(entry)]
    if root_flag:
        argv += ["--root", str(root)]
    argv += list(args)
    if json_output:
        argv.append("--json")
    child_env = dict(os.environ)
    child_env.pop("NEO_SKILL_NOW", None)
    child_env.pop("NEO_SKILL_HOME", None)
    child_env["PYTHONIOENCODING"] = "utf-8"
    if env:
        child_env.update({k: v for k, v in env.items() if v is not None})
        for key, value in env.items():
            if value is None:
                child_env.pop(key, None)
    proc = subprocess.run(
        argv,
        capture_output=True,
        text=True,
        encoding="utf-8",
        env=child_env,
        cwd=str(cwd or root),
        timeout=120,
    )
    return Result(proc)


@pytest.fixture()
def project(tmp_path: Path) -> Path:
    """空项目根（尚未初始化）。"""
    root = tmp_path / "proj"
    root.mkdir()
    return root


@pytest.fixture()
def inited_project(project: Path) -> Path:
    """已执行 `init` 的项目根。"""
    result = run_cli(project, "init")
    assert result.code == 0, result
    return project


def write(path: Path, text: str) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")
    return path


def read(path: Path) -> str:
    return path.read_text(encoding="utf-8")
