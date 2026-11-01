"""ticket 01：共享内核模块契约（原子写、锁、状态机、hash、编号、规则加载）。

这一组是**唯一**直接导入包内模块的测试文件，原因是 ticket 01 里没有通过 CLI
暴露这些原语的命令（`recover` 在 ticket 08）。它们测试的是后续 9 个 ticket 依赖的
公共接口签名与语义，不是内部实现细节。
"""

from __future__ import annotations

from pathlib import Path

import pytest

from conftest import read, run_cli, write

from flow_core import fsutil, hashing, ids, locks, rules as rules_mod, state, workspace as ws
from flow_core.output import FlowError


@pytest.fixture()
def ws_root(tmp_path: Path) -> Path:
    root = tmp_path / "proj"
    root.mkdir()
    return root


@pytest.fixture()
def space(ws_root: Path) -> ws.Workspace:
    return ws.Workspace(ws_root)


# ---------------------------------------------------------------- 原子写
def test_atomic_write_replaces_content_and_leaves_no_temp(tmp_path: Path) -> None:
    target = tmp_path / "doc.md"
    fsutil.atomic_write_text(target, "第一版\n")

    fsutil.atomic_write_text(target, "第二版\n")

    assert read(target) == "第二版\n"
    leftovers = [p.name for p in tmp_path.iterdir() if p.name != "doc.md"]
    assert leftovers == []


def test_atomic_write_failure_keeps_existing_content(tmp_path: Path) -> None:
    blocker = tmp_path / "blocked"
    write(blocker, "我是文件，不是目录\n")

    with pytest.raises(OSError):
        fsutil.atomic_write_text(blocker / "doc.md", "内容\n")

    assert read(blocker) == "我是文件，不是目录\n"


def test_front_matter_round_trip(tmp_path: Path) -> None:
    data = {"id": "REQ-001", "status": "NEW", "related": []}
    text = fsutil.render_front_matter(data, "# 标题\n\n正文\n")

    parsed, body = fsutil.parse_front_matter(text)

    assert parsed == data
    assert body.startswith("# 标题")
    assert fsutil.parse_front_matter("# 无 front matter\n") == ({}, "# 无 front matter\n")


# ---------------------------------------------------------------- 锁
def test_lock_conflict_reports_holder(space: ws.Workspace) -> None:
    locks.acquire(space, "PRD-001", command="prd.approve", rules_version=1)

    with pytest.raises(FlowError) as excinfo:
        locks.acquire(space, "PRD-001", command="prd.submit-review")

    error = excinfo.value
    assert error.code == "LOCK_CONFLICT"
    assert error.object == "PRD-001"
    assert str(__import__("os").getpid()) in error.message
    assert "prd.approve" in error.message
    locks.release(space, "PRD-001")


def test_object_lock_context_releases_on_exception(space: ws.Workspace) -> None:
    with pytest.raises(RuntimeError):
        with locks.object_lock(space, "REQ-001", command="req.plan"):
            raise RuntimeError("boom")

    assert not locks.lock_path(space, "REQ-001").exists()
    assert locks.list_locks(space) == []


def test_object_locks_roll_back_partial_acquisition(space: ws.Workspace) -> None:
    locks.acquire(space, "PRD-002", command="other")

    with pytest.raises(FlowError):
        with locks.object_locks(space, ["PRD-001", "PRD-002"], command="acceptance.approve"):
            pass

    assert not locks.lock_path(space, "PRD-001").exists()
    assert locks.lock_path(space, "PRD-002").exists()


def test_lock_record_contains_required_fields(space: ws.Workspace) -> None:
    locks.acquire(space, "REQ-007", command="req.confirm", rules_version=3)

    info = locks.read_lock(space, "REQ-007")

    assert info is not None
    assert info.object == "REQ-007"
    assert info.pid > 0
    assert info.command == "req.confirm"
    assert info.rules_version == 3
    assert info.created_at.endswith("+08:00") or info.created_at.endswith("Z") or "T" in info.created_at


# ---------------------------------------------------------------- 编号
def test_ids_increase_monotonically_and_never_reuse(space: ws.Workspace) -> None:
    assert ids.allocate(space, "REQ") == "REQ-001"
    assert ids.allocate(space, "REQ") == "REQ-002"
    assert ids.allocate(space, "PRD") == "PRD-001"
    assert ids.allocate(space, "REQ") == "REQ-003"

    counters = fsutil.read_yaml(space.counters_file)
    assert counters["REQ"] == 3
    assert counters["PRD"] == 1


