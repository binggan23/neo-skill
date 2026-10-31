# Neo Skill P0 交付版高层设计

- **文档状态**：已确认
- **适用范围**：P0 最小研发闭环
- **架构目标**：以标准 Skill 提供自然语言协作入口，以本地文件和 `flow-core` CLI 提供确定性、可追踪、可恢复的流程内核。
- **权威需求**：[docs/srs.md](./srs.md)
- **可行性与边界**：[docs/fsr.md](./fsr.md)
- **原始目录与 Skill 方案**：[AI产品研发流程与Skill体系.md](../AI产品研发流程与Skill体系.md)

> 本文只保留实现所需的架构结论、组件边界、数据归属、关键机制和文件指针。业务规则以 SRS 为准，不在这里复制。

## 1. 结论摘要

Neo Skill P0 采用 **AI First 混合架构**：

```text
用户自然语言
    ↓
阶段 Skill：理解、检索、grilling、生成内容、请求人工确认
    ↓
flow-core CLI：规则、编号、状态、hash、门禁、lint、索引、事务
    ↓
本地项目文件：需求、PRD、Spec、代码、测试、验证和验收记录
    ↓
CI / 人工 CLI / Claude Code 试点
```

核心分工：

- AI 负责不确定的理解、追问、内容生成和事实检索。
- CLI 负责确定性校验和受控写入。
- 人负责排期、PRD 批准和验收通过。
- 文件系统是 P0 的持久化边界；不建设 Web、数据库、独立后端、账号或权限平台。
- 取消 Agent 专属 Hook。受控 CLI、lint 和 CI 是流程治理，不是不可绕过的安全边界。

依据：[docs/srs.md §2-§4](./srs.md)、[docs/fsr.md §4](./fsr.md)。

## 2. 设计原则

### 2.1 单一确定性内核

所有 Skill 共用同一个 `flow-core`。目录、编号、状态机、门禁、模板和索引规则以声明式资源为单一事实来源；Skill 不复制这些规则。

依据：[docs/srs.md §5.7](./srs.md)、[docs/fsr.md §4.1-§4.2](./fsr.md)。

### 2.2 AI 与确定性操作分离

Skill 可以提出建议，但不能自行改变受控状态。涉及排期、提交评审、PRD 批准和验收的操作必须经过统一确认协议，并由 CLI 校验 `team.md` 职责后写入。

依据：[docs/srs.md §3-§4](./srs.md)、[docs/srs.md §7](./srs.md)。

### 2.3 项目基线优先

安装资源是发布源，项目 `.specify/` 是项目自己的规则基线。运行时优先使用项目基线；安装或升级只补齐缺失内容，不能静默覆盖项目已有规则。

### 2.4 文件优先、可审计、可恢复

正式产物落在原始项目目录中。状态、hash、人工确认、测试原始输出和验收版本必须可追踪；多文件收尾必须能在中断后恢复。

依据：[docs/srs.md §2.3、§8-§9](./srs.md)、[docs/fsr.md §6、§9](./fsr.md)。

### 2.5 P0 最小闭环

HLD 只覆盖 `project`、`requirements`、`prd`、`dev`、`acceptance` 和 `flow-core`。`flow`、`discovery`、`definition`、`release`、`design`、`growth` 留在后续版本。

依据：[docs/srs.md §2.1-§2.2](./srs.md)、[docs/fsr.md §5](./fsr.md)。

## 3. 组件视图

### 3.1 组件职责

| 组件 | 职责 | 可写边界 | 明确禁止 |
| --- | --- | --- | --- |
| `project` Skill | 初始化项目、收集团队摘要、准备项目约定 | 初始化相关目录、受管理的 `AGENTS.md` 区块 | 自行完成关口决策 |
| `requirements` Skill | 需求草稿、事实比对、grilling、分类、排期请求 | `03-requirements/` | 自行排期、删除或合并需求 |
| `prd` Skill | PRD 草稿、grilling、自审、评审、CR | `04-prds/` | 自行批准或直接修改已批准 PRD |
| `dev` Skill | 绑定 PRD、生成 Spec/Plan/Tasks、执行开发和验证 | `06-specs/`、`src/`、`tests/` | 越界开发、跳过验证、自行修改需求 |
| `acceptance` Skill | Demo、AC checklist、人工验收、收尾同步 | `07-acceptance/`、功能现状目录 | 自行判定验收通过 |
| `flow-core` CLI | 所有确定性操作和受控写入 | `.specify/`、受管理索引、对象 metadata、`.neo-skill/` | 调用模型或替人做业务判断 |
| CI / 人工 CLI | 重复执行 lint、测试和门禁 | 由执行命令决定 | 绕过相同业务校验 |

