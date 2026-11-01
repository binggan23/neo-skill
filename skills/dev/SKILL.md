---
name: dev
description: Neo Skill 开发阶段入口。当用户要从某个已批准的 PRD 开始正式开发、建立 Spec/Plan/Tasks、查看开发基线是否仍然有效、或检查代码/Spec 是否超出 PRD 范围时使用。
---

# dev：从有效 PRD 启动开发

本 Skill 负责把一份**已批准的 PRD** 变成可执行的技术工作：门禁 → Spec/Plan/Tasks →
TDD 实现 → 验证（移交 `verification`/ticket 06）→ 提交验收。它**不**负责需求排期（见
`requirements`）、PRD 编写与批准（见 `prd`）、人工验收（见 `acceptance`）。

词汇以项目 `GLOSSARY.md` 为准：主需求、基线、功能现状、人工确认、收尾。
流程规则以 `docs/srs.md` §5.5（FR-060～063）、§6.3、§7（BR-011/015/018）为准。

## 入口：必须显式绑定一个 PRD

用户说「开始开发」「按 PRD 干活」「继续上次的开发」时，第一件事是把 PRD 编号钉死：

```bash
flow-core dev gate --prd PRD-001
```

- **必须显式给 `--prd`**。不要从「最近改过的 PRD」「唯一的 APPROVED PRD」自动推断；
  缺参数就是 `PRD_NOT_BOUND`，这时去问用户要开发哪一份。
- 返回里 `data.binding` 是本次绑定对象与当前基线（`prd`、`module`、`version`、
  `content_hash`、`scenarios`、`source_reqs`、`spec_dir`），把它原样讲给用户听。

## 你（Agent）能写什么

| 允许 | 禁止 |
| --- | --- |
| 通过 `flow-core dev ...` 写 `06-specs/<module>/PRD-NNN-<slug>/` | 手改 `04-prds/` 下任何文件（PRD 属 `prd` Skill） |
| 写 `src/`、`tests/` 的实现与测试代码 | 改 `03-requirements/`、`02-definition/`、`07-acceptance/` |
| 运行测试与静态检查、记录原始输出 | 为了让门禁通过而修改 PRD、AC、constitution 或 baseline 文件 |
| 按 PRD 场景拆分任务 | 顺手做 PRD 之外的功能（超范围必须走 CR） |

> **只写 `06-specs/`、`src/`、`tests/`。** `dev scope-check --paths ...` 会把这三者之外的
> 路径判为越界（`SCOPE_EXCEEDED`）。确实需要改别处 → 停下来，说明影响，要求 CR 或人工澄清。

## 定位 flow-core

不要硬编码绝对路径，按顺序取第一个存在的（共享设计 §8）：

1. `$NEO_SKILL_HOME` → `$NEO_SKILL_HOME/runtime/flow-core`
2. 本 skill 目录的兄弟目录 → `../neo-skill/runtime/flow-core`（安装后 `.claude/skills/` 下并列）
3. 当前 skill 自身 → `./runtime/flow-core`
4. 仓库布局 → `<repo>/skills/neo-skill/runtime/flow-core`

调用方式：`python3 <path> <命令>`。不确定时先跑 `doctor`，它在 `data.runtime.flow_core` 里报告路径。
所有命令都接受 `--root PATH`、`--json`、`--actor ID`、`--confirm`；`--json` 返回统一信封
（`success` / `command` / `artifacts` / `errors` / `next_actions`，部分命令带 `data`/`checks`）。
退出码：`0` 成功（含幂等无变化）、`1` 业务失败、`2` 用法错误。**以信封与退出码为准。**

## 阶段一：开发门禁（只读，BR-011）

```bash
flow-core dev gate --prd PRD-001
```

九项检查，全部通过才 `success: true`；`data.checks[]` 里每项是
`{name, ok, code, detail, status}`，`data.binding` 是绑定对象与基线。

