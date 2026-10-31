# AI 产品研发流程与 Skill 体系

- **适用范围**：所有从 0 到 1 的产品项目
- **目标**：统一目录、统一流程、统一规则，让每个人都能按流程与 AI 协作，从立项一路走到验收发布和持续迭代
- **核心原则**：PRD 驱动、AI 执行、人工把关

## 目录

1. 总体设计
2. 项目目录结构
3. 编号与命名规范
4. 全流程
5. 功能迭代与变更
6. 状态机
7. 硬规则
8. 角色与职责
9. Skill 体系
10. 落地路线

---

## 一、总体设计

### 1.1 三个层级

| 层级 | 说明 | 示例 |
| --- | --- | --- |
| 项目 | 一个独立产品，一个仓库 | 职引 AI |
| 模块（Module） | 相对稳定的业务领域，作为目录的分组 | 简历、面试、岗位匹配、账号 |
| 功能（Feature） | 模块下的一项产品能力 | 简历解析、简历优化 |
| 场景（Scenario） | 功能下最小的可验收单元，验收标准挂在场景上 | 上传 PDF 简历、解析失败兜底 |

### 1.2 两类文档

| 类型 | 特点 | 存放位置 |
| --- | --- | --- |
| 现状文档（当前事实） | 描述产品现在是什么样，持续更新 | `02-definition/modules/<module>/features/` |
| 变更文档（PRD） | 描述一次改了什么，批准后锁定 | `04-prds/<module>/PRD-xxx/` |

PRD 验收通过后，把改动合并进现状文档。新人和 AI 了解产品时读现状文档，追溯某个功能为什么变成现在这样时读 PRD。

### 1.3 目录设计原则

- **顶层按阶段，第二层按模块**：按顶层目录就能划分 skill 的写入权限，脚本也只需要匹配 `*/PRD-*`。
- **用 PRD 编号串起各阶段**：`04-prds`、`06-specs`、`07-acceptance` 三处路径一致，都是 `<module>/PRD-xxx-<slug>`。
- **区分单文件与按模块拆分**：整个项目只有一份的文档用单文件（立项书、愿景、北极星指标），会随功能增多的文档按模块拆分（需求、旅程、PRD、设计、技术方案、验收）。
- **总表由脚本生成**：`backlog.md`、`feature-map.md`、`04-prds/index.md` 都从元数据生成，不手写。
- **目录名用英文**，保证脚本和 AI 能稳定引用；文档名中英文都可以。
- **废弃不删除**：改状态或移到 `archive/`。

---

## 二、项目目录结构

