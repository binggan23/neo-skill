# AGENTS.md

## Agent skills

### Issue tracker

Issues and specs live as local markdown under `.scratch/<feature-slug>/`. See `docs/agents/issue-tracker.md`.

### Triage labels

Default five triage roles, label string equal to the role name. See `docs/agents/triage-labels.md`.

### 探索与代码导航：subagent + CodeGraph

探索（文件地图、实现落点、调用链与影响面、外部一手资料）**派 subagent 去读**（`Explore` 类型，见
`.pi/subagents-lite.json`），主线程接着干活；产物写成 Markdown 笔记放在**仓库外**，后续 agent 用指针复用。

**代码定位走 CodeGraph**：`codegraph_explore`（宽问题）→ `codegraph_search`（按名找）→ `codegraph_node` /
`codegraph_callers` / `codegraph_callees`（定义、谁调它、它调谁）→ `codegraph_impact`（改动半径）→
`codegraph_files` / `codegraph_status`（目录、索引状态）。`codegraph_status` 的「Changes since index」非 0
就先重建再信结论。`rg` / `read` 兜底：字面量常量、生成代码、迁移/配置、`*.md`、索引漏掉的符号。

**探索的第一动作是 `codegraph_status`，不是 `find` / `grep`。** 这一步不可省略：
「本仓库还没有索引」这个判断只有它能给出，不查就自称没有索引等于跳过整条约定。四种结果四种处理：

| `codegraph_status` 结果 | 处理 |
| --- | --- |
| 有索引，`Changes since index` 为 0 | 正常走上面的 CodeGraph 路径 |
| 有索引，但变更数非 0 | 先 `codegraph index` / `codegraph sync`，再信结论 |
| 明确报「未初始化 / not indexed」 | 跑 `codegraph init`（一次性；之后 daemon 自动 sync），再走 CodeGraph。**本仓库已决定启用**，不必每次再问 |
| 工具本身不可用（报错 / MCP 未连） | 才降级 `rg` / `read`，**并在结论里贴出 status 的原始输出说明原因** |

兜底不是默认路径：宣告走 `rg` / `read` 兜底时必须能贴出 `codegraph_status` 的原始输出，否则视为漏做前置动作。
新克隆、新 worktree、新机器开局都没有 `.codegraph/`（数据库是本机产物，见 `.codegraph/.gitignore`），
所以每个新环境都要重跑一次 `codegraph init`（实测毫秒级）——这是常态，不是例外。

对 `/research`、`/implement`、`/implement-spec`（含其 exploration subagent）同样生效；**skill 文件保持原样**；
给子 agent 的 brief 里必须显式带上「先 `codegraph_status`，再探索」，否则子 agent 继承不到。
（外部资料回到一手来源，不停在二手转述。）

### code-review 之后：派 `fix` 修

聚合后只要有一条可执行 finding，就前台 `Agent` + `agent: "fix"`（`.agents/agents/fix.md`，brief 需求与报告格式见其
prompt），全部 finding 一次修完，修完按同一固定点再跑一轮 `code-review` 复核，最多两轮；`Escalated` 与第二轮复现项交人。

### 测试与内存

写测试时**先控内存，再谈覆盖率**：

- 按包/按目录/按单文件分批跑，不把整个测试套塞进一条命令；重量级命令（测试、构建、E2E）**同一时刻只跑一条**，
  同一个 agent 也不并行跑两条。
- 不写「一次性把整应用挂起来」的测试：避免每个用例都 render 完整应用或加载全量数据集；优先测边界和 seam。
- 大 fixture 不驻留内存：用临时文件、流式读写、按需构造，测完即删；不把大对象塞进模块级常量。
- 出现 OOM、swap 拉满或单文件耗时异常，先改测试或跑法，不要靠加并发硬冲。

**写完测试交给 review 复核**：用 `code-review`（`code-reviewer` agent 类型，一轴一个 spawn）过一遍——重点看
断言是否真的能失败、是否有无谓的全量加载和重复构造、有无更省的跑法。review 发现的问题当场改掉再算完成。

### Domain docs

Single-context: root `GLOSSARY.md` plus `docs/adr/`. See `docs/agents/domain.md`.
