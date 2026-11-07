"""ticket 03：Skill 文档一致性（黑盒，纯读文件，不起子进程）。

对应 SRS-FR-090（按职责拆分 Skill、入口可调用基础 Skill）与 ticket 03 的验收第 6 条
「通过文档结构检查或等价验证证明 Skill 名称、描述、触发条件和交接关系一致」。

为什么是「读文件断言」而不是导入解析器：这几份 Markdown 就是交付物本身，
它们的相互引用（catalog ↔ SKILL.md ↔ 路由/阶段文档）只能从文本层面证明一致。
本文件不启动任何子进程、不建夹具，几十毫秒跑完，可以放心进 `check.sh` 的每轮循环。

刻意**不**断言的东西：具体散文措辞、表格列宽、Skill 数量。数量与集合由
`tests/test_10_install.py`（安装发现）和 `tests/test_10_dist.py`（分发包）负责；
这里只证明「每个随包 Skill 都在唯一导航入口里被列出，且反过来也成立」。
"""

from __future__ import annotations

import re
from pathlib import Path

from conftest import REPO_ROOT

SKILLS_DIR = REPO_ROOT / "skills"
CATALOG = REPO_ROOT / "docs" / "agents" / "p0-workflow" / "07-skill-catalog.md"
STAGE_FLOW = REPO_ROOT / "docs" / "agents" / "p0-workflow" / "02-stage-flow.md"
CONTENTS = REPO_ROOT / "docs" / "agents" / "p0-workflow" / "Contents.md"
ROUTER = SKILLS_DIR / "neo-skill" / "SKILL.md"

# 外部示例名：可以是「示例」，但绝不能作为本版本必需能力被固化（spec 的 Out of Scope）
EXAMPLE_NAMES = ("implement", "grill-me", "tdd", "code-review")

SKILL_LINK_RE = re.compile(r"\]\(\.\./\.\./\.\./skills/(?P<name>[a-z0-9-]+)/SKILL\.md\)")


def read(path: Path) -> str:
    assert path.is_file(), f"缺少文件：{path.relative_to(REPO_ROOT)}"
    return path.read_text(encoding="utf-8")


def shipped_skills() -> list[str]:
    return sorted(
        path.parent.name
        for path in SKILLS_DIR.glob("*/SKILL.md")
    )


def catalog_rows() -> dict[str, str]:
    """catalog 表格里每个 Skill 一行：`| [`name`](../../../skills/name/SKILL.md) | … |`。"""
    rows: dict[str, str] = {}
    for line in read(CATALOG).splitlines():
        if not line.startswith("|"):
            continue
        match = SKILL_LINK_RE.search(line)
        if match:
            rows[match.group("name")] = line
    return rows


def frontmatter(path: Path) -> dict[str, str]:
    text = read(path).replace("\r\n", "\n")
    assert text.startswith("---\n"), f"{path.parent.name}/SKILL.md 没有 frontmatter"
    head, _, _ = text[4:].partition("\n---\n")
    fields: dict[str, str] = {}
    for line in head.splitlines():
        key, sep, value = line.partition(":")
        if sep:
            fields[key.strip()] = value.strip()
    return fields


# --------------------------------------------------------------------------
# Skill 集合与 catalog 双向一致（增删 Skill 六步的第 1 步不能被跳过）
# --------------------------------------------------------------------------
def test_每个随包_skill_都在_catalog_表里被列出():
    listed = set(catalog_rows())
    shipped = set(shipped_skills())
    assert shipped, "skills/ 下一个 SKILL.md 都没有"
    assert shipped - listed == set(), f"这些 Skill 没在 catalog 表里列出：{sorted(shipped - listed)}"


def test_catalog_表里每个_skill_都有真实目录():
    shipped = set(shipped_skills())
    stale = set(catalog_rows()) - shipped
    assert stale == set(), f"catalog 指向了不存在的 Skill：{sorted(stale)}"


