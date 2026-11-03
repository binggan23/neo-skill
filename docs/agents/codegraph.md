# CodeGraph：探索的默认路径

`AGENTS.md` 只留一行指针指向本文件。原因：下面这张表的分支不是每次探索都会走到，
但「先跑 `codegraph_status`」这一条是每次都走的 —— 前者适合披露到这里，后者留在 AGENTS.md。

## 索引状态：每次探索的第一动作

**第一动作是 `codegraph_status`，不是 `find` / `grep`。** 这一步不可省略：
「本仓库还没有索引」这个判断只有它能给出，不查就自称没有索引等于跳过整条约定。

| `codegraph_status` 结果 | 处理 |
| --- | --- |
| 有索引，`Changes since index` 为 0 | 正常走下面的查询路径 |
| 有索引，但变更数非 0 | 先 `codegraph index` / `codegraph sync`，再信结论 |
| 明确报「未初始化 / not indexed」 | 跑 `codegraph init`（一次性；之后 daemon 自动 sync），再走查询路径。**本仓库已决定启用**，不必每次再问 |
| 工具本身不可用（报错 / MCP 未连） | 才降级 `rg` / `read`，**并在结论里贴出 status 的原始输出说明原因** |

兜底不是默认路径：宣告走 `rg` / `read` 兜底时必须能贴出 `codegraph_status` 的原始输出，
否则视为漏做前置动作。

## 查询路径

按问题形状选入口，从宽到窄：

```text
codegraph_explore     宽问题：「这块怎么工作的」「改这个会影响什么」
codegraph_search      按名找符号
codegraph_node        单个符号的定义 + 调用者/被调用者
codegraph_callers     谁调它
codegraph_callees     它调谁
codegraph_impact      改动半径
codegraph_files       目录结构
codegraph_status      索引状态与新鲜度
```

`rg` / `read` 的正当用途：字面量常量、生成代码、迁移/配置、`*.md`、索引漏掉的符号。

## 本仓库的结构性陷阱：worktree 没有索引

`git worktree add` 出来的目录**不带 `.codegraph/`**（数据库是本机产物，被
`.codegraph/.gitignore` 忽略），而且从 worktree 向上找不到主仓库的索引 ——
worktree 在 `../<repo>-worktrees/NN`，主仓库在别处，不在同一路径链上。

后果：在 worktree 里工作的 agent，`codegraph_status` 一律返回 not indexed，
MCP 还会要求「本会话别再调 codegraph」—— 于是结构性地用不上 CodeGraph。

**做法**：每个新 worktree 建好后，在 worktree 根目录跑一次 `codegraph init`
（实测 443ms / 60 文件 / 2046 节点）。派子 agent 前由主线程先做这一步。

## 子 agent

给子 agent 的 brief 里必须显式带「先 `codegraph_status`，再探索」。
父 agent 不写，子 agent 继承不到 —— 父 agent 的上下文不会自动传下去。
