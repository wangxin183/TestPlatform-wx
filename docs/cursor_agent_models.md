# Cursor Coding Agent 可用模型与选型指南

本文档说明在 **Cursor IDE Coding Agent** 环境中可用的模型列表，以及执行任务时的选型建议。

> **与项目内 LLM 的区别**
>
> - 本文档：Cursor IDE 中 Coding Agent / Task 子代理使用的模型白名单。
> - 项目流水线 LLM：见 `config/llm_providers.yaml`（DeepSeek / OpenAI / Anthropic API 路由）。
> - 项目 AgentRuntime：见 [`docs/agent_runtime.md`](agent_runtime.md)（`claude_code` / `codex` / `cursor` 后端按 role 路由）。

---

## 1. 可用模型列表

在 Cursor Coding Agent 环境中，若需**显式指定子任务 / 子代理（Task）使用的模型**，当前允许的模型标识如下（系统白名单）：

| 模型标识 | 简要定位 |
|----------|----------|
| `claude-4.6-opus-high-thinking` | Claude Opus 高推理档 |
| `claude-4.6-sonnet-medium-thinking` | Claude Sonnet 中等推理档 |
| `claude-fable-5-thinking-high` | Fable 高推理档 |
| `claude-opus-4-7-thinking-xhigh` | Opus 超高推理档 |
| `claude-opus-4-8-thinking-high` | Opus 高推理档（复杂推理推荐） |
| `claude-sonnet-5-thinking-high` | Sonnet 高推理档（设计 + 实现平衡） |
| `composer-2.5-fast` | 快速、低成本试探 |
| `gpt-5.3-codex` | 代码工程执行（写代码、改代码、跑命令） |
| `gpt-5.4-medium` | GPT 中等档（一般复杂度任务） |
| `gpt-5.5-medium` | GPT 中等档（工程执行备选） |
| `grok-4.3` | Grok 常规档 |
| `grok-build-0.1` | Grok 构建 / 生成型探索 |

### 说明

- **不指定模型**时，子任务通常使用与当前对话相同的模型。
- 若点名一个**不在上述列表**中的模型，Agent 不能自行替换，只能告知不可用并给出可用列表。

---

## 2. 执行任务时如何选择模型

### 2.1 选型速记

| 场景 | 推荐模型 |
|------|----------|
| 最快出结果 / 低成本试探 | `composer-2.5-fast` |
| 写代码 / 改代码 / 跑命令（偏工程执行） | `gpt-5.3-codex` 或 `gpt-5.5-medium` |
| 复杂推理、方案权衡、疑难 bug 根因 | `claude-opus-4-8-thinking-high`（或 `claude-opus-4-7-thinking-xhigh`） |
| 一般复杂度的设计 + 实现平衡 | `claude-sonnet-5-thinking-high` / `gpt-5.4-medium` |
| 脚手架、原型大量生成等构建型探索 | `grok-build-0.1`（或 `grok-4.3`） |

### 2.2 按任务类型

| 任务类型 | 建议 |
|----------|------|
| 需求 / 架构 / 方案评审、接口设计、边界条件梳理 | Claude Opus / Sonnet 的 thinking 档（长上下文与严谨性更好） |
| 大规模重构、写测试、修 lint、跑脚本、多文件落地 | `gpt-5.3-codex` 或 `gpt-5.5-medium` 通常效率更高 |
| 快速扫一眼、先给一个方向 | 先用 `composer-2.5-fast` 出草案，再用更强模型收敛 |
| 并行子任务 | 快模型做检索 / 总结，强模型做最终决策 / 合并 |

### 2.3 并行子任务示例

```
开两个并行子任务：
- 一个用 composer-2.5-fast 快速扫代码结构
- 另一个用 claude-opus-4-8-thinking-high 给重构方案
```

```
这个 PR 失败检查，用 gpt-5.3-codex 跑命令定位并修复
```

---

## 3. 如何显式指定模型

在与 Cursor Agent 对话时，可直接在指令中写明模型标识，例如：

- 「开两个并行子任务：一个用 `composer-2.5-fast` 快速扫代码结构；另一个用 `claude-opus-4-8-thinking-high` 给重构方案。」
- 「这个 PR 失败检查，用 `gpt-5.3-codex` 跑命令定位并修复。」

也可说明优先级偏好（速度 / 成本 / 质量），由 Agent 按偏好自动选型。

---

## 4. 相关文档

| 文档 / 配置 | 内容 |
|-------------|------|
| [`docs/agent_runtime.md`](agent_runtime.md) | TestPlatform 内 AgentRuntime：按 role 路由到 claude_code / codex / cursor |
| [`config/llm_providers.yaml`](../config/llm_providers.yaml) | 流水线阶段 LLM Provider 与路由规则 |
| [`config/settings.yaml`](../config/settings.yaml) | `agent_runtime.roles` 各 role 的 primary / fallbacks 配置 |
