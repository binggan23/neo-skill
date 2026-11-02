"""ticket 09：规模化性能基准（SRS-NFR-004）。

口径与任务书一致，**断言值不许放宽**：

- 1,000 需求 / 200 PRD 上：`lint`、`index generate`、`prd list`、`req list` 各 < 10.0 秒；
- 单次真实状态变更（`req confirm`：NEW → TRIAGED，含取锁 + 原子写 + 追加 history）< 2.0 秒。

计时一律 `time.monotonic()`，只算被测量的那一次 CLI 调用（不含夹具构建）。
夹具构建耗时单独测量并断言 < 120 秒；若某台机器上构建超过 2 分钟，
夹具会用 `@pytest.mark.slow` 标记（见 `slow_project`），**预算断言保留不放宽**。
"""

from __future__ import annotations

import time
from pathlib import Path

import pytest

import scale_fixture
from conftest import run_cli
from helpers_03 import ENV

LINT_BUDGET_SECONDS = 10.0
INDEX_BUDGET_SECONDS = 10.0
QUERY_BUDGET_SECONDS = 10.0
STATE_CHANGE_BUDGET_SECONDS = 2.0
FIXTURE_BUDGET_SECONDS = 120.0

REQS = 1000
PRDS = 200


def _timed(call):
    started = time.monotonic()
    result = call()
    return result, time.monotonic() - started


@pytest.fixture(scope="module")
def scale_project(tmp_path_factory) -> Path:
    """1,000 需求 / 200 PRD 的项目（module 级复用，构建一次）。"""
    root = tmp_path_factory.mktemp("scale") / "proj"
    root.mkdir()
    summary, elapsed = _timed(lambda: scale_fixture.build_scale_project(
        root, reqs=REQS, prds=PRDS, smoke=True))
    assert summary["reqs"] == REQS, summary
    assert summary["prds"] == PRDS, summary
    # 夹具构建预算：只为发现「规模上去后夹具本身不可用」，不是被测命令的性能指标
    assert elapsed < FIXTURE_BUDGET_SECONDS, (
        f"夹具构建耗时 {elapsed:.1f}s，已超过 {FIXTURE_BUDGET_SECONDS:.0f}s 预算；"
        "请改用更省时的生成方式，或给 slow_project 加 @pytest.mark.slow")
    return root


def test_lint_is_within_budget(scale_project: Path):
    result, elapsed = _timed(lambda: run_cli(scale_project, "lint", env=ENV))
    assert result.code == 0, result.stdout[-2000:]
    assert result.json["data"]["counts"]["error"] == 0, result.json["data"]["counts"]
    assert elapsed < LINT_BUDGET_SECONDS, f"lint 耗时 {elapsed:.2f}s ≥ {LINT_BUDGET_SECONDS}s"


def test_index_generate_is_within_budget(scale_project: Path):
    result, elapsed = _timed(lambda: run_cli(scale_project, "index", "generate", env=ENV))
    assert result.code == 0, result.stdout[-2000:]
    assert elapsed < INDEX_BUDGET_SECONDS, (
        f"index generate 耗时 {elapsed:.2f}s ≥ {INDEX_BUDGET_SECONDS}s")


def test_index_status_is_within_budget(scale_project: Path):
    result, elapsed = _timed(lambda: run_cli(scale_project, "index", "status", env=ENV))
    assert result.code == 0, result.stdout[-2000:]
    assert elapsed < QUERY_BUDGET_SECONDS, (
        f"index status 耗时 {elapsed:.2f}s ≥ {QUERY_BUDGET_SECONDS}s")


def test_prd_list_is_within_budget(scale_project: Path):
    result, elapsed = _timed(lambda: run_cli(scale_project, "prd", "list", env=ENV))
    assert result.code == 0, result.stdout[-2000:]
    assert result.json["data"]["count"] == PRDS
    assert elapsed < QUERY_BUDGET_SECONDS, (
        f"prd list 耗时 {elapsed:.2f}s ≥ {QUERY_BUDGET_SECONDS}s")


def test_req_list_is_within_budget(scale_project: Path):
    result, elapsed = _timed(lambda: run_cli(scale_project, "req", "list", env=ENV))
    assert result.code == 0, result.stdout[-2000:]
    assert result.json["data"]["count"] == REQS
    assert elapsed < QUERY_BUDGET_SECONDS, (
        f"req list 耗时 {elapsed:.2f}s ≥ {QUERY_BUDGET_SECONDS}s")


def test_single_state_change_is_within_budget(scale_project: Path):
    """真实状态变更：`req create`（分配编号 → 原子写 → 计数器）+ `req confirm`（NEW → TRIAGED）。"""
    created, create_elapsed = _timed(lambda: run_cli(
        scale_project, "req", "create", "--title", "性能基准需求", "--module", "mod-01",
        env=ENV))
    assert created.code == 0, created.stdout[-2000:]
    assert create_elapsed < STATE_CHANGE_BUDGET_SECONDS, (
        f"req create 耗时 {create_elapsed:.2f}s ≥ {STATE_CHANGE_BUDGET_SECONDS}s")

    req_id = created.json["data"]["req"]["id"]
    confirmed, confirm_elapsed = _timed(lambda: run_cli(
        scale_project, "req", "confirm", "--id", req_id, "--actor", "bob", "--confirm",
        env=ENV))
    assert confirmed.code == 0, confirmed.stdout[-2000:]
    assert confirmed.json["data"]["req"]["status"] == "TRIAGED"
    assert confirm_elapsed < STATE_CHANGE_BUDGET_SECONDS, (
        f"req confirm 耗时 {confirm_elapsed:.2f}s ≥ {STATE_CHANGE_BUDGET_SECONDS}s")


def test_lint_findings_are_scale_stable(scale_project: Path):
    """规模下 lint 仍然只读且输出稳定（同输入 → 同一份 findings）。"""
    first = run_cli(scale_project, "lint", env=ENV)
    second = run_cli(scale_project, "lint", env=ENV)
    assert first.code == 0, first.stdout[-2000:]
    assert first.json["data"] == second.json["data"]
