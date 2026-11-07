# 04: 验证完整 Skill 集合的安装发现

**What to build:** 让安装模拟和分发验证能够识别 v0.1.1 的完整独立 Skill 集合，确保新增进度 Skill 与既有 Neo runtime 一起安装时可发现、可定位、可运行，并保持现有安装隔离和相对路径行为。

**Blocked by:** 03: 注册可组合 Skills 与路由边界

**Status:** ready-for-agent

- [ ] 安装模拟发现并复制完整的独立 Skill 集合，每个 Skill 都有有效的 `name` 和 `description`。
- [ ] 分发产物包含新增 Skill 以及既有 Neo runtime、规则和模板。
- [ ] 安装后的 `flow-core` 仍从安装布局的相对位置解析，不依赖源码仓库路径。
- [ ] 黑盒测试覆盖新增 Skill 的发现、隔离安装、分发内容和既有 runtime 回归。
- [ ] 相关 CI/安装检查失败时返回非零退出码，不静默放过缺失 Skill。
- [ ] 通过仓库要求的静态检查、安装模拟和相关分批测试。
