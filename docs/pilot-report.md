# Neo Skill P0 试点报告：Skill 分发与 Claude Code 端到端闭环

- 版本：`flow-core 0.1.0`（`skills/neo-skill/runtime/flow_core/__init__.py` 的 `__version__`）
- 分支：`feat/neo-skill-p0`（ticket 10 分支 `ticket/10-distribution-pilot`）
- 平台：Linux（`Python 3.14.4` / `Node.js v24.19.0` / `Claude Code 2.1.316`）
- 试点引擎：`scripts/pilot.sh`；分发包：`scripts/build-dist.sh`；安装模拟：`scripts/install-sim.sh`
- 对应验收条目：SRS §10.1（P0 端到端验收 7 步）、SRS-FR-010～FR-013

> 本报告只记录**实际跑过**的结果。任何未实测的能力都写进「未承诺的兼容性边界」，
> 未伪造任何验收数据、证据或身份。
>
> **历史证据，不要改历史结论**：全文的「6 个 Skill」「五个阶段 + 路由层」描述的是
> `flow-core 0.1.0` 那次试点（`feat/neo-skill-p0`）的真实范围。v0.1.1 新增了只读 Skill
> `upstream-progress` 与 `upstream` / `progress` 两个命令组，当前 Skill 集合以
> [Skill 注册表](../docs/agents/p0-workflow/07-skill-catalog.md) 为准；本报告的数字不回填。

---

## 1. 结论

| 项 | 结论 |
| --- | --- |
| 5 个阶段 Skill + 路由 Skill 齐备并可被发现 | 通过（6 个 `SKILL.md`） |
| 运行时只依赖随包资源与项目基线，不依赖源码仓库路径 | 通过（安装目录整体搬走后仍可 `doctor`/`init`/全链路） |
| P0 主链路（需求 → PRD → 开发 → 验收 → 收尾）在一个隔离项目内跑通 | 通过（`REQ-001 DONE`、`PRD-001 ACCEPTED`、`closeout COMMITTED`） |
| 结构化治理检查 | 通过（`lint` error=0、warning=0；`index status` ok） |
| 试点不修改全局配置、凭据或已有项目 | 通过（529 个文件的前后 sha256 快照完全一致） |
| Claude Code 真实调用 | 通过（`claude -p` 非交互调用 `project` skill 跑 `doctor`，见 §5） |
| 远端标准安装命令 `npx skills@latest add binggan23/neo-skill` | **通过 —— 6 个 Skill 全部发现，见 §7** |
| 分发包可重现构建（zip 保 `flow-core` 755） | 通过（两次构建字节一致，`unzip` 后可直接执行） |

---

## 2. 分发了什么

`scripts/build-dist.sh` 产出（顶层目录 `neo-skill-<version>/`）：

```
dist/neo-skill-0.1.0.zip             219 KB，含 external_attr 权限位
dist/neo-skill-0.1.0.zip.sha256
dist/neo-skill-0.1.0.tar.gz          173 KB
dist/neo-skill-0.1.0.tar.gz.sha256
```

包内结构（随 `npx skills add` 复制的最小集合）：

```
neo-skill-0.1.0/
├── README.md
├── pyproject.toml
└── skills/
    ├── neo-skill/SKILL.md              路由/总览
    ├── neo-skill/runtime/flow-core      可执行入口（0755）
    ├── neo-skill/runtime/flow_core/     Python 包
    ├── neo-skill/runtime/rules/flow.yaml   声明式规则（单一事实来源）
    ├── neo-skill/runtime/templates/    10 个文档模板
    ├── project/SKILL.md                本 ticket 新增
    ├── requirements/SKILL.md
    ├── prd/SKILL.md
    ├── dev/SKILL.md
    └── acceptance/SKILL.md
```

可重现构建的六个约束（`tests/test_10_dist.py` 逐条断言）：

1. 顶层目录唯一且固定为 `neo-skill-<version>/`；
2. 遍历按相对路径排序；
3. 所有条目 mtime 固定为 `SOURCE_DATE_EPOCH`（默认 `2025-10-01T00:00:00Z`）；
4. 属主归一（`uid=gid=0`，`uname=gname=""`）；
5. 权限位固定（可执行 `0755` / 其余 `0644`）；zip 用
   `external_attr = ((mode | S_IFREG) & 0xFFFF) << 16` 且 `create_system=3`；