所有 Skill 可以读取全部项目目录，但只应写自己的职责目录；该约束来自原始方案的 Skill 写入边界，详见[原始方案 §9.1](../AI产品研发流程与Skill体系.md)。

### 3.2 调用边界

```text
Skill
  ├─ 读取项目文件和规则
  ├─ 查询代码与文档事实
  ├─ 运行 grilling
  ├─ 生成候选正文
  └─ 调用 flow-core 命令
          ├─ 校验前置条件
          ├─ 获取对象锁
          ├─ 执行原子或事务写入
          ├─ 记录状态 / hash / 历史
          └─ 返回结构化结果
```

Skill 不直接编辑以下内容：

- 受控对象的 `metadata.yaml`；
- 状态历史和人工确认记录；
- PRD 锁定 hash；
- 受管理索引区块；
- `.neo-skill/locks/` 和 `.neo-skill/transactions/`。

## 4. 规则与资源模型

### 4.1 两层资源

```text
安装目录资源（发布源）
    └─ flow-core、规则、模板、Skill references
             ↓ init 时复制 / 补齐
项目 .specify/（项目规则基线）
             ↓ 运行时优先读取
项目 CLI、CI、Skill
```

- 安装目录资源随 Skill 分发，不能依赖开发机器源码路径。
- `.specify/` 保存项目初始化时采用的规则、模板和约定。
- 项目运行时优先读取 `.specify/`。
- 更新只补齐缺失文件；已有文件需要变更时先展示计划并由用户确认。
- 规则文件的格式和具体字段由实现阶段确定，但目录、状态、模板和门禁必须可被 CLI 与 CI 共同读取。

依据：[docs/srs.md §5.1-§5.2、§5.7](./srs.md)、[docs/fsr.md §4.3、§4.7](./fsr.md)。

### 4.2 内容格式

| 内容 | 格式 | 说明 |
| --- | --- | --- |
| 需求、PRD、Spec、报告 | Markdown | 面向人和 Agent 的正文 |
| metadata、项目配置 | YAML | 可审查的结构化项目数据 |
| CLI 输入输出 | JSON | `--json` 机器接口；不作为正式业务文档 |
| 原始测试输出 | 按命令原始格式保存 | 另存退出码、环境和 AC 关联 |

## 5. 项目文件与数据归属

### 5.1 业务目录

项目使用原始方案规定的完整骨架；P0 只实现其中必要能力，不因未实现后续阶段而伪造内容。

```text
00-management/          team.md、项目约定
02-definition/          mvp-scope.md、模块、场景、功能现状
03-requirements/        backlog.md、items/、feedback/
04-prds/                index.md、<module>/PRD-xxx/
06-specs/               architecture.md、data-model.md、<module>/PRD-xxx/
07-acceptance/          <module>/PRD-xxx/
08-releases/            目录骨架，P0 不实现完整 release
09-growth/              目录骨架，P0 不实现完整 growth
.specify/               项目规则、模板和脚本资源
.neo-skill/             locks/、transactions/ 等运行时元数据
src/                    源代码
tests/                  测试代码
```

完整树见[原始方案 §2](../AI产品研发流程与Skill体系.md)；P0 目录裁剪规则见[docs/srs.md §5.2](./srs.md)。

### 5.2 核心对象

| 对象 | 事实来源 | 关键关联 |
| --- | --- | --- |
| REQ | `03-requirements/items/REQ-xxx-<slug>.md` 及 metadata | 模块、分类、来源、状态、目标版本、主 PRD |
| PRD | `04-prds/<module>/PRD-xxx-<slug>/` | `metadata.yaml`、`prd.md`、可选 `test-cases.md` |
| CR | PRD 目录 `changes/` | 原因、内容、影响、版本和审批 |
| Spec/Plan/Tasks | `06-specs/<module>/PRD-xxx/` | PRD ID、基线版本、验证关联 |
| Verification | `verification.md` 与原始输出 | PRD hash、代码版本、每条 AC、退出码、环境 |
| Acceptance | `07-acceptance/<module>/PRD-xxx/` | PRD hash、代码版本、逐条 AC、人工结论、收尾状态 |
| Team | `team.md` | 人员标识、姓名和职责映射 |
| 索引 | 受管理区块 | backlog、feature-map、PRD index |