```text
<project>/
├── README.md                                 # 项目卡片：定位、当前阶段(G0~G4)、当前版本、负责人、导航
├── AGENTS.md                                 # AI 工作流、硬规则、目录约定、各 skill 职责
├── .specify/
│    ├── constitution.md                      # 项目规范：技术栈、编码规范、安全合规、架构约束
│    ├── templates/                           # 全部文档模板
│    └── scripts/                             # 流程脚本（见 9.4）
│
├── 00-management/                            # ── 项目管理（贯穿全程）──
│    ├── charter.md                           # 立项书
│    ├── roadmap.md                           # 版本路线图
│    ├── team.md                              # 角色、职责、联系人
│    ├── risks.md                             # 风险与问题清单
│    ├── decisions/                           # DEC-001-<slug>.md  关口结论、重大取舍
│    ├── meetings/                            # YYYY-MM-DD-<主题>.md
│    └── reports/                             # YYYY-Www.md        周报
│
├── 01-discovery/                             # ── 需求分析：问题是否真实、值得解决 ──
│    ├── opportunity.md                       # 机会描述：问题、目标用户、为什么是现在
│    ├── market/                              # 市场规模、行业趋势
│    ├── competitors/                         # <竞品>.md + summary.md
│    ├── users/
│    │    ├── personas/                       # <画像>.md
│    │    ├── interviews/                     # YYYY-MM-DD-<受访者>.md
│    │    └── surveys/                        # 问卷与结果
│    ├── hypotheses.md                        # 核心假设：假设 → 验证方式 → 证据 → 结论
│    ├── experiments/                         # EXP-001-<slug>.md 落地页、假门测试、手工 MVP
│    └── insights.md                          # 调研结论汇总（G1 评审的输入）
│
├── 02-definition/                            # ── 产品定义：做什么、不做什么 ──
│    ├── vision.md                            # 愿景、定位、价值主张
│    ├── business-model.md                    # 商业模式（精益画布）
│    ├── metrics.md                           # 北极星指标 + 指标体系
│    ├── mvp-scope.md                         # MVP 范围：必须做 / 不做 / 以后做
│    ├── glossary.md                          # 术语表（统一人和 AI 的用词）
│    ├── feature-map.md                       # 功能地图：模块/功能/场景总表（脚本生成）
│    └── modules/
│         └── <module>/
│                 ├── README.md               # 模块定位、指标、场景清单(SC-xxx)
│                 ├── journeys/               # <旅程>.md 用户旅程
│                 └── features/               # <功能>.md 功能现状文档（验收后持续更新）
│
├── 03-requirements/                          # ── 需求池 ──
│    ├── backlog.md                           # 需求总览（脚本生成）
│    ├── items/                               # REQ-001-<slug>.md 每条需求一个文件
│    └── feedback/                            # YYYY-MM/<来源>.md 用户反馈原始记录
│
├── 04-prds/                                  # ── PRD ──
│    ├── index.md                             # PRD 总览（脚本生成）
│    └── <module>/PRD-001-<slug>/
│         ├── metadata.yaml                   # 状态、版本、模块、场景、来源需求、批准人、hash
│         ├── prd.md                          # 按场景组织的 PRD 正文
│         ├── test-cases.md                   # 测试用例（可选）
│         ├── reviews/                        # YYYY-MM-DD-review.md 评审记录
│         └── changes/                        # CR-001-<slug>.md     变更记录
│
├── 05-design/                                # ── 设计 ──
│    ├── design-system.md                     # 设计规范
│    ├── assets/                              # 公共素材
│    └── <module>/<feature>/                  # 原型、线框图、视觉稿
│
├── 06-specs/                                 # ── 技术方案（AI 开发）──
│    ├── architecture.md                      # 整体架构
│    ├── data-model.md                        # 数据模型
│    ├── api/                                 # 接口文档
│    ├── adr/                                 # ADR-001-<slug>.md 技术决策记录
│    └── <module>/PRD-001-<slug>/
│         ├── spec.md                         # 技术规格
│         ├── plan.md                         # 实施计划
│         ├── tasks.md                        # 任务拆解
│         └── verification.md                 # 自动验证报告
│
├── 07-acceptance/                            # ── Demo 与验收 ──
│    └── <module>/PRD-001-<slug>/
│         ├── demo.md                         # 演示方式：地址、账号、操作步骤、录屏
│         ├── checklist.md                    # 按验收标准逐条核对
│         └── report.md                       # 验收结论、遗留问题
│
├── 08-releases/                              # ── 版本 ──
│    ├── CHANGELOG.md
│    └── v0.1.0/
│         ├── plan.md                         # 版本计划：目标、包含哪些 PRD、时间节点
│         ├── demo.md                         # 版本整体演示（迭代评审会）
│         ├── launch-checklist.md             # 上线检查清单
│         └── release-notes.md                # 发布说明
│
├── 09-growth/                                # ── 上线后运营 ──
│    ├── dashboards.md                        # 看板链接、指标口径
│    ├── analysis/<module>/                   # 数据分析报告
│    ├── experiments/                         # AB-001-<slug>.md      A/B 实验
│    └── retros/                              # v0.1.0-retro.md       版本复盘、阶段复盘
│
├── src/                                      # 源代码
├── tests/                                    # 测试代码
└── archive/                                  # 废弃资料（只归档，不删除）
```

### 2.1 metadata.yaml 示例

```yaml
id: PRD-027
slug: batch-parse
title: 简历批量解析
module: resume
feature: resume-parse
scenarios: [SC-RES-07, SC-RES-08]
related_modules: []
source_reqs: [REQ-023]
release: v0.3.0
status: APPROVED
version: 1.1
owner: 张三
reviewers: [李四, 王五]
approved_by: 李四
approved_at: 2026-10-30
content_hash: sha256:...                 # 批准时锁定，内容一改门禁就失效
history:
  - { at: 2026-10-25, from: DRAFT, to: IN_REVIEW, by: 张三 }
  - { at: 2026-10-30, from: IN_REVIEW, to: APPROVED, by: 李四 }
```

### 2.2 裁剪规则

小项目可以裁掉 `01-discovery/experiments`、`05-design`、`09-growth/experiments` 等目录，但以下内容不能省：

- `00-management/charter.md`
- `02-definition/mvp-scope.md`
- `04-prds/` 的状态机与门禁
- `07-acceptance/` 的人工验收

---

## 三、编号与命名规范

| 对象 | 格式 | 唯一范围 | 示例 |
| --- | --- | --- | --- |
| 决策 | `DEC-001` | 项目内 | `DEC-002-mvp-scope.md` |
| 实验 | `EXP-001` / `AB-001` | 项目内 | `EXP-001-landing-page.md` |
| 需求 | `REQ-001` | 项目内 | `REQ-023-batch-parse.md` |
| 场景 | `SC-<模块缩写>-01` | 模块内 | `SC-RES-07` |
| PRD | `PRD-001` | 项目内 | `PRD-027-batch-parse/` |
| 验收标准 | `AC-<模块缩写>-<场景号>-01` | 跟随场景 | `AC-RES-07-01` |
| 变更 | `CR-001` | PRD 内 | `CR-001-add-docx.md` |
| 技术决策 | `ADR-001` | 项目内 | `ADR-003-use-postgres.md` |
| 版本 | `vMAJOR.MINOR.PATCH` | 项目内 | `v0.3.0` |

