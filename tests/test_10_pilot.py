"""ticket 10：隔离端到端试点（`scripts/pilot.sh`）。

单一用例驱动完整试点（试点本身就是一条串行链路，拆成多个用例会重复跑十几秒的命令序列
并让证据分散）。断言全部落在**试点自己产出的终态**上：`evidence/assertions.txt`、
`evidence/lint.json`、`evidence/index-status.json`、状态历史副本与快照比对结果。

Claude Code 真调用单独一个用例，标记 `@pytest.mark.external`：
它依赖本机安装并已登录的 `claude` CLI，且一次调用要几十秒到几分钟，
不具备 CI 上的确定性。`claude` 不可用时 `pytest.skip` 并写明原因——
不伪造任何"跑过"的结论。

默认执行时用 `-m "not external"` 排除（见 scripts/check.sh 与 CI）；
本地想验证真实调用时去掉该过滤，或直接跑 `scripts/pilot.sh --with-claude`。
"""

from __future__ import annotations

import json
import os
import shutil
import subprocess
from pathlib import Path

import pytest
import yaml

REPO_ROOT = Path(__file__).resolve().parents[1]
PILOT = REPO_ROOT / "scripts" / "pilot.sh"


@pytest.fixture(scope="module")
def pilot_run(tmp_path_factory: pytest.TempPathFactory) -> Path:
    """跑一次完整试点（含隔离快照比对），返回证据目录。"""
    workdir = tmp_path_factory.mktemp("pilot")
    env = dict(os.environ)
    env.pop("NEO_SKILL_HOME", None)
    env.pop("PYTHONPATH", None)
    env["PYTHONIOENCODING"] = "utf-8"
    proc = subprocess.run(
        ["bash", str(PILOT), "--workdir", str(workdir)],
        capture_output=True, text=True, encoding="utf-8", env=env, timeout=900,
    )
    assert proc.returncode == 0, (
        "试点失败：\n--- stdout ---\n%s\n--- stderr ---\n%s" % (proc.stdout[-6000:], proc.stderr[-6000:])
    )
    assert "PILOT OK" in proc.stdout
    (workdir / "evidence" / "pilot-stdout.log").write_text(proc.stdout, encoding="utf-8")
    return workdir / "evidence"


def _load(evid: Path, name: str) -> dict:
    return json.loads((evid / name).read_text(encoding="utf-8"))


def test_pilot_在仓库外运行且证据齐全(pilot_run: Path) -> None:
    assert pilot_run.is_dir()
    required = [
        "doctor.json", "init.json", "req-create.json", "req-update.json", "req-align.json",
        "req-confirm.json", "req-plan.json", "prd-create.json", "prd-update.json",
        "prd-validate.json", "prd-submit-review.json", "prd-approve.json",
        "dev-gate.json", "dev-start.json", "dev-scope-check.json",
        "dev-verify-unit.json", "dev-verify-integration.json",
        "dev-submit-acceptance.json", "acceptance-prepare.json",
        "acceptance-record-AC-RES-01-01.json", "acceptance-record-AC-RES-01-02.json",
        "acceptance-record-AC-RES-02-01.json", "acceptance-approve.json",
        "acceptance-closeout.json", "lint.json", "index-status.json",
        "snapshot-before.txt", "snapshot-after.txt",
        "state-history-req.md", "state-history-prd-metadata.yaml",
        "team.md", "constitution.md", "INDEX.md",
    ]
    missing = [name for name in required if not (pilot_run / name).is_file()]
    assert missing == [], f"缺少证据文件：{missing}"
    # 试点工作目录必须在仓库之外
    assert REPO_ROOT.resolve() not in pilot_run.resolve().parents


def test_pilot_每一步命令都成功(pilot_run: Path) -> None:
    for name in ("install-sim.log",):
        assert "INSTALL-SIM OK" in (pilot_run / name).read_text(encoding="utf-8")
    for name in ("doctor.json", "init.json", "req-create.json", "req-confirm.json",
                 "req-plan.json", "prd-create.json", "prd-validate.json",
                 "prd-submit-review.json", "prd-approve.json", "dev-gate.json",
                 "dev-start.json", "dev-submit-acceptance.json",
                 "acceptance-prepare.json", "acceptance-approve.json",
                 "acceptance-closeout.json", "lint.json", "index-status.json"):
        assert _load(pilot_run, name)["success"] is True, f"{name} 未成功"
    for ac in ("AC-RES-01-01", "AC-RES-01-02", "AC-RES-02-01"):
        assert _load(pilot_run, f"acceptance-record-{ac}.json")["success"] is True