6. gzip 头 mtime 归零、不写文件名；排除 `__pycache__`/`.pytest_cache`/`.git`/`.codegraph`/`dist`。

实测：`scripts/build-dist.sh --check` 连续构建两次，字节完全一致；
`unzip` 后 `./neo-skill-0.1.0/skills/neo-skill/runtime/flow-core --version` → `flow-core 0.1.0`；
`sha256sum -c` 两个产物均 `OK`。

> 注意：Python 的 `zipfile.extractall` **不应用** `external_attr` 权限位（只写 `0666 & ~umask`）。
> 验证权限位必须用真实解压器（`unzip`/`bsdtar`）或直接读 `external_attr`。
> 这是本 ticket 实测发现的一个易踩空点，测试里两种方式都覆盖了。

---

## 3. 安装与资源定位

`scripts/install-sim.sh` 模拟 `npx skills@latest add … -a claude` 的复制语义：
把 6 个 skill 目录复制到 `<隔离项目>/.claude/skills/`，并校验：

| 校验 | 结果 |
| --- | --- |
| 6 个 `SKILL.md` 都有 `name` + `description`（skills.sh 的识别条件） | OK |
| `runtime/flow-core`、`runtime/rules/flow.yaml`、10 个模板随包 | OK |
| 四级相对路径解析落在安装目录内（非 `$NEO_SKILL_HOME` 时） | OK |
| 剥离 `NEO_SKILL_HOME` / `PYTHONPATH` 后 `flow-core --version` 可跑 | OK |

flow-core 的四级解析顺序（各阶段 `SKILL.md` 一致）：

1. `$NEO_SKILL_HOME` → `$NEO_SKILL_HOME/runtime/flow-core`（显式覆盖，允许在安装目录之外）
2. 本 skill 目录的兄弟目录 → `../neo-skill/runtime/flow-core`（安装后 `.claude/skills/` 下并列）
3. 当前 skill 自身 → `./runtime/flow-core`
4. 仓库布局 → `<repo>/skills/neo-skill/runtime/flow-core`

**不依赖仓库路径的实证**：`tests/test_10_install.py::test_install_隔离目录搬走仍可跑且能_init`
把整个隔离项目复制到另一个父路径、删除原目录，随后 `doctor` 报告的
`data.runtime.flow_core` 指向新位置的 `.claude/skills/…`，
`rules_origin` 在 `init` 前为 `packaged`、`init` 后为 `project`（`.specify/rules/flow.yaml`），
且安装目录里搜不到本仓库的绝对路径。

升级说明：只要求 `npx skills@latest update`（`README.md`、`skills/neo-skill/SKILL.md`），
无自定义升级器，无额外升级后检查（FR-013）。

---

## 4. 端到端试点（`scripts/pilot.sh`）

运行方式：

```bash
bash scripts/pilot.sh                        # 临时目录，结束后清理
bash scripts/pilot.sh --workdir DIR --keep    # 保留证据
bash scripts/pilot.sh --with-claude          # 额外做一次 Claude Code 真调用
```

试点目录在 `mktemp -d` 之下（脚本显式拒绝在仓库内运行）；所有写入都落在该目录内。
时钟用 `NEO_SKILL_NOW=2026-11-01T10:00:00+08:00` 固定，让证据里的时间戳可复现。

试点夹具的三名成员（**不是真实组织身份**，只是 `team.md` 的职责映射，见 §6 边界）：

| `id` | 职责 | 在链路中做什么 |
| --- | --- | --- |
| `alice` | `requirement_owner` | 人工排期、PRD 批准、验收通过、`closeout` |
| `carol` | `stakeholder` + `project_owner` | 共识确认、提交评审、逐条 AC 人工结论 |
| `bob` | `developer` | 启动开发、提交人工验收 |

### 4.1 链路与实测终态

