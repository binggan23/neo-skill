# AGENTS.md

## Agent skills

### Issue tracker

Issues and specs live as local markdown under `.scratch/<feature-slug>/`. See `docs/agents/issue-tracker.md`.

### Triage labels

Default five triage roles, label string equal to the role name. See `docs/agents/triage-labels.md`.

### 探索与代码导航：subagent + CodeGraph

探索（文件地图、实现落点、调用链与影响面、外部一手资料）**派 subagent 去读**（`Explore` 类型，见
`.pi/subagents-lite.json`），主线程接着干活；产物写成 Markdown 笔记放在**仓库外**，后续 agent 用指针复用。

**代码定位走 CodeGraph**：`codegraph_explore`（宽问题）→ `codegraph_search`（按名找）→
`codegraph_node` / `codegraph_callers` / `codegraph_callees` → `codegraph_impact`（改动半径）。
`rg` / `read` 兜底：字面量常量、生成代码、迁移/配置、`*.md`、索引漏掉的符号。

**探索第一动作是 `codegraph_status`**，不是 `find` / `grep`。索引缺失、有变更、工具不可用
三种分支的处理见 `docs/agents/codegraph.md`。**每个新 worktree 必须先 `codegraph init`**
（worktree 不共享主仓库索引，这是本仓库的结构性陷阱，详见同一文件）。

对 `/research`、`/implement`、`/implement-spec`（含其 exploration subagent）同样生效；**skill 文件保持原样**；
给子 agent 的 brief 里必须显式带上「先 `codegraph_status`，再探索」，否则子 agent 继承不到。
（外部资料回到一手来源，不停在二手转述。）

### code-review 之后：派 `fix` 修

聚合后只要有一条可执行 finding，就前台 `Agent` + `agent: "fix"`（`.agents/agents/fix.md`，brief 需求与报告格式见其
prompt），全部 finding 一次修完，修完按同一固定点再跑一轮 `code-review` 复核，最多两轮；`Escalated` 与第二轮复现项交人。

### 提交前先跑 `./scripts/check.sh --fast`

一条命令跑完全部静态检查（`compileall` + `ruff` + `mypy`），秒级返回。
`./scripts/check.sh` 不加参数会再按文件分批跑测试。CI 用的是同一套命令，
所以本地绿等于 CI 绿，不必猜。

`ruff` 与本仓库只开「能抓真错误」的规则（未用导入/变量、盲 except、bugbear）。
刻意不开排版类规则：噪音会让人整个关掉检查，反而失去价值。

### 派子 agent：给窄 scope + 强制产出节奏

长时间无工具调用的 agent 会被 watchdog 杀掉。已知最常触发的场景是
**一次读太多大文件**（例如让一个 reviewer 一次评 90 文件 / 26k 行）。

给子 agent 的 brief 里固定写三条：

1. **窄 scope**：点名它只负责哪些文件/哪些模块，其余不要看。
2. **节奏**：每 10–15 分钟至少一次工具调用；每完成一个可测小块就提交。
3. **别通读**：先读主线程准备好的摘要笔记，再按需查细节。

材料多到一次读不完时，切给多个 agent，不要指望一个 agent 扛。

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
