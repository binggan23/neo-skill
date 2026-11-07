---
name: upstream-progress
description: 上游进度查询（只读）。当用户问「上游项目做到哪了/最新发布了什么/上游进度如何/我们的改动推上去了吗」、要核对绑定在上游项目上的远端发布与进行中事项、当前项目没有绑定上游需要临时给 URL 查一次、远端来源打不开或内容过期、或想单独确认本地有没有未提交/未推送的改动时使用。只读，不推进任何流程状态。
---

# upstream-progress：上游进度查询（只读）

本 Skill 负责把「上游现在到底做到哪一步」这个问题回答成**可核查的报告**：
读项目里已登记的上游绑定 → 抓远端资料 → 分「已发布」「进行中」报事实、列来源、
说明不确定性 → 单独提示本地未提交/未推送。
它**不**负责需求、PRD、开发与验收（见 `requirements`、`prd`、`dev`、`acceptance`），
也**不**负责写上游绑定（那是 `project` 阶段七的接入动作，本 Skill 只给命令）、
更**不**负责从本地推测上游进度。

词汇以项目 `GLOSSARY.md` 为准：上游、上游绑定、远端进度来源、本地提醒、受管理区块、人工确认、未配置完成。
规则以 `docs/srs.md` §5.8（SRS-FR-090、092～095）为准。

## 这个 Skill 的层级：入口 + 基础

它同时有两种用法，两种都必须成立：

| 用法 | 谁触发 | 行为 |
| --- | --- | --- |
| **入口性质** | 用户直接问进度 | 走完整流程：定位绑定 → 查询 → 报告五分类 → 交接 |
| **基础性质** | 别的 Skill / 别的判断需要「远端事实」 | 只借「远端证据才是进度来源、本地提醒单列」这条纪律，不启动任何阶段工作流 |

**入口可以调用基础 Skill；基础 Skill 不得隐式启动无关的入口工作流。**
所以被别的 Skill 借用时，本 Skill 不会因为「查过一次进度」就去写 PRD、推状态或跑 `dev gate`。

## 三条硬边界（越界即结论无效）

1. **绑定只用于定位远端**。只读 `AGENTS.md`（其次 `README.md`）里
   `<!-- neo-skill:upstream:begin -->` … `<!-- neo-skill:upstream:end -->` 区块的
   `upstream.id` 与 `upstream.sources`。区块内写着「已发布 v9」也不算证据——那是配置，不是事实。
   区块外的正文一律不读。
2. **远端证据是唯一进度来源**。不扫本地代码、`08-releases/CHANGELOG.md`、业务文档、
   `.scratch/` 里的 issue 草稿或本地 roadmap 来「推断」上游做了什么。
3. **本地 Git 元数据单列**。`dirty` / `ahead` / `behind` / `no_remote` / `unavailable`
   只进报告末尾的「本地提醒（不在上游进度内）」，**绝不**并入已发布或进行中。

## 定位 flow-core

不要硬编码绝对路径，按顺序取第一个存在的：

1. `$NEO_SKILL_HOME` → `$NEO_SKILL_HOME/runtime/flow-core`
2. 本 skill 目录的兄弟目录 → `../neo-skill/runtime/flow-core`（安装后 `.claude/skills/` 下并列）
3. 当前 skill 自身 → `./runtime/flow-core`
4. 仓库布局 → `<repo>/skills/neo-skill/runtime/flow-core`

调用方式：`python3 <path> <命令>`。不确定时先跑 `doctor`，它在 `data.runtime.flow_core` 里报路径。
`--json` 返回统一信封（`success` / `command` / `artifacts` / `errors` / `next_actions` / `data`）。
退出码：`0` 成功、`1` 业务失败、`2` 用法错误。**以信封与退出码为准，不要靠猜输出文本。**

## 你（Agent）能写什么

| 允许 | 禁止 |
| --- | --- |
| 只读地查绑定（`upstream show` / `upstream status`）与远端资料（`progress report`） | 手写或手改绑定区块、手改任何进度文档 |
| 用户明确要求长期跟踪时，**给出** `upstream bind` 的确切命令并说明它的确认要求 | 自己替用户执行 `upstream bind`（那是需要 `--actor` + `--confirm` 的写操作，属 `project` 阶段七） |
| 引用报告里的来源 URL、抓取时间与不确定性说明 | 把本地代码、CHANGELOG、issue 草稿当进度证据补进结论 |
| 明确说「这个来源打不开，所以这部分我不知道」 | 用「应该差不多了」之类的推断填上不可验证的部分 |

**绝不自动写回**：临时查询（`--upstream` + `--source`）不写任何文件；本 Skill 不经手
PRD 状态、基线、验收结论或发布记录。报告只回答「远端现在是什么样」，不改变项目状态。

## 分支判断（先归位，再动手）

