# v0.1.1 可组合 Skills 与上游进度查询

Status: resolved
Version: v0.1.1

## Problem Statement

Neo Skill 当前把 P0 产品阶段能力集中在一组阶段 Skill 中，缺少面向任意仓库、任意项目的可组合 Skills 组织约定。随着能力增加，单个 Skill 容易同时承担入口路由、工作流编排和具体纪律，调用边界不清晰，复用和维护成本上升。

项目接入时也没有标准方式记录“这个项目的上游项目是什么、应从哪些远端资料判断进度”。因此，用户无法稳定地让 Agent 查询上游进度；Agent 也可能误把本地未提交或未推送的工作当成上游已经完成的进度。

本版本需要保留 Neo Skill 的产品阶段边界，同时建立一套适用于所有仓库项目的 Skills 分层和上游进度查询约定。远端进度事实、本地工作区提醒和项目绑定配置必须彼此区分。

## Solution

在同一个 Skills 仓库中按职责拆分独立 Skill：需要被用户调用的入口/编排能力与可复用的基础能力分别描述、分别触发；入口可以调用基础能力，但不得把无关工作流堆进单个 Skill。具体入口名称不在本 spec 中预先锁定，后续实现根据现有能力和调用场景决定。

扩展现有 `project` 接入流程，在项目初始化或接入时询问用户是否绑定上游项目。收集上游 URL 或项目标识，以及用户指定的远端进度资料来源；展示写入内容和目标文档后，经用户确认记录到项目级 Agent 指引，若没有则写入 README 的明确小节，并保留其他内容。

新增一个面向用户的进度查询能力。它读取项目指引中的上游绑定配置，仅用于定位远端；查询远端资料并报告已发布事实、进行中事项、来源和不确定性。它可以读取本地 Git 元数据，单独提示 dirty、ahead/behind 等未提交或未推送风险，但不得读取本地代码或业务正文来推断远端进度。没有绑定时允许用户临时提供上游信息查询，不自动写回文档。

## User Stories

1. As a project maintainer, I want Skills to be separated by responsibility, so that each Skill has a clear trigger and a focused job.
2. As a Skill author, I want an entry Skill to compose reusable foundation Skills, so that shared disciplines are implemented and explained once.
3. As a Skill user, I want to invoke only the workflow entry I need, so that I do not need to understand every underlying Skill before starting.
4. As a Skill user, I want reusable foundation capabilities to remain independently reachable when appropriate, so that I can apply one discipline without running an unrelated workflow.
5. As a maintainer, I want the repository to distinguish user-invoked orchestration from reusable model-invoked discipline, so that trigger behavior is predictable.
6. As a maintainer, I want Neo product-stage Skills and general engineering Skills to live in the same Skills repository as independent files, so that installation remains coherent without collapsing responsibilities.
7. As a project owner, I want project onboarding to ask whether my project has an upstream project, so that progress queries have an explicit target.
8. As a project owner, I want to provide an upstream URL or project identifier, so that an Agent can locate the project without guessing from the current checkout.
9. As a project owner, I want to specify the remote sources that define progress, so that the Agent uses the sources I consider authoritative.
10. As a project owner, I want to review the upstream binding before it is written, so that the project does not record an incorrect source silently.
11. As a project owner, I want the binding stored in the project-level Agent guidance when available, so that future Agent sessions can discover it consistently.
12. As a project owner, I want a README fallback when no Agent guidance exists, so that the binding is still visible and recoverable by humans.
13. As a project owner, I want onboarding to preserve unrelated guidance and documentation, so that adding an upstream binding does not overwrite project knowledge.
14. As a project owner, I want to continue onboarding without an upstream binding, so that upstream tracking remains optional for projects that do not have one.
15. As a user, I want to ask for the current progress of the bound upstream project, so that I can understand what has actually advanced upstream.
16. As a user, I want the progress report to cite the remote sources it used, so that I can verify the conclusion.
17. As a user, I want published releases, tags, or equivalent completed facts separated from open issues, pull requests, roadmap items, or equivalent ongoing work, so that progress is not overstated.
18. As a user, I want the progress query to state when a source is inaccessible, incomplete, stale, or ambiguous, so that uncertainty is visible instead of being presented as fact.
19. As a user, I want the progress query to read only the binding fields from local guidance, so that local business content does not become an accidental progress source.
20. As a user, I want the progress query to report local Git dirty state separately, so that I know there may be work not represented upstream.
21. As a user, I want the progress query to report local commits ahead of or behind a tracked remote when that metadata is available, so that I can identify work that has not been pushed or local drift that needs attention.
22. As a user, I want local reminders clearly labeled as local reminders, so that they cannot be mistaken for upstream progress.
23. As a user, I want to provide an upstream URL and sources temporarily when no project binding exists, so that I can query progress without being forced to modify project documentation.
24. As a maintainer, I want temporary query details not to be persisted automatically, so that a one-off lookup does not change project configuration unexpectedly.
25. As a maintainer, I want the installed Skills to remain discoverable by the installer, so that adding independent Skills does not create silent installation gaps.
26. As a maintainer, I want existing Neo `flow-core` behavior and product-stage boundaries preserved, so that the general Skills work does not turn engineering orchestration into an automatic product state transition.
27. As a maintainer, I want documentation and tests to state the boundary between remote facts, local Git metadata, and local project content, so that future changes do not reintroduce ambiguous progress claims.