# ---------------------------------------------------------------- 规则
def test_rules_prefer_project_baseline(space: ws.Workspace) -> None:
    project_rules = space.project_rules_file
    write(project_rules, "version: 1\nreq:\n  initial: NEW\n  states: [NEW]\n  transitions: {NEW: []}\n")

    loaded = rules_mod.load_rules(space)

    assert loaded.origin == "project"
    assert loaded.states("req") == ["NEW"]


def test_rules_fall_back_to_packaged(space: ws.Workspace) -> None:
    loaded = rules_mod.load_rules(space)

    assert loaded.origin == "packaged"
    assert loaded.states("prd")[0] == "DRAFT"
    assert loaded.roles


def test_state_machine_declares_contract_transitions() -> None:
    loaded = rules_mod.load_rules()

    assert loaded.can_transition("req", "NEW", "TRIAGED")
    assert loaded.can_transition("req", "PLANNED", "TRIAGED")
    assert not loaded.can_transition("req", "NEW", "PLANNED")
    assert loaded.can_transition("prd", "DRAFT", "IN_REVIEW")
    assert loaded.can_transition("prd", "IN_REVIEW", "APPROVED")
    assert loaded.can_transition("prd", "READY_FOR_ACCEPTANCE", "DEVELOPING")
    assert not loaded.can_transition("prd", "DRAFT", "APPROVED")
    assert loaded.limit("max_scenarios_per_prd", 0) == 8
    assert loaded.id_prefixes["req"] == "REQ"
    assert set(loaded.prunable_groups) == {
        "discovery", "design", "growth", "releases", "meetings", "reports",
    }


# ---------------------------------------------------------------- 状态与确认
def test_apply_transition_appends_history_without_touching_prior_entries() -> None:
    loaded = rules_mod.load_rules()
    metadata = {"id": "REQ-001", "status": "NEW", "history": [{"at": "旧记录"}]}

    state.apply_transition(metadata, machine="req", to_state="TRIAGED", actor="alice",
                           action="req.confirm", rules=loaded, role="stakeholder")

    assert metadata["status"] == "TRIAGED"
    assert metadata["history"][0] == {"at": "旧记录"}
    entry = metadata["history"][-1]
    assert entry["from"] == "NEW"
    assert entry["to"] == "TRIAGED"
    assert entry["by"] == "alice"
    assert entry["action"] == "req.confirm"
    assert entry["rules_version"] == loaded.version
    assert metadata["confirmations"][-1]["role"] == "stakeholder"


def test_apply_transition_rejects_illegal_jump() -> None:
    loaded = rules_mod.load_rules()
    metadata = {"id": "REQ-001", "status": "NEW", "history": []}

    with pytest.raises(FlowError) as excinfo:
        state.apply_transition(metadata, machine="req", to_state="DONE", actor="alice",
                               action="req.confirm", rules=loaded)

    assert excinfo.value.code == "INVALID_TRANSITION"
    assert metadata["status"] == "NEW"
    assert metadata["history"] == []


def test_require_confirmation_needs_actor_and_flag() -> None:
    with pytest.raises(FlowError) as excinfo:
        state.require_confirmation(None, False, command="prd.approve")
    assert excinfo.value.code == "CONFIRMATION_REQUIRED"

    with pytest.raises(FlowError):
        state.require_confirmation("alice", False, command="prd.approve")

    assert state.require_confirmation("alice", True, command="prd.approve") == "alice"


def test_require_member_reports_missing_team_unknown_actor_and_role_mismatch(
    space: ws.Workspace,
) -> None:
    with pytest.raises(FlowError) as excinfo:
        state.require_member(None, "alice", role=state.ROLE_REQUIREMENT_OWNER)
    assert excinfo.value.code == "TEAM_NOT_CONFIGURED"

    team = state.Team(
        confirmed=True,
        members=(
            state.Member(id="alice", name="爱丽丝", roles=(state.ROLE_DEVELOPER,)),
            state.Member(id="bob", name="鲍勃", roles=(state.ROLE_REQUIREMENT_OWNER,)),
        ),
    )
    with pytest.raises(FlowError) as excinfo:
        state.require_member(team, "carol", role=state.ROLE_REQUIREMENT_OWNER)
    assert excinfo.value.code == "ACTOR_UNKNOWN"

    with pytest.raises(FlowError) as excinfo:
        state.require_member(team, "alice", role=state.ROLE_REQUIREMENT_OWNER)
    assert excinfo.value.code == "ROLE_MISMATCH"

    member = state.require_member(team, "bob", role=state.ROLE_REQUIREMENT_OWNER)
    assert member.id == "bob"


