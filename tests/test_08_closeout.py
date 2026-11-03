"""ticket 08：可恢复的验收收尾（黑盒 CLI）。

覆盖：完整收尾、首次/已有功能现状、中断恢复、部分失败保留「已确认通过，收尾未完成」、
基线冲突拒绝覆盖、索引受管理区块与区块外内容、索引失败不改业务状态、
`recover release-lock` 的存活/死进程判定、审计记录保留。
"""

from __future__ import annotations

import json
import os
from pathlib import Path


from conftest import read, run_cli, write
from helpers_03 import ENV, prd_meta, req_meta
import helpers_07 as h7
import helpers_08 as h8

SCENARIOS = ("SC-RES-01", "SC-RES-02")


# --------------------------------------------------------------------------
# 完整收尾
# --------------------------------------------------------------------------
def test_closeout_syncs_everything(inited_project: Path):
    h8.accepted(inited_project)
    before = prd_meta(inited_project)
    assert req_meta(inited_project)["status"] == "IN_PRD"

    result = h8.closeout(inited_project, extra=("--actor", "alice"))

    assert result.ok, result
    data = result.json["data"]
    assert data["closeout"]["state"] == "COMMITTED"
    assert data["main_req_status"] == "DONE"

    # ①功能现状被创建，且含本次场景与变更
    text = h8.feature_doc_text(inited_project)
    assert text, "首次验收必须创建功能现状文档"
    assert "SC-RES-01" in text and "SC-RES-02" in text
    assert "本次变化" in text
    assert "PRD-001" in text

    # ②主需求 IN_PRD → DONE
    meta = req_meta(inited_project)
    assert meta["status"] == "DONE"
    assert any(item.get("action") == "acceptance.closeout" for item in meta["history"])

    # ③场景状态 → 已验收
    rows = h8.scenario_rows(inited_project)
    for scenario in SCENARIOS:
        assert rows[scenario]["status"] == "已验收", rows
        assert rows[scenario]["prd"] == "PRD-001"

    # ④受管理索引同步
    for rel in h8.INDEX_FILES:
        text = h8.index_text(inited_project, rel)
        assert "neo-skill:managed:begin" in text, rel
        assert "PRD-001" in (h8.managed_body(text) or ""), rel
    assert "REQ-001" in (h8.managed_body(h8.index_text(
        inited_project, "03-requirements/backlog.md")) or "")

    # PRD 保持 ACCEPTED，验收结论不变，closeout 记录完整
    after = prd_meta(inited_project)
    assert after["status"] == "ACCEPTED"
    acceptance_after = dict(after["acceptance"])
    acceptance_before = dict(before["acceptance"])
    acceptance_after.pop("closeout")
    acceptance_before.pop("closeout")
    assert acceptance_after == acceptance_before, "收尾不得改动人工验收结论"
    assert after["closeout"]["state"] == "COMMITTED"
    assert after["closeout"]["tx"].startswith("TX-")
    assert after["closeout"]["by"] == "alice"
    assert after["acceptance"]["closeout"] == after["closeout"]


def test_closeout_transaction_keeps_baselines_and_order(inited_project: Path):
    h8.accepted(inited_project)
    result = h8.closeout(inited_project, extra=("--actor", "alice"))
    assert result.ok, result

    tx_id = result.json["data"]["tx"]
    tx = h8.tx_yaml(inited_project, tx_id)
    assert tx["id"] == tx_id
    assert tx["object"] == "PRD-001"
    assert tx["state"] == "COMMITTED"
    orders = [op["order"] for op in tx["ops"]]
    assert orders == sorted(orders), "ops 必须按 order 递增"
    assert len(tx["ops"]) == 6
    paths = [op["path"] for op in tx["ops"]]
    assert "03-requirements/items/REQ-001-batch-parse.md" in paths
    assert "02-definition/modules/resume/features/batch-parse.md" in paths
    for op in tx["ops"]:
        assert op["staged"].startswith("staged/")
        assert op["action"] in ("create", "update")
        assert op["baseline"] is None or op["baseline"].startswith("sha256:")
    # 主需求必须是最后一个操作：收尾未完成时不得 DONE（FR-076）
    assert paths[-1] == "03-requirements/items/REQ-001-batch-parse.md"
    # 暂存内容与审计记录都在
    assert len(h8.staged_files(inited_project, tx_id)) == 6