## Implementation Decisions

- Treat the existing Skills repository as the distribution boundary for v0.1.1. Keep each capability in its own Skill definition even when capabilities are installed together.
- Use two conceptual layers: user-facing entry/orchestration Skills and reusable foundation Skills. A user-facing Skill may delegate to a foundation Skill; a foundation Skill must not implicitly invoke an unrelated user-facing workflow.
- Do not hard-code example names such as `implement` or `grill-me` into the v0.1.1 contract. They may inform implementation choices, but the acceptance contract is responsibility separation and composability.
- Extend the existing project onboarding behavior rather than creating a second project initialization path. Keep Neo-specific `flow-core` initialization as a distinct section of that Skill.
- Define an upstream binding as a URL or project identifier plus one or more remote progress-source references. The binding is configuration for locating remote evidence, not progress evidence itself.
- Store the confirmed binding in a dedicated, clearly marked section of project-level Agent guidance. If no such guidance file exists, use a dedicated README section. Preserve all unrelated content and avoid whole-file replacement.
- Require an explicit confirmation step before writing the binding. If the user declines or provides incomplete information, leave the project otherwise usable and report that upstream progress is not configured.
- Let the progress query read the project guidance only to obtain binding configuration. It must not scan local source code, business documents, generated artifacts, or local workflow state to calculate upstream progress.
- Use remote sources as the sole evidence for the upstream progress conclusion. The report must distinguish completed/published evidence from open or in-progress evidence, include source references, and state uncertainty or access failures.
- Read-only local Git metadata is a separate reporting channel. At minimum, report dirty state when available; report ahead/behind metadata when a comparable tracked remote exists. Never merge these signals into the remote progress conclusion.
- Permit a one-time query with user-supplied upstream details when no binding exists. Do not persist those details automatically.
- Keep the existing Neo product distinction: Skills may explain, query, orchestrate, and call existing capabilities, but they must not silently replace human product decisions or make `flow-core` state transitions without the existing confirmation protocol.
- Update the Skill catalog, routing/overview documentation, project onboarding guidance, and relevant workflow documentation together with the Skill definitions. Mark any new capability as a Skill only when its `SKILL.md` and installation discovery are both present.
- Update installation simulation and distribution discovery to account for the complete independent Skill set rather than relying on a stale hard-coded list.
- Treat the SRS requirements SRS-FR-090 through SRS-FR-095 as the product contract for this change.

## Testing Decisions

- Test the highest available seam first: install the Skills into an isolated temporary project and observe discovered `SKILL.md` files, frontmatter, copied resources, and relative-path behavior through the existing installation simulation. Do not test Skill prose through internal parser implementation details.
- Extend the existing installation/distribution black-box tests to prove every shipped Skill is discoverable, independent Skill directories are copied, and Neo runtime resources still resolve from the installed layout.
- Add documentation-contract tests or equivalent deterministic checks for the project onboarding binding section: confirmed URL/identifier and progress sources are written to the selected guidance document, unrelated content is preserved, and the fallback document is used only when Agent guidance is absent.
- Cover the onboarding decision branches: confirmed binding, declined binding, incomplete binding, existing Agent guidance, no Agent guidance, and a conflict or pre-existing binding that must not be silently overwritten.
- Cover progress-query behavior at its external boundary with controlled remote fixtures or mocked fetch responses: published evidence, ongoing evidence, inaccessible source, incomplete source, and conflicting/ambiguous source. Assertions should inspect the report's categories, source references, and uncertainty rather than internal helper calls.
- Cover local metadata separation with isolated Git repositories or controlled command results: clean tree, dirty tree, ahead, behind, and no usable remote metadata. Assert that local reminders are present and clearly separate from remote conclusions.
- Cover the missing-binding temporary-query path and assert that the query can run with explicitly supplied details without changing project guidance.
- Preserve existing P0 regression coverage: run the install simulation, relevant distribution checks, and the existing file-by-file test conventions. No test should require a live upstream service or a real user account.
- Follow repository memory guidance: keep remote fixtures small, avoid starting the complete application, and run heavyweight test groups one at a time.
- Use `git diff --check`, the repository fast static checks, and the installer verification as required pre-merge checks. A passing static check does not replace behavioral tests for the new remote/local boundary.

## Out of Scope

