# 03: 注册可组合 Skills 与路由边界

**What to build:** 将进度查询作为独立 Skill 注册，并在同一 Skills 仓库中明确入口/编排能力与可复用基础能力的职责边界。同步 Skill 清单、路由总览、阶段交接和相关治理文档，使新增能力可被可靠发现，同时不把示例名称或仓库工程流程误写成 Neo 产品状态机能力。

**Blocked by:** 02: 查询远端上游进度

**Status:** resolved

- [x] 新增进度查询 Skill 的可靠触发描述和职责边界。
- [x] Skill catalog、路由总览和受影响的阶段/治理文档互相一致。
- [x] 文档明确入口/编排 Skill 可以调用基础 Skill，但不把无关流程堆进单个 Skill。
- [x] 不将 `implement`、`grill-me`、`tdd` 或 `code-review` 等示例名称固化为本版本必需能力。
- [x] 明确通用工程能力与 Neo 产品阶段能力的边界，保留人工确认和 `flow-core` 规则。
- [x] 通过文档结构检查或等价验证证明 Skill 名称、描述、触发条件和交接关系一致。