def test_closeout_is_idempotent(inited_project: Path):
    h8.accepted(inited_project)
    first = h8.closeout(inited_project, extra=("--actor", "alice"))
    assert first.ok, first
    tx_count = len(h8.transaction_ids(inited_project))

    second = h8.closeout(inited_project, extra=("--actor", "alice"))
    assert second.ok, second
    assert second.json["data"]["closeout"]["state"] == "COMMITTED"
    assert len(h8.transaction_ids(inited_project)) == tx_count, "已完成的收尾不得再开新事务"


def test_closeout_requires_confirmed_acceptance(inited_project: Path):
    h7.ready(inited_project)  # READY_FOR_ACCEPTANCE，尚未人工确认
    result = h8.closeout(inited_project, extra=("--actor", "alice"), expect=1)
    assert "ACCEPTANCE_NOT_CONFIRMED" in h8.error_codes(result)
    assert not h8.feature_doc(inited_project).exists()
    assert req_meta(inited_project)["status"] == "IN_PRD"
    assert h8.transaction_ids(inited_project) == []


def test_closeout_requires_prd(inited_project: Path):
    result = run_cli(inited_project, "acceptance", "closeout", env=ENV)
    assert result.code == 1
    assert "PRD_NOT_BOUND" in h8.error_codes(result)


# --------------------------------------------------------------------------
# 功能现状：首次创建 vs 已有功能现状追加
# --------------------------------------------------------------------------
def test_existing_feature_doc_is_appended_not_overwritten(inited_project: Path):
    human = """# 简历批量解析（功能现状）

## 概览

本功能自 v0.0.9 起存在，由客户成功团队维护说明。

## 已知限制

- 单批上限 50 份。
"""
    write(h8.feature_doc(inited_project), human)

    h8.accepted(inited_project)
    result = h8.closeout(inited_project, extra=("--actor", "alice"))
    assert result.ok, result

    text = h8.feature_doc_text(inited_project)
    assert "本功能自 v0.0.9 起存在，由客户成功团队维护说明。" in text, "既有正文不得被覆盖"
    assert "单批上限 50 份。" in text
    assert "本次变化" in text
    assert "SC-RES-01" in text
    assert text.startswith("# 简历批量解析（功能现状）"), "原有标题必须保留"


# --------------------------------------------------------------------------
# 部分失败：保留「已确认通过，收尾未完成」
# --------------------------------------------------------------------------
def test_partial_failure_keeps_acceptance_and_blocks_done(inited_project: Path):
    h8.accepted(inited_project)
    acceptance_before = prd_meta(inited_project)["acceptance"]

    result = h8.closeout(inited_project, extra=("--actor", "alice"), expect=1,
                         env={**ENV, "NEO_SKILL_FAIL_TX_STEP": "6"})

    assert result.code == 1
    assert "CLOSEOUT_INCOMPLETE" in h8.error_codes(result)
    assert "已确认通过" in result.json["errors"][0]["message"]
    assert "收尾未完成" in result.json["errors"][0]["message"]
    assert result.json["data"]["closeout"]["state"] in ("FAILED", "PREPARED")
    assert result.json["data"]["main_req_status"] == "IN_PRD"
    assert result.json["data"]["pending"], "必须列出未完成的同步目标"
    assert any("recover" in action for action in result.json["next_actions"])

    meta = prd_meta(inited_project)
    assert meta["status"] == "ACCEPTED", "人工验收结论不得被收尾失败影响"
    assert meta["acceptance"] == acceptance_before
    assert meta["closeout"]["state"] != "COMMITTED"
    assert meta["closeout"]["tx"].startswith("TX-")
    assert meta["closeout"]["error"]
    assert req_meta(inited_project)["status"] == "IN_PRD", "收尾未完成不得 DONE"

    # 事务与暂存内容作为审计记录保留
    tx_id = meta["closeout"]["tx"]
    assert h8.tx_yaml(inited_project, tx_id)["state"] in ("FAILED", "COMMITTING", "PREPARED")
    assert h8.staged_files(inited_project, tx_id)


