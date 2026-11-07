# Neo Skill P0

以 Skill 为入口、以本地文件为持久化边界、以 `flow-core` CLI 为确定性内核的最小研发闭环。

Skill 负责自然语言理解、事实检索、grilling、候选文档生成和人工确认提示；
`flow-core` 负责编号、项目骨架、状态机、基线 hash、开发门禁、结构检查、索引、锁与事务；
排期、PRD 批准和验收通过由人明确申报身份后决定，CLI 只做职责匹配与审计记录。

## 安装

```bash
npx skills@latest add binggan23/neo-skill
npx skills@latest add binggan23/neo-skill -a claude
```

升级只使用 `npx skills@latest update`。

> **已实测可用**：上面两条命令均已验证通过，Skill 全部被发现安装，
> `neo-skill/runtime/`（`flow-core`、规则、模板）随包分发，安装后不依赖源码仓库路径。
> （当时的实测范围是 v0.1.0 的 6 个 Skill；v0.1.1 新增只读 Skill `upstream-progress`，
> 它的安装与分发发现需要在安装模拟/分发包的发现列表里同步才能随 `npx` 安装被识别。）
> 完整实测证据与兼容性边界见 `docs/pilot-report.md`。
> 若需在无网络环境离线分发，用 `scripts/build-dist.sh` 产出 `dist/neo-skill-<ver>.zip`。

包内包含 7 个 Skill：路由层 `neo-skill`、5 个阶段 Skill
`project`（初始化/诊断）、`requirements`（需求与排期）、`prd`、`dev`、`acceptance`，
以及只读的 `upstream-progress`（查已绑定上游的远端进度）。
分层：前五个阶段 Skill 与路由层属**入口/编排类**；`upstream-progress` 两层兼有——
用户直接问进度时它是入口，被别的 Skill 借用“远端事实才是进度来源”这条纪律时它是基础。
边界：入口可以调用基础 Skill，基础 Skill 不得隐式启动无关的入口工作流。

## 快速开始

```bash
python3 skills/neo-skill/runtime/flow-core doctor --root /path/to/project
python3 skills/neo-skill/runtime/flow-core init   --root /path/to/project
python3 skills/neo-skill/runtime/flow-core init   --root /path/to/project --json
```

`init` 幂等、不覆盖已有业务文件；冲突只出变更计划，需要 `--apply-conflicts` 才写。
`--prune discovery,design,growth,releases,meetings,reports` 可裁剪非核心目录组，
核心项目管理、需求池、PRD 门禁、人工验收与 `.specify/`、`.neo-skill/` 不可裁剪。

## 仓库结构

```
skills/neo-skill/SKILL.md              总览/路由 Skill
skills/neo-skill/runtime/flow-core     可执行入口
skills/neo-skill/runtime/flow_core/    Python 包（cli、output、workspace、fsutil、rules、…）
skills/neo-skill/runtime/rules/flow.yaml   声明式规则（状态机、编号、裁剪、门禁）单一事实来源
skills/neo-skill/runtime/templates/    文档模板
skills/project/SKILL.md                阶段 Skill：doctor / init / team / constitution
skills/requirements/SKILL.md           阶段 Skill：需求、grilling、人工排期
skills/prd/SKILL.md                    阶段 Skill：PRD、评审、批准、CR
skills/dev/SKILL.md                    阶段 Skill：开发门禁、Spec/Plan/Tasks、验证
skills/acceptance/SKILL.md             阶段 Skill：Demo、逐条 AC、验收、收尾
skills/upstream-progress/SKILL.md      基础 Skill：只读查询上游远端进度（本地提醒单列，不写回）
tests/                                 flow-core CLI 黑盒测试
scripts/build-dist.sh                  打包 zip + tar.gz + sha256（可重现）
scripts/install-sim.sh                 模拟 skills.sh 安装到 .claude/skills/
scripts/pilot.sh                       隔离端到端试点
```

安装后 `runtime/` 随 skill 目录一起复制，运行时只用相对路径解析，不依赖源码仓库位置。

## 命令

| 命令组 | 状态 |
| --- | --- |
| `init`、`doctor`、`req`、`prd`、`dev`（门禁/启动/状态/范围检查/验证/提交验收） | 已可用 |
| `acceptance`（Demo/checklist、逐条 AC 人工结论、验收通过/不通过/有条件通过、收尾同步 `closeout`） | 已可用 |
| `index`（受管理索引区块）、`recover`（中断事务与残留锁） | 已可用 |
| `lint`（`check`/`rules`，`--only CODE,CODE`） | 已可用 |
| `upstream`（绑定：`bind`/`show`/`status`）、`progress`（`report`，只读查询远端进度） | 已可用 |

所有命令支持 `--root`、`--json`、`--actor`、`--confirm`；`--json` 统一返回
`success` / `command` / `artifacts` / `errors` / `next_actions`。退出码 `0`/`1`/`2`。

`lint` 是只读的结构检查（必需产物、ID/目录一致性、孤儿 Spec、失效引用、非法状态、
基线/验证/验收失效、收尾未完成、索引被改写、规则基线漂移）：有 error → 退出码 1，
只有 warning → 0。输出在打印前统一脱敏（凭据不会回显）。

## CI

`.github/workflows/ci.yml`：Linux × Python 3.11/3.12/3.13 矩阵，装 `pyyaml pytest`，
跑 `compileall`，**按文件循环**跑 pytest（不跑 `pytest tests/` 全量），并在临时初始化项目上
跑 `doctor`、`lint` 与一次「违规必须非零退出」的负向验证。
**lint/CI 是流程治理，不是不可绕过的安全边界**：它们能发现绕过 Skill 直接改文件留下的不一致，
无法实时阻止这种编辑（SRS §9）。

## 开发

```bash
./scripts/check.sh --fast    # 静态检查：compileall + ruff + mypy（秒级）
./scripts/check.sh           # 再加上按文件分批的黑盒测试
./scripts/check.sh --tests   # 只跑测试
```

CI 用的是同一套命令（`.github/workflows/ci.yml`），所以本地绿等于 CI 绿。

单独跑某个文件（调试时用）：

```bash
python3 -m pytest tests/test_03_prd.py -q     # PRD、自检、批准
python3 -m pytest tests/test_09_perf.py -q    # 1000 需求/200 PRD 性能基准
python3 -m pytest tests/test_10_pilot.py -q   # 隔离端到端试点（含 Claude Code 真调用）
```

**为什么要分批**：每个用例都起 CLI 子进程，单文件 10–90 秒，`pytest tests/` 全量要 6–10 分钟。
分批能立刻定位哪一批坏了，也避免把大夹具一次挂进内存。

**静态检查的意义**：`compileall` 只查语法，对 `Optional` 未收窄、`dict|None` 上 `.get()`、
参数类型不匹配这类问题是全绿的 —— 这些恰恰是只有运行到那条路径才炸的 bug。`ruff` 与
`mypy` 就是补这一层。规则集刻意只开「能抓真错误」的部分，不开排版偏好。

依赖：Python 3.11+ 与 PyYAML 6.x；无数据库、无服务端、不联网。

## 文档

- `docs/srs.md`：软件需求规格（业务规则权威来源）
- `docs/hld.md`：高层设计（架构结论）
- `docs/pilot-report.md`：P0 端到端试点报告（实测证据、未承诺边界、安装复验）
- `GLOSSARY.md`：领域词汇
- `AI产品研发流程与Skill体系.md`：目录、编号与模板原始方案