- 编号只增不复用，废弃的编号也保留。
- 编号是唯一键，slug 创建后不改。
- 按时间累积的文档用日期前缀：`YYYY-MM-DD-<主题>.md`。

---

## 四、全流程

### 4.1 全景图

```text
                阶段（只走一次）──────────┐ ┌──────────── 迭代循环（每个版本走一次）────────────┐
  ┌────────── 0→1
  立项 ──G0──▶ 需求分析 ──G1──▶ 产品定义 ──G2──▶ 版本规划 ─▶ PRD ─▶ 开发 ─▶ Demo/验收 ─▶ 发布 ──G3──▶ 运营
                                                              ▲                                                        │
                                                              └────────────   反馈 / 数据 / 新需求 ─────────────────┘
                                                                                            阶段性复盘 ──G4── 继续/转向/停止
```

### 4.2 关口

| 关口 | 时机 | 通过条件 | 决策人 | 记录 |
| --- | --- | --- | --- | --- |
| G0 立项评审 | 立项后 | 目标、范围、资源、时间盒明确 | 项目负责人 | `DEC-xxx` |
| G1 问题验证 | 需求分析后 | 核心假设有证据支持 | 需求负责人 | `DEC-xxx` |
| G2 MVP 范围评审 | 产品定义后 | 范围、指标、路线图确认 | 需求负责人 | `DEC-xxx` |
| G3 上线评审 | 每次发布前 | 版本内 PRD 全部 ACCEPTED，上线清单全部完成 | 项目负责人 | `DEC-xxx` |
| G4 PMF 判断 | 每 2~3 个版本或每季度 | 根据北极星指标决定继续、转向还是停止 | 项目负责人 | `DEC-xxx` |

### 4.3 阶段 0：立项

| 项 | 内容 |
| --- | --- |
| 目标 | 确认这件事值得投入资源去探索 |
| 动作 | 描述机会（问题、用户、时机）；明确目标、范围、资源、预算、时间盒；组建团队；识别主要风险 |
| 产出 | `01-discovery/opportunity.md`、`00-management/charter.md`、`team.md`、`risks.md` |
| 关口 | G0 |
| AI 做什么 | 根据一句话想法起草立项书，追问缺失的信息 |

### 4.4 阶段 1：需求分析

| 项 | 内容 |
| --- | --- |
| 目标 | 用证据证明问题真实存在，用户愿意为解决方案付出时间或金钱 |
| 动作 | 市场和竞品分析；列出核心假设；用户访谈（建议 ≥ 5 人）；做低成本验证实验 |
| 产出 | `market/`、`competitors/`、`users/`、`hypotheses.md`、`experiments/`、`insights.md` |
| 关口 | G1：结论是继续、调整方向或终止 |
| AI 做什么 | 整理竞品、生成访谈提纲、汇总访谈记录并提炼洞察。不得编造调研数据 |

### 4.5 阶段 2：产品定义

| 项 | 内容 |
| --- | --- |
| 目标 | 确定做什么、先做多少、怎么衡量成功 |
| 动作 | 写愿景和价值主张、商业模式；拆分模块并列出场景清单；画核心用户旅程；划定 MVP 范围；定北极星指标；初版路线图 |
| 产出 | `vision.md`、`business-model.md`、`modules/*/README.md`、`journeys/`、`mvp-scope.md`、`metrics.md`、`glossary.md`、`roadmap.md`；初始需求写入 `03-requirements/items/` |
| 关口 | G2 |
| AI 做什么 | 根据调研结论拆分模块和场景、建议 MVP 范围、批量生成初始需求条目 |

### 4.6 阶段 3：版本规划（每个版本开始时）

| 项 | 内容 |
| --- | --- |
| 目标 | 决定这个版本做哪些需求 |
| 动作 | 需求分类、去重、打优先级（RICE / MoSCoW）；从 backlog 选需求进入版本；按功能合并成若干 PRD 的规划 |
| 产出 | `08-releases/vX.Y.Z/plan.md`；选中的需求状态改为 PLANNED |
| 决策 | 需求负责人确认版本范围 |
| 规则 | 版本周期建议 2~4 周；版本计划确定后，新增内容必须走变更 |

### 4.7 阶段 4：PRD