def test_partial_failure_before_scenario_and_index(inited_project: Path):
    h8.accepted(inited_project)
    result = h8.closeout(inited_project, extra=("--actor", "alice"), expect=1,
                         env={**ENV, "NEO_SKILL_FAIL_TX_STEP": "2"})
    assert "CLOSEOUT_INCOMPLETE" in h8.error_codes(result)
    assert h8.feature_doc(inited_project).is_file(), "第 1 步已经写入"
    assert h8.scenario_rows(inited_project) == {}, "未写入的目标不得出现半成品"
    assert "PRD-001" not in (h8.managed_body(
        h8.index_text(inited_project, "04-prds/index.md")) or "")
    assert req_meta(inited_project)["status"] == "IN_PRD"


# --------------------------------------------------------------------------
# 恢复
# --------------------------------------------------------------------------
def test_recover_list_shows_pending_transaction(inited_project: Path):
    h8.accepted(inited_project)
    failed = h8.closeout(inited_project, extra=("--actor", "alice"), expect=1,
                        env={**ENV, "NEO_SKILL_FAIL_TX_STEP": "4"})
    tx_id = failed.json["data"]["tx"]

    listing = h8.recover(inited_project, "list")
    assert listing.ok, listing
    items = {item["id"]: item for item in listing.json["data"]["transactions"]}
    assert tx_id in items
    entry = items[tx_id]
    assert entry["object"] == "PRD-001"
    assert entry["state"] in ("FAILED", "COMMITTING", "PREPARED")
    assert entry["pending"], "必须报告未完成的同步目标"
    assert entry["created_at"]


def test_recover_run_completes_interrupted_closeout(inited_project: Path):
    h8.accepted(inited_project)
    failed = h8.closeout(inited_project, extra=("--actor", "alice"), expect=1,
                        env={**ENV, "NEO_SKILL_FAIL_TX_STEP": "5"})
    tx_id = failed.json["data"]["tx"]

    result = h8.recover(inited_project, "run", "--tx", tx_id, "--actor", "alice",
                        "--confirm")
    assert result.ok, result
    assert result.json["data"]["main_req_status"] == "DONE"

    tx = h8.tx_yaml(inited_project, tx_id)
    assert tx["state"] == "COMMITTED"
    assert req_meta(inited_project)["status"] == "DONE"
    rows = h8.scenario_rows(inited_project)
    assert rows["SC-RES-01"]["status"] == "已验收"
    assert prd_meta(inited_project)["closeout"]["state"] == "COMMITTED"
    assert prd_meta(inited_project)["status"] == "ACCEPTED"


def test_recover_run_from_committing_state(inited_project: Path):
    """进程在 COMMITTING 中途被杀：已写入的目标不重复写，未写入的补齐。"""
    h8.accepted(inited_project)
    failed = h8.closeout(inited_project, extra=("--actor", "alice"), expect=1,
                        env={**ENV, "NEO_SKILL_FAIL_TX_STEP": "4"})
    tx_id = failed.json["data"]["tx"]
    h8.set_tx_state(inited_project, tx_id, "COMMITTING")

    result = h8.recover(inited_project, "run", "--tx", tx_id, "--actor", "alice",
                        "--confirm")
    assert result.ok, result
    assert h8.tx_yaml(inited_project, tx_id)["state"] == "COMMITTED"
    assert req_meta(inited_project)["status"] == "DONE"
    assert "PRD-001" in (h8.managed_body(
        h8.index_text(inited_project, "04-prds/index.md")) or "")


def test_recover_run_from_prepared_state(inited_project: Path):
    """进程在 PREPARED（还没写任何目标）处被杀：recover 从头按 order 落地。"""
    h8.accepted(inited_project)
    failed = h8.closeout(inited_project, extra=("--actor", "alice"), expect=1,
                        env={**ENV, "NEO_SKILL_FAIL_TX_STEP": "1"})
    tx_id = failed.json["data"]["tx"]
    # 第 1 步就失败 → 没有任何目标落地；把状态改回 PREPARED 模拟进程刚建完事务就被杀
    h8.set_tx_state(inited_project, tx_id, "PREPARED")
    assert not h8.feature_doc(inited_project).exists()

    result = h8.recover(inited_project, "run", "--tx", tx_id, "--actor", "alice",
                        "--confirm")
    assert result.ok, result
    assert h8.tx_yaml(inited_project, tx_id)["state"] == "COMMITTED"
    assert h8.feature_doc(inited_project).is_file()
    assert req_meta(inited_project)["status"] == "DONE"