准确字段和状态以 [docs/srs.md §6](./srs.md) 为准；本表只说明事实归属。

### 5.3 metadata 与历史

`metadata.yaml` 同时承担两项职责：

1. 提供当前状态、版本、模块、关联对象和当前 hash 等状态投影；
2. 保存追加式状态历史和人工确认记录的结构化引用。

状态变更流程：

```text
读取旧 metadata
    ↓
校验对象 ID、当前状态、目标状态、actor 和前置条件
    ↓
追加 history / confirmation
    ↓
原子写入 metadata
```

CLI 不提供删除或修改历史的命令。直接编辑由 `lint` 尽可能发现；这属于流程治理，不是不可绕过的安全边界。

## 6. 状态、基线与 hash

### 6.1 状态机

P0 的关键状态机由 SRS 定义，HLD 只指定实现位置：

- REQ 状态：`metadata.yaml`，由 `req` 命令校验和变更；见[docs/srs.md §6.2](./srs.md)。
- PRD 状态：`metadata.yaml`，由 `prd`、`dev`、`acceptance` 通过公共状态服务变更；见[docs/srs.md §6.3](./srs.md)。
- 场景状态：功能地图或场景事实文件，由验收收尾事务同步；见[docs/srs.md §6.4](./srs.md)。

状态转换不能由 Skill 直接编辑 YAML，也不能由测试通过、Demo 启动或代码合并隐式触发。

### 6.2 PRD 基线

批准时建立基线，至少关联：

```text
PRD正文 + 锁定范围 metadata + test-cases（若存在）
    ↓ SHA-256（按相对路径和 UTF-8 字节确定性计算）
PRD content_hash
```

评审记录、状态历史和 CR 是追加记录，不参与正文 hash。批准后锁定字段发生变化必须走 CR；CR 使旧 Spec、代码、测试或验收证据按规则标记为基线失效。

依据：[docs/srs.md §5.4、§6.1](./srs.md)。

### 6.3 开发与验收基线

提交验收时保存：

- PRD hash；
- Git commit，或非 Git 项目的源代码与测试文件内容摘要；
- 验证报告摘要及其原始输出位置；
- 验收清单所引用的版本。

后续由 `dev` 门禁、`lint`、`acceptance` 或 `recover` 按需检查。发现 PRD、代码或验证证据变化时，旧提交或验收结论失效，必须重新验证和验收。系统不实现实时文件监听。

## 7. CLI 设计

### 7.1 稳定命令组

| 命令组 | 责任 |
| --- | --- |
| `init` | 初始化目录、规则基线和模板，幂等且不覆盖已有业务文件 |
| `doctor` | 检查 Python、Node、Skill 资源、flow-core、残留锁和恢复提示 |
| `req` | 创建需求、确认对齐、请求/记录人工排期 |
| `prd` | 创建 PRD、提交评审、批准、创建 CR、重新批准 |
| `dev` | 检查开发门禁、绑定 PRD、推进开发和提交验收 |
| `acceptance` | 生成 Demo/checklist、记录人工验收、执行收尾 |
| `lint` | 检查结构、引用、hash、基线和收尾一致性 |
| `index` | 生成受管理索引区块 |
| `recover` | 检查并恢复可恢复事务，或报告不可安全恢复的原因 |

内部可以拆成多个 Python 模块，但 Skill、CI 和人工使用只依赖命令及其参数/结果契约。

### 7.2 通用参数与结果

人工决策命令必须要求：

```text
--actor <team member id>
--confirm <explicit confirmation>
```

每次调用都支持人类输出和 `--json`。JSON 最小结构：

```json
{
  "success": true,
  "artifacts": [],
  "errors": [],
  "next_actions": []
}
```

错误项至少包含错误码、原因、对象和建议下一步。失败返回非零退出码，并保证不修改业务状态或留下半写入正式产物。

依据：[docs/srs.md §5.7](./srs.md)。

## 8. 并发、原子写入与事务

### 8.1 对象锁

- 锁目录：`.neo-skill/locks/`。
- 锁粒度：需求或 PRD 对象；需要跨对象更新时按稳定顺序获取多个锁。
- 锁内容：对象 ID、进程标识、创建时间、命令和规则基线信息。
- 正常完成或失败清理时释放。
- 残留锁不自动抢占或删除；`doctor` 报告，显式恢复操作确认进程已结束后才清理。

