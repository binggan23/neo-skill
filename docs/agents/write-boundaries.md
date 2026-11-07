# 写入边界：谁能改哪些路径

这份文件是**唯一事实来源**。`.agents/agents/fix.md`（repair agent 的 prompt）与
[05-next-version.md](p0-workflow/05-next-version.md)（仓库工程交付）都只留指向这里的指针，
不各自复述一份——复述就是两份事实来源，改一处忘一处，而且会漂成假话。

阅读条件：以 fix / implementer / reviewer 身份改文件之前；或要调整「某个路径能不能动」的规则。

## 先分清「安装产物」与「本仓库源码」

判断只需一句：**这个文件是被安装器生成的，还是本仓库跟踪（tracked）的？**
tracked → 可改；安装器生成 → 不动。

| 路径 | 归属 | 可改？ |
| --- | --- | --- |
| `~/.agents/skills/**`、任何 `.agents/skills/**` | 安装器产物（用户机器上的已安装副本） | **不动**。要让行为生效，改本仓库的 `skills/` 再重新安装 |
| `.pi/**` | 本机配置与会话状态 | **不动**，不进版本控制也不该改 |
| 本仓库 `skills/**` | 产品源码：`skills/neo-skill/runtime/flow_core/**` 是被测的 Python，`skills/*/SKILL.md` 是本仓库交付的 Skill | **可改**，与 `src/` 同级待遇 |
| `scripts/`、`tests/`、`docs/`、`AGENTS.md`、`pyproject.toml` | 仓库工程面 | 可改，按任务范围 |
| `.specify/`、基线 hash、`00-management/team.md`、`07-acceptance/` 记录 | 门禁与人工决定记录 | **不为通过检查而改**。真正的问题是基线错 → 写进报告的 Escalated，不自己动手 |
| `.scratch/<feature>/issues/*.md` | 票 | **关票时可改**：`Status:` 行、`- [ ]` → `- [x]`；正文不改（补充走 `## Comments`） |
| `.scratch/<feature>/spec.md` | 规格 | 关票时**追加** `## 实现结果`；不改已写定的范围 |

这张表早期版本把 `skills/**` 一律列为「安装产物」，导致 repair agent 结构性拒绝真正该修的
finding（runtime 就在那条路径下）。判断依据是 tracked，不是路径前缀。

## Git

- **允许**：在你被指派的分支上提交（Conventional Commits + 中文正文），留干净工作区给调用方合并。
- **禁止**：`push`、`rebase`、`git reset --hard`、`git stash drop`、`git worktree remove`，
  以及任何改写共享历史或删除他人产出的操作。
- `git checkout -- <file>` 会连你自己尚未提交的改动一起吃掉（本仓库真实踩过）。想撤销**别人的**改动前，
  先确认那个文件里有没有你的活；负向验证请在副本上做，不要在受审文件上做。
- brief 若明确要求「留脏工作区」，以 brief 为准。

## 改这份文件本身

这份文件是交付规则的载体，**改它需要仓库所有者授权**——这条不是形式：上一轮有人（AI）
未授权就改写了 `fix.md` 的同类限制，方向虽然对，但流程错。正确做法是先提出冲突、等授权，再改。

## 与工具的分工

能用机械检查钉住的，不要留在散文里：

| 想保证的事 | 放在哪 |
| --- | --- |
| 提交前静态检查通过 | `scripts/check.sh --fast`（缺 ruff / mypy 也算失败） |
| 空白与文件末尾换行 | ruff `W291` / `W292` / `W293` |
| Skill 名单不漂移 | `scripts/install-sim.sh` 从 catalog 派生 + `tests/test_10_install.py` |
| 票的验收项可观察 | `tests/test_12_docs.py` |
| 文档互相一致 | `tests/test_12_docs.py` |

这份文件只写**没有机械检查能替代**的判断（路径归属、授权、git 边界）。