| 项 | 内容 |
| --- | --- |
| 目标 | 把一个功能的需求写成可开发、可验收的规格 |
| 动作 | 读取模块 README、功能现状文档、术语表 → 生成 PRD 草稿 → AI 按清单自审 → 人工评审 → 修改 → 批准；同步出设计稿；AI 需要时生成测试用例 |
| 产出 | `04-prds/<module>/PRD-xxx/`、`05-design/<module>/<feature>/` |
| 状态 | DRAFT → IN_REVIEW → (CHANGES_REQUESTED →) APPROVED |
| 规则 | 一个 PRD 只覆盖一个功能，场景不超过 8 个左右；只有人能批准；批准时锁定内容 hash |

**PRD 结构**

1. 背景与目标（含要改善的指标）
2. 关联需求与场景
3. 范围：本期做 / 本期不做
4. 现状（引用功能现状文档）与本次变化
5. 按场景展开：用户故事 → 交互流程 → 验收标准（Given/When/Then，编号 AC-xxx）
6. 非功能需求：性能、安全、兼容性
7. 埋点与数据需求
8. 依赖与风险
9. 待确认问题

**AI 自审清单**

- 章节完整，"本期不做"已经写明
- 每个场景至少有一条验收标准，每条验收标准都能测
- 没有"快速""友好""适当""尽量"这类说不清的词
- 术语与 `glossary.md` 一致
- 与功能现状文档、其他 PRD 没有冲突

### 4.8 阶段 5：开发（Spec 驱动）

| 项 | 内容 |
| --- | --- |
| 前置 | 门禁检查通过：PRD 是 APPROVED 且 hash 没变 |
| 动作 | 生成 spec → plan → 拆 tasks → 编码 → 单元测试、集成测试、静态检查 → 失败自动修复 → 生成验证报告；重大技术取舍写 ADR |
| 产出 | `06-specs/<module>/PRD-xxx/`、`src/`、`tests/` |
| 状态 | PRD 改为 DEVELOPING，完成后改为 READY_FOR_ACCEPTANCE |
| 规则 | 不得超出 PRD 范围；发现 PRD 有问题时停下来提变更，不能自行改需求 |

### 4.9 阶段 6：Demo 与验收

| 项 | 内容 |
| --- | --- |
| 动作 | 部署可运行的 Demo → 按 AC 编号逐条生成验收清单 → 人工操作验收 → 结论为通过、有条件通过或不通过 |
| 产出 | `07-acceptance/<module>/PRD-xxx/` 下的 `demo.md`、`checklist.md`、`report.md` |
| 状态 | 通过：READY_FOR_ACCEPTANCE → ACCEPTED；不通过：退回 DEVELOPING |
| 收尾 | ① 把改动合并进 `modules/<module>/features/<feature>.md`；② 对应需求改为 DONE；③ 更新功能地图中的场景状态 |

**两级 Demo**

- 功能级：每个 PRD 一个，用于验收。
- 版本级：`08-releases/vX/demo.md`，在迭代评审会上演示整个版本。

### 4.10 阶段 7：发布

| 项 | 内容 |
| --- | --- |
| 前置 | 版本内所有 PRD 都是 ACCEPTED |
| 动作 | 过上线检查清单（数据迁移、监控告警、回滚方案、埋点、隐私合规）→ 发布 → 写发布说明 |
| 产出 | `launch-checklist.md`、`release-notes.md`、`CHANGELOG.md` |
| 关口 | G3 |
| 状态 | 版本内的 PRD 改为 RELEASED，场景改为"已上线" |

### 4.11 阶段 8：运营与迭代

| 项 | 内容 |
| --- | --- |
| 动作 | 看数据、收集用户反馈 → 整理成新需求进入 backlog → 版本复盘 → 进入下一版本的规划 |
| 产出 | `09-growth/analysis/`、`experiments/`、`retros/`；新的 `REQ-xxx` |
| 关口 | G4 |

---

## 五、功能迭代与变更

```text
新的想法、反馈或问题
      │
      ├─ 是 bug（现有行为与功能现状文档不符）──▶ 建缺陷类需求 → 修复 → 回归验收（小改动不需要写 PRD）
      │
      ├─ 要改的 PRD 还没上线（DRAFT ~ ACCEPTED 之间）
      │   ├─ 仍在原范围内 ──▶ 提变更 CR → 影响分析 → 状态退回 IN_REVIEW → 重新批准
      │   └─ 超出原范围 ──▶ 新建需求，排入后续版本
      │
      └─ 改已上线的功能 ──▶ 新建需求 → 新建 PRD（以功能现状文档为基线，写清改了什么）
                        → 走完整流程 → 验收后更新功能现状文档
```

**变更请求（CR）必须包含**：变更内容、原因、影响分析（影响哪些 spec、tasks、test-cases、设计稿、版本计划）、版本号变化、审批人。

**核心原则**：已批准的 PRD 不就地修改；已上线的功能用新 PRD 迭代；功能当前是什么样，以功能现状文档为准。