### 8.2 单文件写入

单文件更新采用：

```text
读取并校验 → 写入同目录临时文件 → flush / close → 原子替换 → 清理临时文件
```

原子替换前不改变正式文件；失败时返回错误，不改变业务状态。

### 8.3 多文件事务

验收收尾等跨文件操作写入 `.neo-skill/transactions/<transaction-id>/`，包含目标文件、原基线、暂存内容、操作顺序和状态。

```text
PREPARED
  └─ 暂存内容和全部校验已完成
       ↓
COMMITTING
  └─ 已开始按固定顺序原子替换；recover 可继续
       ↓
COMMITTED
  └─ 所有目标已替换；保留审计记录
```

`recover` 必须重新获取对象锁，并验证目标文件仍匹配事务记录中的原基线；无法安全继续时保持失败状态，禁止静默覆盖。验收结论可以已经保留，但在收尾完成前必须显示“已确认通过，收尾未完成”。

## 9. 索引生成

`flow-core index` 负责以下文件中的受管理区块：

- `03-requirements/backlog.md`；
- `02-definition/feature-map.md`；
- `04-prds/index.md`。

生成流程：

```text
读取上次生成指纹和当前受管理区块
    ↓
校验区块是否被人工修改
    ├─ 否：生成新内容并原子替换区块
    └─ 是：返回确认请求
             ↓ 用户确认
        只替换受管理区块，保留区块外内容
```

索引失败不改变 REQ、PRD 或验收状态。生成结果应在输入和规则不变时稳定。

依据：[docs/srs.md §5.7、§8](./srs.md)。

## 10. 关键流程时序

### 10.1 需求到排期

```text
requirements Skill
  → flow-core req create
  → 保存 NEW 草稿
  → Skill grilling / 共识摘要
  → 用户确认
  → flow-core req confirm
  → 询问“是否设置 xxx 需求排期”
  → 需求负责人 --actor + 显式确认 + 目标版本
  → flow-core req plan
  → TRIAGED → PLANNED
```

### 10.2 PRD 到批准

```text
prd Skill
  → flow-core prd create（仅 PLANNED 主需求）
  → 生成 DRAFT
  → grilling + AI 自审 + 人工修改
  → 用户确认提交评审
  → flow-core prd submit-review
  → IN_REVIEW
  → 需求负责人确认批准
  → flow-core prd approve
  → APPROVED + content_hash
```

`APPROVED` 拼写和状态值以 SRS 的 `APPROVED` 为准；实现测试必须覆盖非法跳转和 hash 失效。

### 10.3 开发到提交验收

```text
dev Skill
  → flow-core dev gate PRD-xxx
  → 校验 team / constitution / 状态 / hash / 绑定关系
  → 通过后 DEVELOPING
  → 生成 spec / plan / tasks
  → 修改 src / tests
  → 执行适用验证并保存原始输出
  → 生成 verification.md
  → 所有适用项通过
  → READY_FOR_ACCEPTANCE
```

门禁失败时不生成新 Spec、不修改正式代码。测试修复受 constitution 配置的连续轮次上限约束，默认三轮；见[docs/srs.md §5.5](./srs.md)。

### 10.4 人工验收到收尾

```text
acceptance Skill
  → 生成 demo.md / checklist.md
  → 人工逐条记录 AC 结论和证据
  → 只有全部 AC 通过才请求确认
  → 需求负责人 --actor + 显式确认
  → flow-core acceptance approve
  → READY_FOR_ACCEPTANCE → ACCEPTED
  → 事务同步功能现状、主需求、场景和索引
  → 全部同步成功后主需求 DONE
```

任一 AC 为不通过或阻塞都不能 `ACCEPTED`；人工确认通过不等于收尾完成。完整规则见[docs/srs.md §5.6](./srs.md)。

## 11. 安全与治理边界

P0 的安全模型是流程治理：

- `team.md` 做职责匹配，不做身份认证；
- `--actor` 和确认记录防止正常 Skill 流程中 AI 擅自推进，不构成电子签名；
- 本地文件不代表不会发送给模型服务商，团队仍须遵守数据政策；
- CLI、lint、CI 能发现或拒绝正常路径中的非法操作，但不能阻止用户直接编辑文件；
- 不保存凭据，不向独立后端上传项目文件；
- 日志、错误和 CI 输出不得包含凭据。

