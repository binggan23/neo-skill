---
name: dev
description: Neo Skill 开发阶段入口。当用户要从某个已批准的 PRD 开始正式开发、建立 Spec/Plan/Tasks、执行验证并保存证据、查看开发/验证基线是否仍然有效、或检查代码/Spec 是否超出 PRD 范围时使用。
---

# dev：从有效 PRD 启动开发

本 Skill 负责把一份**已批准的 PRD** 变成可执行的技术工作：门禁 → Spec/Plan/Tasks →
TDD 实现 → 验证证据 → 提交人工验收。它**不**负责需求排期（见 `requirements`）、
PRD 编写与批准（见 `prd`）、人工验收结论（见 `acceptance`）。

词汇以项目 `GLOSSARY.md` 为准：主需求、基线、功能现状、人工确认、收尾。
流程规则以 `docs/srs.md` §5.5（FR-060～068）、§6.3、§7（BR-011/014/015/016/018）为准。

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

## 阶段五：验证与证据（FR-064～066、BR-014/015）

只有 `DEVELOPING` 的 PRD 能产出验证证据。每条 AC 都必须有结论：`pass`、`fail`、`blocked`、
`not-run` 或带理由的 `not-applicable`。**未执行/失败/阻塞绝不能记成通过。**

```bash
# 执行项目声明的验证命令（在项目根目录原样执行，保存原始输出与退出码）
flow-core dev verify --prd PRD-001 --suite unit --command "pytest -q" --ac AC-RES-01-01,AC-RES-01-02

# 不能由 flow-core 直接跑的外部验证：先把完整输出写到文件，再登记
flow-core dev verify record --prd PRD-001 --suite integration --result blocked \
  --ac AC-RES-01-03 --exit-code 1 --output-file ci-output.log --reason "无外部环境"

# 本环境确实不适用（必须给理由，不得以环境限制自动豁免）
flow-core dev verify --prd PRD-001 --suite e2e --ac AC-RES-02-01 \
  --not-applicable --reason "本环境无外部存储，由人工验收确认"
```

产物（都在 `06-specs/<module>/PRD-NNN-<slug>/` 下）：

| 产物 | 作用 |
| --- | --- |
| `verification/raw-<suite>-<时间戳>.log` | **原始**命令输出与 stderr、退出码，不改写（NFR-008） |
| `verification.md` | 结构化报告：front matter + 逐条 AC 表格 + 原始输出位置 + 修复轮次 + 结论 |
| `verification.json` | 机器读：`runs[]`、`acs[]`、`snapshot`、`submission`（供 lint 与验收） |

### 修复轮次（FR-065）

```bash
flow-core dev verify repair --prd PRD-001 --note "修空值分支"
```

默认上限 3 轮，可在 `.specify/constitution.md` 用 `max_repair_rounds` 覆盖；达到上限返回
`REPAIR_LIMIT_EXCEEDED`，此时**必须请人介入**（需求不明确或方向错误时不得继续自动修复）。
任一次验证 `pass` 会把轮次归零。

### 防伪造检查（BR-014/015）

```bash
flow-core dev verify guard --prd PRD-001
```

与上一次验证记录的快照对比，命中即 `success: false`：

| 错误码 | 含义 |
| --- | --- |
| `TEST_CASE_REMOVED` | 验证之后测试用例被删除 |
| `ASSERTION_WEAKENED` | 用例的断言条数减少 |
| `TEST_SKIPPED` | 新增 `@skip` / `skipif` / `xfail`，或验证命令含 `-k`/`--ignore`/`--deselect` |
| `AC_MODIFIED` | PRD 的 AC 清单在验证之后被改动（应走 CR） |

确需豁免跳过项：`--allow-skip --reason "<可审计理由>"`，理由记入 `verification.json`。

## 提交人工验收（FR-067/068）

```bash
flow-core dev submit-acceptance --prd PRD-001 --actor carol
```

- 前置：所有适用项都是 `pass` 或带理由的 `not-applicable`，否则 `VERIFICATION_INCOMPLETE`
  并列出未通过项；PRD/代码/证据变化过则 `BASELINE_STALE`（旧证据不得沿用）。
- 记录 `code_version`（Git 项目 `git:<sha>`，非 Git 项目 `src/`+`tests/` 内容摘要 `sha256:<hex>`）、
  `prd_hash`、`verification_digest` 与 AC 清单。
- 机械推进 `DEVELOPING → READY_FOR_ACCEPTANCE`（不需要人工批准），然后提醒人去验收。
- **`READY_FOR_ACCEPTANCE` 不是验收通过。** 验收结论只能由人给（见 `acceptance` Skill，BR-013）。

随时核对基线：

```bash
flow-core dev verification-status --prd PRD-001
```

`data` 里 `acs[]`（逐条结论）、`summary`、`complete`、`stale` + `stale_reasons`
（`PRD_CHANGED` / `CODE_CHANGED` / `EVIDENCE_CHANGED`）、`code_version`、`prd_hash`、
`verification_digest`、`repair_rounds`/`repair_limit`。`stale: true` 就返回
`BASELINE_STALE`，下一步是重新验证，不是继续提交。

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
| `dev verify --prd P --suite S --command "…" --ac …` | 执行验证命令并保存原始输出与 AC 结论 | `06-specs/` 下验证产物 / 否 |
| `dev verify record --prd P --suite S --result … --ac … --exit-code N --output-file F` | 登记外部已执行结果 | 同上 / 否 |
| `dev verify repair --prd P --note T` | 记一轮自动修复（受上限约束） | 同上 / 否 |
| `dev verify guard --prd P [--allow-skip --reason R]` | 防伪造检查（只读判定，写检查记录） | 同上 / 否 |
| `dev submit-acceptance --prd P [--actor A]` | 全部覆盖后提交人工验收 | `verification.*` / `DEVELOPING → READY_FOR_ACCEPTANCE` |
| `dev verification-status --prd P` | 逐条 AC 结论、代码版本与基线是否有效 | 否 / 否 |

`data` 字段：`gate`/`start` 带 `checks[]`、`binding`（`start` 另带 `prd`、`baseline`）；
`status` 带 `files`、`baseline`、`baseline_hash`、`current_hash`、`hash_matches`、
`baseline_valid`、`spec_dir`、`started`；`scope-check` 带 `scope`
（`referenced_scenarios`、`unknown_scenarios`、`referenced_acs`、`unknown_acs`、`paths`、
`path_violations`、`files`）。

## 交接给下一阶段

开始开发前把这几件事说清楚：绑定的 PRD 与版本、`content_hash`、`spec_dir`、
场景与 AC 清单、`.baseline.yaml` 的 `baseline_hash`。
进入验收时交接：Spec/Plan/Tasks 路径、`verification.md` 与原始输出目录、
`code_version`、`prd_hash`、`verification_digest`、逐条 AC 结论。
**未绑定或基线失效的 PRD 不得继续开发**（BR-011/BR-016）。