---

## 六、状态机

| 对象 | 状态流转 | 旁支 |
| --- | --- | --- |
| 需求 REQ | NEW → TRIAGED → PLANNED → IN_PRD → DONE | DEFERRED（延后）/ REJECTED（不做） |
| PRD | DRAFT → IN_REVIEW → APPROVED → DEVELOPING → READY_FOR_ACCEPTANCE → ACCEPTED → RELEASED | CHANGES_REQUESTED（退回修改）/ SUPERSEDED（被新版取代）/ REJECTED |
| 版本 | PLANNING → IN_PROGRESS → RELEASED | CANCELLED |
| 场景 SC | 待规划 → 已规划 → 开发中 → 已上线 | 已下线 |

- 状态只能通过脚本修改，脚本会拒绝不合法的跳转。
- APPROVED、ACCEPTED、RELEASED 这三个状态只能由人确认，必须记录操作人和时间。

---

## 七、硬规则

| # | 规则 | 执行方式 |
| --- | --- | --- |
| 1 | 关口没通过，不进入下一阶段（G0 → G1 → G2） | 阶段状态检查 |
| 2 | 没有批准的 PRD 不能开发；原型探索要单独标记，不能混进正式代码 | 门禁脚本 + Hook |
| 3 | 已批准的 PRD 不能私自修改，变更要走 CR 和影响分析 | 内容 hash 校验 |
| 4 | 不能超出 PRD 范围开发，也不能把 MVP 范围外的功能提前开发 | spec 与 PRD 范围比对 |
| 5 | 单元测试、集成测试、静态检查这些验证步骤不能跳过 | `verification.md` 必填项 |
| 6 | 测试结果和验收材料不能伪造；AI 不得编造调研或用户数据 | 报告必须附原始输出和来源 |
| 7 | 没有人工验收不能标记完成，没全部验收不能发布 | 状态机 + 发布检查 |
| 8 | 验收通过后必须更新功能现状文档 | 结构校验脚本 |
| 9 | 必须遵守 `constitution.md` 的安全、合规和架构规范 | 静态检查 + 代码审查 |

---

## 八、角色与职责

| 角色 | 主要职责 | 关键操作 |
| --- | --- | --- |
| 需求提出者（业务/产品） | 提出需求、确认业务目标 | 提交需求、参与评审、参与验收 |
| 需求负责人（PM） | 需求优先级、产品定义、PRD 评审 | 决策 G1/G2、批准 PRD、验收 |
| AI Agent | 执行标准流程、生成研发产物 | 生成文档、PRD、Spec、代码、测试、Demo |
| 开发（可选） | 技术决策、复杂问题处理 | 审查代码、处理技术难点、写 ADR |
| 测试（可选） | 测试策略、关键用例确认 | 审查测试用例和验证结果 |
| 项目负责人 | 整体进度、质量把控 | 决策 G0/G3/G4、处理例外情况、发布决策 |

**各阶段分工**

| 阶段 | 项目负责人 | 需求负责人 | AI Agent | 开发 / 测试 |
| --- | --- | --- | --- | --- |
| 立项 | 决策 G0 | 起草 | 起草、追问 | — |
| 需求分析 | 参与 | 主导、决策 G1 | 整理、汇总 | — |
| 产品定义 | 参与 | 主导、决策 G2 | 拆模块和场景、写初稿 | 评估可行性 |
| 版本规划 | 确认 | 决定范围 | 去重、排序建议 | 评估工作量 |
| PRD | — | 评审、批准 | 生成、自审 | 参与评审 |
| 开发 | — | 答疑 | 执行 | 审查代码和难点 |
| Demo/验收 | — | 验收 | 生成 Demo 和报告 | 审查测试 |
| 发布 | 决策 G3 | 确认 | 写发布说明 | 部署 |
| 运营 | 决策 G4 | 分析、提需求 | 数据汇总、反馈归类 | — |

---

## 九、Skill 体系

### 9.1 总览

Skill 分为三层：

```text
┌───────────────────────────── 入口层 ─────────────────────────────┐
│ flow：查看项目阶段和状态，告诉你"下一步该做什么"，并路由到对应 skill │
└──────────────────────────────────────────────────────────────────┘
┌───────────────────────────── 阶段层 ─────────────────────────────┐
│ project   discovery  definition  requirements  prd  design       │
│ dev       acceptance  release     growth                         │
└──────────────────────────────────────────────────────────────────┘
┌───────────────────────────── 内核层 ─────────────────────────────┐
│ flow-core：目录约定、模板、编号、状态机、门禁、结构校验、索引生成  │
│ （脚本 + Hook，所有 skill 共用）                                  │
└──────────────────────────────────────────────────────────────────┘
```

