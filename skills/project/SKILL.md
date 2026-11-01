---
name: project
description: Neo Skill 项目阶段入口。当用户要诊断环境、初始化项目工作区、确认团队与 constitution、裁剪目录、修复初始化冲突或重跑初始化时使用。
---

# project：环境诊断与项目初始化

本 Skill 负责把「一个空目录」变成**可排期、可开发、可验收的项目工作区**：
诊断 → 初始化 → 团队摘要确认 → `constitution.md` 确认 → 冲突处理 → 裁剪决策 → 幂等复跑。
它**不**负责需求、PRD、开发与验收（见 `requirements`、`prd`、`dev`、`acceptance`）。

词汇以项目 `GLOSSARY.md` 为准：项目基线、受管理区块、人工确认、未配置完成。
流程规则以 `docs/srs.md` §5.1（FR-010～013）、§5.2（FR-020～025）为准。

## 入口：先 `doctor`，再 `init`

**任何初始化动作之前先跑 `doctor`。** 初始化会写 70+ 个文件，环境不对时先修环境再动手。

```bash
python3 <flow-core> doctor        # 人类可读
python3 <flow-core> doctor --json # data.checks[] + data.runtime
```

`doctor` 的检查项（`data.checks[].name`）：

| 检查项 | 通过条件 | 缺失时的修复提示（`suggestion`） |
| --- | --- | --- |
| `python` | Python ≥ 3.11 | 升级 Python；本工具不自动安装 |
| `node` | Node.js ≥ 18 | 升级 Node.js（`npx skills@latest` 需要） |
| `flow-core` | 入口脚本可解析、可执行 | 检查安装目录；用 `$NEO_SKILL_HOME` 或兄弟目录规则定位 |
| `rules` | 项目基线或随包规则可读 | 已初始化则查 `.specify/rules/flow.yaml`；否则用随包规则 |
| `templates` | 随包模板齐全 | 重新安装 Skill 包；模板不随项目走 |
| `project` | 当前目录已初始化 | 跑 `init` |
| `locks` | 无残留锁 | `recover --release-lock <对象> --actor <人> --confirm`（仅当进程已结束） |

`doctor` **只报告**：`status` 为 `ok` / `warn` / `error`，各带一句 `suggestion`。
它**不**自动安装全局软件、**不**改用户配置、**不**改 `~/.claude/` 或 `~/.agents/`。

- 有 `error` → 先把 `suggestion` 原样转述给用户，等用户处理完再重跑 `doctor`；**不要**绕过。
- 只有 `warn` → 可以继续，但要说明风险，并在结果里记录。
- `--strict` 把 `warn` 也当失败（CI 用）；交互初始化时不要默认加。
- **身份不明时**：`doctor` 的 `data.runtime.flow_core` 就是权威的 `flow-core` 路径，别猜。

## 定位 flow-core

不要硬编码绝对路径，按顺序取第一个存在的：

1. `$NEO_SKILL_HOME` → `$NEO_SKILL_HOME/runtime/flow-core`
2. 本 skill 目录的兄弟目录 → `../neo-skill/runtime/flow-core`（安装后 `.claude/skills/` 下并列）
3. 当前 skill 自身 → `./runtime/flow-core`
4. 仓库布局 → `<repo>/skills/neo-skill/runtime/flow-core`

调用方式：`python3 <path> <命令>`。所有命令都接受 `--root PATH`、`--json`、`--actor ID`、`--confirm`；
`--json` 返回统一信封（`success` / `command` / `artifacts` / `errors` / `next_actions`，部分命令带 `data`）。
退出码：`0` 成功（含幂等无变化）、`1` 业务失败、`2` 用法错误。**以信封与退出码为准，不要靠猜文件。**

`--root` 缺省时从当前目录向上找含 `.neo-skill/` 或 `.specify/` 的目录；**不在目标项目里时显式传 `--root`**。

## 你（Agent）能写什么

