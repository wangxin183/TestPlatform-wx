---
description: 
alwaysApply: true
---

# CLAUDE.md

本文件为 Claude Code (claude.ai/code) 提供在此仓库中工作的指导。

## 项目北极星（最高优先级）

**目标：** 用 Agent 能力平衡**确定性**与**自主决策**，让 RA → TCG → 编译 → 执行向无人值守演进。

| 确定性高速路 | Harness 内 Agent |
|--------------|------------------|
| DSL、Setup、锚点、路径缓存、组批、规则门禁 | 阻塞时在工具/预算/校验围栏内诊断换招 |
| 便宜、可回放、可审计 | 模型只出决策/产物；ledger 可追溯 |
| | 人不当日常 diagnoser，只处理真缺陷与耗尽升级 |

**要解决的场景：** 人反复「看日志 → 改代码/改特例 → 再跑」；用例能看不能稳跑；编译失败靠死表猜建议；产品文案一变规则全废。

**设计戒律：**

1. 后续设计**禁止短视硬编码**——不为解决当前一个失败 case 而堆 if / 文案白名单 / 错误码 suggestion 表。
2. 先从大局观想维护：模块会变多、文案会变；能配置单源、Skill、role Agent 解决的，不写进业务常量。
3. 能确定性则确定性，否则走 `agent_runtime`；规则只守边界，不代替 Agent 说话。
4. 改动前自检：是否在加强高速路或 Harness？三个月后是资产还是债？

权威展开：`memory-feedback-project-north-star.md`、`docs/superpowers/plans/2026-07-22-agent-engineering-self-heal.md`。

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
│   ├── platforms.yaml         #   平台执行器映射及能力配置
│   └── acn_modules.yaml       #   ACN 模块目录、入口描述与页面锚点
├── execution_runtime/         # Appium NL/DSL 双轨执行运行时
│   ├── compiler/              # 本地/Agent 编译器
│   ├── navigation/            # 模块导航路径缓存
│   ├── session/               # 同模块 Appium Session 复用
│   └── tools/                 # AI 可调用工具、页面观察与状态匹配
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
│   │   ├── files.py           #   文件下载
│   │   ├── requirement_analysis.py  # 独立需求分析 RA-xxxx
│   │   └── testcase_generation.py   # 独立用例生成 TCG-xxxx
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

### 独立模块：需求分析（RA）与用例生成（TCG）

与 Project / Pipeline **解耦**的两条业务线，均必须走统一 `agent_runtime`（按 `role` 路由），禁止自建 Agent 类绕过。

| 模块 | ID | 输入 | Agent role | 落盘 |
|------|-----|------|------------|------|
| 需求分析 | `RA-xxxx` | 需求文档 | `requirement.analyzer` 等 | `storage/requirement_analyses/` |
| 用例生成 | `TCG-xxxx` | 仅选中 `test_type=ui` 的测试点 | `testcase.generator` | `storage/testcase_generations/` |

**需求分析产品约束（已确认）：**

- FR/NFR 范围必须从上传文档的实际标题、正文和原文证据动态解析，不得要求需求位于第三章或符合固定 `### 3.x xxx模块` 格式
- 每条 FR/NFR 必须有能在上传文档中核验的 `source_evidence`；不能从常识、知识库或模型推断补造需求
- 知识库只用于分析方法和质量参考，不得作为 FR/NFR 的事实来源；「自定义要求」只传给 Analyzer
- 结构或证据不足时应明确报告缺失，不能为了通过范围校验而编造模块、标题或需求

**用例生成产品约束（已确认）：**

- 写入 `TestCase`（`project_id=NULL`），详情页 + 用例库可见；字段含 `generation_id` / `source_analysis_id` / `test_point_id`
- 评审：逐条通过/驳回/编辑；无待审用例时任务自动 `completed`
- Skill：`.agents/skills/ui-testcase-from-testpoint/SKILL.md`（完整正文只 `save_snapshot("SKILL_used.md")` 审计）
- **降本提速（已落地）**：`pack_tp_batches_by_tokens` + 精简指令注入 + `Semaphore(max_concurrency)` + 缺失 TP 合并补齐
  - 配置：`config/settings.yaml` → `testcase_generation`（`max_tps_per_batch=12`，`target_input_tokens=7000`，`max_concurrency=3`）
  - 核心文件：`src/services/testcase_coverage.py`、`src/services/testcase_generation_service.py`
  - 验收参照：193 UI TP 从约 49 次串行 → 约 ≤20 批；进度文案含「并发中」
- **明确不做**：单次一把梭全部 TP；绕过 `agent_runtime`；跨任务结果缓存
- 页面：`/testcase-generation`（`testcase_generation.html` + `testcase_generation.js`）

### 模块化可执行用例与 execution_runtime