| 检查 | `name` | 失败错误码 | 说明与处理 |
| --- | --- | --- | --- |
| 显式绑定 | `binding` | `PRD_NOT_BOUND` | 缺 `--prd`。问用户要开发哪一份，不要猜 |
| PRD 存在 | `prd_exists` | `PRD_NOT_FOUND` | 用 `prd list` 核对编号 |
| 状态允许 | `prd_state` | `PRD_STATE_NOT_ALLOWED` | 只有 `APPROVED`（首次）与 `DEVELOPING`（幂等重启）可启动 |
| 批准记录 | `approval` | `NOT_APPROVED` | 缺 `approved_by`/`approved_at`；回 `prd` Skill 走批准 |
| 基线 hash | `baseline_hash` | `BASELINE_MISSING` / `HASH_MISMATCH` | 无基线→先批准；不一致→内容被改，必须走 CR |
| 项目约束 | `constitution` | `CONSTITUTION_MISSING` / `CONSTITUTION_NOT_CONFIRMED` | `.specify/constitution.md` 缺失或 `confirmed != true` |
| 团队配置 | `team` | `TEAM_NOT_CONFIGURED` | `00-management/team.md` 缺失、无成员或未 `confirmed` |
| 引用关系 | `references` | `REQ_REFERENCE_INVALID` | `source_reqs` 必须恰好一个，该 REQ 存在、处于 `IN_PRD`/`DONE`、且 `prd` 指回本 PRD |
| 模块一致 | `module` | `MODULE_MISMATCH` | 目录 `04-prds/<module>/PRD-NNN-<slug>` 与 metadata 的 `module` 必须一致 |

失败时：

- `dev gate` **不写任何文件**、不改状态（可以放心反复跑）。
- 一次给出所有失败项（不是只报第一个）；逐条解释并给下一步，不要自行「修」PRD 或 team 文件。
- `HASH_MISMATCH` 的 `suggestion` 与 `next_actions` 会指向 `flow-core prd cr`（CR 属 ticket 04）。
  **不要**直接改 `prd.md` 让 hash 对上。

## 阶段二：建立 Spec / Plan / Tasks（`dev start`）

门禁全绿后：

```bash
flow-core dev start --prd PRD-001 --actor carol --confirm
```

- `--actor` 必须是团队里具备 `developer` 职责的成员（`gates.dev.start.role`）；
  `APPROVED → DEVELOPING` 是机械状态，但仍要求身份供审计（`CONFIRMATION_REQUIRED` /
  `ACTOR_UNKNOWN` / `ROLE_MISMATCH` 都不会改状态）。
- 生成 `06-specs/<module>/PRD-NNN-<slug>/spec.md`、`plan.md`、`tasks.md` 与 `.baseline.yaml`。
  三个文件 front matter 固定带 `prd`、`baseline_version`、`baseline_hash`、`module`、`status: DRAFT`，
  正文按 `.specify/templates/` 模板生成，场景表已按 PRD `scenarios` 预填行。
- 门禁任一项失败 → 什么都不创建、状态不变（零副作用）。
- **幂等**：PRD 已是 `DEVELOPING` 时再跑 `dev start` 只补缺失文件，不覆盖已有文件、
  不重复追加 `history`（`artifacts` 里是 `unchanged` / 缺失项 `created`）。
- `.baseline.yaml` 由这个命令写；`baseline_valid: false` 由 CR 流程写，**你不要手动改**。

## 阶段三：写 Spec / Plan / Tasks（只写 06-specs/）

1. **Spec**：写清目标、方案、与 PRD 场景/AC 的对应关系（表格每行都要落到 PRD 已声明的
   `SC-...` 或 `AC-...`）、影响面、风险与回滚。Spec 不重复 PRD 的需求描述，只讲怎么做。
2. **Plan**：实施顺序、每步产出与完成判据、验证安排、回滚点。
3. **Tasks**：「任务 | 对应场景 / AC | 状态 | 产出」表格，一行一个可交付任务。
   **每条任务都要能追溯到 PRD 场景或 AC**；追溯不了的就是超范围。
4. 写自己 `06-specs/` 下的文件用正常编辑；不要改 front matter 的 `baseline_hash`/`baseline_version`。

## 阶段四：TDD 实现（`src/` + `tests/`）

1. 先按 AC 写失败测试（`tests/`），再写实现（`src/`），红 → 绿 → 重构。
2. 每轮改动前后都自查范围：

```bash
flow-core dev scope-check --prd PRD-001 --paths src/resume/parse.py,tests/test_parse.py
```

