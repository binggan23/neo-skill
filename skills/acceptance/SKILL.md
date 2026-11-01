---
name: acceptance
description: Neo Skill 人工验收阶段入口。当用户要把一份 READY_FOR_ACCEPTANCE 的 PRD 交给验收人演示、按 AC 逐条记录通过/不通过/阻塞及证据、请求验收通过确认、验收不通过退回开发或做有条件通过时使用。
---

# acceptance：人工验收

本 Skill 负责把一份**已提交人工验收**的 PRD 走到人工验收结论：Demo 材料 → 逐条 AC 记录 →
（只有全部通过才）请求确认 → 需求负责人显式确认 → 收尾同步。它**不**负责验证证据的生产
（见 `dev`）、PRD 编写与批准（见 `prd`）、需求排期（见 `requirements`）。

词汇以项目 `GLOSSARY.md` 为准：人工确认、验收人、遗留问题、功能现状、收尾。
流程规则以 `docs/srs.md` §5.6（FR-070～074）、§6.3、§7（BR-013/014/016）为准。

## 入口：必须显式绑定一个 PRD

```bash
flow-core acceptance status --prd PRD-001
```

- **必须显式给 `--prd`**。不要从「唯一 READY_FOR_ACCEPTANCE 的 PRD」推断；缺参数就是 `PRD_NOT_BOUND`。
- 前置状态：只有 `READY_FOR_ACCEPTANCE` 能准备与记录，其他状态一律 `PRD_STATE_NOT_ALLOWED`，
  且**不写任何文件、不改状态**。`ACCEPTED` 之后仍可 `status` 与 `prepare`（复核用）。

## 你（Agent）能写什么

| 允许 | 禁止 |
| --- | --- |
| 通过 `flow-core acceptance ...` 写 `07-acceptance/<module>/PRD-NNN-<slug>/` | 手改 `04-prds/`、`06-specs/` 下任何文件 |
| 写 `02-definition/modules/<module>/features/<feature>.md`（功能现状，收尾阶段） | 改 `03-requirements/`、`src/`、`tests/` |
| 调 `acceptance suggest` 记 AI 建议 | **判**验收通过：不得写 `conclusion`、不得置 `ACCEPTED` |
| 汇总证据、指出缺口、替人起草报告结构 | 替人编造身份、证据、验收时间（BR-014） |

> **只写 `07-acceptance/` 与功能现状目录。** 代码问题退回 `dev`；PRD 问题退回 `prd`（走 CR）。

## 定位 flow-core

不要硬编码绝对路径，按顺序取第一个存在的（共享设计 §8）：

1. `$NEO_SKILL_HOME` → `$NEO_SKILL_HOME/runtime/flow-core`
2. 本 skill 目录的兄弟目录 → `../neo-skill/runtime/flow-core`（安装后 `.claude/skills/` 下并列）
3. 当前 skill 自身 → `./runtime/flow-core`
4. 仓库布局 → `<repo>/skills/neo-skill/runtime/flow-core`

调用方式：`python3 <path> <命令>`。不确定时先跑 `doctor`，它在 `data.runtime.flow_core` 里报告路径。
所有命令都接受 `--root PATH`、`--json`、`--actor ID`、`--confirm`；`--json` 返回统一信封
（`success` / `command` / `artifacts` / `errors` / `next_actions`，多数命令带 `data`）。
退出码：`0` 成功（含幂等无变化）、`1` 业务失败、`2` 用法错误。**以信封与退出码为准。**

## 阶段一：准备 Demo 材料与 checklist（FR-070）

```bash
flow-core acceptance prepare --prd PRD-001
```

生成 `07-acceptance/<module>/PRD-NNN-<slug>/` 下的三份文件：

| 文件 | 内容 | 谁填 |
| --- | --- | --- |
| `demo.md` | 访问方式、启动命令、逐场景操作步骤（**待人工补齐**）、已知限制 | 人补环境信息 |
| `checklist.md` | 每条 AC 一行：`AC ID \| 场景 \| 结论 \| 证据 \| 记录人`（受管理渲染） | 只读，由命令重写 |
| `checklist.yaml` | 机器读事实来源：`items[].conclusion/evidence/recorded_by/suggestion`、`leftovers`、`decisions` | 只由命令写 |

