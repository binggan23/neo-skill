# Skill 调用时机与作用（注册表）

阅读条件：判断该调用哪个 Skill、新增/删除/改名 Skill、或检查某个 Skill 的写入边界。

本文件是 Skill 的**注册表**：一张表回答「什么时候用它、它做什么、它写哪里、交给谁」。触发条件的权威原文在各自 `skills/<name>/SKILL.md` 的 `description`；本表只做导航，冲突时以 SKILL.md 与 [flow.yaml](../../../skills/neo-skill/runtime/rules/flow.yaml) 为准。阶段之间的先后与完成标准见 [02-stage-flow.md](02-stage-flow.md)，不要在本表复制流程细节。

## 两个层级：入口/编排 vs 可复用基础

Skill 按**职责**分两层（SRS-FR-090）。分层看的是「谁触发它」，不是「它有多重要」：

| 层 | 触发方式 | 可以做什么 | 不可以做什么 |
| --- | --- | --- | --- |
| **入口/编排类** | 用户直接调用，或路由层按阶段意图分派 | 编排多步工作流；**可以**调用基础 Skill 里现成的纪律与命令 | 把无关流程堆进同一个 Skill（「顺手」写另一阶段的产物） |
| **可复用基础类** | 需要那条纪律时被调用（用户或别的 Skill 都行） | 单一、可复用的工作纪律；独立触发时也只做自己那件事 | **不得隐式启动无关的入口工作流**（被借用 ≠ 可以推进阶段状态） |

**一句话边界：入口可以调用基础 Skill，基础 Skill 不得隐式启动无关的入口工作流。**
具体来说：`dev` 借用「远端事实才是进度来源」这条纪律是允许的；`upstream-progress`
不会因为被借用就把 PRD 推到 `DEVELOPING`，也不会替你跑 `dev gate`。

本版本各 Skill 的归属（如实反映现状，不留灰色地带）：

| Skill | 层 | 理由 |
| --- | --- | --- |
| `neo-skill` | 入口/编排 | 路由层：识别意图并分派到阶段 Skill，本身不干活 |
| `project` | 入口/编排 | 阶段 0 的多步接入流程（诊断 → 初始化 → 团队/约束 → 冲突 → 裁剪 → 绑定） |
| `requirements` | 入口/编排 | 阶段 1：草稿 → grilling → 共识 → 排期 |
| `prd` | 入口/编排 | 阶段 2：草稿 → 自审 → 评审 → 批准 → CR |
| `dev` | 入口/编排 | 阶段 3：门禁 → Spec/Plan/Tasks → 验证 → 提交验收 |
| `acceptance` | 入口/编排 | 阶段 4：Demo → 逐条 AC → 结论 → 收尾 |
| `upstream-progress` | **两层兼有** | 用户直接问「上游做到哪了」时它是入口（走完整查询并交接）；同时它承载一条可复用纪律——「远端证据才是进度来源、本地提醒单列、不写回」——别的 Skill 判断事实时可以直接借，借的时候不启动任何阶段工作流 |

`upstream-progress` 之所以不硬塞进某一层：把它只算入口，会让别的 Skill 无法复用这条纪律、
只好各写一遍（SRS-FR-090 要避免的重复）；只算基础，又会藏掉「用户可以直接问进度」这个
真实触发（SRS-FR-092）。所以两层兼有，并且两个方向的行为都在它的 `SKILL.md` 里写明。

## 已实现的 7 个 Skill

| Skill | 调用时机（触发词） | 作用 | 写入边界 | 主要命令组 | 交给谁 |
| --- | --- | --- | --- | --- | --- |
| [`neo-skill`](../../../skills/neo-skill/SKILL.md) | 不知道该找哪个 Skill；首次接触；要查安装/升级、命令索引、人工确认协议 | 路由层：安装升级说明、`flow-core` 路径解析、命令总索引、人工确认协议，把意图分派到阶段 Skill | 不写业务文件 | —（不绑定命令组） | 下列任一阶段 Skill |
| [`project`](../../../skills/project/SKILL.md) | 首次接入项目；补齐骨架/规则基线；诊断环境；裁剪目录；团队与 constitution 待确认；修初始化冲突；重跑初始化 | 环境诊断与初始化：建完整骨架、`.specify/` 基线、团队摘要确认、项目约束确认、冲突计划 | 骨架目录、`.specify/`、`00-management/team.md`、`AGENTS.md` 受管理区块；不写 `~/.claude/` 等全局位置 | `doctor`、`init`（含 `--prune`、`--apply-conflicts`） | `requirements` |
| [`requirements`](../../../skills/requirements/SKILL.md) | 用户提新需求/抱怨/优化点；继续未完成草稿；分类与优先级；问「要不要排期」；需求负责人确认排期 | 需求对齐与人工排期：草稿、grilling（设计树/frontier/共识摘要）、查重提示、分类、优先级、人工排期 | 只写 `03-requirements/` | `req`（create/update/show/list/candidates/align/classify/set-priority/triage-prompt/confirm/plan/unplan/defer/reject） | `prd` |
| [`prd`](../../../skills/prd/SKILL.md) | 把已排期主需求写成 PRD；改草稿；自审；提交评审；请需求负责人批准；怀疑 hash 失效；要改已批准内容 | PRD 全周期：草稿、grilling、结构自审、提交评审、人工批准并锁 hash、CR 变更与重新批准 | 只写 `04-prds/` | `prd`（create/update/validate/self-review/submit-review/approve/reject/request-changes/verify-hash/baseline-status + `cr` 子组） | `dev` |
| [`dev`](../../../skills/dev/SKILL.md) | 「开始开发」「按 PRD 干活」「继续上次开发」；建 Spec/Plan/Tasks；跑验证留证据；查基线是否有效；怀疑超出 PRD 范围 | 从有效 PRD 启动开发：十项门禁、Spec/Plan/Tasks、范围检查、验证证据、防伪造检查、机械提交验收 | 只写 `06-specs/`、`src/`、`tests/` | `dev`（gate/start/status/scope-check/verify/verification-status/submit-acceptance） | `acceptance` |
| [`acceptance`](../../../skills/acceptance/SKILL.md) | PRD 已 `READY_FOR_ACCEPTANCE`，要演示、逐条核对、下验收结论；已确认通过后同步功能现状与索引 | 人工验收与收尾：Demo 材料、逐条 AC checklist、人工结论、通过/不通过/有条件通过、收尾事务 | 只写 `07-acceptance/` 与功能现状目录 | `acceptance`（prepare/record/suggest/approve/reject/conditional/status/closeout/closeout-status）、`recover`、`index` | 收尾完成 → 主需求 `DONE` |
| [`upstream-progress`](../../../skills/upstream-progress/SKILL.md) | 问「上游做到哪了/最新发布了什么」；核对远端发布与进行中事项；无绑定时临时给 URL 查一次；来源打不开或过期；只想看本地有没有未提交/未推送 | 上游进度只读查询：读绑定定位远端 → 抓远端资料 → 分已发布/进行中报事实、列来源、说不确定性 → 本地提醒单列 | **不写任何文件**（只读）；绑定区块只读，不读本地代码/业务文档/issue 草稿 | `upstream`（show/status，只读）、`progress`（report）；写绑定用 `upstream bind`，归 `project` 阶段七执行 | 想开需求 → `requirements`；想长期跟踪 → `project` 阶段七；只想看进度 → 到此为止 |

