---
name: prd
description: Neo Skill PRD 阶段入口。当用户要把已排期的主需求写成 PRD、改 PRD 草稿、做 PRD 自审、提交评审、请需求负责人批准 PRD，或想问 PRD 基线 hash 是否失效时使用。
---

# prd：PRD 草稿、自审、评审与人工批准

本 Skill 负责把一个**已排期的主需求**变成一份可开发、可验收的 PRD：
草稿 → grilling → 自审 → 提交评审 → 需求负责人批准。它**不**负责需求排期（见 `requirements`）、
开发（见 `dev`）与验收（见 `acceptance`）。

词汇以项目 `GLOSSARY.md` 为准：主需求、相关需求、功能现状、grilling、人工确认、基线。
流程规则以 `docs/srs.md` §5.4（FR-040～051）、§6.1、§6.3、§7（BR-010/012/013/016）为准。

## 你（Agent）能写什么

| 允许 | 禁止 |
| --- | --- |
| 通过 `flow-core prd ...` 写 `04-prds/` | 手改 `metadata.yaml`（状态、hash、history、confirmations、source_reqs） |
| 在 `DRAFT`/`CHANGES_REQUESTED` 改正文、场景、功能标识、目标版本 | 直接改 `APPROVED` 及之后状态的 `prd.md` 或锁定字段 |
| 运行 `prd validate` / `prd self-review` 自查 | 自行批准 PRD，或替用户填 `--actor`/`--confirm` |
| 读需求条目、模块 README、功能现状、术语表、constitution 作为 PRD 依据 | 编造业务结论、用户、指标、验收数据 |

- **只写 `04-prds/`**：PRD 正文与 metadata 由 `prd create` / `prd update` 写入。
  改 `03-requirements/` 走 `requirements` Skill，改 `02-definition/` 走对应阶段 Skill。
- **不自行批准**：`prd approve` 必须由**需求负责人本人**申报身份并确认（BR-013）。
  AI 只能整理评审意见、准备命令、解释影响（含 hash 失效后果）。
- **已批准内容不可直接改**：`prd update` 在 `APPROVED` 及之后返回 `PRD_LOCKED`，
  必须走 CR（`flow-core prd cr`，见下文「后续能力」），CR 实现前请停下来问用户。

## 定位 flow-core

不要硬编码绝对路径，按顺序取第一个存在的（共享设计 §8）：

1. `$NEO_SKILL_HOME` → `$NEO_SKILL_HOME/runtime/flow-core`
2. 本 skill 目录的兄弟目录 → `../neo-skill/runtime/flow-core`（安装后 `.claude/skills/` 下并列）
3. 当前 skill 自身 → `./runtime/flow-core`
4. 仓库布局 → `<repo>/skills/neo-skill/runtime/flow-core`

调用方式：`python3 <path> <命令>`。不确定时先跑 `doctor`，它在 `data.runtime.flow_core` 里报告路径。
所有命令都接受 `--root PATH`、`--json`、`--actor ID`、`--confirm`；`--json` 返回统一信封
（`success` / `command` / `artifacts` / `errors` / `next_actions`，部分命令带 `data`）。
退出码：`0` 成功（含幂等无变化）、`1` 业务失败、`2` 用法错误。**以信封与退出码为准，不要靠猜文件。**

## 阶段零：前置检查（BR-010）

PRD 只能从一个 `PLANNED` 主需求创建。开工前先确认：

```bash
flow-core req show --id REQ-001     # 看 data.prd_readiness：ready 是否为 true
```

`ready: false` 时按 `data.prd_readiness.reasons` 逐条解释，并把用户送回 `requirements` Skill：
未排期、没有目标版本、已关联 PRD、正文关键小节仍是占位，都不能建 PRD。

> **一个 PRD 只有一个主需求、只覆盖一个功能。** 其他需求用 `--related` 作为相关需求引用；
> 把两个主需求塞进一个 PRD 会被拒绝（`MULTIPLE_PRIMARY_REQS`）。

## 阶段一：创建草稿（DRAFT）

1. 与用户确认：主需求、功能标识、模块、目标版本、本期场景清单（场景编号形如 `SC-RES-01`）。
   这些是人的决定，不要自己编。
2. 查现状：读 `02-definition/modules/<module>/README.md`、`features/<feature>.md`、`GLOSSARY.md`。
   **新功能且没有功能现状文档**时，PRD 必须明确写「新功能，无现状」——这不会阻塞创建（SRS-FR-044）。
3. 创建：

```bash
flow-core prd create --req REQ-001 --scenarios SC-RES-01,SC-RES-02 \
  [--feature resume-parse] [--module resume] [--slug batch-parse] \
  [--release v0.1.0] [--related REQ-005]
```

