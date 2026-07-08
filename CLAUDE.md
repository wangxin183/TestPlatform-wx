---
description: 
alwaysApply: true
---

# CLAUDE.md

本文件为 Claude Code (claude.ai/code) 提供在此仓库中工作的指导。

## 技术栈

| 类别 | 技术 | 说明 |
|------|------|------|
| Web 框架 | FastAPI + Uvicorn | 异步 HTTP 服务 |
| 前端 | Jinja2 + 原生 JS | 无框架，每个页面对应一个 JS 文件 |
| 数据库 | SQLAlchemy async + SQLite (aiosqlite) | 文件数据库 `storage/test_platform.db` |
| 状态机 | transitions | 轻量 FSM 库 |
| LLM 调用 | httpx + openai SDK + anthropic SDK | 3 个 Provider |
| 文档解析 | python-docx, openpyxl, PyPDF2, weasyprint | 多格式文档提取 |
| 测试执行 | Playwright, Appium, httpx | Web/Mobile/API 执行器 |
| 实时通信 | WebSocket (FastAPI 原生) | 流水线状态广播 |
| 日志 | structlog | 结构化日志 |
| 配置 | YAML + Pydantic Settings | `config/*.yaml` |
| Python | >= 3.9 (实际 3.10) | |
| 依赖管理 | pip + requirements.txt | 无 Poetry/PDM |

## 项目结构

```
TestPlatform-wx/
├── config/                    # YAML 配置文件
│   ├── settings.yaml          #   应用、数据库、LLM、流水线配置
│   ├── llm_providers.yaml     #   LLM Provider 及路由规则
│   └── platforms.yaml         #   平台执行器映射及能力配置
├── src/
│   ├── main.py                # FastAPI 入口，create_app()
│   ├── api/v1/                # REST API 层（/api/v1）
│   │   ├── router.py          #   聚合 8 个子路由
│   │   ├── projects.py        #   项目 CRUD
│   │   ├── documents.py       #   文档上传/列表
│   │   ├── pipelines.py       #   流水线创建/控制 + 独立阶段运行 + 重试
│   │   ├── test_cases.py      #   用例列表/评审/编辑/批处理
│   │   ├── executions.py      #   执行结果查询
│   │   ├── defects.py         #   缺陷管理
│   │   ├── reports.py         #   报告查询
│   │   └── files.py           #   文件下载
│   ├── core/
│   │   ├── config.py          # Settings 单例
│   │   ├── database.py        # async_session_factory, get_db
│   │   └── models/
│   │       ├── base.py        # Base, UUIDMixin, TimestampMixin
│   │       └── models.py      # 11 个 ORM 模型
│   ├── pipeline/              # 流水线编排核心
│   │   ├── orchestrator.py    # run_pipeline(), run_stage(), STAGE_MAP
│   │   ├── state_machine.py   # PipelineStateMachine (14 状态)
│   │   ├── context.py         # PipelineContext (跨阶段数据总线)
│   │   └── stages/            # 8 个阶段实现
│   ├── llm/                   # LLM 子系统
│   │   ├── caller.py          # llm_call() 统一入口（重试/容错）
│   │   ├── router.py          # Provider 选择器（规则驱动）
│   │   ├── types.py           # LLMRequest, LLMResponse
│   │   ├── providers/         # DeepSeek/OpenAI/Anthropic Provider
│   │   ├── agents/            # BaseAgent + 具体 Agent
│   │   │   ├── base.py        #   AgentContext, AgentOutput, BaseAgent
│   │   │   ├── requirement_agent.py      # RequirementAgent
│   │   │   └── testcase_generator_agent.py # TestCaseGeneratorAgent
│   │   └── prompts/           # 提示词模板 + Skill 加载器
│   ├── executor/              # 平台执行器
│   │   ├── registry.py        # ExecutorRegistry（按 platform_type 注册）
│   │   └── *.py               # Playwright/Appium/httpx/MiniProgram 执行器
│   ├── web/                   # Web 页面
│   │   ├── router.py          # 10 个 HTML 页面路由
│   │   ├── static/css/app.css # 全局样式（CSS 变量 + 组件样式）
│   │   ├── static/js/         # 每页一个 JS 文件
│   │   └── templates/         # Jinja2 模板（base.html + pages/）
│   ├── ws/manager.py          # WebSocket 连接管理器
│   └── utils/                 # 工具（日志、文件存储、中间件）
├── .agents/skills/            # Skill 文件（SKILL.md）
├── scripts/                   # init_db.py, iterate_skill.py
├── storage/                   # 运行时数据（数据库、文档、报告、截图）
├── deployments/               # Docker Compose + Nginx 配置
├── tests/                     # 测试目录（暂无测试文件）
└── docs/                      # 架构文档
```

