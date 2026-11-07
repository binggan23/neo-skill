# 流程图索引与维护

阅读条件：人想看流程图，或 AI 需要维护/重建图件。AI 理解流程优先读 [阶段流程](02-stage-flow.md)、[完整状态表](03-state-and-decisions.md) 和 [恢复说明](04-baseline-and-recovery.md)。四张图只把四个阶段 Skill 画成节点；`project`、`neo-skill` 与 `upstream-progress` 的覆盖说明见 [Skill 注册表](07-skill-catalog.md#图中的-skill-覆盖范围)。

## 四张图（源在仓库，HTML 本地重建）

四份 Archify JSON 源已入库；`.archify/` 整体不入库（渲染产物共 3.3MB，且只有人看，AI 应读 Markdown）。已跟踪的四份源不受忽略规则影响，重建后 `git status` 保持干净。

| 图 | 内容 | 源 |
| --- | --- | --- |
| ① 需求到批准 | requirements、排期、prd、评审与批准的概览 | [`01.json`](../../../.archify/workflow-neo-skill-p0-flow-20261103-213934/01.json) |
| ② 开发到收尾 | dev、证据、acceptance、收尾的概览 | [`02.json`](../../../.archify/workflow-neo-skill-p0-flow-20261103-213934/02.json) |
| ③ PRD 状态 | PRD 主链与常用旁支 | [`03a.json`](../../../.archify/workflow-neo-skill-p0-flow-20261103-213934/03a.json) |
| ④ REQ 状态 | 主需求生命周期与常用旁支 | [`03b.json`](../../../.archify/workflow-neo-skill-p0-flow-20261103-213934/03b.json) |

重建并打开（在仓库根目录执行；`<archify>` 是已安装 Archify Skill 的目录）：

```bash
D=.archify/workflow-neo-skill-p0-flow-20261103-213934
node <archify>/bin/archify.mjs finalize workflow $D/01.json $D/01.html --repo-root . --quality showcase --json
# 02.json 用同样的参数；状态图把 workflow 换成 lifecycle
xdg-open $D/01.html
```

源对应 `ff6a747` 的静态快照：内容与校验回执留在本地，不入库。重建后用 `finalize` 的退出码和 `gates` 判断是否通过；自动通过不证明业务语义正确，也没有人工视觉复核记录。

## 读图时必须补足的语义

| 图中简化或错位 | 按当前规则阅读 |
| --- | --- |
| 第一张从 requirements 开始 | 之前还有 neo-skill 路由与 project 的 doctor/init、团队和 constitution 确认，见 02-stage-flow 的阶段 0 |
| 第一张把“拒绝或缺版本”画到拒绝排期节点 | 两者都不进入 PLANNED，但缺版本是继续询问，不能记成人拒绝 |
| 第二张“不通过/有条件通过”共用回退标签 | reject → DEVELOPING；conditional 保持 READY_FOR_ACCEPTANCE，不能直接 ACCEPTED |
| 第二张“全部 pass → 自动进入验收” | 还需有效基线与 guard 通过；submit 只推进状态，prepare 才生成 Demo/checklist |
| “guard 防伪造” | 当前是 Python 文本级线索检测，不证明任意断言语义或外部证据真实 |
| “主需求最后 DONE” | 指收尾业务同步顺序；PRD closeout 完成投影仍需在事务后记录 |
| PRD 的 READY、ACCEPTED 旁“收尾后 DONE” | READY 是 READY_FOR_ACCEPTANCE 缩写；DONE 属于主需求，PRD 保持 ACCEPTED |
| 状态图省略边 | 03a 省略 DRAFT→REJECTED、CHANGES_REQUESTED→DRAFT、READY_FOR_ACCEPTANCE→IN_REVIEW；03b 省略 PLANNED→DEFERRED/REJECTED、DEFERRED→REJECTED。完整表见 03-state-and-decisions |
| CR 回环 | APPROVED/DEVELOPING/READY 可回 IN_REVIEW；ACCEPTED/SUPERSEDED 没有该出边，当前只有失效标记，不能当作完整重开闭环 |
| 节点 sources 的历史行号 | 部分行号与标注符号不对应；以 Markdown 引用的源码路径/符号、规则键重新定位，不把点击到一行当作证实整项行为 |

JSON 源与 HTML 字节均未修改，因此旧校验结果仍对应同一份源。上述修正以文字给出；若后续重画，必须重新生成校验回执并按退出码判断通过。

## 产物保留规则

本地目录共 36 个文件，清理后保留 24 个（4 份源 + 4 个 HTML + 16 份回执）：

- 4 个 JSON 源：**入库**，是本目录引用的可复现输入。
- 4 个 HTML + 16 份回执：**本地保留、不入库**（`.archify/` 已在 `.gitignore` 排除）。回执记录来源 hash 与浏览器检查依据，重建时重新生成。

已删除 12 个只属于失败尝试的文件：`candidate.json`、`main.json`、`03.json`，以及 `neo-skill-p0-flow`、`neo-skill-p0-main-flow`、`03-state-machines` 各自的失败 finalize、summary、delivery-pending 回执。未改动其他 `.archify/` 请求目录。

## 重建时怎么做

1. 阅读安装的 Archify Skill 及对应 workflow/lifecycle schema；不修改安装的 Skill。
2. 用 CodeGraph 与源码重新核实节点、角色、条件、状态边；保留“当前支持/限制/候选”区分。多图按阶段分开，不用删除必要条件换取布局通过。
3. 修改对应 JSON 时先更新 repository revision 与有效 sources；`meta.output` 与输出路径保持一致。
4. 执行 `finalize <type> <source.json> <output.html> --repo-root <repo> --quality showcase --json`。已到 browser-check 的输出若再修复，使用新的 `--out-dir` 保存后续检查证据。
5. 检查非零退出、摘要、更新提示与产物 hash；通过后同步本页限制说明。只有做过视觉复核才能声明已目视确认。

这些 HTML 位于仓库根目录的 `.archify/`，不是网页发布地址；复制本指南时同时保留相对路径下的图件目录。文档的规则说明已自包含，不要求 AI 读取 HTML 才能推进流程。