- 编号 `PRD-NNN` 由 flow-core 单调分配；`slug` 创建后不可变，它决定跨阶段目录名。
- `module`/`release` 默认取主需求上的值；`feature` 默认取 slug。
- 场景数超过 `limits.max_scenarios_per_prd`（默认 8）→ `TOO_MANY_SCENARIOS`，先请用户拆分 PRD。
- 成功后**主需求自动转 `IN_PRD`**（机械状态，只追加 `history`，不加人工确认记录）。
  此后该需求的改期必须走 PRD 变更或重新评审（SRS-FR-035）。

## 阶段二：写正文 + grilling

`prd create` 生成按模板组织的骨架（背景与目标、范围、现状与变化、场景、非功能需求、
数据需求、依赖与风险、待确认问题），每个场景预填了 `### SC-...` 与 `- AC-...-01` 骨架。

1. 用 grilling 协议推进：**AI 查事实，人做决策**；每轮只问 frontier，一次 1–3 个问题，
   每个问题给推荐答案与依据。
2. 正文必须包含这些小节（缺一节就是 `MISSING_SECTION` 阻断项）：
   背景与目标、范围、现状与变化、场景、非功能需求、数据需求、依赖与风险、待确认问题。
3. 每个场景至少一条编号 AC，AC 必须能测：写成三段式

   ```markdown
   - AC-RES-01-01
     - Given 招聘专员已登录且被授予上传权限
     - When 一次上传 100 份简历
     - Then 上传完成后页面显示 100 份简历的待解析状态
   ```

4. 「范围」要写明本期不做的事（含「不做」「本期不做」等字样），否则是 `SCOPE_UNSPECIFIED` 提示。
5. 写好后落盘（**只有 `DRAFT`/`CHANGES_REQUESTED` 能改**）：

```bash
flow-core prd update --id PRD-001 --body-file prd-draft.md
flow-core prd update --id PRD-001 --set feature=resume-parse --scenarios SC-RES-01,SC-RES-03
```

`--set` 只能改 `title`/`feature`/`release`/`scenarios`/`related_reqs`；
`id`/`slug`/`module`/`source_reqs` 是创建后不变的标识与范围 → `IMMUTABLE_FIELD`。

## 阶段三：自审（只读）

自审只读，**不会修改正文**，输出问题清单 `data.issues`
（`{code, severity, object, message, suggestion}`）：

```bash
flow-core prd validate --id PRD-001       # 有 error → 退出码 1
flow-core prd self-review --id PRD-001    # 清单投影：data.checklist
```

| 问题码 | 严重度 | 含义与处理 |
| --- | --- | --- |
| `MISSING_SECTION` | error | 必备小节缺失或仍是模板占位 → 补写 |
| `MISSING_CURRENT_STATE` | error | 没有「现状与变化」也没声明「新功能，无现状」 |
| `SCENARIO_WITHOUT_AC` | error | 某个场景没有编号 AC → 补 AC |
| `TOO_MANY_SCENARIOS` | error | 场景超上限 → 拆分 PRD |
| `SCOPE_UNSPECIFIED` | warning | 「范围」没写本期不做的事 |
| `TESTABILITY_WEAK` | warning | AC 缺 Given/When/Then 之一 → 改写成三段式 |
| `AMBIGUOUS_WORD` | warning | 出现「快速/友好/尽量」等说不清的词 → 换成可验证描述 |
| `TERM_INCONSISTENT` | warning | 与 `02-definition/glossary.md` 的同义词混用 |
| `SCENARIO_NOT_IN_BODY` | warning | metadata 声明的场景在正文里找不到小节 |
| `CROSS_DOC_CONFLICT` | warning | 与其他 PRD 撞场景编号，或声明「新功能」但现状文档已存在 |

**error 必须清零才能批准**（`prd approve` 会返回 `VALIDATION_FAILED`）；warning 不阻断，
但要在评审记录里向用户说明，由人决定是否修改。

## 阶段四：提交评审（DRAFT → IN_REVIEW）

提交评审是**第一个**独立确认点（SRS-FR-046）。

1. 先把自审结果讲清楚：error 数、warning 数、每一项的处理。
2. 然后问用户：**是否提交评审？现在以谁的身份提交？**
3. 执行（提交人职责见 `gates.prd.submit-review.role`，默认 `stakeholder`）：

```bash
flow-core prd submit-review --id PRD-001 --actor bob --confirm [--note "请重点看范围一节"]
```

- 会追加写 `reviews/YYYY-MM-DD-review.md`（提交人、时间、自审摘要、提交说明）。
- 失败处理（**状态都不变**）：`CONFIRMATION_REQUIRED`（缺 `--actor`/`--confirm`）、
  `TEAM_NOT_CONFIGURED`、`ACTOR_UNKNOWN`、`ROLE_MISMATCH`、`INVALID_TRANSITION`、
  `PRD_LOCKED`（已批准/开发中，必须走 CR）、`LOCK_CONFLICT`。
- 提交不要求 error 清零，但批准会要求；有阻断项时先修再提交更省事。

## 阶段五：人工批准（IN_REVIEW → APPROVED）