生成器只拼接 **PRD 里已有的场景名** 与 **`verification.json` 里已有的 AC 结论**：
环境地址、账号、启动命令、具体操作步骤一律留 `<!-- TODO -->`。**不要自己编**演示环境。

准备好后对用户说清三件事：Demo 入口在哪、仍有哪几条 AC 没证据、本次验收绑定的
PRD hash 与代码版本是什么（`data.checklist` / `data.prd` 里有）。

**幂等**：重复 `prepare` 不覆盖人工已填内容；但基线（PRD hash / 代码版本 / 证据摘要）变了，
旧的人工结论会被**归档到 `superseded` 并重置为 `pending`** —— 这是 BR-016，不是 bug。

## 阶段二：人工逐条记录结论（FR-070）

```bash
flow-core acceptance record --prd PRD-001 --ac AC-RES-01-01 \
  --result pass --evidence "现场演示：100 份 3 分钟完成，截图 docs/ac/01.png" \
  --actor alice
```

- `--result` 只接受 `pass` / `fail` / `blocked`。**没记录的就是 `pending`，不是通过。**
- `--result fail|blocked` 必须给 `--evidence`（`EVIDENCE_REQUIRED`）：只写结论无法复核。
- `--actor` 必填，必须是 `00-management/team.md` 里已登记的成员（否则 `ACTOR_UNKNOWN`）。
  **验收人不必是需求负责人** —— 任何有职责的成员都能记录，只有「验收通过」这一步要求需求负责人。
- 一次一条 AC（`--ac` 不接受逗号列表）：每条都要单独核对，**不得批量填 pass**。
- `checklist.yaml` 的 `conclusion` 只能由这个命令写。人没说「通过」就不要记 pass。

### AI 只能建议（BR-013 的关键 seam）

```bash
flow-core acceptance suggest --prd PRD-001 --ac AC-RES-01-03 \
  --result blocked --reason "verification.json 里该 AC 是 blocked，缺外部环境证据"
```

- 只写 `items[].suggestion` + `suggestion_reason`，**绝不写 `conclusion`**。
- 建议与人工结论冲突时只提示（`data.suggestion_conflict: true`），**以人工结论为准**。
- 只有 suggestion、没有人记录的清单，`acceptance approve` 仍然返回 `AC_NOT_ALL_PASSED`。

## 阶段三：只有全部通过才请求确认（FR-071）

```bash
flow-core acceptance approve --prd PRD-001 --actor alice --confirm \
  --leftover "导出文件名格式下一版统一" --note "本次按 PRD v1 验收"
```

前置（任一不满足就**不进入 ACCEPTED**，且零副作用）：

| 检查 | 失败错误码 | 说明 |
| --- | --- | --- |
| `--actor` + `--confirm` | `CONFIRMATION_REQUIRED` | 缺任一都不动状态 |
| 状态 | `PRD_STATE_NOT_ALLOWED` | 必须是 `READY_FOR_ACCEPTANCE` |
| 清单存在 | `ACCEPTANCE_NOT_PREPARED` | 先跑 `acceptance prepare` |
| 职责 | `ROLE_MISMATCH` / `ACTOR_UNKNOWN` / `TEAM_NOT_CONFIGURED` | 必须是与 `gates.acceptance.approve.role` 匹配的**需求负责人** |
| 状态机 | `INVALID_TRANSITION` | `READY_FOR_ACCEPTANCE → ACCEPTED` |
| 验证基线 | `BASELINE_STALE` | PRD/代码/证据变化过 → 重新验证并再次提交验收 |
| 全部 AC | `AC_NOT_ALL_PASSED` | 列出未通过项，并给 `acceptance record` / `reject` / `conditional` 三条出路 |
| 有条件通过未处理 | `ACCEPTANCE_CONDITIONAL` | 遗留问题未处理前不能进 ACCEPTED |