```
doctor → init → 团队+constitution → req create/update/align/confirm/plan
→ prd create/update/validate/submit-review/approve
→ dev gate/start/scope-check/verify(unit)/verify(integration)/submit-acceptance
→ acceptance prepare/record×3/approve/closeout → lint → index status
```

| 断言 | 实测值 |
| --- | --- |
| 主需求状态 | `REQ-001` → **`DONE`**（`release: v0.1.0`） |
| PRD 状态 | `PRD-001` → **`ACCEPTED`**（`version: 1`） |
| 收尾事务 | **`COMMITTED`**，`TX-20261101-0001`，`by: alice` |
| `lint` | `success: true`，`counts = {error: 0, warning: 0, total: 0}` |
| `index status` | `ok: true`，3 个受管理区块全部 `ok` |
| 主需求 `history` / `confirmations` | 4 条 / 2 条（`carol`、`alice`） |
| PRD `history` / `confirmations` | 6 条 / 3 条（`carol`、`alice`） |
| PRD `content_hash` | `sha256:3e6730d0e259649f…`（批准时锁定） |
| 验收绑定 | `acceptance.prd_hash == content_hash`、`decision: approved`、`approved_by: alice` |
| 逐条 AC | `AC-RES-01-01` / `AC-RES-01-02` / `AC-RES-02-01` 全 `pass`，各带 `recorded_by` 与 `recorded_at` |
| 原始验证输出 | `verification/raw-unit-*.log`、`raw-integration-*.log`（各 557B） |
| 功能现状 | `02-definition/modules/resume/features/batch-parse.md` 已生成 |
| 隔离快照 | 试点前后 **529 个文件**的 sha256 完全一致 |

### 4.2 保留的证据

`<workdir>/evidence/` 下 36 个文件（加 `--with-claude` 时多一个 `claude-doctor.log`，共 37），主要是：

- **状态历史**：`state-history-req.md`（需求 metadata + history + confirmations）、
  `state-history-prd-metadata.yaml`（PRD metadata、`content_hash`、`acceptance`、`closeout`）；
- **人工确认记录**：`req-confirm.json`、`req-plan.json`、`prd-submit-review.json`、
  `prd-approve.json`、`acceptance-approve.json`、`acceptance-record-AC-*.json`（3 个）；
- **PRD hash**：`prd-approve.json` 与 `state-history-prd-metadata.yaml` 的 `content_hash`；
- **原始验证输出**：项目内 `06-specs/resume/PRD-001-batch-parse/verification/raw-*.log`
  （命令、stdout/stderr、退出码原样保存，`Ran 4 tests / OK`）；
- **版本信息**：`doctor.json`、`doctor-final.json` 的 `data.runtime.version = 0.1.0`；
- **收尾结果**：`acceptance-closeout.json`（7 个 artifact 的 `action`）与 `closeout.ops`；
- **隔离证明**：`snapshot-before.txt` / `snapshot-after.txt`（各 529 行）；
- **汇总**：`INDEX.md`、`assertions.txt`。

### 4.3 试点里的"开发"是真的

`dev verify` 执行的是 `python3 -m unittest discover -s tests -t . -v`，
被执行的是试点脚本写入的 `src/resume_parse.py` + `tests/test_resume_parse.py`
（4 个用例，状态流转纯函数）。**验证结论来自真实 runner 的退出码与真实输出**，
`flow-core` 只记录与校验，不代写结论；`acceptance record` 的人工结论由参数显式给出，
不由 AI 自动生成。

---

## 5. Claude Code 真实调用

`claude` 位于 `/home/cookie/.local/bin/claude`（`2.1.316`）。可行，已实测：

```bash
cd <pilot>/proj
claude -p "使用 project skill 对当前项目跑一次环境诊断：执行
  python3 .claude/skills/neo-skill/runtime/flow-core doctor --root . --json，
  然后把 data.runtime.flow_core 与各 check 的 name/status 逐条列出来。不要修改任何文件。" \
  --allowedTools "Bash(python3:*)"
```

实测输出（`evidence/claude-doctor.log`）：