def test_catalog_每个_skill_只有一行():
    lines = [line for line in read(CATALOG).splitlines()
             if line.startswith("|") and SKILL_LINK_RE.search(line)]
    names = [SKILL_LINK_RE.search(line).group("name") for line in lines]  # type: ignore[union-attr]
    duplicates = sorted({name for name in names if names.count(name) > 1})
    assert duplicates == [], f"catalog 表里重复出现的 Skill：{duplicates}"


# --------------------------------------------------------------------------
# frontmatter：skills.sh 的识别条件 + name 必须与目录名一致
# --------------------------------------------------------------------------
def test_每个_skill_的_name_与_description_都非空且_name_等于目录名():
    problems: list[str] = []
    for name in shipped_skills():
        path = SKILLS_DIR / name / "SKILL.md"
        fields = frontmatter(path)
        if not fields.get("name", "").strip():
            problems.append(f"{name}: name 为空")
        elif fields["name"].strip() != name:
            problems.append(f"{name}: name={fields['name']!r} 与目录名不一致")
        if not fields.get("description", "").strip():
            problems.append(f"{name}: description 为空")
    assert problems == [], "frontmatter 不合规：" + "；".join(problems)


def test_每个_description_都说明何时触发():
    """触发条件必须写在 description 里（catalog 只做导航，不做权威触发词）。"""
    problems = [
        name for name in shipped_skills()
        if "当" not in frontmatter(SKILLS_DIR / name / "SKILL.md").get("description", "")
        and "使用" not in frontmatter(SKILLS_DIR / name / "SKILL.md").get("description", "")
    ]
    assert problems == [], f"这些 description 没有写触发时机：{problems}"


# --------------------------------------------------------------------------
# 路由同步：neo-skill 必须能指向新命令组与新 Skill
# --------------------------------------------------------------------------
def test_路由层列出_upstream_与_progress_命令组并指向进度_Skill():
    text = read(ROUTER)
    for group in ("upstream", "progress"):
        assert f"`{group}`" in text, f"neo-skill/SKILL.md 的命令索引里没有 `{group}` 命令组"
    new_skills = [name for name in shipped_skills() if "upstream" in name]
    assert new_skills, "没有名字里含 upstream 的进度查询 Skill"
    for name in new_skills:
        assert f"`{name}`" in text, f"neo-skill/SKILL.md 没有指向 `{name}`"


def test_阶段文档把进度查询写成只读分支():
    text = read(STAGE_FLOW)
    assert "上游进度查询" in text, "02-stage-flow.md 没有「上游进度查询」小节"
    section = text.split("上游进度查询", 1)[1][:2000]
    assert "只读" in section, "进度查询小节没有说明它是只读的"
    assert "不" in section and "状态" in section, "进度查询小节没有说明它不改状态"
    assert "upstream-progress" in text, "02-stage-flow.md 没有指向进度查询 Skill"


def test_阅读索引能查到进度查询():
    text = read(CONTENTS)
    assert "upstream-progress" in text, "Contents.md 的「按任务阅读」表没有进度查询入口"


# --------------------------------------------------------------------------
# 分层：入口/编排 vs 可复用基础
# --------------------------------------------------------------------------
def test_catalog_写出入口层与基础层并用一句话定边界():
    text = read(CATALOG)
    assert "入口" in text and "基础" in text, "catalog 没有区分入口类与基础类 Skill"
    # 边界的可执行表述：入口可调用基础，基础不得隐式启动入口工作流
    assert "不得隐式" in text or "不得隐式启动" in text, "catalog 没有写明基础 Skill 不得隐式启动入口工作流"
    lines = [line for line in text.splitlines() if "不得隐式" in line]
    assert any("入口" in line for line in lines), "「不得隐式」没有和「入口」写在同一句里"


# --------------------------------------------------------------------------
# 示例名不得被固化成 v0.1.1 必需能力
# --------------------------------------------------------------------------
def paragraph_around(text: str, needle: str) -> str:
    """包含 `needle` 的那个 Markdown 段落（空行分段）：免责声明可以折成多行。"""
    lines = text.splitlines()
    hits = [index for index, line in enumerate(lines) if needle in line]
    assert hits, f"没有找到包含 {needle} 的段落"
    start = hits[0]
    while start > 0 and lines[start - 1].strip():
        start -= 1
    stop = hits[-1]
    while stop + 1 < len(lines) and lines[stop + 1].strip():
        stop += 1
    return " ".join(lines[start:stop + 1])