成功后：

- 状态 `READY_FOR_ACCEPTANCE → ACCEPTED`，追加 `history`（`action: acceptance.approve`）与
  `confirmations`（`role: requirement_owner`）；
- 写 `report.md`（验收人、时间、PRD hash、代码版本、逐项结论与证据、遗留问题、证据摘要）；
- 写 PRD metadata 的 `acceptance` 字段（`approved_by`、`approved_at`、`prd_hash`、`code_version`、
  `checklist_digest`、`verification_digest`、`leftovers`、`checklist[]`）并保证 `closeout: null`；
- `data.next_step == "closeout"`，`next_actions` 提示「下一步：收尾同步」。

> **验收通过 ≠ 需求完成。** 本命令**不会**把主需求置 `DONE`，也不会同步功能现状/场景/索引 ——
> 那是收尾（FR-075～077）。

## 阶段四：不通过（FR-073）

```bash
flow-core acceptance reject --prd PRD-001 --actor alice --confirm \
  --reason "AC-RES-01-02 上传 100 份时中断" --leftover "需要补断点续传"
```

- `READY_FOR_ACCEPTANCE → DEVELOPING`（`history` + `confirmations` 追加，`acceptance` 清空）；
- `report.md` 记不通过结论、理由与遗留问题；
- 下一步：`dev verify` 修好 → `dev submit-acceptance` 重新提交 → 重新 `prepare` 验收。
- **`--reason` 必填**（`REASON_REQUIRED`）：不通过必须说清哪条 AC 出了什么问题。

## 阶段五：有条件通过（FR-073）

```bash
flow-core acceptance conditional --prd PRD-001 --actor alice --confirm \
  --reason "核心场景通过，导出文件名格式待统一" --leftover "导出文件名格式下一版统一"
```

- **不进入 `ACCEPTED`**：状态保持 `READY_FOR_ACCEPTANCE`，`checklist.yaml` 记 `conditional: true`
  与 `leftovers`，`report.md` 记「有条件通过」；
- 返回值 `success: true` 且 `data.conditional: true`、`data.accepted: false`
  （**成功执行了一个明确不允许验收通过的决定**；`next_actions` 给 return/re-accept 两条路）；
- `--reason` 与至少一条 `--leftover` 必填（`REASON_REQUIRED` / `LEFTOVER_REQUIRED`）；
- 之后 `approve` 一律返回 `ACCEPTANCE_CONDITIONAL`，直到遗留问题处理完、重新记录结论。

## 阶段六：收尾同步（FR-075～077，后续 `acceptance closeout`）

验收通过后的下一步是**收尾**，不是结束：

1. 更新（首次验收的新功能则创建）功能现状 `02-definition/modules/<module>/features/<feature>.md`；
2. 主需求 `IN_PRD → DONE`；
3. 场景状态改为「已验收」；
4. 重新生成索引（`backlog.md`、`feature-map.md`、`04-prds/index.md`）。

全部成功才算完成；任一步失败必须保留「已确认通过，收尾未完成」，不得报全部完成。
本阶段命令 `flow-core acceptance closeout` **在本版本尚未提供**（后续 ticket 实现）；
现在只做只读核对：

```bash
flow-core acceptance status --prd PRD-001
```

`data.next_step == "closeout"` 且 `data.stale == false` 才允许进入收尾。

## 结论失效（BR-016）

```bash
flow-core acceptance status --prd PRD-001
```

`data` 里：`items[]`（逐条 `conclusion` / `suggestion` / `evidence` / `recorded_by`）、
`all_pass`、`conditional`、`leftovers`、`acceptance`（验收记录）、`decision`、
`stale` + `stale_reasons`、`baseline_stale` + `baseline_reasons`、`prd_hash`、`code_version`、
`verification_digest`、`checklist_digest`、`next_step`。

失效原因：