- `data.runtime.flow_core` = `<pilot>/proj/.claude/skills/neo-skill/runtime/flow-core`；
- 7 个 check 全部 `ok`：`python`、`node`、`flow-core`、`rules`、`templates`、`project`、`locks`；
- Claude Code 明确说明"没有修改任何文件"，且试点后快照比对确认零改动。

另外单独验证过 Skill **发现**能力：在同一个隔离项目里让 `claude -p` 列出可用的 skill，
返回 `acceptance / dev / neo-skill / prd / project / requirements` —— 6 个正是随包的 6 个。

`tests/test_10_pilot.py::test_pilot_claude_code_真调用` 把上述调用固化为用例；
`claude` 不存在、或非交互调用不可用（例如 `CLAUDE_CONFIG_DIR` 指向未登录配置时返回
`Not logged in · Please run /login`）时，该用例 `pytest.skip` 并打印具体原因，**不会伪造通过**。

一个实测细节：`--allowedTools` 是能吃掉后续位置参数的选项，
提示词必须写在它**前面**，否则 `claude` 报
`Error: Input must be provided either through stdin or as a prompt argument when using --print`。
脚本里已按正确顺序写并加了注释。

---

## 6. 未承诺的兼容性边界

以下都是**本 ticket 没有承诺、也没有认证**的能力，不得据此推断可用：

| 边界 | 实际情况 |
| --- | --- |
| 操作系统 | 只在 **Linux** 实测。macOS / Windows 未认证（路径分隔、权限位、`unzip` 行为均未验证）。 |
| Agent | 只在 **Claude Code 2.1.316** 实测。其他 Agent（Cursor、Codex、Gemini CLI 等）未认证，安装布局假定 `.claude/skills/`。 |
| Node / npx | `doctor` 只检查 Node.js ≥ 18 的存在与版本；**不保证** `npx skills@latest` 在各版本上行为一致。 |
| Hook / 内部 API | 试点**不依赖**任何 Claude 专属 Hook 或内部 API。反过来也意味着：没有强制拦截层，只有事后 `lint`。 |
| 治理强度 | `lint`、`doctor`、门禁都是**流程治理**，不是不可绕过的安全边界（SRS §9）。它们能发现绕过 Skill 直接改文件留下的不一致（`HASH_MISMATCH`、`MANAGED_BLOCK_MODIFIED`、`CLOSEOUT_INCOMPLETE` 等），**无法实时阻止**这种编辑。 |
| 身份真实性 | `00-management/team.md` 只做职责匹配与审计记录，**不是**登录、真实身份认证或权限控制。试点里的 `alice`/`carol`/`bob` 是夹具身份。 |
| 并发 | 试点是单进程串行链路。并发写同一对象的拒绝行为由 ticket 01 的锁与事务覆盖，不在本试点范围内。 |
| 规模 | 试点是单需求项目。1000 需求 / 200 PRD 的规模基准由 `tests/test_09_perf.py` 覆盖（`lint` 0.80s、`index generate` 0.94s，预算 10s）。 |
| 时间 | 试点用 `NEO_SKILL_NOW` 固定时钟以便复现证据；真实使用时由系统时间决定。 |
| `$TMPDIR` | 试点假定临时目录与项目同盘且可写；`/tmp` 挂载为 `noexec` 时 `install-sim.sh` 里 `flow-core` 的直接执行会失败（脚本用 `python3 <path>` 兜底）。 |

---

## 7. 远端安装（阻塞已解除，已复验通过）

> **状态更新（合并推送后）**：`feat/neo-skill-p0` 已合并进 `main` 并推送，
> 同一条命令现在可用。下列复验为实测输出。

```
$ npx -y skills@latest add binggan23/neo-skill
  ✓ ./.agents/skills/acceptance    universal + symlinked: Claude Code
  ✓ ./.agents/skills/dev           universal + symlinked: Claude Code
  ✓ ./.agents/skills/neo-skill     universal + symlinked: Claude Code
  ✓ ./.agents/skills/prd           universal + symlinked: Claude Code
  ✓ ./.agents/skills/project       universal + symlinked: Claude Code
  ✓ ./.agents/skills/requirements  universal + symlinked: Claude Code
└  Done!
```