def test_recover_run_without_tx_recovers_all_unfinished(inited_project: Path):
    h8.accepted(inited_project)
    failed = h8.closeout(inited_project, extra=("--actor", "alice"), expect=1,
                        env={**ENV, "NEO_SKILL_FAIL_TX_STEP": "5"})
    tx_id = failed.json["data"]["tx"]

    result = h8.recover(inited_project, "run", "--actor", "alice", "--confirm")
    assert result.ok, result
    assert [item["id"] for item in result.json["data"]["recovered"]] == [tx_id]
    assert result.json["data"]["main_req_status"] == "DONE"

    # 恢复后没有未完成事务；--all 仍能看到审计记录
    assert h8.recover(inited_project, "list").json["data"]["transactions"] == []
    everything = h8.recover(inited_project, "list", "--all").json["data"]["transactions"]
    assert [item["id"] for item in everything] == [tx_id]
    assert everything[0]["finished"] is True
    assert everything[0]["pending"] == []


def test_conflict_then_fresh_closeout_works(inited_project: Path):
    """基线冲突的恢复建议（重新执行 closeout 重算基线）是可执行的。"""
    h8.accepted(inited_project)
    failed = h8.closeout(inited_project, extra=("--actor", "alice"), expect=1,
                        env={**ENV, "NEO_SKILL_FAIL_TX_STEP": "6"})
    tx_id = failed.json["data"]["tx"]
    req_path = inited_project / "03-requirements/items/REQ-001-batch-parse.md"
    write(req_path, read(req_path).replace("status: IN_PRD", "status: IN_PRD\nnote: 人工补充"))

    assert "BASELINE_CONFLICT" in h8.error_codes(
        h8.recover(inited_project, "run", "--tx", tx_id, "--actor", "alice",
                   "--confirm", expect=1))

    fresh = h8.closeout(inited_project, extra=("--actor", "alice"))
    assert fresh.ok, fresh
    assert fresh.json["data"]["closeout"]["tx"] != tx_id, "应开一条新事务"
    assert req_meta(inited_project)["status"] == "DONE"
    # 旧的失败事务作为审计记录保留
    assert h8.tx_yaml(inited_project, tx_id)["state"] != "COMMITTED"


def test_recover_refuses_baseline_conflict(inited_project: Path):
    h8.accepted(inited_project)
    failed = h8.closeout(inited_project, extra=("--actor", "alice"), expect=1,
                        env={**ENV, "NEO_SKILL_FAIL_TX_STEP": "6"})
    tx_id = failed.json["data"]["tx"]

    # 收尾失败后有人改了主需求文件：恢复必须拒绝静默覆盖
    req_path = inited_project / "03-requirements/items/REQ-001-batch-parse.md"
    edited = read(req_path).replace("status: IN_PRD", "status: IN_PRD\nnote: 人工补充")
    write(req_path, edited)

    result = h8.recover(inited_project, "run", "--tx", tx_id, "--actor", "alice",
                        "--confirm", expect=1)
    assert "BASELINE_CONFLICT" in h8.error_codes(result)
    error = result.json["errors"][0]
    assert error.get("suggestion"), "基线冲突必须给出恢复建议"
    assert read(req_path) == edited, "冲突时不得覆盖目标文件"
    assert req_meta(inited_project)["status"] == "IN_PRD"
    assert h8.tx_yaml(inited_project, tx_id)["state"] != "COMMITTED"


def test_recover_requires_confirmation(inited_project: Path):
    h8.accepted(inited_project)
    failed = h8.closeout(inited_project, extra=("--actor", "alice"), expect=1,
                        env={**ENV, "NEO_SKILL_FAIL_TX_STEP": "2"})
    tx_id = failed.json["data"]["tx"]
    result = h8.recover(inited_project, "run", "--tx", tx_id, expect=1)
    assert "CONFIRMATION_REQUIRED" in h8.error_codes(result)
    assert h8.tx_yaml(inited_project, tx_id)["state"] != "COMMITTED"


