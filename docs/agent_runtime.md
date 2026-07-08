# AgentRuntime —— 统一智能体运行时接入指南

面向 TestPlatform 全流程（需求分析 → 用例生成 → 用例评审 → 执行 → 缺陷分析 …）的
通用智能体调用入口。业务代码只需面向 **角色（Role）**，无需关心底层是 Claude
Code / Codex / Cursor 或未来任何一种智能体。

- 模块位置：[`src/agent_runtime/`](../src/agent_runtime/)
- 配置入口：`config/settings.yaml` 的 `agent_runtime:` 段
- 单例引用：`from src.agent_runtime import agent_runtime, AgentTask`

---

## 1. 三条硬约束

1. **业务代码不允许出现 `claude` / `codex` / `cursor` 等具体智能体字符串**，一律通过 role。
2. **不允许直接 `subprocess.run(["claude", ...])` 或 `cursor_sdk.AsyncClient(...)`**，一律走 `agent_runtime.run(AgentTask(...))`。
3. **Role 命名统一使用两段式 `<domain>.<role>`**，方便按测试阶段分组：
   - `requirement.analyzer` / `requirement.reviewer`
   - `testcase.generator` / `testcase.reviewer`
   - `execution.planner`
   - `defect.analyzer`
   - `regression.selector`
   - `utility.diagnoser`（跨阶段通用的自愈诊断角色）

---

## 2. 新阶段接入 checklist（3 步）

### 第 1 步 —— 加 role 配置

在 [`config/settings.yaml`](../config/settings.yaml) 的 `agent_runtime.roles` 段追加：

```yaml
agent_runtime:
  roles:
    testcase.generator:            # 你的新 role
      primary: claude_code         # 首选 backend
      fallbacks: [cursor, codex]   # 依次尝试的备选
      default_timeout_seconds: 600 # 单次调用超时
```

已支持的 backend：`claude_code` / `codex` / `cursor`。新增 backend 见 §4。

### 第 2 步 —— 业务代码调 runtime

```python
from src.agent_runtime import agent_runtime, AgentTask

result = await agent_runtime.run(AgentTask(
    role="testcase.generator",           # 与配置对应
    prompt=prompt,                        # 拼好的完整提示词（system + user）
    workdir=str(alog.dir_path),           # 智能体的工作目录
    timeout=None,                         # None → 使用 role 配置的默认超时
    stage_name="testcase_generation",     # 业务阶段名（用于日志聚合）
    task_id=task.task_id,                 # 业务任务/流水线 ID
))

if not result.success:
    # 走 self_healing 或直接报错
    raise RuntimeError(result.error)

output_text = result.raw_output
# result.backend 告诉你实际是哪个后端跑的（便于日志、告警、账单）
```

### 第 3 步 —— 提供 SKILL.md（可选）

如果你的 role 有专属 prompt 模板，放到 `.agents/skills/<skill-name>/SKILL.md`，
业务层用现有 [`src/llm/prompts/skill_loader.py`](../src/llm/prompts/skill_loader.py) 的
`load_skill()` 加载后拼接到 prompt。

---

## 3. 你**不需要**写的东西

runtime 层已经包办：

- CLI subprocess 调用、超时、异常归一（`src/agent_runtime/cli_shared.py`）
- Cursor SDK 客户端初始化、API Key 检查、错误归一（`backends/cursor.py`）
- Backend 可用性健康检查、fail-fast（`AgentRuntime._health_check`）
- Primary → Fallbacks 顺序路由 + 结构化日志
- JSON 提取 5 种策略 + Codex 双引号修复（`extract_json` / `repair_json_text`）
- 自愈重试 / 后端切换（`SelfHealingOrchestrator`，通过 `role` 复用）

---

## 4. 增加新的智能体后端

例如接入 gemini-cli：

1. 在 [`src/agent_runtime/backends/`](../src/agent_runtime/backends/) 新增 `gemini_cli.py`，继承 `CliBackend` 并声明 `DEFAULT_COMMAND`。
2. 在 [`src/agent_runtime/runtime.py`](../src/agent_runtime/runtime.py) 的 `BACKEND_CLASSES` 字典注册 `"gemini_cli": GeminiCliBackend`。
3. 在 `config/settings.yaml` 的 `agent_runtime.backends` 段添加配置，并按需在 `roles.<role>.fallbacks` 中引用。

不需要改任何业务代码；已接入的 role 只要修改配置就能切到新后端。

---

## 5. 与 pipeline 内 `src/llm/agents/BaseAgent` 的关系

- `src/llm/agents/BaseAgent`：pipeline 阶段内走 `llm_call()` → provider API（DeepSeek / OpenAI / Anthropic），面向纯 LLM completion。
- `src/agent_runtime/`：面向 **成熟智能体（CLI + SDK）** —— 带工具调用能力的 Claude Code / Codex / Cursor。

两者定位不同、**并存不冲突**。未来若要合并，可在 `agent_runtime` 增加 `LLMApiBackend`
将 `llm_call()` 包装为 backend，业务层不必改动。

---

## 6. 常见问题

**Q: 本地没装 Cursor CLI / 未设 CURSOR_API_KEY，能启动吗？**
A: 能。`CursorBackend.is_available()` 会返回 False，runtime 将自动跳过并 fallback。
配置里的 `strict_startup_check: true` 才会 fail-fast，本地开发建议 `false`。

**Q: 如何知道实际用了哪个后端？**
A: `AgentRunResult.backend` 字段；日志里的结构化字段 `backend` / `fallback_from`
也会在 alog 中记录，前端会以 `[role] <backend>` 的形式展示。

**Q: 自愈时如何切换后端？**
A: `SelfHealingOrchestrator` 会调用 `agent_runtime.run(..., force_fallback=True)`，
runtime 会跳过 primary 直接从 fallbacks 开始尝试。业务代码无需感知。

**Q: 新加的 role 忘记在配置里注册会怎样？**
A: `agent_runtime.run(AgentTask(role="missing.role", ...))` 会立即返回
`AgentRunResult(success=False, error="未配置 role 'missing.role' ...")`，日志有明确的
`agent_runtime_role_not_found` 事件，方便定位。
