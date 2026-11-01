---
name: neo-skill
description: Neo Skill P0 研发闭环入口。当用户要初始化或诊断项目工作区、管理需求与排期、编写/评审/批准 PRD、从 PRD 启动开发、提交验收或执行人工验收时使用。
---

# Neo Skill

Neo Skill 把「需求 → 排期 → PRD → 开发 → 验证 → 人工验收」变成一条**可审计的本地文件流程**：
Skill 负责理解意图、检索事实、grilling 和生成候选内容；`flow-core` 负责编号、状态机、基线 hash、
门禁、锁、结构检查和受控写入。人工负责排期、PRD 批准和验收通过——AI 不得代确认。

术语（见项目 `GLOSSARY.md`）：Skill、flow-core、grilling、人工确认、主需求、基线、收尾、功能现状、受管理区块。

## 安装

```bash
npx skills@latest add binggan23/neo-skill
npx skills@latest add binggan23/neo-skill -a claude
```

`flow-core`、声明式规则（`runtime/rules/flow.yaml`）和模板（`runtime/templates/`）随 skill 目录一起分发，
安装后的项目不依赖源码仓库路径。

## 升级

```bash
npx skills@latest update
```

只使用这一条官方升级命令。没有自定义升级器，升级后也不需要额外检查步骤。
项目已有的 `.specify/rules/flow.yaml` 与 `.specify/templates/` 是**项目基线**，升级不会静默改写；
需要补齐时运行 `flow-core init`（只补齐缺失内容，冲突先出计划）。

## 定位 flow-core

不要硬编码绝对路径，按以下顺序解析，取第一个存在的：

1. `$NEO_SKILL_HOME` → `$NEO_SKILL_HOME/runtime/flow-core`
2. 本 skill 目录的兄弟目录 → `../neo-skill/runtime/flow-core`（安装后 `.claude/skills/` 下并列）
3. 当前 skill 自身 → `./runtime/flow-core`
4. 仓库布局 → `<repo>/skills/neo-skill/runtime/flow-core`

拿到路径后用 `python3 <path>` 调用即可（入口脚本自带 shebang，也可 `chmod +x` 后直接执行）。
不确定时先跑 `doctor`，它会在 `data.runtime` 里报告自己算出的路径。

## 命令索引

所有命令都接受全局参数 `--root PATH`、`--json`、`--actor ID`、`--confirm`。
`--json` 返回统一信封：`success`、`command`、`artifacts`、`errors`、`next_actions`。
退出码：`0` 成功（含幂等无变化）、`1` 业务/校验失败、`2` 参数用法错误。

| 命令组 | 用途 | 状态 |
| --- | --- | --- |
| `init` | 初始化项目骨架、项目规则基线、模板；幂等，不覆盖已有内容 | 已可用 |
| `doctor` | 检查 Python / Node / Skill 资源 / `flow-core` / 残留锁，给出修复建议 | 已可用 |
| `req` | 需求草稿、grilling 共识确认、人工排期 | 已可用 |
| `prd` | PRD 草稿、结构自审、提交评审、人工批准与基线 hash（CR 属后续 ticket） | 已可用 |
| `dev` | 开发门禁、绑定 PRD、Spec/Plan/Tasks、状态与范围检查（验证与提交验收属后续 ticket） | 已可用 |
| `acceptance` | Demo/checklist、人工验收结论、收尾同步 | 计划中 |
| `lint` | 结构、引用、hash、基线与收尾一致性检查 | 计划中 |
| `index` | 生成受管理索引区块（backlog / feature-map / PRD 索引） | 计划中 |
| `recover` | 残留锁与中断事务的显式恢复 | 计划中 |

「计划中」的命令当前不注册：调用会返回用法错误，而不是伪装成功。

### 常用起步

```bash
python3 <flow-core> doctor          # 先确认环境与资源
python3 <flow-core> init            # 建立项目骨架与规则基线
python3 <flow-core> init --prune discovery,design,growth   # 小项目裁剪非核心目录
python3 <flow-core> init --apply-conflicts                 # 按变更计划覆盖冲突文件
```

`init` 的冲突处理：已有文件内容与生成内容不同时**默认不覆盖**，该文件以
`action: planned` 出现在 `artifacts` 里，`next_actions` 给出 `--apply-conflicts`。
`AGENTS.md` 只维护 `<!-- neo-skill:begin -->` 与 `<!-- neo-skill:end -->` 之间的区块。

## 人工确认协议

排期、PRD 批准、验收结论等**人工决策**必须同时提供 `--actor` 与 `--confirm`：

```bash
python3 <flow-core> req plan --id REQ-001 --actor alice --confirm --release v0.1.0
```

处理顺序固定，任一步失败都不改变状态：

1. 解析对象；2. 校验 `00-management/team.md`（存在 + actor 存在 + 职责匹配）；
3. 校验状态跳转；4. 获取对象锁；5. 追加 `history` 与 `confirmations`；6. 原子写；7. 释放锁。

- 缺 `--actor` 或 `--confirm` → `CONFIRMATION_REQUIRED`。
- 团队未配置 → `TEAM_NOT_CONFIGURED`；身份未登记 → `ACTOR_UNKNOWN`；职责不匹配 → `ROLE_MISMATCH`。
- 非法跳转 → `INVALID_TRANSITION`；对象被其他流程锁定 → `LOCK_CONFLICT`（附持有 pid / 时间 / 命令）。

Agent 必须先把决定和影响讲清楚，再询问用户是否确认；不得替用户选择、不得预先填好 `--confirm`。
`team.md` 只做职责匹配与审计记录，不做登录、真实身份认证或权限控制。

## 阶段细节

各阶段的操作步骤、检查清单与话术由对应的阶段 Skill 提供：`project`、`requirements`、`prd`、`dev`、`acceptance`。
本文件只做总览与命令索引，不重复阶段细节。