def test_pilot_终态_REQ_DONE_PRD_ACCEPTED_CLOSEOUT_COMMITTED(pilot_run: Path) -> None:
    req = yaml.safe_load(
        (pilot_run / "state-history-req.md").read_text(encoding="utf-8").split("---\n", 2)[1]
    )
    assert req["status"] == "DONE"
    prd = yaml.safe_load((pilot_run / "state-history-prd-metadata.yaml").read_text(encoding="utf-8"))
    assert prd["status"] == "ACCEPTED"
    assert (prd.get("closeout") or {})["state"] == "COMMITTED"
    assert prd["closeout"]["main_req"] == req["id"]
    # 收尾必须把功能现状、backlog、feature-map、PRD 索引都写进事务操作
    ops = " ".join(prd["closeout"]["ops"])
    for expected in ("02-definition/modules", "03-requirements/backlog.md",
                     "02-definition/feature-map.md", "04-prds/index.md"):
        assert expected in ops, f"closeout 未覆盖 {expected}"


def test_pilot_lint_无_error且索引_ok(pilot_run: Path) -> None:
    lint = _load(pilot_run, "lint.json")
    counts = lint["data"]["counts"]
    assert counts["error"] == 0, counts
    index = _load(pilot_run, "index-status.json")["data"]
    assert index["ok"] is True, index
    assert len(index["files"]) == 3
    assert all(item["status"] == "ok" for item in index["files"])


def test_pilot_保留状态历史与人工确认记录(pilot_run: Path) -> None:
    req = yaml.safe_load(
        (pilot_run / "state-history-req.md").read_text(encoding="utf-8").split("---\n", 2)[1]
    )
    prd = yaml.safe_load((pilot_run / "state-history-prd-metadata.yaml").read_text(encoding="utf-8"))
    # 状态历史：至少 create / confirm / plan / closeout 各一条
    assert len(req["history"]) >= 4
    assert len(prd["history"]) >= 6
    # 人工确认记录：带 actor 与时间
    req_conf = req["confirmations"]
    assert len(req_conf) >= 2
    assert {c.get("by") for c in req_conf} >= {"carol", "alice"}
    prd_conf = prd["confirmations"]
    assert len(prd_conf) >= 3
    assert {c.get("by") for c in prd_conf} >= {"carol", "alice"}
    for record in (*req_conf, *prd_conf):
        assert record.get("at"), f"确认记录缺时间：{record}"
        assert record.get("role"), f"确认记录缺职责：{record}"
        assert record.get("object"), f"确认记录缺对象：{record}"
    # 批准人 = 需求负责人
    assert prd["approved_by"] == "alice"


def test_pilot_保留_PRD_hash_与验收绑定(pilot_run: Path) -> None:
    prd = yaml.safe_load((pilot_run / "state-history-prd-metadata.yaml").read_text(encoding="utf-8"))
    content_hash = prd["content_hash"]
    assert content_hash.startswith("sha256:")
    acceptance = prd["acceptance"]
    assert acceptance["prd_hash"] == content_hash
    assert acceptance["decision"] == "approved"
    assert acceptance["approved_by"] == "alice"
    assert acceptance["prd_version"] == prd["version"]
    # 逐条 AC 人工结论 + 证据
    checklist = {item["ac"]: item for item in acceptance["checklist"]}
    assert set(checklist) == {"AC-RES-01-01", "AC-RES-01-02", "AC-RES-02-01"}
    for ac, item in checklist.items():
        assert item["conclusion"] == "pass"
        assert item["evidence"], f"{ac} 缺证据"
        assert item["recorded_by"] and item["recorded_at"]