def test_recover_unknown_tx(inited_project: Path):
    result = h8.recover(inited_project, "run", "--tx", "TX-19700101-0001",
                        "--actor", "alice", "--confirm", expect=1)
    assert "TX_NOT_FOUND" in h8.error_codes(result)


# --------------------------------------------------------------------------
# closeout-status（ticket 09 lint 复用入口）
# --------------------------------------------------------------------------
def test_closeout_status_before_confirmation(inited_project: Path):
    """未确认不是错误：closeout-status 只报告事实（confirmed=false），不改任何东西。"""
    h7.ready(inited_project)
    result = h8.closeout_status(inited_project)
    assert result.ok, result
    data = result.json["data"]
    assert data["confirmed"] is False
    assert data["closeout_state"] is None
    assert data["closeout_ok"] is False
    assert req_meta(inited_project)["status"] == "IN_PRD"
    assert h8.transaction_ids(inited_project) == []


def test_closeout_status_reports_progress(inited_project: Path):
    h8.accepted(inited_project)

    pending = h8.closeout_status(inited_project)
    assert pending.ok, pending
    data = pending.json["data"]
    assert data["confirmed"] is True
    assert data["closeout_state"] is None
    assert data["main_req_status"] == "IN_PRD"
    assert data["resumable"] is True
    assert data["pending_ops"], "未收尾时必须列出待办"
    assert data["index_ok"] is False
    assert data["scenarios"] == {}, "收尾前磁盘上还没有场景状态"
    assert data["target_scenarios"]["SC-RES-01"] == "已验收"

    assert h8.closeout(inited_project, extra=("--actor", "alice")).ok
    done = h8.closeout_status(inited_project)
    data = done.json["data"]
    assert data["closeout_state"] == "COMMITTED"
    assert data["main_req_status"] == "DONE"
    assert data["index_ok"] is True
    assert data["pending_ops"] == []
    assert data["scenarios"]["SC-RES-01"] == "已验收"


# --------------------------------------------------------------------------
# 索引
# --------------------------------------------------------------------------
def test_index_generates_three_managed_blocks(inited_project: Path):
    h8.accepted(inited_project)
    result = h8.index(inited_project)
    assert result.ok, result

    for rel in h8.INDEX_FILES:
        text = h8.index_text(inited_project, rel)
        body = h8.managed_body(text)
        assert body is not None, f"{rel} 缺少受管理区块"
        assert h8.managed_fingerprint(text), f"{rel} 缺少 fingerprint"
        assert "PRD-001" in body, rel

    # 确定性：同输入重复生成 → 无变化
    again = h8.index(inited_project)
    assert again.ok, again
    actions = set(again.actions().values())
    assert actions <= {"unchanged"}, again.actions()


def test_index_preserves_content_outside_block(inited_project: Path):
    path = inited_project / "03-requirements/backlog.md"
    human = "# 需求总览\n\n人工写的说明：本季度只看 P0。\n"
    write(path, human)

    h8.accepted(inited_project)
    assert h8.index(inited_project).ok

    text = read(path)
    assert "人工写的说明：本季度只看 P0。" in text
    assert text.startswith("# 需求总览"), "区块外内容必须原样保留"
    assert "neo-skill:managed:begin" in text
    assert "REQ-001" in (h8.managed_body(text) or "")


def test_index_missing_block_is_appended(inited_project: Path):
    path = inited_project / "04-prds/index.md"
    write(path, "# PRD 总览\n\n人工序言。\n")
    h8.accepted(inited_project)
    assert h8.index(inited_project).ok
    text = read(path)
    assert "人工序言。" in text
    assert text.rstrip().endswith("<!-- neo-skill:managed:end -->")


