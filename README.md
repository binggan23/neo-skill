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
tests/                                 flow-core CLI 黑盒测试
```

安装后 `runtime/` 随 skill 目录一起复制，运行时只用相对路径解析，不依赖源码仓库位置。

## 命令

| 命令组 | 状态 |
| --- | --- |
| `init`、`doctor`、`req`、`prd`、`dev`（门禁/启动/状态/范围检查/验证/提交验收） | 已可用 |
| `acceptance`（Demo/checklist、逐条 AC 人工结论、验收通过/不通过/有条件通过、收尾同步 `closeout`） | 已可用 |
| `index`（受管理索引区块）、`recover`（中断事务与残留锁） | 已可用 |
| `lint`（`check`/`rules`，`--only CODE,CODE`） | 已可用 |

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
python3 -m pytest tests/test_01_init.py      # 按文件分批跑，不要一次跑全套
python3 -m pytest tests/test_02_requirements.py
python3 -m pytest tests/test_03_prd.py
python3 -m pytest tests/test_03_baseline.py
python3 -m pytest tests/test_05_dev.py
python3 -m pytest tests/test_06_verification.py
python3 -m pytest tests/test_07_acceptance.py
python3 -m pytest tests/test_08_closeout.py
python3 -m pytest tests/test_09_lint.py
python3 -m pytest tests/test_09_request_changes.py
python3 -m pytest tests/test_09_ci.py
python3 -m pytest tests/test_09_security.py
python3 -m pytest tests/test_09_perf.py       # 1000 需求/200 PRD 规模基准
python3 -m compileall skills/neo-skill/runtime
```

依赖：Python 3.11+ 与 PyYAML 6.x；无数据库、无服务端、不联网。

## 文档

- `docs/srs.md`：软件需求规格（业务规则权威来源）
- `docs/hld.md`：高层设计（架构结论）
- `GLOSSARY.md`：领域词汇
- `AI产品研发流程与Skill体系.md`：目录、编号与模板原始方案