| 允许 | 禁止 |
| --- | --- |
| 通过 `flow-core init` 生成骨架、项目基线、模板 | 手写/手改 `status`、`history`、`confirmations`、基线 |
| 按用户原话填 `00-management/team.md` 的成员与职责 | 自己编人名、编职责、替用户把 `confirmed` 改成 `true` |
| 把用户口述的技术/测试/安全约束写进 `.specify/constitution.md` | 代为确认 constitution；编造团队没声明的技术栈 |
| 展示 `init` 的变更计划、解释 `--prune` 影响 | 覆盖已有 `README.md`、业务文档或代码；改受管理区块之外的 `AGENTS.md` |
| 只读地查环境、查仓库来回答可查证事实 | 伪造 charter、调研结论、roadmap 或任何业务内容 |

- **只写骨架与基线**：`00-management/`、`01-discovery/` 等目录里的正文默认是模板占位（`<!-- TODO -->`）。
  `init` 生成的是**空壳**，不是内容。用户没给的业务结论一律留 `TODO`（FR-020）。
- **不删除**：`init` 只新增与补齐，从不删除用户文件。裁剪（`--prune`）只在**首次**初始化时影响是否创建目录。
- **不动全局**：不写 `~/.claude/`、`~/.agents/`、`~/.config/`；所有写入都在 `--root` 之下。

## 阶段一：初始化骨架（`init`）

```bash
python3 <flow-core> init --root . --json
```

生成的骨架（76 项 artifacts，全部 `action: created`）：

```
.specify/rules/flow.yaml        项目规则基线（流程的单一事实来源，随包复制）
.specify/templates/             10 个模板：req / prd / spec / plan / tasks /
                                verification / demo / checklist / report / constitution
.specify/constitution.md        项目约束（默认 confirmed: false）
.specify/scripts/README.md
.neo-skill/counters.yaml        编号计数器
.neo-skill/init-state.yaml      初始化状态（幂等判断依据）
.neo-skill/locks/               锁目录
.neo-skill/transactions/        事务日志目录
00-management/{charter,roadmap,team,risks}.md + decisions/ meetings/ reports/
01-discovery/{opportunity,hypotheses,insights}.md + market/ competitors/ users/ experiments/
02-definition/{vision,business-model,metrics,mvp-scope,glossary,feature-map}.md + modules/
03-requirements/{backlog.md,items/,feedback/}
04-prds/index.md
05-design/{design-system.md,assets/}
06-specs/{architecture,data-model}.md + api/ adr/
07-acceptance/
08-releases/CHANGELOG.md
09-growth/{dashboards.md} + analysis/ experiments/ retros/
src/  tests/  archive/
README.md  AGENTS.md
```

`success: true` 只说明「没有失败」；**是否真的建好了看 `artifacts[].action`**：
`created`（新建）/ `unchanged`（已存在且内容一致）/ `planned`（有冲突，待确认）。

## 阶段二：裁剪决策（`--prune`，默认不裁剪）

`init --prune discovery,design,growth,releases,meetings,reports` 只**省略**原始方案允许省略的目录组。
取值以 `.specify/rules/flow.yaml` 的 `directories` 为准，**不要凭记忆列**：

| 可裁剪（`directories.prunable`） | 含义 |
| --- | --- |
| `discovery` | `01-discovery/`：机会、假设、调研、竞品、用户访谈 |
| `design` | `05-design/`：设计系统与素材 |
| `growth` | `09-growth/`：看板、增长实验、复盘 |
| `releases` | `08-releases/`：发布与 CHANGELOG |
| `meetings` | `00-management/meetings/` |
| `reports` | `00-management/reports/` |

| 不可裁剪（`directories.not_prunable`） | 原因 |
| --- | --- |
| `03-requirements` | 需求是主链路起点（BR-010 的前置） |
| `04-prds` | PRD 状态与门禁的载体 |
| `07-acceptance` | 人工验收证据的唯一去处 |
| `.specify` | 项目规则基线、模板、constitution |
| `.neo-skill` | 计数器、锁、事务日志 |

