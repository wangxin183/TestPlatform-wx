# 项目记忆索引（压缩上下文入口）

按需加载，避免整段对话重放。**常读不超过 5 份**；日记进 `memory/archive/`，勿与域约定并列膨胀。

## 1. 北极星（先读）

- [项目北极星](memory-feedback-project-north-star.md) — 确定性 × Agent 自主；禁止短视硬编码；改动前三问

## 2. 工程实践

- [500 错误处理](memory-feedback-500.md) — 读堆栈不猜测；模型变更同步 DB schema

（原索引中的 `feedback_workflow.md` / `feedback_context_cost.md` / `feedback_debugging.md` 文件不存在，已移除死链。调试原则见北极星 + 500；上下文成本见 CLAUDE.md / TCG 组批约定。）

## 3. 域约定（按模块读）

- [TCG 用例生成](memory-feedback-testcase-generation.md) — 边界、token 组批、前缀剥离、编译诊断、成功态
- [执行运行时 EXE](memory-feedback-execution-runtime.md) — NL/DSL、模块入口、跨页合同、登录/安全检测、断言词表、EXE-0013

## 4. 权威工程文档（非 memory，但常对照）

- `CLAUDE.md` — 北极星摘要 + 技术栈 + 架构约束
- `AGENTS.md` — 前端规范 + 禁止短视硬编码
- `docs/superpowers/plans/2026-07-22-agent-engineering-self-heal.md` — StageAgentHarness
- `docs/agent_runtime.md` — role 路由说明

## 5. 归档（低频）

- [2026-07-23 硬化回顾](memory/archive/2026-07-23-hardening.md) — 当日踩坑日记；永久条款已吸收进 §1/§3

## 维护规则

1. **新会话沉淀**：永久约定写进 §1/§2/§3 对应文件；日记/长复盘放 `memory/archive/YYYY-MM-DD-*.md`，索引 §5 加一行即可。
2. **禁止**再建根目录 `memory-feedback-*.md` 日记；工具边车（如已删除的 xmind-mcp）不单独立 memory，要点写进 CLAUDE.md 即可。
3. 改完约定后更新本索引链接，勿留死链。