- 用例评审面向自然语言（NL），执行使用确定性 DSL；编译结果包含 `exec_script`、`step_contracts`、`compile_status`、`execution_mode`
- 执行优先级：确定性 DSL 优先；定位漂移、目标歧义或无法确定性编译时切换 `AgentToolRunner`，所有页面操作仍必须经 `ToolGateway`
- `automation_level` 分为 `ready` / `semi` / `manual`；审批不强制阻断，但执行默认只放行可执行用例
- 每条 UI 用例必须关联 ACN 一级模块；模块入口由执行端负责，用例步骤只描述模块内行为，不得重复生成“进入 tab/点击卡片进入模块”等前缀
- 同模块用例复用 Appium Session；模块间通过页面锚点 + Agent 智能导航 + `NavigationPathCache` 进入，禁止恢复固定入口步骤作为主方案
- 页面锚点配置在 `config/acn_modules.yaml`，可使用 `package`、`activity`、`required_all`、`required_any`、`forbidden_any`
- 步骤合同必须描述真实状态链。跨页操作如“跳转至会员页”应生成 `reader_main -> external:会员页`，下一步从目标状态开始，不能硬编码为同状态
- 运行时执行跨页动作后必须验证目标状态或页面变化；目标页面是预期结果时禁止误判偏离并自动 `back`
- 已落库旧合同在执行前由 `repair_step_contract_states` 按 NL 步骤重算，保证旧任务可直接重跑
- Agent 页面上下文使用 `PageObservation.as_agent_dict()` 精简；工具网关兼容 `resource-id:xxx` locator 简写
- **断言文案**：编译只认 expected 中「」包裹的文案，**不维护**产品 UI 文案白名单；例句见 Skill `examples.md`
- **策略词表**：主观/模糊/动作动词在 `config/automation_lexicon.yaml`，compiler 与 lint 共用
- 编译诊断走 `testcase.compile_advisor`；禁止错误码→suggestion 硬编码表
- 漫画阅读器稳定锚点：Activity `.comic.creader.AcgCReaderActivity` + `reader_root` / `fragment_read_real`
- 会员页稳定锚点：Activity `com.iqiyi.vipcashier.activity.PhonePayActivity` + `vip_gold_page` / `price_card`

**XMind：**

- 已有导入解析：`src/utils/case_parser.py`（`.xmind` = ZIP + `content.json`）
- 产品内导出（服务端自写 ZIP）尚未落地；Cursor 侧可用 MCP `xmind-generator` 做会话级导出
- MCP 本地安装：`.tools/xmind-mcp/`；`~/.cursor/mcp.json` 指向本地 `node .../dist/index.js`（避免 npmmirror 403）
- 注意：上游 `xmind-generator.writeLocalFile` 与 MCP 调用须 `await`，否则会写出 0 字节空文件

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
8. **独立 RA/TCG 模块不得绕过 `agent_runtime`** — 业务只面向 `role`；backend 与 fallback 由 `config/settings.yaml` → `agent_runtime.roles` 驱动。
9. **用例生成批处理不得退回固定 `TP_BATCH_SIZE=4` 串行** — 保持 token 预算组批 + 有限并发；改配置项而非硬编码。
10. **需求分析不得依赖固定章节或知识库生成需求** — 文档原文证据是 FR/NFR 唯一事实来源。
11. **跨页面步骤不得强制回到模块主状态** — 合同编译、DSL 后置验证和 Agent 状态守卫必须共同尊重目标状态。
12. **模块入口不得重新塞回生成步骤** — 更新 `acn_modules.yaml` 页面锚点或导航策略，不在 TCG 前端/Skill 中维护硬编码入口步骤。
13. **谨记北极星** — 确定性高速路 + Harness 内 Agent 自主；人不当日常 diagnoser。方案须说明落在哪一层。见文首「项目北极星」与 `memory-feedback-project-north-star.md`。
14. **禁止短视硬编码** — 不为当前失败 case 堆产品文案白名单、错误码 suggestion 表、复制第二份词表；调试捷径不能晋升为机制则删除，勿「抽成 yaml」假装治理。
15. **能确定性则确定性，否则 Agent** — 规则只守边界；诊断/改写/探索走 `agent_runtime` role，禁止业务内伪智能死表。

## 注意事项

- `tests/` 已有用例生成单测：`tests/unit/test_testcase_generation.py`（组批/压缩/slim）。
- execution_runtime 相关回归位于 `tests/unit/test_execution_runtime.py`、`test_execution_tools.py`、`test_agent_tool_runner.py`、`test_testcase_contract_compiler.py`。
- EXE-0013 根因是“开通会员”实际跨页但旧合同写成 `reader_main -> reader_main`，触发错误返回；修复后同一用例真实复跑通过（2分05秒，0 broken）。
- `storage/` 目录不在版本控制中（`.gitignore`），含数据库、RA/TCG 任务日志、报告、截图等。
- 部署配置（`deployments/`）中 Celery Worker 已注释，当前使用 asyncio 后台任务执行流水线，未使用分布式任务队列。
- 旧版 `generation.txt` 提示词文件已不再被引用（GenerationStage 改用 Agent+Skill 模式），但保留在 `src/llm/prompts/` 中备用。
- `src/core/schemas/` 仍空；`src/report/` 已有 HTML/PDF/JSON 导出，但无 XMind 产品导出。
- `.tools/xmind-mcp/` 为本地 MCP 依赖（含 node_modules），勿提交；仅会话/开发辅助。
- `skills-lock.json` 跟踪外部 Skill 版本（来自 anthropics/skills 仓库），通常无需手动编辑。
- 执行 `make clean` 会删除整个数据库和日志，开发调试时需谨慎操作。
- 进行中的旧 TCG 任务不会自动切换到新组批逻辑；新任务才生效。