| # | Skill | 负责阶段 | 可写目录 | 优先级 |
| --- | --- | --- | --- | --- |
| 0 | flow-core | 全程（内核） | `.specify/`、所有自动生成的索引文件 | P0 |
| 1 | flow | 全程（入口） | 无（只读、路由） | P1 |
| 2 | project | 立项、项目管理、关口 | `00-management/`、`README.md`、`AGENTS.md` | P0 |
| 3 | discovery | 需求分析 | `01-discovery/` | P1 |
| 4 | definition | 产品定义 | `02-definition/`（`features/` 除外） | P1 |
| 5 | requirements | 需求池、版本选需求 | `03-requirements/` | P0 |
| 6 | prd | PRD 生成、评审、变更、测试用例 | `04-prds/` | P0 |
| 7 | design | 交互说明、原型 | `05-design/` | P2 |
| 8 | dev | Spec 驱动开发 | `06-specs/`、`src/`、`tests/` | P0 |
| 9 | acceptance | Demo、验收、合并功能现状 | `07-acceptance/`、`02-definition/modules/*/features/` | P0 |
| 10 | release | 版本规划、上线、发布说明 | `08-releases/`、`00-management/roadmap.md` | P1 |
| 11 | growth | 数据分析、实验、反馈归类、复盘 | `09-growth/`、`03-requirements/feedback/` | P2 |

> 所有 skill 都可以读取全部目录，但只能写自己负责的目录。

### 9.2 各 Skill 详细说明

#### 0. flow-core（流程内核）

| 项 | 内容 |
| --- | --- |
| 定位 | 不直接面向用户，是其他所有 skill 的公共基础 |
| 能力 | ① 初始化项目骨架（重复执行也安全，不覆盖已有文件）<br>② 分配编号（REQ/PRD/SC/DEC/CR/ADR…）<br>③ 状态流转：校验合法跳转，人工状态必须带操作人<br>④ 门禁检查：PRD 是否 APPROVED、hash 是否一致<br>⑤ 结构校验：必需文件、id 与目录名一致、孤儿 spec、引用失效、验收后功能现状是否已更新<br>⑥ 生成索引：`backlog.md`、`feature-map.md`、`04-prds/index.md`<br>⑦ 全套文档模板 |
| 交付物 | `.specify/scripts/*`、`.specify/templates/*`、Claude Code Hook 配置 |

#### 1. flow（流程入口）

| 项 | 内容 |
| --- | --- |
| 触发 | "下一步做什么""项目现在什么状态""这个需求走到哪了" |
| 能力 | 读取 `README.md` 中的阶段和全部 metadata，输出：当前阶段、关口状态、进行中的 PRD 和版本、阻塞项、建议下一步，并给出对应 skill |
| 禁止 | 不写任何文件 |

#### 2. project（立项与项目管理）

| 项 | 内容 |
| --- | --- |
| 触发 | 新建项目、立项、写周报、会议纪要、记录决策、发起关口评审 |
| 读 | 全部（汇总进度用） |
| 写 | `00-management/`、`README.md`、`AGENTS.md`、`01-discovery/opportunity.md` |
| 能力 | ① 调用 flow-core 初始化项目<br>② 一句话想法 → 机会描述 + 立项书，追问缺失信息<br>③ 团队与角色、风险清单<br>④ 会议纪要整理（提取决议和待办）<br>⑤ 根据各目录的变化自动汇总周报<br>⑥ 关口评审：生成评审材料和检查项，记录 DEC，更新 README 中的阶段 |
| 禁止 | 不能替人做关口决策，只能准备材料并记录结论 |

#### 3. discovery（需求分析）

| 项 | 内容 |
| --- | --- |
| 触发 | 竞品分析、市场调研、访谈提纲、整理访谈、列假设、设计验证实验 |
| 读 | `opportunity.md`、`charter.md` |
| 写 | `01-discovery/` |
| 能力 | ① 竞品分析（按统一模板，可联网检索并标注来源）<br>② 用户画像<br>③ 访谈提纲生成、访谈记录结构化、跨访谈提炼共性<br>④ 假设清单：假设 → 验证方式 → 证据 → 结论<br>⑤ 设计低成本实验（落地页、假门、手工 MVP）<br>⑥ 汇总 `insights.md` 作为 G1 输入 |
| 禁止 | 不得编造用户原话、数据或市场数字；没有来源的内容必须标注"推测" |

#### 4. definition（产品定义）

| 项 | 内容 |
| --- | --- |
| 触发 | 写愿景、商业模式、拆模块、列场景、用户旅程、MVP 范围、指标体系、术语表 |
| 读 | `01-discovery/`、`charter.md` |
| 写 | `02-definition/`（`features/` 由 acceptance 写） |
| 能力 | ① 愿景与价值主张、精益画布<br>② 拆分模块，建立模块 README 和场景清单（SC 编号）<br>③ 用户旅程<br>④ MVP 范围：必须做 / 不做 / 以后做，并附理由<br>⑤ 北极星指标与指标体系<br>⑥ 术语表维护<br>⑦ 批量生成初始需求（调用 requirements） |
| 禁止 | MVP 范围在 G2 之后的修改必须记 DEC |