def test_team_file_is_read_from_project(inited_project: Path) -> None:
    team_file = inited_project / "00-management/team.md"
    text = read(team_file).replace(
        "members: []",
        "members:\n  - {id: alice, name: 爱丽丝, roles: [requirement_owner, project_owner]}",
    ).replace("confirmed: false", "confirmed: true")
    write(team_file, text)

    team = state.load_team(ws.Workspace(inited_project))

    assert team is not None
    assert team.confirmed is True
    assert team.member("alice").has_role(state.ROLE_REQUIREMENT_OWNER)
    assert team.member("bob") is None


# ---------------------------------------------------------------- hash
def test_prd_content_hash_ignores_history_and_reviews(tmp_path: Path) -> None:
    prd_dir = tmp_path / "PRD-001-demo"
    prd_dir.mkdir()
    write(prd_dir / "prd.md", "# PRD\n\n正文\n")
    metadata = {
        "id": "PRD-001",
        "version": 1,
        "status": "APPROVED",
        "locked_fields": ["id", "version"],
        "history": [],
        "confirmations": [],
        "content_hash": None,
    }

    first = hashing.prd_content_hash(prd_dir, metadata)

    metadata["status"] = "DEVELOPING"
    metadata["history"].append({"at": "后来"})
    (prd_dir / "reviews").mkdir()
    write(prd_dir / "reviews/2026-01-01-review.md", "评审记录\n")
    assert hashing.prd_content_hash(prd_dir, metadata) == first

    write(prd_dir / "prd.md", "# PRD\n\n正文改了\n")
    assert hashing.prd_content_hash(prd_dir, metadata) != first

    write(prd_dir / "prd.md", "# PRD\n\n正文\n")
    metadata["version"] = 2
    assert hashing.prd_content_hash(prd_dir, metadata) != first


def test_prd_content_hash_includes_optional_test_cases(tmp_path: Path) -> None:
    prd_dir = tmp_path / "PRD-001-demo"
    prd_dir.mkdir()
    write(prd_dir / "prd.md", "# PRD\n")
    metadata = {"id": "PRD-001", "version": 1, "locked_fields": ["id"]}

    without = hashing.prd_content_hash(prd_dir, metadata)
    write(prd_dir / "test-cases.md", "- 用例 1\n")

    assert hashing.prd_content_hash(prd_dir, metadata) != without


# ---------------------------------------------------------------- 根目录与裁剪
def test_resolve_root_walks_up_from_cwd(inited_project: Path) -> None:
    nested = inited_project / "src" / "deep"
    nested.mkdir(parents=True)

    resolved = ws.resolve_root(None, start=nested)

    assert resolved == inited_project.resolve()


def test_resolve_root_defaults_to_cwd_when_nothing_found(tmp_path: Path) -> None:
    plain = tmp_path / "plain"
    plain.mkdir()

    assert ws.resolve_root(None, start=plain) == plain.resolve()


def test_prune_group_mapping_covers_contract_groups() -> None:
    assert ws.prune_group_of("01-discovery/market") == "discovery"
    assert ws.prune_group_of("05-design") == "design"
    assert ws.prune_group_of("09-growth/retros") == "growth"
    assert ws.prune_group_of("00-management/meetings") == "meetings"
    assert ws.prune_group_of("00-management/reports") == "reports"
    assert ws.prune_group_of("08-releases/CHANGELOG.md") == "releases"
    assert ws.prune_group_of("03-requirements/items") is None


def test_core_paths_are_not_prunable() -> None:
    for rel in ("03-requirements", "04-prds/index.md", "07-acceptance", ".specify",
                ".neo-skill", "00-management/charter.md", "02-definition/mvp-scope.md"):
        assert ws.is_not_prunable(rel), rel
    assert not ws.is_not_prunable("09-growth")


# ---------------------------------------------------------------- CLI 层无副作用
def test_failed_init_leaves_no_files_or_temp(project: Path) -> None:
    result = run_cli(project, "init", "--prune", "03-requirements")

    assert result.code == 1
    assert result.json["errors"][0]["code"] == "NOT_PRUNABLE"
    assert list(project.iterdir()) == []


def test_init_leaves_no_temp_files(project: Path) -> None:
    assert run_cli(project, "init").code == 0

    leftovers = [p for p in project.rglob("*.tmp")] + [p for p in project.rglob(".*.tmp")]
    assert leftovers == []