- `--paths` 只接受 `src/`、`tests/`、`06-specs/` 下的相对路径；绝对路径与含 `..` 的路径一律越界。
- 它还会扫 Spec/Tasks 里出现的 `SC-...` 与 `AC-...`：PRD 未声明的场景/AC → `SCOPE_EXCEEDED`。
- 越界处理（BR-012/BR-013、SRS-FR-062/063）：**停止**相关工作 → 说明哪一处超出 PRD →
  要求 CR 或人工澄清 → **绝不自作主张改 PRD、AC 或扩大范围**。
  需要变更时提示 `flow-core prd cr --id PRD-001 --reason "<原因>"`。

## 阶段五：验证（ticket 06 提供入口，本阶段只留位置）

适用验证的执行、`verification.md` 生成、`READY_FOR_ACCEPTANCE` 提交属 ticket 06
（`docs/srs.md` FR-064～068）。现在能做的：

- 手工跑项目测试并保留原始输出与退出码，**不得删用例、降断言、跳测试**（BR-015）；
- `06-specs/<module>/PRD-NNN-<slug>/verification.md` 由验证命令生成，不要在 `dev start` 时伪造。

遇到「测试失败想改 AC 让它过」「不适用就当作通过」这类念头 → 停下来，那是 BR-015 禁止的。

## 提交验收（ticket 06）

全部适用验证通过后由 `acceptance`/验证流程把 PRD 推到 `READY_FOR_ACCEPTANCE`，
并记录 Git commit 或非 Git 的内容摘要。**AI 不得把 `READY_FOR_ACCEPTANCE` 当成验收通过**，
人工验收见 `acceptance` Skill（BR-013）。

## 基线失效与中断

```bash
flow-core dev status --prd PRD-001
```

- `data.files`：`{spec, plan, tasks}` 是否存在；
- `data.hash_matches`：PRD 当前内容 hash 是否仍等于 `content_hash`；
- `data.baseline_valid`：`.baseline.yaml` 未被标记失效**且** hash 一致；
- `data.baseline`：`.baseline.yaml` 原文（CR 会追加 `invalidated_by`/`invalidated_at`）。

任一为 false 就是「基线失效」：不得继续开发、不得沿用旧 Spec 结论。
处理路径是把 PRD 走 CR → 重新评审 → 重新批准 → 重新 `dev start`：

- PRD 已是 `DEVELOPING` 但 `.baseline.yaml` 被 CR 标成 `baseline_valid: false`
  → `dev start` 返回 `BASELINE_INVALID`（不允许在失效基线上继续开发）；
- PRD 重新批准后回到 `APPROVED` 且版本/hash 都变了 → `dev gate` 会提示「旧 Spec 基线已失效，
  本次启动将按新基线重建」，`dev start` 会重写 `.baseline.yaml`、把三份产物的 front matter
  更新到新 `baseline_version`/`baseline_hash`（**正文原样保留**，`artifacts` 里是 `updated`）。

## 命令速查

| 命令 | 用途 | 写文件 / 改状态 |
| --- | --- | --- |
| `dev gate --prd PRD-001` | 九项门禁（只读） | 否 / 否 |
| `dev start --prd PRD-001 --actor A --confirm` | 建 Spec/Plan/Tasks + `.baseline.yaml` | `06-specs/` / `APPROVED → DEVELOPING` |
| `dev status --prd PRD-001` | 产物与基线有效性报告（只读） | 否 / 否 |
| `dev scope-check --prd PRD-001 [--paths a,b]` | 场景/AC 与变更路径范围检查（只读） | 否 / 否 |

`data` 字段：`gate`/`start` 带 `checks[]`、`binding`（`start` 另带 `prd`、`baseline`）；
`status` 带 `files`、`baseline`、`baseline_hash`、`current_hash`、`hash_matches`、
`baseline_valid`、`spec_dir`、`started`；`scope-check` 带 `scope`
（`referenced_scenarios`、`unknown_scenarios`、`referenced_acs`、`unknown_acs`、`paths`、
`path_violations`、`files`）。

## 交接给下一阶段

开始开发前把这几件事说清楚：绑定的 PRD 与版本、`content_hash`、`spec_dir`、
场景与 AC 清单、`.baseline.yaml` 的 `baseline_hash`。
进入验收时交接：Spec/Plan/Tasks 路径、验证证据位置、代码版本（commit 或内容摘要）。
**未绑定或基线失效的 PRD 不得继续开发**（BR-011/BR-016）。