## 常用命令

```bash
# 启动开发服务器（实际端口 8999）
make run
# 或：
python -m uvicorn src.main:app --reload --host 0.0.0.0 --port 8999

# 初始化/重建数据库表（无迁移，直接 create_all）
make init-db

# 运行测试
make test

# 释放 8999 端口（端口占用常见问题）
lsof -ti:8999 | xargs kill -9

# 验证 Python 编译
python -m compileall -q src/

# 验证 JS 语法
node --check src/web/static/js/pipeline.js

# 清理（数据库 + 日志 + __pycache__）
make clean
```

## 开发环境

### 环境要求

- Python >= 3.9（推荐 3.10）
- 虚拟环境位于 `.venv/`
- 安装依赖：`make install`（生产）或 `make install-dev`（含 pytest）
- 使用清华镜像源（`pypi.tuna.tsinghua.edu.cn/simple`）

### 配置

- 所有配置在 `config/*.yaml` 中，通过 `src/core/config.py` 的 `Settings` 类加载
- LLM API Key 通过环境变量设置：`DEEPSEEK_API_KEY`、`OPENAI_API_KEY`、`ANTHROPIC_API_KEY`
- 部署模板：`deployments/.env.example`
- 数据库路径在 `config/settings.yaml` → `database.url`，默认 `sqlite+aiosqlite:///storage/test_platform.db`

### 数据库

- 使用 SQLite，文件位于 `storage/test_platform.db`
- **无迁移系统**：`Base.metadata.create_all()` 创建新表，但不会修改已有表
- 向已有表添加新列需手动执行 `ALTER TABLE`（通过 `sqlite3` 命令行或 `make init-db` 重建库并丢失数据）
- 异步引擎通过 `aiosqlite` 驱动

### 前端 JS 缓存

- 模板中 JS 引用带版本号查询参数（如 `pipeline.js?v=24`）
- **每次修改 JS 后必须递增版本号**，否则浏览器使用旧缓存
- 验证需用户执行 **Cmd+Shift+R**（或 Ctrl+Shift+R）强制刷新

## 核心架构

### 三层体系

1. **Web 展示层** — Jinja2 模板 + 原生 JS（无框架）。`src/web/router.py` 提供 10 个页面路由。`src/web/static/js/` 中每页一个 JS 文件。全局 API 封装在 `app.js`（`api` 对象、`i18n.t()`、`fmt.date()`）。

2. **流水线编排层** — 核心引擎。`src/pipeline/orchestrator.py` 通过 while 循环驱动 8 个阶段，使用 `transitions` FSM。各阶段通过 `PipelineContext` dataclass 共享状态，暂停/恢复时序列化为 JSON。

3. **基础设施层** — SQLAlchemy async + SQLite，LLM 路由器（3 个 Provider），平台执行器注册表（Playwright/Appium/httpx），WebSocket 管理器（实时流水线状态推送）。

### 流水线 FSM

```
ingestion → parsing → analysis → generation → review ── approve → execution → reporting → regression → completed
                                        ↑_____________ reject _______________↓
```

- 14 个状态定义在 `src/pipeline/state_machine.py`
- 每个阶段是 `AbstractStage` 子类，附带 `required_context_fields()` / `produced_context_fields()` 契约方法
- `review` 阶段暂停流水线（状态变为 `paused`）等待人工决定
- 重试：失败阶段可从 `POST /pipelines/{id}/retry` 重新运行最后失败阶段到结束

### 双模式执行

- **流水线模式**：Orchestrator 通过 FSM 顺序执行，上下文快照持久化到 DB，WebSocket 广播阶段变更。`review` 阶段设人工卡点。
- **独立模式**：`POST /api/v1/stages/{name}/run` 直接调用 `run_stage()`，无需创建流水线、不触发 FSM、不广播。运行前校验上下文前置条件。

### Agent + Skill 模式

LLM 密集型阶段使用此模式：`Stage` → `BaseAgent` → `SKILL.md`：