def test_免责声明把示例名一次性说清():
    disclaimer = paragraph_around(read(CATALOG), "grill-me")
    for name in EXAMPLE_NAMES:
        assert name in disclaimer, f"免责声明没有列出示例名 {name}"
    assert "不是" in disclaimer, "免责声明没有说明这些名字不是本版本的能力"
    assert "必需" in disclaimer, "免责声明没有说明这些名字不是必需能力"


def test_没有把示例名做成随包_skill():
    shipped = set(shipped_skills())
    offenders = sorted(shipped.intersection(EXAMPLE_NAMES))
    assert offenders == [], f"示例名被做成了随包 Skill：{offenders}"


def test_catalog_没有把示例名写成必需能力():
    """反向断言：任何一行只要同时出现「必需」与示例名，就是固化（这些行不该存在）。"""
    offenders = [
        line for line in read(CATALOG).splitlines()
        if "必需" in line and any(name in line for name in EXAMPLE_NAMES)
    ]
    # 免责声明那一行是唯一允许同时出现的：它说「不是必需能力」
    offenders = [line for line in offenders if "不是" not in line]
    assert offenders == [], f"catalog 把示例名写成了必需能力：{offenders}"


# --------------------------------------------------------------------------
# 新 Skill 的边界：只读绑定、不读本地正文、绝不写回
# --------------------------------------------------------------------------
def test_进度_Skill_写明只读边界与本地提醒的独立地位():
    name = next((item for item in shipped_skills() if "upstream" in item), None)
    assert name, "没有进度查询 Skill"
    text = read(SKILLS_DIR / name / "SKILL.md")
    # 三条硬边界（SRS-FR-093）必须在 Skill 散文里可检索到
    assert "本地提醒" in text, "没有写「本地提醒」这条独立通道"
    assert "不读" in text and "代码" in text, "没有写「不读本地代码」"
    assert "写回" in text, "没有写「不自动写回文档」"
    assert "upstream bind" in text, "没有给出写入绑定的确切命令"
    assert "progress report" in text, "没有给出查询的确切命令"


# --------------------------------------------------------------------------
# 票的验收项必须带可观察断言（retro：v0.1.1 的 REQUIRED_SKILLS 振荡就出在这）
# --------------------------------------------------------------------------
# 「不能漏掉 Skill」这类验收项，用两份名字清单都能自称满足；只有点明**可观察结果**
# （哪个命令、什么退出码、哪个错误码、哪个文件状态）才排除掉另一种设计。
# 这里做机械检查：每条验收 bullet 至少含一处可观察物。
OBSERVABLE_RE = re.compile(
    r"`[^`]+`"                 # 反引号里的命令/路径/错误码
    r"|[A-Z][A-Z0-9_]{3,}"     # 全大写错误码/常量（MISSING-REQUIRED、NEO_SKILL_HOME）
    r"|退出码|非零|退出 \d"     # 退出码表述
    r"|→"                       # 因果箭头（删掉 X → 非零退出）
    r"|\d+ ?(个|条|次|秒|项)"    # 可数的断言（7 个 Skill、10 秒）
)
BULLET_RE = re.compile(r"^\s*- \[[ x]\] ")
SPEC_GLOB = ".scratch/*/issues/*.md"


def is_closed(rel_path: str) -> bool:
    """票是否已 resolved（只看文件开头的状态行，不读整份票）。"""
    return "**Status:** resolved" in read(REPO_ROOT / rel_path).split("\n## ", 1)[0]


def acceptance_bullets() -> list[tuple[str, int, str]]:
    """(相对路径, 行号, 正文) —— 全部 spec 目录下的验收勾选项。"""
    found: list[tuple[str, int, str]] = []
    for path in sorted(REPO_ROOT.glob(SPEC_GLOB)):
        for number, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
            if BULLET_RE.match(line):
                found.append((str(path.relative_to(REPO_ROOT)), number, line))
    assert found, "一个验收 bullet 都没找到（票的格式变了？）"
    return found


