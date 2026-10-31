# 05: 从有效 PRD 启动开发

**What to build:** 让开发者从明确的“开发某个 PRD”入口开始工作。系统必须验证 PRD 存在、状态允许、批准记录有效、hash 一致、项目约束已确认且引用关系正确；通过后建立与同一 PRD 绑定的 Spec、Plan 和 Tasks，并将 PRD 置为 `DEVELOPING`。任何门禁失败都不得生成正式开发产物或修改正式代码。

**Blocked by:** 03: 完成 PRD 草稿、评审与批准基线

**Status:** ready-for-agent

- [ ] 开发入口必须显式绑定一个 PRD，并在结果中返回绑定对象和当前基线。
- [ ] 门禁检查 PRD 存在、状态允许、批准记录、hash、项目约束确认、模块/编号引用和主需求关系。
- [ ] 未绑定、不存在、未批准、hash 失效、状态不允许或项目约束未确认时返回结构化错误、非零退出码和下一步建议。
- [ ] 门禁失败时不生成 Spec、Plan、Tasks，不改变 PRD 状态，不修改正式代码或测试。
- [ ] 门禁通过后建立 Spec、Plan、Tasks 与 PRD 版本和 hash 的关联，并将 PRD 置为 `DEVELOPING`。
- [ ] AI 或 CLI 发现需求不明确、Spec/Tasks 越界或代码变更疑似扩大范围时停止相关工作，要求 CR 或人工澄清。
- [ ] CLI 黑盒测试覆盖未绑定、DRAFT、hash 失效、constitution 未确认、引用不匹配、合法启动和越界停止。
