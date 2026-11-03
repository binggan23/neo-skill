"""`doctor`：环境与资源诊断。

只报告与建议，**绝不**自动安装软件、修改用户配置或清理残留锁
（共享设计 §8）。检查项以 `checks` 数组进入信封，每项含
`name` / `status`（ok|warn|fail）/ `detail` / `suggestion`。
"""

from __future__ import annotations

import argparse
import os
import shutil
import subprocess
import sys
from pathlib import Path

from .. import __version__, locks
from ..output import Result
from ..rules import Rules, packaged_rules_file, packaged_templates_dir, runtime_dir
from ..workspace import Workspace

NAME = "doctor"
HELP = "检查 Python、Node.js、Skill 资源、flow-core 可用性与残留锁"

MIN_PYTHON = (3, 11)
MIN_NODE = 18
FLOW_CORE_ENTRY = Path(__file__).resolve().parents[2] / "flow-core"


def add_arguments(parser: argparse.ArgumentParser) -> None:
    parser.add_argument(
        "--strict",
        action="store_true",
        help="把「注意」（warn）也视为失败（CI 用）",
    )


def run(workspace: Workspace, rules: Rules, args: argparse.Namespace) -> Result:
    result = Result(command=NAME)
    checks = [
        _check_python(),
        _check_node(),
        _check_flow_core(),
        _check_rules(rules),
        _check_templates(),
        _check_project(workspace),
        _check_locks(workspace),
    ]
    result.checks.extend(checks)

    strict = bool(getattr(args, "strict", False))
    failing = [c for c in checks if c["status"] == "fail"]
    warning = [c for c in checks if c["status"] == "warn"]
    result.data["runtime"] = {
        "flow_core": str(FLOW_CORE_ENTRY),
        "runtime_dir": str(runtime_dir()),
        "root": str(workspace.root),
        "rules_source": str(rules.source),
        "rules_origin": rules.origin,
        "version": __version__,
    }

    if failing:
        for check in failing:
            result.fail(
                "DOCTOR_CHECK_FAILED",
                f"{check['name']}：{check['detail']}",
                object=check["name"],
                suggestion=check.get("suggestion"),
            )
    elif warning and strict:
        for check in warning:
            result.fail(
                "DOCTOR_CHECK_FAILED",
                f"{check['name']}：{check['detail']}",
                object=check["name"],
                suggestion=check.get("suggestion"),
            )
    else:
        for check in warning:
            suggestion = check.get("suggestion")
            if suggestion and suggestion not in result.next_actions:
                result.next_actions.append(suggestion)
        if not workspace.initialized and "flow-core init" not in result.next_actions:
            result.next_actions.append("flow-core init")
    return result


# --------------------------------------------------------------------------
# 检查项
# --------------------------------------------------------------------------
def _check_python() -> dict:
    version = sys.version_info
    current = f"{version.major}.{version.minor}.{version.micro}"
    ok = (version.major, version.minor) >= MIN_PYTHON
    return {
        "name": "python",
        "status": "ok" if ok else "fail",
        "detail": f"Python {current}（要求 ≥ {MIN_PYTHON[0]}.{MIN_PYTHON[1]}）",
        "suggestion": None if ok else "安装 Python 3.11 或更新版本，并用它运行 flow-core",
    }


def _check_node() -> dict:
    node = shutil.which("node")
    if not node:
        return {
            "name": "node",
            "status": "fail",
            "detail": "未找到 node（要求 ≥ 18）",
            "suggestion": "安装 Node.js 18+（仅安装/升级 Skill 时需要），然后重新运行 doctor",
        }
    try:
        proc = subprocess.run([node, "--version"], capture_output=True, text=True, timeout=10)
        raw = (proc.stdout or "").strip().lstrip("v")
        major = int(raw.split(".")[0])
    except (OSError, ValueError, subprocess.SubprocessError):
        return {
            "name": "node",
            "status": "warn",
            "detail": f"无法解析 node 版本（{node}）",
            "suggestion": "确认 `node --version` 可正常输出后重新运行 doctor",
        }
    ok = major >= MIN_NODE
    return {
        "name": "node",
        "status": "ok" if ok else "fail",
        "detail": f"Node.js v{raw}（要求 ≥ {MIN_NODE}）",
        "suggestion": None if ok else f"升级 Node.js 到 {MIN_NODE}+，然后重新运行 doctor",
    }