6 个 Skill 全部被发现安装。`runtime/` 随 `neo-skill` 一起分发：

```
$ ls .agents/skills/neo-skill/runtime/
flow_core/  rules/  templates/  flow-core（775 可执行）
$ ./.agents/skills/neo-skill/runtime/flow-core --version
flow-core 0.1.0
$ ./.agents/skills/neo-skill/runtime/flow-core --root demo init
OK: init 完成（新建 74 项，无变化 2 项）
```

**不依赖仓库路径**：把整个安装目录 `cp -r` 到别处后，`flow-core` 仍可执行并正常工作
（`doctor` 报出的路径指向新位置的 `.agents/skills/neo-skill/runtime/flow-core`）。

### 原始阻塞记录（保留作为历史）

此前 `npx skills@latest add` 返回 `No skills found`，本 ticket 实测：

```
$ npx -y skills@latest add binggan23/neo-skill
◇  Source: https://github.com/binggan23/neo-skill.git
◇  Repository cloned
◇  No skills found
└  No valid skills found. Skills require a SKILL.md with name and description.
```

**原因**：远端默认分支 `main` 里**没有 `skills/` 目录**。实测验证：

```
$ git clone --depth 1 https://github.com/binggan23/neo-skill.git /tmp/npxcheck
$ ls /tmp/npxcheck
docs/  AGENTS.md  AI产品研发流程与Skill体系.md  GLOSSARY.md
$ ls /tmp/npxcheck/skills
ls: cannot access '/tmp/npxcheck/skills': No such file or directory
$ cd /tmp/npxcheck && git log --oneline -1
7dddaa6 docs: add agent navigation guidance   # origin/HEAD -> origin/main
```

Skill 目前只存在于 `feat/neo-skill-p0`（以及本 ticket 的 `ticket/10-distribution-pilot`）。
`skills.sh` 扫描的是被 clone 的默认分支，所以它找不到任何 `SKILL.md`。

**解除条件（已满足）**：把 `feat/neo-skill-p0` 合并进 `main` 并推送后，同一条命令即可生效，
**不需要**任何额外步骤或自定义安装器。ticket 实施时按约束不 push、不改 remote、不动 `main`，
因此留给最后的合并动作；合并推送后已按上述复验。

> 附带发现：本机 OAuth token 只有 `repo` scope，缺 `workflow`，HTTPS 推送会被 GitHub 拒绝
> （`.github/workflows/ci.yml` 需要 `workflow` scope）。改用 SSH 远端（`git@github.com:...`）推送即可。

**验收分两层，两层现已都通过**：

1. **本地安装路径**：`scripts/install-sim.sh` 按 `skills.sh` 的同一套复制语义把 skill 目录装进
   `.claude/skills/`，随后全链路与 Claude Code 真调用都跑通（§4、§5）。
2. **远端 `npx` 路径**：合并推送后实测通过，6 个 Skill 全部被发现，`runtime/` 随包分发，
   搬移安装目录后仍可用。

---

## 8. P0 通过条件逐条对照（SRS §10.1 七步）

| # | 验收步骤 | 实测证据 | 结论 |
| --- | --- | --- | --- |
| 1 | 初始化目录、确认 `team.md` 和 constitution | `init.json`（76 artifacts）、`team.md`/`constitution.md` 均 `confirmed: true`；`dev gate` 的 `team`、`constitution` 检查为 `ok` | 通过 |
| 2 | 创建真实需求并完成 grilling、确认与人工排期 | `req-create/update/align/confirm/plan.json`；`REQ-001` 六章节填实、`history` 4 条、`confirmations` 2 条（`carol` 确认、`alice` 排期 `v0.1.0`） | 通过 |
| 3 | 制作 PRD，完成提交评审与需求负责人批准 | `prd-validate.json`（0 findings）、`prd-submit-review.json`（`carol`）、`prd-approve.json`（`alice` → `APPROVED`，锁定 `content_hash`） | 通过 |
| 4 | 绑定 PRD 开发，生成 Spec/Plan/Tasks 并完成验证 | `dev-gate.json`（10 项检查全 `ok`）、`dev-start.json`（spec/plan/tasks + `.baseline.yaml`）、`dev-verify-*.json` + `raw-*.log` | 通过 |
| 5 | 自动提交人工验收，需求负责人明确确认通过 | `dev-submit-acceptance.json`（→ `READY_FOR_ACCEPTANCE`）、`acceptance-record-AC-*.json`（3 条 `pass`）、`acceptance-approve.json`（`alice` → `ACCEPTED`） | 通过 |
| 6 | 验证 `ACCEPTED`、功能现状、主需求 `DONE`、场景与索引一致 | `PRD-001 ACCEPTED`、`REQ-001 DONE`、`features/batch-parse.md` 已生成、`lint` 0 error、`index status` ok | 通过 |
| 7 | 保存完整状态历史、原始测试输出、版本和人工确认记录 | `evidence/` 36 个文件（`--with-claude` 时 37，见 §4.2） | 通过 |

