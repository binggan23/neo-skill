# AGENTS.md

## Agent skills

### Issue tracker

Issues and specs live as local markdown under `.scratch/<feature-slug>/`. See `docs/agents/issue-tracker.md`.

### Triage labels

Default five triage roles, label string equal to the role name. See `docs/agents/triage-labels.md`.

### P0 流程与版本演进

理解阶段 Skill 如何协作、修改状态/门禁/基线/恢复，或规划后续版本前，先读
[流程与演进目录](docs/agents/p0-workflow/Contents.md)，再按任务加载分篇；图为概览，规则与实现差异见文内说明。

### 探索与代码导航：subagent + CodeGraph

探索（文件地图、实现落点、调用链与影响面、外部一手资料）**派 subagent 去读**（`Explore` 类型，见
`.pi/subagents-lite.json`），主线程接着干活；产物写成 Markdown 笔记放在**仓库外**，后续 agent 用指针复用。

**代码定位先 `codegraph_status`，再按问题形状选 `codegraph_explore` / `search` / `node` / `impact`**；
索引缺失、有变更、工具不可用、以及 **worktree 没有索引**四条分支的处理见
[docs/agents/codegraph.md](docs/agents/codegraph.md)。`rg` / `read` 用于字面量常量、生成代码、`*.md`
与索引漏掉的符号。给子 agent 的 brief 里显式带上「先 `codegraph_status`」，否则子 agent 继承不到。
对 `/research`、`/implement`、`/implement-spec`（含其 exploration subagent）同样生效；skill 文件保持原样。

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

给子 agent 的 brief 只写**指针 + 约束 + 报告格式**，不重述材料：

```text
1. 先读这两份（按顺序）：<共享笔记路径>（仓库外，口径权威）、<票/规格路径>
2. 窄 scope：只负责 <文件/模块/条目>，其余不要看
3. 节奏：每 10–15 分钟至少一次工具调用；每完成一个可测小块就提交
4. 开工先确认分支基线是 <集成分支 tip>，不是就 reset 上去；完工前合回来
5. 报告格式：<分支+commit / 改动文件 / 跑过的命令与结果 / 给下一张票的接口契约 / 遗留>
```

**共享笔记**是 brief 的主体：主线程探索一次，写成仓库外的 Markdown（仓库结构、已定口径、
命令名与错误码），后续每个子 agent 只拿指针。brief 里不要再复制一遍笔记的内容——
复制就是两份事实来源，改一处忘一处，而且每份 brief 都在重复付 token。

唯一**必须**在 brief 里重复的，是子 agent 继承不到的东西：它看不到你的上下文，
也看不到别的子 agent 的产出。所以「上一张票定下的接口契约」要原样贴进去，
而不是写「见 t01 的报告」。

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