## 图中的 Skill 覆盖范围

四张流程/状态图（见 [06-visualization.md](06-visualization.md)）只把**四个阶段 Skill** 画成节点：`requirements`、`prd`、`dev`、`acceptance`。

| Skill | 图里是否出现 | 说明 |
| --- | --- | --- |
| `requirements`、`prd`、`dev`、`acceptance` | 是，节点标注「X Skill」 | 各阶段主驱动者 |
| `project` | 否 | 图从「已初始化」开始；`doctor`/`init` 与团队、constitution 确认在阶段 0，见 [02](02-stage-flow.md) |
| `neo-skill` | 否 | 路由层不是流程节点；它决定「该进哪个阶段」 |
| `upstream-progress` | 否 | 只读查询，不是阶段转移；它可以在任意时刻被调用而不改任何状态 |

读图时不要据此推断 `project` 或 `neo-skill` 不存在。

## 规划中但**不是** Skill 的部分

新初始化项目的骨架里有 `01-discovery/`、`05-design/`、`08-releases/`、`09-growth/` 目录，这是占位骨架，**没有**对应的 `SKILL.md`，也没有命令组。它们属于后续版本候选（见 [05-next-version.md](05-next-version.md)），不能当成可用能力。

判断一个阶段是否真的可用：`skills/<name>/SKILL.md` 存在 且 `flow-core <组> --help` 有对应命令组。缺任一项就是未实现。

## 这些名字**不是**本版本的能力

`implement` / `grill-me` / `tdd` / `code-review` 等只是外部示例或本仓库自身的工程流程
（开发这个仓库时用的 `implement-spec`、`code-review`、`fix`、PR、CI 也一样）。
它们**不是** v0.1.1 的产品必需能力，也不由 `flow-core` 自动执行；v0.1.1 的验收契约是
**职责分离与可组合**（SRS-FR-090），不是某个固定名字的 Skill 清单。
两种流程的区别见 [Contents.md](Contents.md) 的「两种流程要区分」一节。

## 增删 Skill 必须经过这里

本文件是 Skill 清单的唯一导航入口。新增、删除、改名或改变写入边界时，按顺序做完再算完成：

1. **先改本表**：写清触发时机、作用、写入边界、主要命令组、交接对象，以及它属于入口层还是基础层。没有对应命令组的要写明「尚未实现」。
2. **再改 SKILL.md**：`name` 与 `description` 才是真正的触发条件；description 必须让 Agent 在该触发时可靠地选中它，并列出不同分支。还要在开头一句写明「本 Skill 负责…；它**不**负责…」，以及它属于入口层还是基础层（见本文件开头的分层表）。写这类文档的规范见已安装的 `writing-for-agents` Skill（不在本仓库内，避免引用外部绝对路径）。
3. **同步路由**：`skills/neo-skill/SKILL.md` 的阶段表与命令索引要能指向新 Skill。
4. **同步流程**：[02-stage-flow.md](02-stage-flow.md) 的阶段顺序与交接；若改了状态或职责，另改 [03](03-state-and-decisions.md)。
5. **同步图件**：需要时按 [06](06-visualization.md) 重画并重新取校验回执，不要沿用旧回执。
6. **验证**：`./scripts/install-sim.sh` 必须仍能发现全部 Skill。它的发现是**自动派生**的——`skills/` 下每个含 `SKILL.md` 的目录就是一个 Skill，逐个校验 frontmatter 的 `name`（须等于目录名）与 `description` 非空，并要求源目录与安装目标的 Skill 集合完全一致；`neo-skill` 及其 `runtime/` 必须存在。脚本里还留了一条**必需底线**（`REQUIRED_SKILLS`，当前核心七个），它只是防「源与目标一致地少一个核心能力」的静默缺口，不是完整清单：**新增 Skill 自动发现、不用改脚本；删除或改名核心 Skill 才需同步那一行与本表第 1 步**。CI 会跑这条。

删除 Skill 同样走这六步：先在本表标为移除并写清原因与替代路径，再删 SKILL.md、清路由、清命令引用，避免留下指向不存在文件的说明。
