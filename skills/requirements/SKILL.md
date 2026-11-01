---
name: requirements
description: Neo Skill 需求阶段入口。当用户提出新需求、想继续未完成的需求草稿、要给需求分类/优先级、询问是否排期或需要需求负责人确认排期时使用。
---

# requirements：需求对齐与人工排期

本 Skill 负责把「一句模糊的需求」收敛成一条**可排期的需求**：草稿 → grilling → 共识确认 →
询问是否排期 → 需求负责人人工排期。它**不**负责 PRD、开发与验收（见 `prd`、`dev`、`acceptance`）。

词汇以项目 `GLOSSARY.md` 为准：grilling、人工确认、主需求、功能现状。
流程规则以 `docs/srs.md` §5.3（FR-030～035）、§6.2、§7（BR-010/013/014/019）为准。

## 你（Agent）能写什么

| 允许 | 禁止 |
| --- | --- |
| 通过 `flow-core req ...` 写 `03-requirements/` | 手改 `status`、`history`、`confirmations`、`release` |
| 在草稿期（`NEW`/`TRIAGED`）改正文与分类、来源、模块、关联 | 自行排期、取消排期、删除或合并需求 |
| 只读地查代码/文档来回答「可查证事实」 | 用工具结果替代人工决策；替用户填 `--confirm` |

- **只写 `03-requirements/`**：需求正文与 metadata 由 `req create` / `req update` 写入。
  需要改其他目录（`02-definition/`、`04-prds/`）时，交给对应阶段的 Skill。
- **不删除、不合并**：`req candidates` 只提示重复/关联；是否合并由人决定，P0 不提供删除/合并命令。
  废弃需求用 `req reject` / `req defer`，编号与历史保留（BR-019）。
- **不排期**：`req plan` 必须由**需求负责人本人**申报身份并确认。AI 只能提问、准备命令、解释影响。

## 定位 flow-core

不要硬编码绝对路径，按顺序取第一个存在的：

1. `$NEO_SKILL_HOME` → `$NEO_SKILL_HOME/runtime/flow-core`
2. 本 skill 目录的兄弟目录 → `../neo-skill/runtime/flow-core`（安装后 `.claude/skills/` 下并列）
3. 当前 skill 自身 → `./runtime/flow-core`
4. 仓库布局 → `<repo>/skills/neo-skill/runtime/flow-core`

调用方式：`python3 <path> <命令>`。不确定时先跑 `doctor`，它在 `data.runtime.flow_core` 里报告路径。
所有命令都接受 `--root PATH`、`--json`、`--actor ID`、`--confirm`；`--json` 返回统一信封
（`success` / `command` / `artifacts` / `errors` / `next_actions`，部分命令带 `data`）。
退出码：`0` 成功（含幂等无变化）、`1` 业务失败、`2` 用法错误。**以信封与退出码为准，不要靠猜文件。**

## 阶段一：草稿

1. 用一句话确认要解决什么；标题与模块必须来自用户，不要自己编业务结论。
2. 先查重：`flow-core req candidates --title "<标题>" [--module <模块>]`。
   - 有候选就**先问用户**：「和 REQ-002 / 功能现状 parse 是同一件事吗？」；
   - 用户确认是重复 → 建议在原有需求上继续（`req update --body-file`），不要新建；
   - 用户确认不是重复 → 继续。
3. 建草稿：`flow-core req create --title "<标题>" --module <模块> [--category ...] [--source ...]`。
   - 分类：`new-feature`（新功能）/ `optimization`（优化）/ `defect`（缺陷）/ `tech-debt`（技术债）；
   - `source` 记录来源（客户访谈、反馈、数据、内部决策），没有来源就写清楚「未确认来源」。
4. 草稿可随时保存/恢复：`req update --id REQ-001 --body-file draft.md`、`--set module=...`。
   `NEW`/`TRIAGED` 才能改；排期后 `req update` 返回 `REQ_LOCKED`。

编号 `REQ-NNN` 由 flow-core 单调分配，只增不复用；`slug` 创建后不变（跨阶段路径靠它稳定）。

## 阶段二：grilling（设计树 + frontier）