def test_index_modified_block_requires_confirm(inited_project: Path):
    h8.accepted(inited_project)
    assert h8.index(inited_project).ok

    path = inited_project / "02-definition/feature-map.md"
    tampered = read(path).replace("| PRD-001 |", "| PRD-001（人工备注） |")
    write(path, tampered)

    blocked = h8.index(inited_project, expect=1)
    assert "MANAGED_BLOCK_MODIFIED" in h8.error_codes(blocked)
    assert "PRD-001（人工备注）" in read(path), "未确认前不得覆盖人工修改"

    forced = h8.index(inited_project, "--confirm", "--actor", "alice")
    assert forced.ok, forced
    text = read(path)
    assert "人工备注" not in (h8.managed_body(text) or "")
    assert "PRD-001" in (h8.managed_body(text) or "")


def test_index_failure_does_not_change_business_state(inited_project: Path):
    h8.accepted(inited_project)
    assert h8.index(inited_project).ok
    before = h8.index_text(inited_project, "03-requirements/backlog.md")
    prd_before = read(inited_project / "04-prds/resume/PRD-001-batch-parse/metadata.yaml")
    req_before = read(inited_project / "03-requirements/items/REQ-001-batch-parse.md")

    write(inited_project / "03-requirements/backlog.md",
          before.replace("REQ-001", "REQ-001（人工改动）"))
    blocked = h8.index(inited_project, expect=1)
    assert "MANAGED_BLOCK_MODIFIED" in h8.error_codes(blocked)

    assert read(inited_project / "04-prds/resume/PRD-001-batch-parse/metadata.yaml") == prd_before
    assert read(inited_project / "03-requirements/items/REQ-001-batch-parse.md") == req_before
    assert req_meta(inited_project)["status"] == "IN_PRD"


def test_closeout_confirm_overrides_modified_block(inited_project: Path):
    h8.accepted(inited_project)
    assert h8.index(inited_project).ok
    path = inited_project / "04-prds/index.md"
    write(path, read(path).replace("| PRD-001 |", "| PRD-001（人工改动） |"))

    result = h8.closeout(inited_project, extra=("--actor", "alice", "--confirm"))
    assert result.ok, result
    assert "人工改动" not in (h8.managed_body(read(path)) or "")
    assert req_meta(inited_project)["status"] == "DONE"


def test_closeout_refuses_modified_scenario_block(inited_project: Path):
    """模块 README 的场景区块也是受管理区块：人工改过就要 --confirm 才覆盖。"""
    readme = h8.module_readme(inited_project)
    write(readme, (
        "# resume 模块\n\n人工说明：本模块由招聘团队维护。\n\n"
        "## 场景状态\n\n"
        "<!-- neo-skill:managed:begin fingerprint=deadbeef -->\n"
        "| 场景 | 状态 | 主 PRD | 说明 |\n"
        "| --- | --- | --- | --- |\n"
        "| SC-RES-01 | 开发中 | PRD-001 | 人工填的状态 |\n"
        "<!-- neo-skill:managed:end -->\n"
    ))
    h8.accepted(inited_project)

    blocked = h8.closeout(inited_project, extra=("--actor", "alice"), expect=1)
    assert "MANAGED_BLOCK_MODIFIED" in h8.error_codes(blocked)
    assert "人工填的状态" in h8.module_readme_text(inited_project)
    assert req_meta(inited_project)["status"] == "IN_PRD"

    forced = h8.closeout(inited_project, extra=("--actor", "alice", "--confirm"))
    assert forced.ok, forced
    text = h8.module_readme_text(inited_project)
    assert "人工说明：本模块由招聘团队维护。" in text, "区块外人工内容必须保留"
    body = h8.managed_body(text) or ""
    assert "人工填的状态" not in body
    assert "| SC-RES-01 | 已验收 |" in body
    assert req_meta(inited_project)["status"] == "DONE"


def test_closeout_retry_completes_interrupted_closeout(inited_project: Path):
    h8.accepted(inited_project)
    failed = h8.closeout(inited_project, extra=("--actor", "alice"), expect=1,
                        env={**ENV, "NEO_SKILL_FAIL_TX_STEP": "6"})
    tx_id = failed.json["data"]["tx"]

    result = h8.closeout(inited_project, extra=("--actor", "alice", "--retry"))
    assert result.ok, result
    assert h8.tx_yaml(inited_project, tx_id)["state"] == "COMMITTED"
    assert req_meta(inited_project)["status"] == "DONE"
    assert prd_meta(inited_project)["closeout"]["state"] == "COMMITTED"


