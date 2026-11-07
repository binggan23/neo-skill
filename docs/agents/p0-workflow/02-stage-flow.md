# 阶段流程：何时调用什么 Skill

阅读条件：接手目标项目、执行某阶段，或设计阶段之间的交接。命令参数以已安装版本的 `--help` 为准；本页给顺序与完成标准。单个 Skill 的触发条件、写入边界与命令组查 [Skill 注册表](07-skill-catalog.md)。Skill 是由 Agent 读取执行的交互说明，flow-core 不会自动启动下一个 Skill。

## 总路线

```text
neo-skill 路由
  → project：初始化并确认配置
  → requirements：NEW → TRIAGED → PLANNED
  → prd：DRAFT → IN_REVIEW → APPROVED（主需求转 IN_PRD）
  → dev：DEVELOPING → READY_FOR_ACCEPTANCE
  → acceptance：逐项记录 → ACCEPTED → 收尾事务 → 主需求 DONE
```

从既有项目继续时，先查询 REQ/PRD、验证和收尾状态，再进入对应阶段。会话里说“做完了”不是持久化完成证据。

## 0. 路由与开工：neo-skill、project

来源：[`neo-skill`](../../../skills/neo-skill/SKILL.md)、[`project`](../../../skills/project/SKILL.md)。

| 触发 | Agent 做什么 | CLI / 文件 | 本阶段完成标准 |
| --- | --- | --- | --- |
| 用户不知道从哪里开始 | 通过 neo-skill 识别初始化、需求、PRD、开发或验收意图 | 只读查询当前项目 | 给出明确对象与对应阶段 |
| 首次接入、补齐骨架、检查环境 | project 运行诊断，解释缺项；询问裁剪与冲突处理 | `doctor` → `init`；建立完整骨架与 `.specify/` | 环境满足，骨架存在；已有业务内容被保留 |
| 配置团队与约束 | 展示团队摘要，取得确认后记录成员；准备并确认技术/安全/测试/架构约束 | `00-management/team.md`；`.specify/constitution.md` | 团队与 constitution 均经人确认；缺配置时如实停在未配置状态 |

`init` 产生的模板不代表团队已配置或约束已确认。初始化幂等；冲突先出计划，确认后才应用；AGENTS 只维护自己的受管理区块。不得为“跑通流程”伪造人员、业务结论或确认。

## 1. 需求：requirements

来源：[`requirements`](../../../skills/requirements/SKILL.md)，SRS-FR-030–035。

1. `req create` 保存 NEW 草稿，逐步补齐背景、用户、问题、场景、期望结果、来源。用 `req update/show` 续写和恢复。
2. 查已有需求与功能现状，`req candidates` 提示重复/关联。相似度是建议，不是自动合并决定。
3. grilling 把决策组织成依赖树，只问当前 frontier；可查证事实由 Agent 查询。给推荐答案，记录事实、决定、假设和未决问题，`req align` 保存进展。
4. 展示共识后取得人工确认，`req confirm` 推到 TRIAGED。职责以规则的 `req.confirm` 为准，P0 默认为 stakeholder。
5. 明确询问“是否设置 xxx 需求排期”。拒绝保持 TRIAGED；缺目标版本继续询问；需求负责人同意且确认版本后 `req plan` 推到 PLANNED。

完成标准：需求内容完整、排期决定和目标版本可追溯。交给 prd：REQ ID、模块、版本、范围共识、未决问题。进入 IN_PRD 前可确认调整/取消排期；之后需 PRD 变更路径。

## 2. 产品基线：prd

来源：[`prd`](../../../skills/prd/SKILL.md)，SRS-FR-040–051。

1. `prd create --req …` 只从 PLANNED 主需求创建 DRAFT；建立模块、功能、场景、版本关系，同时主需求机械转 IN_PRD。
2. 用 PRD 模板写范围、现状变化、场景与 AC 等。新功能无现状时明确声明。继续 grilling；`prd validate/self-review` 提供结构与自审问题，人决定产品取舍。
3. 场景最多八个；每场景至少有可测试的 Given/When/Then AC。语法校验能发现空壳，不能证明业务标准充分。
4. 人工确认 `prd submit-review`：DRAFT/CHANGES_REQUESTED → IN_REVIEW。
5. 需求负责人另行确认 `prd approve`：IN_REVIEW → APPROVED，记录批准人、版本和 content_hash。提交评审与批准必须分开。

完成标准：当前 PRD 批准有效、hash 可核对、关联对象一致。交给 dev：PRD ID/目录、主需求、版本/hash、场景/AC、存在的测试用例。批准后修改锁定内容走 CR，见 [04](04-baseline-and-recovery.md)。

## 3. 实现与验证：dev

来源：[`dev`](../../../skills/dev/SKILL.md)，SRS-FR-060–068。