- Choosing or implementing a fixed set of example Skills such as `implement`, `grill-me`, `tdd`, or `code-review` as v0.1.1 product requirements.
- Automatically discovering an upstream project from the current Git `origin`, branch name, local directory name, or unconfirmed guess.
- Treating the current repository checkout, local roadmap, local issue files, or local code as evidence of upstream progress.
- Requiring a persistent server, database, account system, authentication, or a new backend for progress queries.
- Automatically writing temporary query details back into project documentation.
- Automatically changing project state, PRD status, release status, or human approval records based on a progress report.
- Implementing a complete release lifecycle, G0-G4 gates, deployment tracking, rollback, or production operations.
- Supporting every remote tracker or authentication flow in this version. The implementation must support the documented source forms and clearly report unsupported or inaccessible sources.
- Splitting the Skills repository into multiple published repositories or packages.
- Rewriting existing project guidance or README files outside the dedicated upstream-binding section.
- Replacing the existing Neo `flow-core` confirmation, baseline, recovery, or acceptance rules.

## Further Notes

- The SRS was updated to v0.1.1 before this spec and records the accepted scope as SRS-FR-090 through SRS-FR-095.
- The upstream reference used for design comparison is `mattpocock/skills`. The relevant transferable idea is the separation between user-invoked orchestration and reusable model-invoked discipline; the wording, names, and repository structure must remain this project's own.
- The project onboarding binding is configuration, not a claim that the upstream project is healthy, current, or complete.
- The progress report must say what it knows, what it inferred only from explicitly labeled remote sources, and what it could not verify.
- The local Git reminder is intentionally not a second progress calculation. A dirty or ahead local checkout may explain why local work is absent upstream, but it cannot prove that upstream work is complete.
- This spec is ready for an implementation agent. Implementation should be split into narrow tickets, with documentation/routing and installer changes kept separate from remote-query behavior where practical.

## 实现结果

四个 ticket 已全部实现并合并到集成分支 `feat/v0-1-1-composable-skills`（HEAD `f7863ea`，起点 `bc422c5`）。

- **交付**：`flow-core` 新增两个命令组——
  `upstream`（`bind` / `show` / `status`：把上游 URL 与远端进度来源写进 `AGENTS.md`
  或 `README.md` 的独立受管理区块 `<!-- neo-skill:upstream:begin/end -->`，区块内是纯 YAML；
  写入前要 `--actor` + `--confirm`，冲突要 `--force` 才覆盖，区块外内容一个字节不动）与
  `progress`（`report`：只读，从绑定或本次临时参数定位远端，产出
  `published` / `ongoing` / `sources` / `uncertainty` / `local_reminders` 五分类报告，
  本地 Git 只作单列的「本地提醒」，绝不并入远端结论，临时查询不写回任何文档）。
  新增 Skill `upstream-progress`（`skills/upstream-progress/SKILL.md`），共 7 个 Skill；
  `07-skill-catalog.md` 把它们分成「入口/编排」与「可复用基础」两层并写清边界与示例名免责声明。
  `__version__` → `0.1.1`。
- **测试**：全仓 **592 个用例**，全部黑盒（进程边界 / 磁盘断言）。本轮新增
  `test_11_upstream.py`（18）、`test_11_progress.py`（22）、`test_12_docs.py`（13），
  并扩展 `test_10_install.py`、`test_10_dist.py`、`test_01_cli.py`、`test_09_ci.py`。
  远端证据用 `file://` 夹具与本地 `http.server`，本地通道用隔离 `git init` 仓库，
  不依赖真实上游服务或账号。
- **安装发现**：`install-sim.sh` 从 `skills/` **自动派生** Skill 集合（不再有手写全量清单），
  校验 frontmatter、源与目标集合一致、核心必需底线；新增 `--skills-root` 供缺陷注入测试。
  CI 的测试循环改为 `tests/test_*.py` 通配，并由 `test_09_ci.py` 反查「每个测试文件都被 CI 覆盖」。
- **验证**：`./scripts/check.sh` 全量通过（compileall / ruff / mypy + 26 个测试文件分批
  + install-sim + 隔离端到端 pilot）。
- **code-review**：两轴评审（Standards / Spec）→ 一次修复（复用 `_normalize_sources` 与
  `NEO_SKILL_NOW` 解析、绑定阶段校验来源 scheme、install-sim 名单机制）→ 二轮复核
  （补回必需底线防源侧静默缺口、修注释口径与末尾换行）→ 三轮收尾确认。
- **已知边界**（spec Out of Scope 内，未做）：只支持 `http` / `https` / `file` 三种来源协议，
  不支持私有 token、分页与 `git+https`；`stale` 阈值固定 180 天且不可配；
  `npx skills@latest add` 的远端真实安装仍以安装模拟为代理（见 `docs/pilot-report.md`）。