依据：[docs/srs.md §2.3、§3、§8](./srs.md)、[docs/fsr.md §6.4、§9](./fsr.md)。

原始方案中的 Claude 专属 Hook 设计不属于 P0 实现边界；SRS 明确取消 Agent 专属 Hook，详见[docs/srs.md §2.3](./srs.md)和[docs/fsr.md §3.2、§4.6](./fsr.md)。

## 12. 性能与可靠性目标

HLD 只绑定架构责任，不重复验收条款：

- 常规 `lint`、索引和状态查询在 1,000 条需求或 200 个 PRD 数据集上 10 秒内完成；
- 单次状态变更 2 秒内完成，不含 AI 生成和项目测试；
- 写入采用原子替换或事务恢复；
- 并发冲突拒绝且不损坏已有文件；
- 相同输入和规则时索引结果稳定。

完整 NFR 见[docs/srs.md §8](./srs.md)。

## 13. 实现分层建议

这是实现边界建议，不改变 SRS 对外行为：

```text
flow_core/
├── cli.py              # 参数解析、命令路由、退出码
├── output.py           # 人类输出和 --json 结果
├── rules/              # 项目 .specify/ 规则加载与校验
├── workspace.py        # 项目根目录、路径和受管理区块
├── ids.py              # 编号只增不复用
├── metadata.py         # YAML 投影、history、人工确认
├── state.py            # 状态机与门禁前置条件
├── hashing.py          # PRD / 代码 / 验证证据摘要
├── locks.py            # .neo-skill/locks
├── transactions.py     # PREPARED / COMMITTING / COMMITTED
├── index.py            # 索引区块生成
└── commands/           # init、req、prd、dev、acceptance、recover
```

建议优先使用 Python 3.11+ 标准库；YAML 解析使用项目声明并锁定的必要依赖。模块划分可以调整，但以下能力不能散落到 Skill 提示词中：状态校验、hash、锁、事务、编号、索引受管理区块和 CLI 结果契约。

## 14. 验证策略

验证分三层：

1. **flow-core 单元/集成测试**：编号、状态、hash、门禁、锁、事务、索引、原子写入和结构检查。
2. **违规路径测试**：未排期建 PRD、DRAFT 开发、hash 失效、AI 自行批准、跳过测试、条件通过、收尾未完成、并发冲突等。
3. **Claude Code 隔离端到端试点**：安装 Skill、初始化项目、完成需求到验收主链路，并确认不污染现有环境。

测试证据必须保存原始输出、退出码、环境和 AC 关联；不得只保存 AI 摘要。测试矩阵和 P0 通过条件见[docs/fsr.md §7](./fsr.md)和[docs/srs.md §10](./srs.md)。

## 15. 追踪矩阵

| HLD 主题 | 权威指针 |
| --- | --- |
| P0 范围与非目标 | `docs/srs.md §2`、`docs/fsr.md §2、§5` |
| AI / CLI 分工 | `docs/srs.md §1-§4`、`docs/fsr.md §4.1-§4.2` |
| Skill 写入边界 | `AI产品研发流程与Skill体系.md §9.1-§9.3` |
| grilling 与人工确认 | `docs/srs.md §3`、`docs/fsr.md §4.4` |
| REQ / PRD 状态机 | `docs/srs.md §6.2-§6.4` |
| PRD hash、CR 和基线 | `docs/srs.md §5.4、§7` |
| 开发门禁与验证 | `docs/srs.md §5.5` |
| 人工验收与收尾 | `docs/srs.md §5.6` |
| CLI、lint、索引、结构化结果 | `docs/srs.md §5.7` |
| 性能、可靠性和数据边界 | `docs/srs.md §8-§9` |
| 可行性、取消 Hook、Agent 承诺 | `docs/fsr.md §3-§4、§6、§9-§10` |

## 16. 明确不解决的问题

以下问题不在本 HLD 的 P0 设计内：

- 完整 0→1 流程和 G0-G4；
- 完整版本发布、运营和增长流程；
- 无 PRD 的缺陷快速通道；
- Web、数据库、独立后端、账号、权限、登录和电子签名；
- MCP 和自定义 npm 安装器；
- macOS、Windows 或其他 Agent 的 P0 认证；
- Git commit、merge、push 自动化；
- 不可绕过的系统安全强制。

依据：[docs/srs.md §2.2](./srs.md)和[docs/fsr.md §2.3、§10](./fsr.md)。