def test_pilot_保留原始验证输出(pilot_run: Path) -> None:
    """raw-*.log 必须是 runner 的真实输出，不是伪造的一句话。"""
    logs = sorted(pilot_run.parent.glob("proj/06-specs/*/*/verification/raw-*.log"))
    assert len(logs) >= 2, logs
    for log in logs:
        body = log.read_text(encoding="utf-8")
        assert "unittest" in body or "Ran 3 tests" in body, log.name
        assert "OK" in body
        assert "退出码" in body or "exit" in body.lower()


def test_pilot_隔离证明_零污染(pilot_run: Path) -> None:
    before = (pilot_run / "snapshot-before.txt").read_text(encoding="utf-8")
    after = (pilot_run / "snapshot-after.txt").read_text(encoding="utf-8")
    assert before == after, "试点改动了 ~/.claude/skills、~/.agents 或仓库目录"
    lines = before.strip().splitlines()
    assert len(lines) >= 50, "快照太小，隔离证明没有意义"
    labels = {line.split("\t")[0] for line in lines}
    assert any(".claude/skills" in label for label in labels) or any(
        ".agents" in label for label in labels
    )
    assert any(str(REPO_ROOT) == label.rstrip("/") or str(REPO_ROOT) in label for label in labels)


def test_pilot_不依赖源码仓库路径(pilot_run: Path) -> None:
    """安装目录里不得出现本仓库的绝对路径（FR-011）。"""
    proj = pilot_run.parent / "proj"
    marker = str(REPO_ROOT).encode("utf-8")
    offenders = [
        str(p.relative_to(proj))
        for p in (proj / ".claude" / "skills").rglob("*")
        if p.is_file() and p.suffix in {".md", ".py", ".yaml"} and marker in p.read_bytes()
    ]
    assert offenders == [], offenders


def test_pilot_断言清单无失败项(pilot_run: Path) -> None:
    text = (pilot_run / "assertions.txt").read_text(encoding="utf-8")
    assert "FAIL" not in text, text


# --------------------------------------------------------------------------
# Claude Code 真调用（不可行时如实 skip，绝不伪造）
# --------------------------------------------------------------------------
def _claude_bin() -> str | None:
    found = shutil.which("claude")
    if found:
        return found
    fallback = Path.home() / ".local" / "bin" / "claude"
    return str(fallback) if fallback.is_file() and os.access(fallback, os.X_OK) else None


@pytest.fixture(scope="module")
def claude_ok() -> str:
    binary = _claude_bin()
    if not binary:
        pytest.skip("未安装 claude CLI，跳过 Claude Code 真调用")
    probe = subprocess.run(
        [binary, "-p", "回答：1+1=?"], capture_output=True, text=True,
        encoding="utf-8", timeout=180, cwd=str(REPO_ROOT.parent),
    )
    if probe.returncode != 0 or "2" not in probe.stdout:
        pytest.skip(
            "claude CLI 存在但非交互调用不可用（可能未登录）："
            f"rc={probe.returncode} out={probe.stdout.strip()[:200]!r} err={probe.stderr.strip()[:200]!r}"
        )
    return binary


@pytest.mark.external
def test_pilot_claude_code_真调用(claude_ok: str, tmp_path: Path) -> None:
    env = dict(os.environ)
    env.pop("NEO_SKILL_HOME", None)
    env.pop("PYTHONPATH", None)
    env["PYTHONIOENCODING"] = "utf-8"
    env["CLAUDE_BIN"] = claude_ok
    workdir = tmp_path / "pilot-claude"
    workdir.mkdir()
    proc = subprocess.run(
        ["bash", str(PILOT), "--workdir", str(workdir), "--with-claude"],
        capture_output=True, text=True, encoding="utf-8", env=env, timeout=1200,
    )
    assert proc.returncode == 0, proc.stdout[-4000:] + proc.stderr[-2000:]
    log = (workdir / "evidence" / "claude-doctor.log").read_text(encoding="utf-8")
    # Claude Code 必须真的看到并调用了随包分发的 skill（报出 flow-core 路径与 check 列表）
    assert "flow-core" in log
    assert ".claude/skills/neo-skill/runtime/flow-core" in log.replace("\\", "/")
    for check in ("python", "node", "flow-core", "rules", "templates", "project", "locks"):
        assert check in log, f"Claude Code 输出里没有 {check}"
    assert "ok" in log.lower()
