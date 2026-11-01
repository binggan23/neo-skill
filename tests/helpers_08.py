"""ticket 08 测试共用夹具：把项目推进到「已人工确认通过」（ACCEPTED）并读取收尾产物。

对象一律通过 CLI 命令搭建（共享设计 §10）。只有「构造中断事务 / 手工改受管理区块」
这类被测场景才直接改文件。
"""

from __future__ import annotations

import re
from pathlib import Path

import yaml

from conftest import read, run_cli, write
from helpers_03 import ENV, prd_dir, prd_meta, req_meta  # noqa: F401  （测试也会用到）
import helpers_07 as h7

MODULE = h7.MODULE
SLUG = h7.SLUG
PRD_ID = h7.PRD_ID
FEATURE = SLUG  # `prd create` 默认 feature = slug
REQ_ID = "REQ-001"

AC_ALL = h7.ALL_ACS

INDEX_FILES = (
    "03-requirements/backlog.md",
    "02-definition/feature-map.md",
    "04-prds/index.md",
)

MANAGED_BEGIN_RE = re.compile(r"<!-- neo-skill:managed:begin fingerprint=([0-9a-f]+) -->")


# --------------------------------------------------------------------------
# 建链路
# --------------------------------------------------------------------------
def accepted(project: Path) -> Path:
    """完整链路 + 全部 AC 通过 + 需求负责人确认 → PRD ACCEPTED。"""
    h7.prepared(project)
    h7.record_all_pass(project)
    h7.approve(project)
    meta = prd_meta(project)
    assert meta["status"] == "ACCEPTED", meta
    assert meta["acceptance"] and meta["acceptance"]["approved_at"]
    assert meta["closeout"] is None
    return project


# --------------------------------------------------------------------------
# 调用
# --------------------------------------------------------------------------
def closeout(project: Path, *, extra: tuple[str, ...] = (), expect: int | None = 0,
             env: dict | None = None):
    result = run_cli(project, "acceptance", "closeout", "--prd", PRD_ID, *extra,
                     env=env if env is not None else ENV)
    if expect is not None:
        assert result.code == expect, result
    return result


def closeout_status(project: Path, *, env: dict | None = None):
    return run_cli(project, "acceptance", "closeout-status", "--prd", PRD_ID,
                   env=env if env is not None else ENV)


def recover(project: Path, *args: str, expect: int | None = 0, env: dict | None = None):
    result = run_cli(project, "recover", *args, env=env if env is not None else ENV)
    if expect is not None:
        assert result.code == expect, result
    return result


def index(project: Path, *args: str, expect: int | None = 0, env: dict | None = None):
    result = run_cli(project, "index", *args, env=env if env is not None else ENV)
    if expect is not None:
        assert result.code == expect, result
    return result


def error_codes(result) -> set[str]:
    return {item["code"] for item in (result.json or {}).get("errors", [])}


# --------------------------------------------------------------------------
# 产物读取
# --------------------------------------------------------------------------
def feature_doc(project: Path, module: str = MODULE, feature: str = FEATURE) -> Path:
    return project / "02-definition" / "modules" / module / "features" / f"{feature}.md"


def feature_doc_text(project: Path, module: str = MODULE, feature: str = FEATURE) -> str:
    path = feature_doc(project, module, feature)
    return read(path) if path.is_file() else ""


def module_readme(project: Path, module: str = MODULE) -> Path:
    return project / "02-definition" / "modules" / module / "README.md"


def module_readme_text(project: Path, module: str = MODULE) -> str:
    path = module_readme(project, module)
    return read(path) if path.is_file() else ""


def index_text(project: Path, rel: str) -> str:
    path = project / rel
    return read(path) if path.is_file() else ""


def managed_body(text: str) -> str | None:
    """取受管理区块 body（两个标记之间的原文）；没有区块返回 None。"""
    start = text.find("<!-- neo-skill:managed:begin")
    if start < 0:
        return None
    begin_end = text.find("-->", start)
    if begin_end < 0:
        return None
    end = text.find("<!-- neo-skill:managed:end -->", begin_end)
    if end < 0:
        return None
    body_start = text.find("\n", begin_end)
    if body_start < 0 or body_start > end:
        return None
    return text[body_start + 1:end]


def managed_fingerprint(text: str) -> str | None:
    match = MANAGED_BEGIN_RE.search(text)
    return match.group(1) if match else None


def scenario_rows(project: Path, module: str = MODULE) -> dict[str, dict]:
    """模块 README 受管理区块里的场景状态表 → {场景: {状态, 主 PRD}}。"""
    text = module_readme_text(project, module)
    body = managed_body(text) or ""
    rows: dict[str, dict] = {}
    for line in body.splitlines():
        if not line.startswith("|"):
            continue
        cells = [cell.strip() for cell in line.strip().strip("|").split("|")]
        if len(cells) < 3 or not cells[0].startswith("SC-"):
            continue
        rows[cells[0]] = {"status": cells[1], "prd": cells[2]}
    return rows


# --------------------------------------------------------------------------
# 事务读取
# --------------------------------------------------------------------------
def transactions_dir(project: Path) -> Path:
    return project / ".neo-skill" / "transactions"


def transaction_ids(project: Path) -> list[str]:
    directory = transactions_dir(project)
    if not directory.is_dir():
        return []
    return sorted(path.name for path in directory.iterdir() if path.is_dir())


def tx_yaml(project: Path, tx_id: str | None = None) -> dict:
    ids = transaction_ids(project)
    assert ids, "没有任何事务目录"
    target = tx_id or ids[-1]
    data = yaml.safe_load(read(transactions_dir(project) / target / "tx.yaml"))
    assert isinstance(data, dict)
    return data


def staged_files(project: Path, tx_id: str) -> list[Path]:
    directory = transactions_dir(project) / tx_id / "staged"
    return sorted(directory.glob("*.bin")) if directory.is_dir() else []


def set_tx_state(project: Path, tx_id: str, state: str) -> None:
    """把一个中断的事务改成指定状态（模拟进程在 PREPARED/COMMITTING 处被杀）。"""
    path = transactions_dir(project) / tx_id / "tx.yaml"
    data = yaml.safe_load(read(path))
    data["state"] = state
    write(path, yaml.safe_dump(data, sort_keys=False, allow_unicode=True))


def last_tx_id(project: Path) -> str:
    ids = transaction_ids(project)
    assert ids, "没有任何事务目录"
    return ids[-1]