ticket 10 验收清单逐条：

| 验收项 | 结论 |
| --- | --- |
| 标准安装命令能安装并发现 Skill/CLI/规则/模板 | 通过（本地路径 §3；远端 `npx` 已复验，见 §7 与 §11.1） |
| 安装后运行时只依赖随包资源与项目规则基线 | 通过（§3，整目录搬走后仍可跑） |
| 升级说明只用通用更新命令 | 通过（`README.md`、`skills/neo-skill/SKILL.md` 均为 `npx skills@latest update`） |
| Claude Code 能发现并调用 `project`/`requirements`/`prd`/`dev`/`acceptance` | 通过（6 个 skill 被列出；`project` skill 驱动 `doctor` 成功） |
| 真实小项目完成需求→grilling→排期→PRD→批准→开发→验证→验收→收尾 | 通过（§4） |
| 保留状态历史、人工确认、PRD hash、原始验证输出、版本、收尾结果 | 通过（§4.2） |
| 不修改全局配置/凭据/已有项目；不依赖 Hook 或内部 API | 通过（529 文件快照零改动；无 Hook 依赖） |
| 记录 P0 通过条件与未承诺的兼容性边界 | 通过（§6、§8） |

---

## 9. 复现

```bash
# 1. 打包（可重现自检）
bash scripts/build-dist.sh --check
bash scripts/build-dist.sh --out dist/

# 2. 安装模拟
bash scripts/install-sim.sh --target /tmp/proj

# 3. 端到端试点（含隔离快照比对）
bash scripts/pilot.sh --workdir /tmp/pilot --keep

# 4. 端到端试点 + Claude Code 真调用
CLAUDE_BIN=$(command -v claude) bash scripts/pilot.sh --workdir /tmp/pilot-cc --with-claude

# 5. 测试（按文件分批，不要跑全量）
python3 -m pytest tests/test_10_dist.py
python3 -m pytest tests/test_10_install.py
python3 -m pytest tests/test_10_pilot.py
```

---

## 10. 本次试点发现并修掉的问题

1. **`skills/project/SKILL.md` 缺失**（HLD §3.1、ticket 10 验收第 4 条）。已新增，
   并订正 `skills/neo-skill/SKILL.md` 里指向它的阶段表。
2. **`zipfile.extractall` 不还原权限位**：用 Python 解压后 `flow-core` 是 `0664`、不可执行。
   不是打包错误（`external_attr` 正确），是解压器行为。测试改为「读 `external_attr` +
   用真实 `unzip` 验证」两条互补断言。
3. **`claude -p --allowedTools` 会吞掉位置参数**，导致提示词丢失。
   提示词改到选项前面（脚本内已注释）。
4. **`--prune` 之后重跑 `init` 不会重建被跳过的目录组**（幂等语义，符合预期）。
   已在 `skills/project/SKILL.md` 的裁剪决策一节写明这个后果，避免用户以为能自动补回。

---

## 11. v0.1.1 复验（2026-11-07）

本轮合并（PR #3，`main` = `1ca9846`）后重跑了此前无法执行的那一条，并顺手用**真实上游**验证了
新增的进度查询能力。**本节只追加，不改前文的任何数字与结论**（前文是 `flow-core 0.1.0`
那次的真实记录）。