def test_closeout_refuses_modified_index_block(inited_project: Path):
    h8.accepted(inited_project)
    assert h8.index(inited_project).ok
    path = inited_project / "04-prds/index.md"
    write(path, read(path).replace("| PRD-001 |", "| PRD-001（人工改动） |"))

    result = h8.closeout(inited_project, extra=("--actor", "alice"), expect=1)
    assert "MANAGED_BLOCK_MODIFIED" in h8.error_codes(result)
    assert "PRD-001（人工改动）" in read(path)
    assert req_meta(inited_project)["status"] == "IN_PRD"
    assert prd_meta(inited_project)["status"] == "ACCEPTED"


# --------------------------------------------------------------------------
# 残留锁
# --------------------------------------------------------------------------
def _write_lock(project: Path, object_id: str, pid: int) -> Path:
    """构造一个残留锁（锁文件格式是 JSON，见共享设计 §7）。"""
    path = project / ".neo-skill" / "locks" / f"{object_id}.lock"
    write(path, json.dumps({
        "object": object_id, "pid": pid, "created_at": "2026-03-01T10:00:00+08:00",
        "command": "prd.approve", "rules_version": 1,
    }, ensure_ascii=False, indent=2))
    return path


def test_release_lock_refuses_live_pid(inited_project: Path):
    lock = _write_lock(inited_project, "REQ-001", os.getpid())
    result = h8.recover(inited_project, "release-lock", "--object", "REQ-001",
                        "--actor", "alice", "--confirm", expect=1)
    assert "LOCK_HELD" in h8.error_codes(result)
    assert lock.exists(), "持有进程仍存活时不得删除锁"


def test_release_lock_removes_dead_pid(inited_project: Path):
    lock = _write_lock(inited_project, "PRD-001", 999_999)
    result = h8.recover(inited_project, "release-lock", "--object", "PRD-001",
                        "--actor", "alice", "--confirm")
    assert result.ok, result
    assert result.json["data"]["released"] is True
    assert not lock.exists()


def test_release_lock_accepts_doctor_suggested_form(inited_project: Path):
    """`doctor` 的建议文案是 `recover --release-lock <对象> --actor <id> --confirm`：
    这条命令必须能照抄执行。"""
    lock = _write_lock(inited_project, "PRD-001", 999_999)
    result = run_cli(inited_project, "recover", "--release-lock", "PRD-001",
                     "--actor", "alice", "--confirm", env=ENV)
    assert result.ok, result
    assert result.json["data"]["released"] is True
    assert not lock.exists()


def test_release_lock_requires_confirmation(inited_project: Path):
    lock = _write_lock(inited_project, "PRD-001", 999_999)
    result = h8.recover(inited_project, "release-lock", "--object", "PRD-001",
                        "--actor", "alice", expect=1)
    assert "CONFIRMATION_REQUIRED" in h8.error_codes(result)
    assert lock.exists()


def test_release_lock_unknown_object(inited_project: Path):
    result = h8.recover(inited_project, "release-lock", "--object", "REQ-009",
                        "--actor", "alice", "--confirm", expect=1)
    assert "LOCK_NOT_FOUND" in h8.error_codes(result)


# --------------------------------------------------------------------------
# 审计
# --------------------------------------------------------------------------
def test_transaction_audit_is_kept_after_success(inited_project: Path):
    h8.accepted(inited_project)
    result = h8.closeout(inited_project, extra=("--actor", "alice"))
    assert result.ok, result
    tx_id = result.json["data"]["tx"]
    assert h8.transactions_dir(inited_project).is_dir()
    assert (h8.transactions_dir(inited_project) / tx_id / "tx.yaml").is_file()
    assert len(h8.staged_files(inited_project, tx_id)) == 6
    assert (h8.transactions_dir(inited_project) / tx_id / "tx.yaml").is_file()


def test_no_lock_left_after_closeout(inited_project: Path):
    h8.accepted(inited_project)
    assert h8.closeout(inited_project, extra=("--actor", "alice")).ok
    leftovers = sorted((inited_project / ".neo-skill/locks").glob("*.lock"))
    assert leftovers == [], leftovers
