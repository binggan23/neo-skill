# Neo Skill P0 流程与演进指南

本目录供 AI 理解 Neo Skill 的现有闭环、定位决策依据、规划后续版本。先读本页，再按任务加载分篇。

核对基线：`ff6a747dfd079f23f0e77efb0db455c2b5f40396`。本文记录该版本的流程与设计意图；运行新版本时重新核对来源，不能把本文当成实时状态。

## 按任务阅读

| 当前任务 | 阅读入口 | 读完应能回答 |
| --- | --- | --- |
| 首次理解系统或划分新能力 | [01-architecture.md](01-architecture.md) | Skill、人工、CLI、文件分别负责什么，为什么这样分工 |
| **查某个 Skill 何时用、作用、写哪里，或要增删 Skill** | [07-skill-catalog.md](07-skill-catalog.md) | 七个 Skill 各自的触发条件、作用、写入边界、主要命令组；入口/基础分层；增删 Skill 的六步清单 |
| 接手一个项目，判断下一步调用谁 | [02-stage-flow.md](02-stage-flow.md) | 七个 Skill 何时用、读写什么、交接什么、完成标准是什么 |
| 用户问「上游做到哪了」、要查远端发布/进行中事项，或只想看本地有没有未提交/未推送 | [07-skill-catalog.md](07-skill-catalog.md) 的 `upstream-progress` 行 + [02-stage-flow.md](02-stage-flow.md) 「上游进度查询」小节 | 只读查询与阶段转移的区别；远端事实、来源、不确定性、本地提醒分别怎么报；为什么不自动写回 |
| 改状态、职责或审批流程 | [03-state-and-decisions.md](03-state-and-decisions.md) | REQ/PRD 全部声明边、人工决定与机械推进如何区分 |
| 改 hash、验证、验收或恢复 | [04-baseline-and-recovery.md](04-baseline-and-recovery.md) | 基线怎样失效，证据怎样留存，失败怎样安全恢复 |
| 讨论 P1、重构或跨 Agent 支持 | [05-next-version.md](05-next-version.md) | 怎样从真实痛点形成候选，哪些约束必须保留，怎样验证与交付 |
| 看图、修图或清理可视化产物 | [06-visualization.md](06-visualization.md) | 四张图各表示什么、哪些地方被简化、保留哪些文件及如何重建 |

顺序阅读：01 → 07 → 02 → 03 → 04 → 05；06 为配套图件索引。AI 优先读取 Markdown 与源码，避免把大型渲染 HTML 当作主要上下文。

**分层**：七个 Skill 分入口/编排类（`neo-skill`、`project`、`requirements`、`prd`、`dev`、`acceptance`）
与可复用基础类（`upstream-progress`，同时也可被用户直接调用）。入口可以调用基础 Skill，
基础 Skill 不得隐式启动无关的入口工作流；具体理由见 [07](07-skill-catalog.md) 开头的分层表。

**Skill 增删的闸门**：本目录把 [07-skill-catalog.md](07-skill-catalog.md) 当作 Skill 清单的唯一导航入口。新增、删除、改名或改变写入边界时，先改那张表并走完其中六步（SKILL.md、路由、流程、图件、验证），再算完成。

## 来源与冲突处理

| 材料 | 用途 |
| --- | --- |
| [GLOSSARY.md](../../../GLOSSARY.md) | 使用领域词汇：主需求、基线、人工确认、收尾、功能现状、受管理区块 |
| [SRS](../../srs.md) | 已确认的需求与验收标准；以 SRS-FR/BR/NFR 编号引用 |
| [HLD](../../hld.md)、[FSR](../../fsr.md) | 架构分工、取舍及可行性边界 |
| [P0 spec](../../../.scratch/neo-skill-p0-closed-loop/spec.md) 及其 issues | 本轮交付范围与历史实现拆分 |
| [flow.yaml](../../../skills/neo-skill/runtime/rules/flow.yaml)、[runtime](../../../skills/neo-skill/runtime/flow_core/) | 当前发布源规则与实际实现；项目运行时优先读取自己的 `.specify/` 基线 |
| [pilot-report.md](../../pilot-report.md) | 已记录的安装、试点证据与兼容性边界 |

需求与实现不一致时，记录“应有行为 / 实际行为 / 证据 / 待决定事项”，由问题单或 ADR 处理。图和本目录的摘要不能反过来更改需求，也不能把实现缺口描述成已批准设计。

## 两种流程要区分

这里主要描述**用户安装 Neo Skill 后如何推进项目**。开发本仓库时使用的 `implement-spec`、`code-review`、`fix`、PR 与 CI 是**仓库工程流程**；它们没有因为本次开发使用过，就成为 Neo Skill 自动提供的产品功能。工程交付注意事项见 [05-next-version.md](05-next-version.md)。
