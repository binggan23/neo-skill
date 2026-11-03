"""规模夹具与真实 CLI 产出的形状一致性（守住 SRS-NFR-004 的性能数字）。

`tests/scale_fixture.py` 为速度绕过了 CLI 进程边界，直接用 `flow_core` 的写入函数
生成对象文件（理由见该模块 docstring：3,400 次子进程要 6 分钟以上）。代价是：
夹具形状一旦与 CLI 真实产出漂移，性能测的还是「畸形数据」，数字就失去意义。

本文件就是那道闸门 —— 用 CLI 造一个对象，用夹具造一个同 ID 对象，逐字段比对。
"""

from __future__ import annotations

import sys
from pathlib import Path

import yaml

from conftest import RUNTIME_DIR, read, run_cli

if str(RUNTIME_DIR) not in sys.path:  # 与 tests/test_01_scale 相同做法
    sys.path.insert(0, str(RUNTIME_DIR))

import scale_fixture  # noqa: E402

# 夹具与 CLI 都必须产出的关键字段：少任何一个都说明形状漂移了
REQ_KEYS = ("id", "slug", "title", "module", "category", "source", "status")


def _req_front_matter(root: Path) -> dict:
    files = sorted((root / "03-requirements/items").glob("REQ-*.md"))
    assert files, "没有生成任何需求文件"
    return yaml.safe_load(read(files[0]).split("---")[1])


def test_fixture_requirement_shape_matches_cli(tmp_path: Path) -> None:
    """夹具生成的需求 front matter 必须带齐 CLI 生成时的关键字段。"""
    from conftest import write

    cli_root = tmp_path / "cli"
    cli_root.mkdir()
    assert run_cli(cli_root, "init").code == 0
    write(cli_root / "00-management/team.md", scale_fixture.TEAM)
    write(cli_root / ".specify/constitution.md", scale_fixture.CONSTITUTION)
    created = run_cli(cli_root, "req", "create", "--title", "形状对比",
                      "--module", "mod-00", "--category", "new-feature",
                      "--source", "性能夹具", "--slug", "req-0001")
    assert created.code == 0, created
    cli_meta = _req_front_matter(cli_root)

    fixture_root = tmp_path / "fixture"
    fixture_root.mkdir()
    scale_fixture.build_scale_project(fixture_root, reqs=2, prds=1)
    fixture_meta = _req_front_matter(fixture_root)

    missing_cli = [key for key in REQ_KEYS if key not in cli_meta]
    missing_fixture = [key for key in REQ_KEYS if key not in fixture_meta]
    assert not missing_cli, f"CLI 产出缺字段（测试前提有误）：{missing_cli}"
    assert not missing_fixture, f"夹具漂移：缺字段 {missing_fixture}（见 scale_fixture.py）"

    assert set(fixture_meta) >= set(REQ_KEYS), (
        "夹具字段集与 CLI 不一致："
        f"缺少 {set(REQ_KEYS) - set(fixture_meta)}"
    )


def test_fixture_prd_metadata_shape_matches_cli_keys(tmp_path: Path) -> None:
    """夹具生成的 PRD metadata 必须带齐 CLI 生成时的关键字段。"""

    fixture_root = tmp_path / "fixture"
    fixture_root.mkdir()
    scale_fixture.build_scale_project(fixture_root, reqs=2, prds=1)

    metas = sorted(fixture_root.glob("04-prds/*/PRD-*/metadata.yaml"))
    assert metas, "夹具没有生成任何 PRD"
    data = yaml.safe_load(read(metas[0]))

    # CLI 的 prd create 一定写这些字段（见 flow_core/metadata.initial_metadata）
    for key in ("id", "slug", "title", "module", "status", "version", "source_reqs"):
        assert key in data, f"夹具 PRD metadata 缺字段 `{key}`（形状漂移）"
    assert data["status"] in ("DRAFT", "APPROVED", "DEVELOPING", "IN_REVIEW"), data["status"]
    assert isinstance(data["source_reqs"], list) and data["source_reqs"], data["source_reqs"]