def _check_flow_core() -> dict:
    entry = FLOW_CORE_ENTRY
    if not entry.is_file():
        return {
            "name": "flow-core",
            "status": "fail",
            "detail": f"找不到入口脚本 {entry}",
            "suggestion": "重新安装 Neo Skill（npx skills@latest add ...），确认 runtime/flow-core 存在",
        }
    executable = os.access(entry, os.X_OK)
    return {
        "name": "flow-core",
        "status": "ok" if executable else "warn",
        "detail": f"{entry}" + ("" if executable else "（缺少可执行权限，可直接用 python3 调用）"),
        "suggestion": None if executable else f"chmod +x {entry}",
    }


def _check_rules(rules: Rules) -> dict:
    packaged = packaged_rules_file()
    if not packaged.is_file():
        return {
            "name": "rules",
            "status": "fail",
            "detail": f"随包规则缺失：{packaged}",
            "suggestion": "重新安装 Neo Skill",
        }
    detail = f"规则 v{rules.version}，来源 {rules.source}（{rules.origin}）"
    if rules.origin == "packaged":
        return {
            "name": "rules",
            "status": "warn",
            "detail": detail + "；项目尚未生成规则基线",
            "suggestion": "flow-core init",
        }
    return {"name": "rules", "status": "ok", "detail": detail, "suggestion": None}


def _check_templates() -> dict:
    directory = packaged_templates_dir()
    if not directory.is_dir():
        return {
            "name": "templates",
            "status": "fail",
            "detail": f"随包模板目录缺失：{directory}",
            "suggestion": "重新安装 Neo Skill",
        }
    count = len(list(directory.glob("*.md")))
    if count == 0:
        return {
            "name": "templates",
            "status": "fail",
            "detail": f"{directory} 下没有模板文件",
            "suggestion": "重新安装 Neo Skill",
        }
    return {
        "name": "templates",
        "status": "ok",
        "detail": f"{directory}（{count} 个模板）",
        "suggestion": None,
    }


def _check_project(workspace: Workspace) -> dict:
    if not workspace.root.is_dir():
        return {
            "name": "project",
            "status": "fail",
            "detail": f"项目根目录不存在：{workspace.root}",
            "suggestion": "确认 --root 指向已存在的项目目录",
        }
    if not workspace.initialized:
        return {
            "name": "project",
            "status": "warn",
            "detail": "项目尚未初始化（缺少 .neo-skill/init-state.yaml）",
            "suggestion": "flow-core init",
        }
    if not workspace.constitution_file.is_file():
        return {
            "name": "project",
            "status": "warn",
            "detail": "缺少 .specify/constitution.md",
            "suggestion": "flow-core init 补齐模板，然后由项目负责人确认项目约束",
        }
    return {
        "name": "project",
        "status": "ok",
        "detail": f"{workspace.root}（已初始化）",
        "suggestion": None,
    }


def _check_locks(workspace: Workspace) -> dict:
    if not workspace.locks_dir.is_dir():
        return {
            "name": "locks",
            "status": "ok",
            "detail": "无锁目录，未发现残留锁",
            "suggestion": None,
        }
    infos = locks.list_locks(workspace)
    unreadable = [
        path.name for path in sorted(workspace.locks_dir.glob("*.lock"))
        if locks.read_lock(workspace, path.stem) is None
    ]
    stale: list[str] = []
    for info in infos:
        if not locks.pid_alive(info.pid):
            stale.append(f"{info.object}（pid={info.pid} 已结束，创建于 {info.created_at}）")
    if not stale and not unreadable:
        return {
            "name": "locks",
            "status": "ok",
            "detail": f"{len(infos)} 个锁文件，未发现残留锁" if infos else "未发现残留锁",
            "suggestion": None,
        }
    parts: list[str] = []
    if stale:
        parts.append("发现残留锁：" + "；".join(stale))
    if unreadable:
        parts.append("发现无法解析的锁文件：" + "、".join(unreadable))
    targets = [item.split("（")[0] for item in stale]
    targets += [name.removesuffix(".lock") for name in unreadable]
    return {
        "name": "locks",
        "status": "warn",
        "detail": "；".join(parts) + "（不自动删除）",
        "suggestion": (
            "确认持有进程已结束后执行 flow-core recover --release-lock <对象> "
            "--actor <id> --confirm（对象：" + "、".join(targets) + "）"
        ),
    }