**怎么问用户**：先说明「默认全建、不裁剪」，再说裁剪的真实代价（该阶段暂时没有落盘位置，
以后要补只能重跑 `init`——`init` 不会重建被 `--prune` 跳过的目录组，需手工建目录或重新初始化）。
只有用户明确说「小项目、不要这些」才裁剪，并把裁剪列表写进结果。**charter、MVP scope、PRD 门禁、
人工验收相关能力永远不能关。**

裁剪只会**少建目录**，不会关闭任何能力；`init` 之后仍可手工补目录。

## 阶段三：团队摘要收集与确认（FR-022）

`init` 生成的 `00-management/team.md` 是**未配置完成**状态：

```yaml
---
confirmed: false
confirmed_by: null
confirmed_at: null
members: []
---
```

目标形状（`id` 是审计记录里 `--actor` 要填的值，`roles` 取值见 `flow.yaml` 的 `roles`）：

```yaml
---
confirmed: true
confirmed_by: carol
confirmed_at: 2026-11-01T10:00:00+08:00
members:
  - {id: alice, name: 张三, roles: [requirement_owner]}
  - {id: bob,   name: 李四, roles: [stakeholder]}
  - {id: carol, name: 王五, roles: [project_owner, developer]}
---
```

步骤：

1. **先问，别编**：至少需要一名 `requirement_owner`（排期、PRD 批准、验收结论）
   和一名 `project_owner`（项目约束与团队配置）。问清成员的**标识、姓名、职责**。
2. **展示摘要让用户确认**：把 `id / name / roles` 三列念一遍（「alice=张三 负责排期与验收」），
   明确问「以上团队配置是否正确？确认后我会把 `confirmed` 置为 `true`」。
3. 用户确认后才写 `confirmed: true`、`confirmed_by`、`confirmed_at`。
4. 校验：`python3 <flow-core> doctor --root . --json`，确认没有 `TEAM_NOT_CONFIGURED` 类问题的隐患；
   也可以在第一次需要人工确认的命令上验证——会返回 `TEAM_NOT_CONFIGURED` / `ACTOR_UNKNOWN` / `ROLE_MISMATCH`。
5. 用户暂时给不出人 → **保留 `confirmed: false`**，明确告知「项目为未配置完成，不得排期或批准」。
   不要为了「让流程跑起来」先填假名字。

`team.md` 只做职责匹配与审计记录，**不做登录、真实身份认证或权限控制**。

## 阶段四：`constitution.md` 确认（FR-023）

`.specify/constitution.md` 默认 `confirmed: false`，六个待填小节：技术栈、编码规范、测试要求、
安全与合规、架构约束、交付约束。

1. 逐节问用户（或从现有代码/文档里查可查证事实，例如语言、测试框架），**不要编技术栈**。
2. 写正文，把 `confirmed` 置 `true` 前必须**明确告知**：「未确认时 `dev gate` 会拒绝启动开发」。
3. 首次正式开发前必须确认（`dev gate` 会检查）。
4. 拿不准的项写「未确认」并保留 `TODO`，说明后果；不要写看起来完整但假的约束。

## 阶段五：冲突处理（FR-024）

已有文件内容与生成内容不同时，`init` **默认不覆盖**：

- 该文件以 `artifacts[].action = planned` 出现，`errors` 里给出冲突原因；
- `next_actions` 给出带 `--apply-conflicts` 的确切命令。

处理顺序：

1. 逐条展示**变更计划**（哪些文件、差异性质），解释「覆盖会丢掉你的手改内容」。
2. 用户确认后：`python3 <flow-core> init --root . --apply-conflicts`。
3. **`AGENTS.md` 只更新受管理区块**：`<!-- neo-skill:begin -->` 与 `<!-- neo-skill:end -->`
   之间的内容由 `flow-core init` / `flow-core index` 维护；区块外的一律保留，不得重排。