#### 5. requirements（需求管理）

| 项 | 内容 |
| --- | --- |
| 触发 | 提需求、整理反馈、需求去重、排优先级、为版本选需求 |
| 读 | `02-definition/`、`03-requirements/`、`04-prds/index.md` |
| 写 | `03-requirements/` |
| 能力 | ① 自然语言需求 → 结构化需求条目（背景、用户、问题、场景、期望、来源）<br>② 缺关键信息时主动追问<br>③ 与已有需求、功能现状比对，去重或关联<br>④ 分类：新功能 / 优化 / 缺陷 / 技术债，并标注所属模块<br>⑤ 按 RICE / MoSCoW 排优先级并写明理由<br>⑥ 判断变更走哪条路（见第五章）<br>⑦ 生成 `backlog.md` |
| 禁止 | 不能把需求直接改成 PLANNED，版本范围由 release 和人确定 |

#### 6. prd（PRD）

| 项 | 内容 |
| --- | --- |
| 触发 | 写 PRD、改 PRD、评审 PRD、提变更、生成测试用例 |
| 读 | 需求条目、模块 README、功能现状文档、术语表、`constitution.md`、相关 PRD |
| 写 | `04-prds/` |
| 能力 | ① 新建 PRD 目录与 metadata（调用 flow-core 分配编号）<br>② 按模板生成按场景组织的 PRD，验收标准用 Given/When/Then 并编号<br>③ AI 自审（见 4.7 清单），输出问题列表<br>④ 评审：整理评审意见、逐条修改、留下评审记录<br>⑤ 批准：人确认后记录批准人、时间并锁定 hash<br>⑥ 变更：生成 CR 与影响分析，版本号加一，状态退回 IN_REVIEW<br>⑦ 从验收标准派生测试用例（正常 / 异常 / 边界）<br>⑧ 检查 PRD 大小，过大时建议拆分 |
| 禁止 | 不能自己批准；不能修改 APPROVED 状态的 `prd.md`（只能走 CR） |

#### 7. design（设计，可选）

| 项 | 内容 |
| --- | --- |
| 触发 | 交互说明、线框图、可点击原型、设计规范 |
| 读 | PRD、`design-system.md` |
| 写 | `05-design/` |
| 能力 | ① 根据 PRD 场景写交互说明（页面、状态、异常态、空状态）<br>② 生成 HTML 可点击原型或线框图<br>③ 维护设计规范<br>④ 检查设计稿是否覆盖 PRD 所有场景 |
| 禁止 | 不能改变 PRD 定义的范围和规则 |

#### 8. dev（Spec 驱动开发）

| 项 | 内容 |
| --- | --- |
| 触发 | 开发某个 PRD、修复缺陷 |
| 前置 | 必须先通过门禁检查 |
| 读 | PRD、设计稿、`06-specs/architecture.md`、`constitution.md`、现有代码 |
| 写 | `06-specs/`、`src/`、`tests/` |
| 能力 | ① 生成 spec（技术规格）→ plan（实施计划）→ tasks（任务拆解）<br>② 按任务编码<br>③ 编写并运行单元测试、集成测试、静态检查<br>④ 失败时自动修复，直到通过或需要人介入<br>⑤ 生成 `verification.md`：每条 AC 的验证结果，附原始测试输出<br>⑥ 重大技术取舍写 ADR<br>⑦ 更新 PRD 状态为 DEVELOPING / READY_FOR_ACCEPTANCE |
| 禁止 | 不超出 PRD 范围；发现需求问题时停下提问，不自行改需求；不跳过测试；不伪造测试结果 |

#### 9. acceptance（Demo 与验收）

| 项 | 内容 |
| --- | --- |
| 触发 | 准备 Demo、生成验收清单、记录验收结论 |
| 读 | PRD、`verification.md`、测试用例 |
| 写 | `07-acceptance/`、`02-definition/modules/*/features/` |
| 能力 | ① 启动或部署 Demo，写出访问方式和操作步骤<br>② 按 AC 编号生成验收清单，自动测试已覆盖的标注出来<br>③ 引导人工逐条验收，记录结论和遗留问题<br>④ 人确认通过后改为 ACCEPTED（记录验收人）<br>⑤ 把 PRD 的改动合并进功能现状文档<br>⑥ 关联需求改为 DONE，更新场景状态 |
| 禁止 | 不能自己判定验收通过 |

#### 10. release（版本）

