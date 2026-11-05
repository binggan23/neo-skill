# 基线、验证证据与失败恢复

阅读条件：改 PRD/hash、验证或验收规则、处理锁冲突和未完成收尾。依据：[SRS-FR-048–051、064–077、083–088](../../srs.md)，[HLD §6、8、9](../../hld.md)。

## 一条贯穿交付的关联

```text
REQ ID → PRD ID + version + content_hash
       → Spec/Plan/Tasks 的 baseline_version / baseline_hash
       → 验证记录：PRD hash + 代码版本 + 原始输出摘要 + AC 关联
       → 人工 checklist 与验收结论：引用同一提交基线
       → 收尾事务：目标文件原基线 + 暂存内容 + 操作顺序
```

| 基线 | 当前实现来源 | 判断目的 |
| --- | --- | --- |
| PRD 正文基线 | [`hashing.py`](../../../skills/neo-skill/runtime/flow_core/hashing.py) 的 `prd_content_hash` | `prd.md`、锁定 metadata 字段、存在的 test-cases.md 是否改变；评审/CR/状态历史追加不影响正文 hash |
| 开发引用 | [`development.py`](../../../skills/neo-skill/runtime/flow_core/development.py) 的 `evaluate_gate`，Spec `.baseline.yaml` | 这批技术工作是否仍归属于批准过的版本 |
| 代码与验证证据 | [`verification.py`](../../../skills/neo-skill/runtime/flow_core/verification.py) 的 `code_version`、`evaluate` | 验证之后源代码/测试或证据是否变化 |
| 验收有效性 | [`acceptance.py`](../../../skills/neo-skill/runtime/flow_core/acceptance.py) 的 `evaluate` | 人确认的是不是当前 PRD、代码、验证及清单 |
| 收尾一致性 | [`closeout.py`](../../../skills/neo-skill/runtime/flow_core/closeout.py) 的 `evaluate_closeout` | 人工通过之后，功能现状、场景、索引、主需求是否同步完成 |

Git 项目记录 HEAD；代码根目录有未提交改动时附 dirty 内容摘要。非 Git 项目使用源码/测试内容摘要。默认根目录为 `src/`、`tests/`，来自规则 `dev.code_roots`。验证日志本身不应被算作代码变化；新版本调整根目录时，要审查忽略文件、生成物与作用范围，而非只看 commit。

## CR 的正常闭环与限制

在 APPROVED/DEVELOPING/READY_FOR_ACCEPTANCE：

1. 人确认 `prd cr create`，记录原因、内容及 Spec/Tasks/测试/设计影响面，必要时说明版本影响。
2. PRD 回 IN_REVIEW；保留旧工作，标记基线失效。普通 `prd approve` 遇未决 CR 不应代替 `prd cr approve`。
3. 在 CR 路径下修改并重新评审；需求负责人重新批准后版本递增、重算 hash。
4. 旧批准、旧验收保留为历史；后续工作重新绑定基线并验证，不自动继承旧结论。

**ACCEPTED/SUPERSEDED 的实现缺口**：当前允许记录 CR/失效标记，但状态表没有回 IN_REVIEW 的边，后续 `cr approve` 不能完成重新批准，拒绝 CR 也不是通用恢复入口。这应作为新版本的明确设计问题；不要指导用户手改状态/hash 来继续流程。

## 证据能够说明什么

- `dev verify` 保留执行命令、完整 stdout/stderr、退出码、时间与环境，并关联 AC。
- `verify record` 是登记外部执行结果的入口；仅有“pass”字段并不能证明执行过，仍需原始证据与人工审查。
- `submit-acceptance` 检查状态、覆盖、证据有效性并执行 guard。失败/阻塞/未执行不能冒充通过；不适用需给理由。
- guard 当前是有限检测：针对 Python 测试文本、测试名/裸 assert 数量、skip 等线索；AC 差异检测也不能代替正文 hash。它无法证明任意语言的断言语义未被弱化，不是防伪造安全边界。
- 验收结论与证据是两层：AI 的 suggestion 不等于人工 conclusion；技术通过不等于人工通过；人工通过不等于收尾完成。

## 收尾与恢复

收尾业务同步按固定顺序：功能现状 → 模块 README 场景区块 → 三个受管理索引 → 主需求 DONE。主需求是这些**业务同步目标**中的最后一项；事务后还需保存 PRD 的 closeout 完成投影，不能把“主需求最后”理解为最后一次磁盘写入。

事务目录在 `.neo-skill/transactions/`。PREPARED、COMMITTING、COMMITTED 是事务生命周期，失败还会保留 FAILED/错误记录；PRD metadata 的 closeout 投影不一定逐个呈现每个中间态。

恢复需重新取锁并检查所有目标：当前内容等于原基线的继续应用；等于暂存目标的视为已应用并跳过；两者都不等则 BASELINE_CONFLICT，保持人工修改，交由人判断。不能只比较“是否还等于旧基线”，否则已应用的半程目标会被误判为冲突。

| 现象 | 查询与下一步 | 必须保留 |
| --- | --- | --- |
| 缺团队/约束或职责不匹配 | doctor、对象 show、team/constitution 核对；取得真实确认后补配置 | 原状态与已有业务文件 |
| hash/基线失效 | prd verify-hash、baseline-status；判断直接编辑、CR 或证据变化原因 | 原版本、旧批准与工作成果 |
| 验证失败或覆盖不全 | dev verification-status；修复后执行适用验证，达到轮次上限请人介入 | 原始失败日志，不能删用例制造通过 |
| fail/blocked/conditional | acceptance status；继续记录或 reject 返工，重新验收 | 未解决问题与逐项证据 |
| 已通过但收尾未完成 | closeout-status、recover list；closeout --retry 或 recover run --tx | 人工结论、事务、待办目标 |
| 受管理区块被修改 | index status；展示差异，人确认覆盖后再生成 | 区块外说明原样保留 |
| 锁冲突/残留锁 | doctor；确认持有进程结束后显式 recover release-lock | 存活进程锁不能被自动删除 |

普通门禁拒绝应无业务写入；执行验证失败会留下失败证据，收尾部分失败会留下事务与已应用目标。后两类属于可审计失败，不能为了“零副作用”的字面说法删掉恢复依据。