批准是**第二个**独立确认点（SRS-FR-047），也是唯一能建立内容基线的地方。

1. 讲清后果：批准后 `prd.md`、锁定字段与已有的 `test-cases.md` 被 hash 锁定；
   再改就是 `HASH_MISMATCH`，必须走 CR 并重新批准（BR-012）。
2. 问身份：**谁以需求负责人身份批准？**（`gates.prd.approve.role = requirement_owner`）
3. 执行：

```bash
flow-core prd approve --id PRD-001 --actor alice --confirm
```

成功后会记录 `approved_by` / `approved_at` / `version`、追加 `history` 与
`confirmations`（含 `hash` 与 `version`），并把 `content_hash` 写进 `metadata.yaml`。

失败处理（**状态都不变**）：

| 错误码 | 说明 |
| --- | --- |
| `VALIDATION_FAILED` | 还有 error 级自审问题；先修再（必要时重新）提交评审 |
| `CONFIRMATION_REQUIRED` | 缺 `--actor` 或 `--confirm` |
| `ROLE_MISMATCH` | 该成员不是需求负责人——**不要换身份绕过**，去请需求负责人 |
| `INVALID_TRANSITION` | 当前不是 `IN_REVIEW`（例如还在 `DRAFT`） |
| `LOCK_CONFLICT` | 另一流程正在改这个 PRD，等它结束 |

## 基线 hash 与失效

```bash
flow-core prd verify-hash --id PRD-001
```

- 一致 → `success: true`，`data.match = true`；
- 不一致 → `HASH_MISMATCH`（`data.expected` / `data.actual` 便于解释哪一侧变了）；
- 从未批准 → `BASELINE_MISSING`。

hash 参与范围（共享设计 §6）：`prd.md` + 存在的 `test-cases.md`（按相对路径与 UTF-8 字节）
+ `locked_fields` 指定的 metadata 字段（`id`、`slug`、`module`、`feature`、`scenarios`、
`source_reqs`、`release`、`version`）。`reviews/`、`changes/`、`history`、`confirmations`、
`status`、`content_hash` 不参与——**追加记录不会无故让基线失效**。

开发门禁（`dev` Skill）会用同一个 hash 判断「能不能开发」；这里只负责报告，不拦开发。

## 拒绝（→ REJECTED）

`prd reject --id PRD-001 --actor alice --confirm --reason "<理由>"`（理由必填，`REASON_REQUIRED`）。
可用起点由规则表决定：`DRAFT`/`IN_REVIEW` → `REJECTED`。拒绝是终态旁支，
编号与历史保留，不回收编号（BR-019）。

## 后续能力（尚未实现，遇到就停下来说明）

| 意图 | 正式入口 | 现状 |
| --- | --- | --- |
| 改已批准 PRD 的正文或锁定字段 | `flow-core prd cr --id PRD-001 --reason ...` | 未实现（后续 ticket）。**不要**用 `prd update`，会被 `PRD_LOCKED` 拦住 |
| 从 AC 派生测试用例 | `test-cases.md`（参与 hash） | 可直接写文件，但必须在批准**之前**写完 |
| 拆分过大的 PRD | 新建一个 PRD，旧 PRD 走 CR/`SUPERSEDED` | `SUPERSEDED` 状态已声明，命令属后续 ticket |

## 命令速查

| 命令 | 用途 | 改状态 |
| --- | --- | --- |
| `prd create --req REQ-001 [--scenarios ...] [--feature ...] [--release ...] [--related ...]` | 建 `DRAFT` 草稿 | 建 `DRAFT`；主需求 → `IN_PRD` |
| `prd update --id [--body-file F] [--set k=v ...] [--scenarios ...]` | 保存草稿 | 否（锁 `DRAFT`/`CHANGES_REQUESTED`） |
| `prd validate --id` | 结构自审（只读，有 error 退出码 1） | 否 |
| `prd self-review --id` | 自审清单（只读） | 否 |
| `prd submit-review --id --actor A --confirm [--note T]` | 提交评审 | `DRAFT`/`CHANGES_REQUESTED → IN_REVIEW` |
| `prd approve --id --actor A --confirm` | 需求负责人批准 + 锁定 hash | `IN_REVIEW → APPROVED` |
| `prd reject --id --actor A --confirm --reason R` | 拒绝 | `→ REJECTED` |
| `prd show --id` / `prd list [--status S] [--module M]` | 投影与清单 | 否 |
| `prd verify-hash --id` | 校验基线 hash | 否 |

## 交接给下一阶段

批准后把这几件事说清楚再交给 `dev` Skill：PRD 编号与目录、`content_hash`、版本、
场景与 AC 清单、`test-cases.md` 是否存在、主需求编号。
**未批准或 hash 失效的 PRD 不得进入开发**（BR-011）；发现 PRD 问题要停下来提 CR，
不能自行改需求或扩大范围（SRS-FR-063）。