目标：**AI 查事实，人做决策**。不要把可查证的事实当问题问用户。

1. 把问题组织成一棵**设计树**：根是「这个需求要解决什么」，子节点是背景、用户、问题、场景、
   期望结果、来源，以及取舍（范围、优先级、验收口径）。
2. **每轮只问 frontier**：只问前置条件已经明确的那一层，一次问 1–3 个问题。
   已经被上一轮答案决定的问题不要重复问。
3. 每个问题都给**推荐答案**，并说明依据；用户可以直接接受或修正。
4. **事实与决策分离**：能从代码、文档、数据里查到的写进「事实」，只有人才能定的写进「决策」。
   查不到的事实标注「未验证」，不要编。
5. 每轮记录：`flow-core req align --id REQ-001 --frontier "<本轮问题>" --fact "<已查证事实>" --decision "<已确认决策>" --assumption "<假设>" --open-question "<未决>"`。
   设计树可用 `--design-tree tree.md` 整体记入。这些只写 metadata 的 `grilling`，**状态仍是 `NEW`**。
6. 收敛后给**共识摘要**：已确认决策、假设、未决问题、下一步。
   `req align --id REQ-001 --consensus-file consensus.md` 或 `--consensus "<一句话>"`。

共识摘要必须显式列出：`假设`（尚未验证的前提）与 `未决问题`（还不影响排期但需要跟踪）。
未决问题如果影响范围或验收口径，先解决再进入确认。

## 阶段三：共识确认（NEW → TRIAGED）

1. 展示共识摘要，然后**问用户**：「以上共识是否确认？确认后需求进入 TRIAGED。」
2. 用户确认后，问「**你是谁**」，再执行：

```bash
flow-core req confirm --id REQ-001 --actor <成员标识> --confirm
```

3. 失败处理（**状态都不变**，不要重试到成功）：
   - `CONFIRMATION_REQUIRED` → 缺 `--actor`/`--confirm`，回去问用户；
   - `TEAM_NOT_CONFIGURED` → `00-management/team.md` 缺失、没有成员或未 `confirmed: true`，请项目负责人补齐；
   - `ACTOR_UNKNOWN` → 该标识不在团队配置里；
   - `ROLE_MISMATCH` → 该成员没有 `stakeholder` 职责（见 `.specify/rules/flow.yaml` 的 `gates`）；
   - `INVALID_TRANSITION` → 当前状态不能进 `TRIAGED`。
4. 成功信封里的 `data.prompt` 就是下一句要问用户的话（见阶段四），`next_actions` 是确切命令。

## 阶段四：询问「是否设置需求排期」

`req confirm` 成功后，**必须**明确问一句：

> **是否设置 <需求标题> 需求排期？**

拿不准下一步时用只读命令 `flow-core req triage-prompt --id REQ-001`：
它返回 `data.prompt`（就是这句问话，状态不需要问时是 `null`）与 `next_actions`（确切命令）。
文案里 `<title>` 是需求的 `title` 字段，不要改写句式。

三种回答：

| 用户回答 | 做法 | 状态 |
| --- | --- | --- |
| 拒绝排期 | `req plan --id REQ-001 --actor <负责人> --confirm --decline --reason "<原因>"` | 保持 `TRIAGED` |
| 同意但没给版本 | 继续问目标版本（形如 `v0.1.0`）；**不要**执行命令 | 保持 `TRIAGED` |
| 同意并确认版本 | 见阶段五 | `TRIAGED → PLANNED` |

拒绝排期同样需要 `--actor` 与 `--confirm`：这是一个人工决定，要留审计记录（`decision: declined`）。

## 阶段五：人工排期（TRIAGED → PLANNED）

排期是**需求负责人**的决定（`gates.req.plan.role = requirement_owner`）。

1. 讲清影响：进入 `PLANNED` 后需求内容冻结，改期/取消只能 `req unplan`（PRD 之前）或 PRD 变更；
   `PLANNED` 是创建 PRD 的唯一前置状态（BR-010）。
2. 问身份：**现在需要你确认，你是谁？** 再问目标版本。
3. 执行：

