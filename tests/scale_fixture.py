"""ticket 09 规模化夹具：约 1,000 条需求 / 200 个 PRD 的项目（SRS-NFR-004）。

**为什么「init 走真 CLI + 对象文件用 flow_core 写入函数生成」**（实测依据，见 09-report.md）：

`req create` → `req confirm` → `req plan` → `prd create` → `prd approve` 全部走 CLI 意味着
每条需求至少 3 个进程、每个 PRD 再 2~3 个：1,000 需求 + 200 PRD ≈ 3,400 次子进程启动，
按单次约 0.12 s 计算要 6 分钟以上，会让 CI 与本地测试不可用。所以：

1. **只调一次真 CLI**（`init`），骨架、项目规则基线、计数器由 CLI 自己生成；
2. **对象文件用 `flow_core` 的写入函数生成**（`requirements.initial_metadata` /
   `requirements.render_body` / `requirements.item_path` / `metadata.initial_metadata` /
   `metadata.render_body` / `fsutil.render_front_matter` / `fsutil.dump_yaml`），
   与被测 CLI 用的是同一套代码路径，所以文件形状与 CLI 产出一致（不是手抄格式）；
3. **生成后立刻用 CLI 做结构冒烟**（`req list` / `prd list` 数量、`lint` 的
   `data.counts.error == 0`）：夹具一旦与真实产出形状不一致就当场失败，
   而不是让性能数字建立在畸形数据上。

只有本模块（测试辅助）可以直接 import `flow_core`；被测断言仍然全部走 CLI 进程边界。
"""

from __future__ import annotations

import sys
from pathlib import Path

from conftest import RUNTIME_DIR, run_cli, write

if str(RUNTIME_DIR) not in sys.path:  # 与 tests/test_01_kernel.py 相同做法
    sys.path.insert(0, str(RUNTIME_DIR))

from flow_core import fsutil, metadata, requirements  # noqa: E402
from flow_core.rules import load_rules  # noqa: E402
from flow_core.workspace import Workspace  # noqa: E402

TEAM = """---
confirmed: true
confirmed_by: alice
confirmed_at: 2026-03-01T09:00:00+08:00
members:
  - {id: alice, name: 爱丽丝, roles: [requirement_owner, project_owner]}
  - {id: bob, name: 鲍勃, roles: [stakeholder]}
  - {id: carol, name: 卡罗尔, roles: [developer, tester]}
---

# 团队与职责

性能夹具使用的固定团队配置。
"""

CONSTITUTION = """---
confirmed: true
confirmed_by: alice
confirmed_at: 2026-03-01T09:00:00+08:00
---

# 项目约束（constitution）

## 技术栈

Python 3.11 + pytest；仅使用标准库。
"""

# 20 个模块，让 PRD 分散在多个目录里（目录遍历深度与真实项目接近）
MODULES = tuple(f"mod-{index:02d}" for index in range(20))

BASE_TIME = "2026-03-01T10:00:00+08:00"


def build_scale_project(root: Path, *, reqs: int = 1000, prds: int = 200,
                        smoke: bool = True) -> dict:
    """在 `root` 上生成规模化项目，返回夹具摘要（数量由调用方断言，耗时由调用方测）。"""
    inited = run_cli(root, "init")
    assert inited.code == 0, inited
    write(root / "00-management/team.md", TEAM)
    write(root / ".specify/constitution.md", CONSTITUTION)

    workspace = Workspace(root)
    rules = load_rules(workspace)

    workspace.req_items_dir.mkdir(parents=True, exist_ok=True)
    for module in MODULES:
        (workspace.prds_dir / module).mkdir(parents=True, exist_ok=True)

    req_paths = _write_requirements(workspace, rules, reqs=reqs, prds=prds)
    prd_dirs = _write_prds(workspace, rules, prds=prds)
    _write_counters(workspace, reqs=reqs, prds=prds)

    summary = {"root": str(root), "reqs": len(req_paths), "prds": len(prd_dirs)}
    if smoke:
        _smoke(root, reqs=reqs, prds=prds)
    return summary