| 阶段 | Agent | Skill 文件 |
|------|-------|-----------|
| analysis | `RequirementAgent` | `.agents/skills/requirement-analyzer/SKILL.md` |
| generation | `TestCaseGeneratorAgent` | `.agents/skills/test-case-generator/SKILL.md` |

- `BaseAgent._load_skill(**interpolations)` 加载 SKILL.md 并替换 `{variable}` 占位符
- `BaseAgent._retry_llm()` 统一重试逻辑（最多 2 次重试）
- 抽象接口：`run(ctx: AgentContext) -> AgentOutput`
- SKILL.md 缺失时，Agent 通过 LLM 即时生成 Skill（`skill_creator.txt` 模板）

非 LLM 阶段（ingestion、parsing、review、execution、reporting、regression）直接调用 `llm_call()` 或执行纯自动化逻辑。

### API 响应规范

所有 API 端点统一使用信封格式：
```json
{"success": bool, "data": ..., "error": null | string}
```
可选 `meta` 字段用于分页信息。前端 `api.get/post/put/upload` 均依赖此格式。

### WebSocket

- WS 端点：`/ws/pipelines/{pipeline_id}/live`
- `pipeline.js` 页面加载时自动连接，发送 `ping` 保活，接收 `pong` 响应
- Orchestrator 在阶段变更、暂停、失败、完成时通过 `ws_manager.broadcast_stage_change()` 广播

### 后台执行

流水线创建后通过 `asyncio.create_task` 在后台运行（非 Celery — Celery 已配置但注释掉未使用）。

## 编码规范

### Python

- 文件头使用 `from __future__ import annotations`
- 日志使用 structlog：`logger = get_logger(__name__)`，记录时使用 `logger.info("event_name", key=value, ...)`
- 异步 SQLAlchemy 操作始终使用 `async_session_factory()` 上下文管理器创建独立 session
- Stage 契约：每个 `AbstractStage` 子类必须声明 `required_context_fields()` 和 `produced_context_fields()` 类方法
- API 端点：`_serialize()` 函数将 ORM 对象转为字典，使用 `isoformat()` 转换时间字段

### 前端

- 使用原生 JS（ES6 箭头函数、模板字面量）
- HTML 转义使用 `escapeHtml()` 函数（定义在 `app.js`）
- 状态标签使用 `badge-*` CSS 类
- 表格通过字符串拼接渲染到 `innerHTML`

### 统一惯例

- **对话语言**：与用户的所有对话、解释说明、代码注释、文档内容均使用中文
- 所有面向用户的文本使用中文
- 代码日志、变量名、函数名使用英文
- 文件路径保持英文

## 架构约束

1. **不要修改 `StateMachine` 的状态定义**，除非同步更新 `STAGE_MAP` 和所有 Stage 契约。顺序变更可能破坏流水线恢复逻辑。
2. **数据库无迁移系统** — 向已有表添加列需手动 `ALTER TABLE` 或重建库（会丢失数据）。
3. **JS 文件无构建步骤** — 每次修改后必须递增模板中的版本号。
4. **Stage 可独立运行** — 修改 `required_context_fields()` 后检查 `POST /api/v1/stages/{name}/run` 是否受影响。
5. **Agent 的 `_retry_llm()` 不能用于非 Agent 代码** — 普通阶段继续使用 `llm_call()`。
6. **WebSocket 不持久化** — 断开连接不重放消息，刷新页面或重连后需重新拉取完整状态。
7. **不引入新的前端框架或 npm 依赖** — 保持零构建步骤。

## 注意事项

- `tests/` 目录存在子目录（unit/integration/fixtures）但暂无测试文件，test-dependencies 在 `requirements-dev.txt` 中已定义。
- `storage/` 目录不在版本控制中（`.gitignore`），包含数据库、上传的文档、生成的报告、截图和 Skill 使用日志。
- 部署配置（`deployments/`）中 Celery Worker 已注释，当前使用 asyncio 后台任务执行流水线，未使用分布式任务队列。
- 旧版 `generation.txt` 提示词文件已不再被引用（GenerationStage 改用 Agent+Skill 模式），但保留在 `src/llm/prompts/` 中备用。
- `src/core/schemas/` 和 `src/report/` 目录为空（已准备但未使用）。
- `skills-lock.json` 跟踪外部 Skill 版本（来自 anthropics/skills 仓库），通常无需手动编辑。
- 执行 `make clean` 会删除整个数据库和日志，开发调试时需谨慎操作。