```bash
flow-core req plan --id REQ-001 --actor <成员标识> --confirm --release v0.1.0
```

4. 失败处理（**状态都不变**）：
   - `RELEASE_REQUIRED` → 还没确认版本，`next_actions` 给出带 `--release` 的命令，回去问用户；
   - `INCOMPLETE_REQUIREMENT` → 正文关键小节仍是模板占位，先 `req update --body-file` 补齐；
   - `ROLE_MISMATCH` → 该成员不是需求负责人；
   - `INVALID_TRANSITION` → 当前不是 `TRIAGED`；
   - `INVALID_RELEASE` → 版本不是 `vMAJOR.MINOR.PATCH`；
   - `LOCK_CONFLICT` → 另一流程正在改这条需求，等它结束（必要时提示 `recover --release-lock`）。
5. 成功后 metadata 记录 `release`、`planned_by`、`planned_at`，`history` 与 `confirmations` 各追加一条
   （含 `--actor`、时间、对象、决定）。接下来交给 `prd` Skill：`PLANNED` 主需求才能创建 PRD。

## 改期、暂缓与拒绝

| 意图 | 命令 | 前置状态 |
| --- | --- | --- |
| 取消排期 | `req unplan --id --actor --confirm [--reason]` | `PLANNED`；`IN_PRD` 起返回 `REQ_LOCKED` |
| 暂缓 | `req defer --id --actor --confirm [--reason]` | `NEW`/`TRIAGED`/`PLANNED` |
| 拒绝 | `req reject --id --actor --confirm [--reason]` | `NEW`/`TRIAGED`/`PLANNED` |

进入 `IN_PRD` 之后**不得**无记录改期或取消：必须走 PRD 变更（CR）或重新评审（FR-035）。

## 分类与优先级

```bash
flow-core req classify --id REQ-001 --category optimization
flow-core req set-priority --id REQ-001 --scheme RICE --score 42 --reason "影响 3 个客户，上线阻塞"
flow-core req set-priority --id REQ-001 --scheme MoSCoW --score must --reason "不排就做不了验收"
```

优先级**必须**带理由；没有理由的优先级等于没有优先级。方案与取值：RICE 用整数，
MoSCoW 用 `must`/`should`/`could`/`wont`。

## 命令速查

| 命令 | 用途 | 改状态 |
| --- | --- | --- |
| `req create --title --module [--category] [--source] [--slug]` | 建 `NEW` 草稿 | 初始 `NEW` |
| `req update --id [--set k=v ...] [--body-file F]` | 保存/恢复草稿 | 否 |
| `req show --id` | 投影 + 正文 | 否 |
| `req list [--status S] [--module M]` | 清单 | 否 |
| `req candidates --title T [--module M] [--body-file F]` | 重复/关联提示 | 否 |
| `req align --id [--design-tree F] [--frontier ...] [--fact ...] [--decision ...] [--assumption ...] [--open-question ...] [--consensus/--consensus-file]` | 记录 grilling | 否 |
| `req triage-prompt --id` | 「是否设置排期」的下一步 | 否 |
| `req classify --id --category` | 分类 | 否 |
| `req set-priority --id --scheme --score --reason` | 优先级 + 理由 | 否 |
| `req confirm --id --actor --confirm` | 共识确认 | `NEW → TRIAGED` |
| `req plan --id --actor --confirm --release vX.Y.Z` | 人工排期 | `TRIAGED → PLANNED` |
| `req plan ... --decline --reason R` | 拒绝排期 | 保持 `TRIAGED` |
| `req unplan --id --actor --confirm [--reason]` | 取消排期 | `PLANNED → TRIAGED` |
| `req defer` / `req reject --id --actor --confirm [--reason]` | 暂缓/拒绝 | `→ DEFERRED`/`REJECTED` |

## 交接给下一阶段

需求进入 `PLANNED` 后，把这几件事说清楚再交给 `prd` Skill：
主需求 ID、目标版本 `release`、所属模块、共识摘要、未决问题。
`PLANNED` 需求才可以创建正式 PRD（BR-010）；一个 PRD 只有一个主需求。