def _write_requirements(workspace: Workspace, rules, *, reqs: int,
                        prds: int) -> list[Path]:
    width = rules.id_width
    paths: list[Path] = []
    for number in range(1, reqs + 1):
        req_id = f"REQ-{number:0{width}d}"
        slug = f"req-{number:04d}"
        module = MODULES[number % len(MODULES)]
        linked = number <= prds
        meta = requirements.initial_metadata(
            req_id=req_id,
            slug=slug,
            title=f"规模化需求 {number:04d}",
            module=module,
            category="new-feature",
            source="性能夹具",
            rules=rules,
        )
        # 前 `prds` 条是 PRD 的主需求（已进入 IN_PRD），其余停在 PLANNED
        meta["status"] = "IN_PRD" if linked else "PLANNED"
        meta["release"] = "v0.1.0"
        meta["prd"] = f"PRD-{number:0{width}d}" if linked else None
        body = requirements.render_body(workspace, req_id, meta["title"])
        path = requirements.item_path(workspace, req_id, slug)
        path.write_text(fsutil.render_front_matter(meta, body), encoding="utf-8")
        paths.append(path)
    return paths


def _write_prds(workspace: Workspace, rules, *, prds: int) -> list[Path]:
    width = rules.id_width
    directories: list[Path] = []
    for number in range(1, prds + 1):
        module_index = number % len(MODULES)
        module = MODULES[module_index]
        prd_id = f"PRD-{number:0{width}d}"
        slug = f"prd-{number:04d}"
        req_id = f"REQ-{number:0{width}d}"
        # 场景编号在同一模块内唯一（真实项目一个场景只属于一个 PRD）
        scenarios = [f"SC-M{module_index:02d}-{number:03d}0{n}" for n in (1, 2)]
        directory = workspace.prds_dir / module / f"{prd_id}-{slug}"
        (directory / metadata.REVIEWS_DIR).mkdir(parents=True, exist_ok=True)
        (directory / metadata.CHANGES_DIR).mkdir(parents=True, exist_ok=True)

        meta = metadata.initial_metadata(
            prd_id=prd_id,
            slug=slug,
            title=f"规模化功能 {number:04d}",
            module=module,
            feature=slug,
            scenarios=scenarios,
            source_reqs=[req_id],
            related_reqs=[],
            release="v0.1.0",
            actor="alice",
            rules=rules,
        )
        body = metadata.render_body(
            workspace, prd_id=prd_id, title=meta["title"], module=module,
            feature=slug, scenarios=scenarios, source_reqs=[req_id], release="v0.1.0",
        )
        (directory / metadata.BODY_FILE).write_text(body, encoding="utf-8")

        # 已批准：content_hash 必须与磁盘内容一致，否则 lint 会报 HASH_MISMATCH
        record = metadata.PrdRecord(directory=directory,
                                    rel=workspace.rel(directory), meta=meta, body=body)
        meta["status"] = "APPROVED"
        meta["submitted_by"] = "bob"
        meta["approved_by"] = "alice"
        meta["approved_at"] = BASE_TIME
        meta["content_hash"] = record.compute_content_hash()
        (directory / metadata.METADATA_FILE).write_text(
            fsutil.dump_yaml(meta), encoding="utf-8")
        directories.append(directory)
    return directories


def _write_counters(workspace: Workspace, *, reqs: int, prds: int) -> None:
    payload = {"REQ": reqs, "PRD": prds, "CR": 0, "SC": 0, "ADR": 0, "DEC": 0}
    workspace.counters_file.write_text(fsutil.dump_yaml(payload), encoding="utf-8")


def _smoke(root: Path, *, reqs: int, prds: int) -> None:
    """结构冒烟：夹具必须让 CLI 看起来和真实产出一致，否则性能数字没有意义。"""
    listed_reqs = run_cli(root, "req", "list")
    assert listed_reqs.code == 0, listed_reqs
    assert listed_reqs.json["data"]["count"] == reqs, listed_reqs.json["data"]["count"]

    listed_prds = run_cli(root, "prd", "list")
    assert listed_prds.code == 0, listed_prds
    assert listed_prds.json["data"]["count"] == prds, listed_prds.json["data"]["count"]

    linted = run_cli(root, "lint")
    assert linted.code == 0, linted.stdout[-2000:]
    assert linted.json["data"]["counts"]["error"] == 0, linted.json["data"]["counts"]