4. `AGENTS.md` 区块被手工改过时 `lint` 会报 `MANAGED_BLOCK_MODIFIED`：
   先说明「这是绕过工具的直接编辑留下的不一致」，再问用户是否让 `init --apply-conflicts` 或
   `index generate` 重建区块——**不要静默覆盖用户的区块外内容**。

## 阶段六：幂等复跑（FR-025）

`init` 可重复执行：已满足条件的步骤不重复创建、不覆盖。

```bash
python3 <flow-core> init --root . --json     # 第二次跑：已存在的文件 action: unchanged
```

- 全部 `unchanged` → 直接告诉用户「无需改动」，**不要**为了「做点什么」再加参数。
- `.neo-skill/init-state.yaml` 记录上次初始化状态，是幂等判断依据；不要手改。
- 新增了成员/约束之后重跑 `init` **不会**回退用户内容；它只补缺失的文件与目录。
- 复跑后建议再跑一次 `python3 <flow-core> lint` 看基线是否一致（只读，有 error 退出码 1）。

## 角色对照表（从 `rules/flow.yaml` 的 `gates` 读，不要硬编码）

| 人工决策 | 需要的职责（`gates.<key>.role`） | 状态变化 |
| --- | --- | --- |
| `req.confirm`（共识确认） | `stakeholder` | `NEW → TRIAGED` |
| `req.plan` / `req.unplan`（排期 / 取消排期） | `requirement_owner` | `TRIAGED ↔ PLANNED` |
| `req.defer` / `req.reject`（暂缓 / 拒绝） | `requirement_owner` | `→ DEFERRED` / `REJECTED` |
| `prd.submit-review`（提交评审） | `stakeholder` | `DRAFT → IN_REVIEW` |
| `prd.approve` / `prd.request-changes` / `prd.reject` | `requirement_owner` | `IN_REVIEW → APPROVED` / `CHANGES_REQUESTED` / `REJECTED` |
| `prd.cr.*`（变更请求） | `requirement_owner` | 见状态机 |
| `dev.start`（启动开发） | `developer` | `APPROVED → DEVELOPING` |
| `acceptance.submit`（提交人工验收） | `developer` | `DEVELOPING → READY_FOR_ACCEPTANCE` |
| `acceptance.approve` / `acceptance.reject` | `requirement_owner` | `READY_FOR_ACCEPTANCE → ACCEPTED` / `DEVELOPING` |

**这张表只是转述。权威值在 `.specify/rules/flow.yaml` 的 `gates`（项目基线，升级不静默改写）；
两者可疑地不一致时，以 `flow-core lint rules` 与文件内容为准，不要按这张表硬编码判断。**
`roles` 的完整取值也在同一个文件里。

## 命令速查

| 命令 | 用途 | 改动 |
| --- | --- | --- |
| `doctor [--strict]` | 环境、资源、残留锁诊断 + 修复提示 | 否（只读） |
| `init` | 建立骨架、项目基线、模板 | 是（幂等，不覆盖） |
| `init --prune discovery,design,growth,releases,meetings,reports` | 首次初始化时省略可裁剪目录组 | 是 |
| `init --apply-conflicts` | 按变更计划覆盖冲突文件 | 是（需人工确认） |
| `lint` | 结构/引用/hash/基线/收尾一致性检查 | 否（只读） |
| `lint rules` | 项目规则基线 vs 随包规则的版本与字段一致性 | 否（只读） |
| `recover list` / `recover --release-lock <对象> --actor --confirm` | 残留锁清理 | 是（需人工确认） |

## 交接到下一阶段

初始化完成、`team.md` 与 `constitution.md` 都确认后，把这几件事说清楚再交给 `requirements` Skill：
项目根目录、裁剪了哪些目录、团队成员与职责（谁能排期 / 谁能批准 / 谁能验收）、
`constitution.md` 里影响设计与测试的硬约束。
自检一遍：`python3 <flow-core> doctor` 全 `ok`、`python3 <flow-core> lint` 无 error。
之后由 `requirements` 接手第一条需求。
