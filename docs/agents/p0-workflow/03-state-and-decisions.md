# 状态与人工决定

阅读条件：修改状态、职责、审批或新增阶段。以下表格覆盖核对基线中规则文件声明的全部出边；它不保证每条边都有独立 CLI 入口。来源：[`flow.yaml`](../../../skills/neo-skill/runtime/rules/flow.yaml) 的 `req.transitions`、`prd.transitions`、`gates`；语义依据 [SRS §6–7](../../srs.md)。

## REQ 全部声明边

| 当前状态 | 允许目标 |
| --- | --- |
| NEW | TRIAGED、DEFERRED、REJECTED |
| TRIAGED | PLANNED、DEFERRED、REJECTED |
| PLANNED | IN_PRD、TRIAGED、DEFERRED、REJECTED |
| IN_PRD | DONE |
| DONE | 无 |
| DEFERRED | TRIAGED、REJECTED |
| REJECTED | 无 |

主链路：共识确认 → 排期 → 创建 PRD → 收尾完成。`PLANNED → TRIAGED` 是取消排期；进入 IN_PRD 后修改排期要留下 PRD 变更或重新评审记录。终态与废弃记录保留编号，slug 创建后稳定。

## PRD 全部声明边

| 当前状态 | 允许目标 |
| --- | --- |
| DRAFT | IN_REVIEW、REJECTED |
| IN_REVIEW | APPROVED、CHANGES_REQUESTED、REJECTED |
| CHANGES_REQUESTED | IN_REVIEW、DRAFT |
| APPROVED | DEVELOPING、IN_REVIEW、SUPERSEDED |
| DEVELOPING | READY_FOR_ACCEPTANCE、IN_REVIEW |
| READY_FOR_ACCEPTANCE | ACCEPTED、DEVELOPING、IN_REVIEW |
| ACCEPTED | 无 |
| REJECTED | 无 |
| SUPERSEDED | 无 |

主链路：提交评审 → 批准并锁基线 → 开发 → 提交验收 → 人工通过。`READY_FOR_ACCEPTANCE` 是完整状态名，图中的 READY 只是缩写；PRD 不使用 DONE，DONE 属于主需求。

场景使用“待规划、已规划、开发中、已验收”的产品阶段词汇；收尾把本次场景同步为已验收。这不意味着已有完整、独立的场景状态机 CLI，也不表示已上线。

## 谁确认，谁机械推进

以下角色是发布源默认配置；目标项目以自己的规则基线和 team.md 为准。

| 决定或推进 | 入口 | 默认职责与确认方式 |
| --- | --- | --- |
| 需求共识确认 | `req confirm` | stakeholder；显式 actor + confirm |
| 排期、取消、暂缓、拒绝需求 | `req plan/unplan/defer/reject` | requirement_owner；显式 actor + confirm，排期另需 release |
| 提交 PRD 评审 | `prd submit-review` | stakeholder；显式 actor + confirm |
| 批准、打回、拒绝 PRD | `prd approve/request-changes/reject` | requirement_owner；显式 actor + confirm |
| CR 创建、重新批准、拒绝 | `prd cr create/approve/reject` | requirement_owner；显式 actor + confirm；影响面须声明 |
| 开发启动 | `dev start` | developer；当前命令要求 actor + confirm 用于门禁与审计 |
| 创建 PRD 时主需求转 IN_PRD | `prd create` | 机械关联变更，不额外请求“批准 IN_PRD” |
| 验证完整后提交验收 | `dev submit-acceptance` | 机械推进，无额外人工批准；申报 actor 时按开发职责核对 |
| 逐条记录 AC | `acceptance record` | 已登记成员申报 actor；这是人工结论的记录入口 |
| 验收通过/不通过/有条件通过 | `acceptance approve/reject/conditional` | 需求负责人 actor + confirm |
| 验收后收尾 | `acceptance closeout` | 机械同步已有决定；受管理区块有人工改动时另需确认覆盖 |

普通起草、查询、grilling 不应每次索要身份。AI 可以在用户明确决定后代为执行命令，不能自行补出“用户已确认”。角色匹配只是审计条件，不是身份认证。

## 改动状态机时必须逐层检查

1. 规则：这条边是否声明，目标项目旧基线怎样升级？
2. 命令：是否有对应入口、前置状态、职责、确认、失败退出码？不能只加 YAML 就宣布支持闭环。
3. 关联：REQ、PRD、Spec、验证、验收、收尾哪些投影会失效或需要更新？
4. 历史：旧批准、旧结论、废弃对象是否仍可查询？
5. 测试：合法路径、错误角色、缺确认、非法跳转、失败无副作用都需覆盖。

**当前需保留的差异**：`prd.cr_states` 包含 ACCEPTED/SUPERSEDED，但上表这两个状态没有出边。代码可记录失效标记，却没有完整的“验收后 CR 再批准”闭环。后续设计见 [05](05-next-version.md)，不要默认为已支持。