def test_未关闭的票每条验收项都写明了可观察断言():
    """只管**还没关闭**的票。

    已 resolved 的票是历史记录，改它们的勾选项等于改历史（tracker 的补充走 `## Comments`，
    不改原 bullet）。这条检查保护的是**下一次写票**——v0.1.1 的 REQUIRED_SKILLS
    在三个 commit 里 add → delete → re-add，根因就是那条验收只写了「不能静默漏掉 Skill」，
    而两份相反的设计都能自称满足。
    """
    scope = [item for item in acceptance_bullets() if not is_closed(item[0])]
    offenders = [
        f"{path}:{line} {text.strip()[:80]}"
        for path, line, text in scope
        if not OBSERVABLE_RE.search(text)
    ]
    assert offenders == [], (
        "这些验收项只有「应该怎样」没有「怎么看出来」（补一个命令/退出码/错误码/路径）：\n"
        + "\n".join(offenders)
    )


def test_可观察断言检查能抓住模糊的验收项(tmp_path: Path) -> None:
    """合成一张票，证明上一条检查真的会红（否则它只是个永远为真的装饰）。"""
    vague = "- [ ] 系统不能静默漏掉 Skill。"
    sharp = "- [ ] 删掉一个 Skill 目录 → install-sim.sh 退出码非零，并报 MISSING-REQUIRED。"
    assert not OBSERVABLE_RE.search(vague), "模糊的验收项逃过了检查，检查没有作用"
    assert OBSERVABLE_RE.search(sharp), "写得清楚的验收项被误判"


# --------------------------------------------------------------------------
# 写入边界只允许一处事实来源（retro：fix.md 与 05-next-version.md 各写一份，漂成假话）
# --------------------------------------------------------------------------
BOUNDARIES = REPO_ROOT / "docs" / "agents" / "write-boundaries.md"
FIX_AGENT = REPO_ROOT / ".agents" / "agents" / "fix.md"
NEXT_VERSION = REPO_ROOT / "docs" / "agents" / "p0-workflow" / "05-next-version.md"


def test_写入边界文档存在且列出关键路径():
    text = read(BOUNDARIES)
    for marker in ("~/.agents/skills", "skills/**", ".specify/", ".scratch/"):
        assert marker in text, f"写入边界没提到 {marker}"
    assert "授权" in text, "写入边界没写「改规则需要授权」"


def test_写入边界的消费者都只留指针不复制规则():
    """fix agent 与交付文档都必须指向 write-boundaries.md。

    复制一份规则就必然漂移（本仓库真实发生过：一处说 skills/** 一律不动，
    另一处说 runtime 就在这条路径下）。所以这里断言两件事：
    两边都出现指针；且都不再出现**旧版**的具体断言（`It restricts `skills/**``、
    `本地脚本可能 SKIP`），因为它们已经过期。
    """
    fix_text = read(FIX_AGENT)
    assert "docs/agents/write-boundaries.md" in fix_text, "fix.md 没有指向写入边界"
    assert "single source of truth" in fix_text, "fix.md 没声明写入边界是唯一事实来源"

    next_text = read(NEXT_VERSION)
    assert "write-boundaries.md" in next_text, "05-next-version 没有指向写入边界"

    stale = [
        ("fix.md", fix_text, "installer-owned inputs"),
        ("05-next-version", next_text, "本地脚本可能 SKIP"),
    ]
    offenders = [name for name, text, needle in stale if needle in text]
    assert offenders == [], f"还留着过期的规则副本：{offenders}"


def test_写入边界指针检查会抓住复制的规则(tmp_path: Path) -> None:
    """合成一份复制了规则的 agent 文件，证明上一条断言真的会红。"""
    copied = "## Hard limits\n\n- **Never touch skills.** `skills/**` are installer-owned inputs.\n"
    assert "installer-owned inputs" in copied
    assert "docs/agents/write-boundaries.md" not in copied
    # 两条断言各自都会失败，所以上一条测试不是装饰