| 项 | 内容 |
| --- | --- |
| 触发 | 规划版本、准备上线、写发布说明 |
| 读 | `backlog`、PRD 索引、验收报告 |
| 写 | `08-releases/`、`00-management/roadmap.md` |
| 能力 | ① 版本计划：根据优先级和容量建议版本范围，人确认后需求改为 PLANNED<br>② 跟踪版本内各 PRD 进度<br>③ 版本级 Demo 材料<br>④ 上线检查清单（迁移、监控、回滚、埋点、合规）<br>⑤ 发布说明、CHANGELOG<br>⑥ G3 评审材料；发布后 PRD 改为 RELEASED |
| 禁止 | 有 PRD 未 ACCEPTED 时不能发布 |

#### 11. growth（运营与复盘）

| 项 | 内容 |
| --- | --- |
| 触发 | 数据分析、A/B 实验、反馈归类、版本复盘、PMF 判断 |
| 读 | `metrics.md`、`dashboards.md`、反馈、发布记录 |
| 写 | `09-growth/`、`03-requirements/feedback/` |
| 能力 | ① 根据数据写分析报告（指标变化、原因假设）<br>② A/B 实验设计与结论<br>③ 用户反馈归类，转成需求（调用 requirements）<br>④ 版本复盘（做得好的、问题、改进项）<br>⑤ G4 材料 |
| 禁止 | 不得编造数据；结论必须附数据来源 |

### 9.3 Skill 之间的协作

```text
project ──▶ discovery ──▶ definition ──▶ requirements ──▶ release(规划)
                                              ▲                  │
                                              │                  ▼
                    growth ◀── release(发布) ◀── acceptance ◀── dev ◀── prd (+design)
                      │
                      └──▶ requirements （新需求，进入下一轮）

所有 skill ──调用──▶ flow-core（编号 / 状态流转 / 门禁 / 校验 / 索引）
```

### 9.4 flow-core 脚本清单

| 脚本 | 用途 |
| --- | --- |
| `init.py` | 初始化项目骨架和模板（重复执行也安全） |
| `new_id.py` | 分配各类编号 |
| `new_req.py` / `new_prd.py` | 创建需求条目 / PRD 目录 |
| `set_status.py` | 状态流转，校验合法性，记录操作人和历史 |
| `gate.py` | 门禁检查：PRD 是 APPROVED 且 hash 一致才放行 |
| `hash.py` | 计算并锁定 PRD 内容 hash |
| `lint.py` | 结构与一致性校验 |
| `build_index.py` | 生成 `backlog.md`、`feature-map.md`、`04-prds/index.md` |

### 9.5 用 Hook 强制执行硬规则

只写在 skill 说明里的规则，AI 有可能不遵守。关键规则用 Claude Code Hook 在工具调用层面拦截：

| Hook | 触发时机 | 行为 |
| --- | --- | --- |
| PRD 门禁 | 写入 `src/`、`06-specs/` 前 | 调用 `gate.py`，没有对应的 APPROVED PRD 就拒绝 |
| PRD 锁定 | 写入 APPROVED 状态的 `prd.md` 前 | 拒绝，提示走 CR |
| 状态保护 | 直接编辑 `metadata.yaml` 的 `status` 字段 | 拒绝，必须通过 `set_status.py` |
| 结构校验 | 会话结束时 | 运行 `lint.py`，报告问题 |

### 9.6 单个 Skill 的文件结构

```text
<skill-name>/
├── SKILL.md                 # 触发描述、职责、读写范围、流程步骤、禁止事项
├── references/              # 写作指南、检查清单、示例
└── templates/               # 该 skill 专用的模板（公共模板放在 flow-core）
```

### 9.7 分发方式

- 全部 skill、Hook 和模板打包成一个 Claude Code 插件，团队成员安装一次后所有项目都能用。
- 项目仓库内只保留 `.specify/`（项目规范、模板副本、脚本）、`AGENTS.md` 和项目文档。
- 插件升级后，用 `init.py` 把新模板补进已有项目（不覆盖已修改的文件）。

---

## 十、落地路线

| 阶段 | 内容 | 目标 |
| --- | --- | --- |
| 第一期（P0） | flow-core、project（初始化部分）、requirements、prd、dev、acceptance；PRD 门禁 Hook | 打通"需求 → PRD → 开发 → 验收"主链路，让"没有 PRD 不开发"真正执行 |
| 第二期（P1） | flow、discovery、definition、release；关口记录；PRD 锁定和状态保护 Hook | 覆盖 0→1 全流程和版本管理 |
| 第三期（P2） | design、growth；周报自动汇总；数据分析 | 覆盖设计和上线后的运营迭代 |

**试点方式**：选一个真实的小项目，从立项到第一个版本发布完整走一遍，根据试点中暴露的问题调整模板和规则，再推广到所有项目。