| 情形 | 判断依据 | 做什么 |
| --- | --- | --- |
| 有绑定 | `upstream status` → `data.configured: true` | 直接 `progress report`；报告里 `data.upstream.origin` 应为 `binding` |
| 无绑定，用户愿意临时给 | `data.configured: false`，用户能给上游 URL + 来源 | `progress report --upstream … --source …`；`origin: temporary`，**不写回** |
| 无绑定，用户也不给 | 同上，但用户给不出 | 说明「上游进度查询尚未配置」，给出 `upstream bind` 命令；**不要**从 `origin`、分支名、目录名自动推断 |
| 绑定区块在但解析不出来 | `upstream show` → `UPSTREAM_BINDING_INVALID` | 如实报告；可先临时查询，长期修法是以 `--force` 重写区块（仍需人确认） |
| 来源不可访问 | `data.uncertainty[].reason` = `inaccessible` / `unsupported_scheme` | 原样转述原因，其余来源照常报；**不要让整次查询失败** |
| 内容不完整或过期 | `reason` = `incomplete` / `unparsable` / `stale` | 说明「这份资料不承载进度证据 / 可能已过期」，不要据此下结论 |
| 来源互相矛盾 | `reason` = `conflicting` | 报「同一事项两侧都有证据，未计入任何一侧」，请用户裁定哪份权威 |
| 本地有未提交/未推送 | `data.local_reminders[]` | 单独提示，说明「这是本地状态，不代表上游进度」 |

## 命令速查

| 命令 | 用途 | 改动 |
| --- | --- | --- |
| `upstream status` | 只读：`data.configured` 是不是 true（未配置也算成功） | 否 |
| `upstream show` | 读当前绑定：`data.identifier` / `data.sources` / `data.document`（未配置 → 退出码 1，`UPSTREAM_NOT_CONFIGURED`） | 否 |
| `progress report` | 按绑定查远端进度 | 否（只读） |
| `progress report --upstream <URL> --source <来源> [--source …]` | 无绑定时的一次性临时查询（`origin: temporary`） | 否（**不写回**） |
| `upstream bind --id <URL/标识> --source <来源> [--source …] --actor <人> --confirm` | 长期绑定（**交给 `project` 阶段七执行**，本 Skill 只给命令） | 是（写受管理区块） |

`progress report` **不需要** `--actor` / `--confirm`：它不改状态、不写文档。
它接受 `https://` / `http://` / `file://` 来源；其他协议会被报成 `unsupported_scheme`。

## 报告怎么读（`data` 五个分类）

| 字段 | 含义 | 会不会被当成「进度」 |
| --- | --- | --- |
| `published` | 远端已发布/已完成的条目（release、tag、已关闭事项），每条带 `source` | 是，这是唯一「做完了」的证据 |
| `ongoing` | 远端仍开放/进行中的条目（open issue、draft release、`- [ ]` 未勾选） | 是，但只说明「在做」，不是「做完了」 |
| `sources` | 本次用到的每个来源：`url`、`accessible`、`fetched_at`、`kind`、`summary` | 是「从哪来」，不是结论 |
| `uncertainty` | `reason` + `detail`：不可访问 / 协议不支持 / 解析不了 / 过期 / 内容不完整 / 相互矛盾 | 否，但必须原样转述 |
| `local_reminders` | `dirty` / `ahead` / `behind` / `no_remote` / `unavailable` | **否**，永远不进上面两桶 |

`data.upstream` 是本次查谁：`{"identifier": …, "origin": "binding" | "temporary"}`。
人类可读输出以「上游进度报告：…」开头，本地提醒用独立标题「本地提醒（不在上游进度内）：」，
排在远端各节之后——两者**不得混排**。

## 错误码速查（以信封 `errors[].code` 为准）

| 错误码 | 含义 | 怎么办 |
| --- | --- | --- |
| `UPSTREAM_NOT_CONFIGURED` | `upstream show`：项目中根本没有绑定区块（退出码 1） | 如实说“尚未配置”；给 `upstream bind` 命令，或改用临时查询 |
| `UPSTREAM_BINDING_INVALID` | 区块在，但解析不出 `id` / `sources` | 报出 `next_actions`；确认后以 `--force` 重写（写操作，需人确认） |
| `UPSTREAM_BINDING_CONFLICT` | `upstream bind` 碰到不同绑定，拒绝静默覆盖 | 问用户哪份权威；**不要**自己加 `--force` |
| `UPSTREAM_BINDING_INCOMPLETE` | `upstream bind` 缺 `--id` 或 `--source` | 补齐信息；或按“没有上游”继续，不改文档 |
| `UPSTREAM_QUERY_INPUT_REQUIRED` | `progress report` 既无绑定、又没给全 `--upstream` + `--source` | 问用户要上游 URL 与至少一个来源；或给绑定命令 |

`progress report` 本身**不**因为单个来源不可访问而失败：那些只进 `data.uncertainty[]`。
退出码 `1` 只意味着“没定位到要查谁”（上面最后一行），不是“远端没进度”。

### 禁止的表述

- 把 `ongoing` 说成「已完成」或「快好了」。
- 把 `dirty` / `ahead` 说成「上游有新进展」或「我们的工作已同步到上游」。
- 把绑定本身说成「上游状态良好 / 最新」——绑定只说明「去哪里看」。
- 来源不可访问时用推测补齐结论，而不是说「这部分无法核实」。

## 报告之后交给谁

- 用户只是想知道进度 → **到此为止**，不改任何状态，不提「顺手把 PRD 批了」之类。
- 用户因此想开新需求 → 交 `requirements`（附：上游已发布/进行中的事实、来源、仍未确定的部分）。
- 用户发现本地有未推送改动并想推进 → 交 `dev` 或对应的开发流程，由它按门禁处理。
- 用户想长期跟踪同一个上游 → 交 `project` 阶段七（展示待写入内容 → 人确认 → `upstream bind`）。

本 Skill 与 `flow-core` 都不做发布生命周期、部署跟踪或上游状态同步；
它只是「一次可核查的只读观察」。**看到进度就等于可以做下一步** 是错误推论。
