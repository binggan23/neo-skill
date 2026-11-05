# Skill 调用时机与作用（注册表）

阅读条件：判断该调用哪个 Skill、新增/删除/改名 Skill、或检查某个 Skill 的写入边界。

本文件是 Skill 的**注册表**：一张表回答「什么时候用它、它做什么、它写哪里、交给谁」。触发条件的权威原文在各自 `skills/<name>/SKILL.md` 的 `description`；本表只做导航，冲突时以 SKILL.md 与 [flow.yaml](../../../skills/neo-skill/runtime/rules/flow.yaml) 为准。阶段之间的先后与完成标准见 [02-stage-flow.md](02-stage-flow.md)，不要在本表复制流程细节。

## 已实现的 6 个 Skill

| Skill | 调用时机（触发词） | 作用 | 写入边界 | 主要命令组 | 交给谁 |
| --- | --- | --- | --- | --- | --- |
| [`neo-skill`](../../../skills/neo-skill/SKILL.md) | 不知道该找哪个 Skill；首次接触；要查安装/升级、命令索引、人工确认协议 | 路由层：安装升级说明、`flow-core` 路径解析、命令总索引、人工确认协议，把意图分派到阶段 Skill | 不写业务文件 | —（不绑定命令组） | 下列任一阶段 Skill |
| [`project`](../../../skills/project/SKILL.md) | 首次接入项目；补齐骨架/规则基线；诊断环境；裁剪目录；团队与 constitution 待确认；修初始化冲突；重跑初始化 | 环境诊断与初始化：建完整骨架、`.specify/` 基线、团队摘要确认、项目约束确认、冲突计划 | 骨架目录、`.specify/`、`00-management/team.md`、`AGENTS.md` 受管理区块；不写 `~/.claude/` 等全局位置 | `doctor`、`init`（含 `--prune`、`--apply-conflicts`） | `requirements` |
| [`requirements`](../../../skills/requirements/SKILL.md) | 用户提新需求/抱怨/优化点；继续未完成草稿；分类与优先级；问「要不要排期」；需求负责人确认排期 | 需求对齐与人工排期：草稿、grilling（设计树/frontier/共识摘要）、查重提示、分类、优先级、人工排期 | 只写 `03-requirements/` | `req`（create/update/show/list/candidates/align/classify/set-priority/triage-prompt/confirm/plan/unplan/defer/reject） | `prd` |
| [`prd`](../../../skills/prd/SKILL.md) | 把已排期主需求写成 PRD；改草稿；自审；提交评审；请需求负责人批准；怀疑 hash 失效；要改已批准内容 | PRD 全周期：草稿、grilling、结构自审、提交评审、人工批准并锁 hash、CR 变更与重新批准 | 只写 `04-prds/` | `prd`（create/update/validate/self-review/submit-review/approve/reject/request-changes/verify-hash/baseline-status + `cr` 子组） | `dev` |
| [`dev`](../../../skills/dev/SKILL.md) | 「开始开发」「按 PRD 干活」「继续上次开发」；建 Spec/Plan/Tasks；跑验证留证据；查基线是否有效；怀疑超出 PRD 范围 | 从有效 PRD 启动开发：十项门禁、Spec/Plan/Tasks、范围检查、验证证据、防伪造检查、机械提交验收 | 只写 `06-specs/`、`src/`、`tests/` | `dev`（gate/start/status/scope-check/verify/verification-status/submit-acceptance） | `acceptance` |
| [`acceptance`](../../../skills/acceptance/SKILL.md) | PRD 已 `READY_FOR_ACCEPTANCE`，要演示、逐条核对、下验收结论；已确认通过后同步功能现状与索引 | 人工验收与收尾：Demo 材料、逐条 AC checklist、人工结论、通过/不通过/有条件通过、收尾事务 | 只写 `07-acceptance/` 与功能现状目录 | `acceptance`（prepare/record/suggest/approve/reject/conditional/status/closeout/closeout-status）、`recover`、`index` | 收尾完成 → 主需求 `DONE` |

## 图中的 Skill 覆盖范围

四张流程/状态图（见 [06-visualization.md](06-visualization.md)）只把**四个阶段 Skill** 画成节点：`requirements`、`prd`、`dev`、`acceptance`。

| Skill | 图里是否出现 | 说明 |
| --- | --- | --- |
| `requirements`、`prd`、`dev`、`acceptance` | 是，节点标注「X Skill」 | 各阶段主驱动者 |
| `project` | 否 | 图从「已初始化」开始；`doctor`/`init` 与团队、constitution 确认在阶段 0，见 [02](02-stage-flow.md) |
| `neo-skill` | 否 | 路由层不是流程节点；它决定「该进哪个阶段」 |

读图时不要据此推断 `project` 或 `neo-skill` 不存在。

## 规划中但**不是** Skill 的部分

新初始化项目的骨架里有 `01-discovery/`、`05-design/`、`08-releases/`、`09-growth/` 目录，这是占位骨架，**没有**对应的 `SKILL.md`，也没有命令组。它们属于后续版本候选（见 [05-next-version.md](05-next-version.md)），不能当成可用能力。

判断一个阶段是否真的可用：`skills/<name>/SKILL.md` 存在 且 `flow-core <组> --help` 有对应命令组。缺任一项就是未实现。

## 增删 Skill 必须经过这里

本文件是 Skill 清单的唯一导航入口。新增、删除、改名或改变写入边界时，按顺序做完再算完成：

1. **先改本表**：写清触发时机、作用、写入边界、主要命令组、交接对象。没有对应命令组的要写明「尚未实现」。
2. **再改 SKILL.md**：`name` 与 `description` 才是真正的触发条件；description 必须让 Agent 在该触发时可靠地选中它，并列出不同分支。写这类文档的规范见已安装的 `writing-for-agents` Skill（不在本仓库内，避免引用外部绝对路径）。
3. **同步路由**：`skills/neo-skill/SKILL.md` 的阶段表与命令索引要能指向新 Skill。
4. **同步流程**：[02-stage-flow.md](02-stage-flow.md) 的阶段顺序与交接；若改了状态或职责，另改 [03](03-state-and-decisions.md)。
5. **同步图件**：需要时按 [06](06-visualization.md) 重画并重新取校验回执，不要沿用旧回执。
6. **验证**：`./scripts/install-sim.sh` 必须仍能发现全部 Skill（它按 `name`+`description` 判断）；CI 会跑这条。

删除 Skill 同样走这六步：先在本表标为移除并写清原因与替代路径，再删 SKILL.md、清路由、清命令引用，避免留下指向不存在文件的说明。