| 原因 | 触发 |
| --- | --- |
| `PRD_CHANGED` | `prd.md` 或锁定字段变了（应走 CR） |
| `CODE_CHANGED` | 验收范围内的代码版本变了 |
| `EVIDENCE_CHANGED` | 原始输出被替换或删除（`verification/raw-*.log`） |
| `CHECKLIST_CHANGED` | 验收清单被越过 CLI 直接改动 |

`stale: true` → 返回 `ACCEPTANCE_STALE`，下一步是 `acceptance prepare` 并按新基线逐条重新记录，
**不是**继续 approve。

## 命令速查

| 命令 | 用途 | 写文件 / 改状态 |
| --- | --- | --- |
| `acceptance prepare --prd P` | 生成 Demo 与逐条 AC 清单 | `07-acceptance/` / 否 |
| `acceptance record --prd P --ac A --result R [--evidence E] [--note N] --actor A` | **人工**逐条记录结论 | `checklist.*` / 否 |
| `acceptance suggest --prd P --ac A --result R [--reason R]` | **AI 建议**（不写结论） | `checklist.*` / 否 |
| `acceptance approve --prd P --actor A --confirm [--note N] [--leftover L]` | 全部通过后验收通过 | `checklist.*`、`report.md`、metadata / `→ ACCEPTED` |
| `acceptance reject --prd P --actor A --confirm --reason R [--leftover L]` | 验收不通过 | 同上 / `→ DEVELOPING` |
| `acceptance conditional --prd P --actor A --confirm --reason R --leftover L` | 有条件通过 | 同上 / **状态不变** |
| `acceptance status --prd P` | 逐条状态、是否全部通过、结论是否失效 | 否 / 否 |

## 错误码速查

| 错误码 | 处理 |
| --- | --- |
| `PRD_NOT_BOUND` | 问用户要验收哪一份 PRD，不要猜 |
| `PRD_STATE_NOT_ALLOWED` | 未提交验收 / 已终态：先 `dev submit-acceptance` 或按提示处理 |
| `ACCEPTANCE_NOT_PREPARED` | 先 `acceptance prepare` |
| `AC_NOT_FOUND` | PRD 正文没有编号 AC：回 `prd` Skill 补 AC（已批准的要走 CR） |
| `UNKNOWN_AC` | 编号不存在：用清单里的编号 |
| `AC_REQUIRED` | 缺 `--ac`，或一次给了多条（必须逐条） |
| `INVALID_RESULT` | 只允许 `pass` / `fail` / `blocked` |
| `EVIDENCE_REQUIRED` | `fail`/`blocked` 必须给证据 |
| `REASON_REQUIRED` / `LEFTOVER_REQUIRED` | `reject`/`conditional` 必填理由与遗留问题 |
| `CONFIRMATION_REQUIRED` | 补 `--actor` 与 `--confirm`；**不要代替人确认** |
| `TEAM_NOT_CONFIGURED` / `ACTOR_UNKNOWN` / `ROLE_MISMATCH` | 回 `00-management/team.md` 核对成员与职责 |
| `INVALID_TRANSITION` | 当前状态不允许这个跳转 |
| `LOCK_CONFLICT` | 同一 PRD 有并发操作：等待或让持有人结束后重试 |
| `AC_NOT_ALL_PASSED` | 逐条补齐；有 fail/blocked 就走 `reject` 或 `conditional` |
| `ACCEPTANCE_CONDITIONAL` | 遗留问题未处理：重新记录结论后再请求确认 |
| `BASELINE_STALE` / `ACCEPTANCE_STALE` | 重新验证并再次提交验收 / 重新 prepare 并逐条重记 |

## 边界提醒

- 需求不明确、范围疑似扩大 → **停止**，要求 CR 或人工澄清，不得自动改 PRD/AC。
- 证据不足就是 `blocked`，不要用「应该没问题」换 `pass`；`dev verify guard` 能发现伪造。
- 人类说「验收通过」但还有 `fail`/`blocked`/`pending` → 拒绝，并把 `data.pending` 讲清楚。
- 收尾未完成时如实报告「已确认通过，收尾未完成」，例如「已确认通过」不等于「完成」。