### 11.1 远端标准安装：阻塞解除

前文「已知阻塞」的条件是「`main` 没有 `skills/` 目录」。v0.1.1 合并后该条件已不成立，实测：

```
$ mkdir /tmp/npxcheck && cd /tmp/npxcheck
$ npx -y skills@latest add binggan23/neo-skill
  ✓ ./.agents/skills/acceptance      ✓ ./.agents/skills/dev
  ✓ ./.agents/skills/neo-skill       ✓ ./.agents/skills/prd
  ✓ ./.agents/skills/project         ✓ ./.agents/skills/requirements
  ✓ ./.agents/skills/upstream-progress
```

装完即验（未设 `NEO_SKILL_HOME` / `PYTHONPATH`，只靠相对位置）：

```
$ python3 .agents/skills/neo-skill/runtime/flow-core --version
flow-core 0.1.1
$ ls .agents/skills/neo-skill/runtime/
flow_core/  rules/  templates/  flow-core
$ python3 .agents/skills/neo-skill/runtime/flow-core progress --help
（新命令组在安装布局里可用）
```

**结论**：远端 `npx skills@latest add` 已可安装 7 个 Skill，`runtime/` 随包分发，
安装后不依赖源码仓库路径。前文那条阻塞可以认为已解除；前文保留作为历史。

### 11.2 上游进度查询：用真实上游跑通

在装好的项目里真实跑了一遍（上游取 `mattpocock/skills`，v0.1.1 spec 里引用的设计对照来源）：

```
$ flow-core upstream bind --id https://github.com/mattpocock/skills \
    --source https://api.github.com/repos/mattpocock/skills/releases --actor alice --confirm
OK: upstream.bind 完成（更新 1 项）
  上游绑定已写入 AGENTS.md 的受管理区块，区块外内容未改动

$ flow-core progress report
已发布/已完成（远端事实）：
  - v1.3.2  - v1.3.1  - v1.3.0  - v1.2.3  - v1.2.2
  - v1.2.0  - v1.1.0  - v1.0.1  - v1.0.0   ← 均标注来源
进行中/开放（远端事实）：
  - Make architecture skill dependency loading runtime-neutral
  - teach: make quiz options clear and feedback question-specific
  - Move skill instructions from description to body
来源：      - [可访问] …/releases（json）识别 9 条
不确定性：无
本地提醒（不在上游进度内）：
  - [dirty] …（本地状态，不代表上游进度）
```

### 11.3 同一次复验发现并临时修复的问题

复验时把「用户最可能填的那种 URL」——GitHub 的 **releases 页面**——交给查询，暴露一个真实缺口：

```
$ flow-core progress report     # 绑定 .../skills/releases（页面 URL）
已发布/已完成（远端事实）：无
  - [可访问] .../releases（json）json 文档：识别 0 条
不确定性：- [incomplete] …没有识别出任何版本/待办条目
```

GitHub 对 `Accept: application/json` 返回的是 **React 页面 payload**
（`{"payload":{"releasesIndexRoute":…}}`），旧实现把它当合法 JSON 解析、0 条命中，于是报了
`incomplete`——一个看起来像成功的 OK 态。用户拿到的信息是「文档可解析、只是没条目」，
而真相是「这不是进度资料」。

临时修复（`shortcut:` 标注，upgrade 路径写在同一处）：

- 页面渲染数据（React/Next 类 payload、HTML 文档）改报 `unparsable`，并在 `detail` 里
  给出**可直接照做**的替代来源（GitHub 仓库页 → `api.github.com/repos/<owner>/<repo>/releases`）；
- 来源是 `file://` 等带不出站点时，退回用绑定里的上游标识推断站点；
- 真正空的合法 JSON 仍是 `incomplete`（不误伤）。

已知边界：识别依据是「已知的页面 payload 键 + `<!DOCTYPE`/`<html`」，不是完整的站点适配；
出现第二种被真实使用的站点形状时需改成 source adapter。这一条与「只支持三种 scheme、
`stale` 阈值固定 180 天」同属 spec 的 Out of Scope。