1. 显式绑定 `--prd`，先 `dev gate`。检查绑定、存在、状态、批准、hash、constitution、团队、主需求关系、模块、上一轮 Spec 基线；当前实现是十项检查。
2. 通过后 `dev start` 创建同一 PRD 的 Spec/Plan/Tasks 与 `.baseline.yaml`，首次启动时 PRD 转 DEVELOPING。用 scope-check 与人工审查约束范围。
3. 按 Tasks 实现与测试，写入 `06-specs/`、`src/`、`tests/`。需求不清或疑似越界时停下，请求澄清/CR，不能改 AC 让代码显得符合需求。
4. `dev verify` 执行适用验证；外部已执行结果可用 `verify record` 登记并附证据。逐条关联 AC，保留原始输出、退出码、环境；不适用项必须有理由。
5. 失败修复受连续轮次限制（默认三轮，项目可覆盖）。提交前 `submit-acceptance` 还检查证据新鲜度、AC 覆盖与 guard；满足条件后机械转 READY_FOR_ACCEPTANCE。

完成标准：验证结果与当前 PRD/代码一致，提交验收基线已保存。交给 acceptance：PRD hash、代码版本、verification 摘要、原始日志、AC 清单、Demo 操作线索。READY 不会自动生成 Demo/checklist，仍需下一阶段 `prepare`。

## 4. 人工验收与收尾：acceptance

来源：[`acceptance`](../../../skills/acceptance/SKILL.md)，SRS-FR-070–077。

| 操作 | 发生什么 | 继续条件 |
| --- | --- | --- |
| `acceptance prepare` | 生成 Demo 材料、逐条 AC checklist | 说明访问/操作方法，补真实材料 |
| `acceptance suggest` | 只保存 AI 建议 | 建议不算人工 conclusion |
| `acceptance record` | 以团队成员身份逐条记录 pass/fail/blocked 与证据 | 处理全部 AC；记录证据，不能凭猜测通过 |
| `acceptance approve` | 需求负责人显式确认；全 pass 且基线有效才 ACCEPTED | 接着收尾，不能先报主需求 DONE |
| `acceptance reject` | 不通过，退回 DEVELOPING | 返工、重验、重新提交验收 |
| `acceptance conditional` | 保持 READY_FOR_ACCEPTANCE，保留遗留问题 | 选择返工或重新验收，不能当作 ACCEPTED |
| `acceptance closeout` | 事务同步功能现状、场景、索引、主需求 | 全部同步完成且完成记录一致才报告 DONE |

收尾失败保留人工结论，显示“已确认通过，收尾未完成”。用 closeout-status 查询并按 [恢复流程](04-baseline-and-recovery.md) 处理。

## 上游进度查询：任意时刻可用的只读分支

来源：[`upstream-progress`](../../../skills/upstream-progress/SKILL.md)，SRS-FR-092～095。

它**不是阶段转移**。上面的 0→1→2→3→4 是产品状态的单向推进；进度查询可以在任意时刻发生
（初始化前后、需求中间、验收之后），结果不改变任何文件、任何状态。

| 触发 | Agent 做什么 | CLI | 状态变化 |
| --- | --- | --- | --- |
| 用户问「上游做到哪了 / 最新发布了什么」 | `upstream status` 先看有没有绑定；有就 `progress report` | `upstream show`、`progress report` | 无 |
| 没有绑定，用户愿意临时给 | 向用户要上游 URL + 至少一个来源，用临时查询 | `progress report --upstream … --source …` | 无（也**不**写回文档） |
| 没有绑定，用户也不给 | 如实说“进度查询尚未配置”，给出绑定命令 | —（只给命令） | 无 |
| 用户想长期跟踪 | 转给 [`project`](../../../skills/project/SKILL.md) 阶段七：展示→确认→`upstream bind` | `upstream bind` | 写受管理区块（需 `--actor` + `--confirm`） |

三条口径固定：

- **远端证据是唯一进度结论**。已发布（`published`）与进行中（`ongoing`）分列，每条带来源；
  不可访问 / 不完整 / 过期 / 相互矛盾归入 `uncertainty`，不能说成结论。
- **本地提醒单列**。`dirty` / `ahead` / `behind` 等只意味着“本地有未提交/未推送的改动”，
  不是上游进度，也不解释成“上游已经做了”。
- **不读本地正文、不自动写回**。只读绑定区块的 `upstream.id` 与 `upstream.sources`；
  不扫本地代码、业务文档、`.scratch/` 草稿。

与 0 阶段（`project`）的交接只有一处：接入收尾时可以顺便问一句“有没有上游项目”，
确认后由 `project` 写绑定（SRS-FR-091/095）。绑定是可选项，不绑定不影响接入完成；
但必须在结果里明确标注“进度查询尚未配置”。

与后续阶段的关系：进度报告**不**触发也**不**代替任何产品决定。看到“上游已发布 v1.2”
不等于本地 PRD 可以批准、开发可以开始或验收可以通过；那些走的仍是各阶段自己的门禁与人工确认。

继续：[状态与决定](03-state-and-decisions.md)
