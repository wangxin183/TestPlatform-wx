---
description: TestPlatform 仓库工作指导（聚焦需求分析 / 用例生成 / 用例执行）
alwaysApply: true
---

# CLAUDE.md

本文件为在本仓库工作的 Agent / 开发者提供指导。**当前产品主线**是 ACN App 的三条独立业务：

**需求分析（RA）→ 用例生成（TCG）→ 用例执行（EXE）**

仪表盘、旧 Project Pipeline 等仍存在于代码中，但**不是当前总结与演进重点**；改动前先确认是否落在 RA/TCG/EXE。

更深约定见：`memory-feedback-project-north-star.md`、`memory-feedback-testcase-generation.md`、`memory-feedback-execution-runtime.md`、`memory-MEMORY-update.md`。

---

## 项目北极星（最高优先级）

**目标：** 用 Agent 能力平衡**确定性**与**自主决策**，让 RA → TCG → 编译 → 执行向无人值守演进。

| 确定性高速路 | Harness 内 Agent |
|--------------|------------------|
| DSL、Setup、锚点、路径缓存、组批、规则门禁 | 阻塞时在工具/预算/校验围栏内诊断换招 |
| 便宜、可回放、可审计 | 模型只出决策/产物；ledger 可追溯 |
| | 人不当日常 diagnoser，只处理真缺陷与耗尽升级 |

**要解决的场景：** 人反复「看日志 → 改特例/硬编码 → 再跑」；用例能看不能稳跑；编译失败靠死表猜建议；产品文案一变规则全废。

**设计戒律：**

1. **禁止短视硬编码** — 不为当前一个失败 case 堆 if / 文案白名单 / 错误码 suggestion 表。
2. **维护优先于当下省事** — 能配置单源、Skill、`agent_runtime` role 解决的，不写进业务常量。
3. **能确定性则确定性，否则 Agent** — 规则只守边界；诊断/改写/探索走 role，禁止业务内伪智能死表。
4. 改动前自检：落在高速路还是 Harness？三个月后是资产还是债？

计划：`docs/superpowers/plans/2026-07-22-agent-engineering-self-heal.md`。前端展示侧规范见 `AGENTS.md`。

---

## 技术栈（摘要）

| 类别 | 技术 |
|------|------|
| Web | FastAPI + Uvicorn；Jinja2 + 原生 JS（无前端框架） |
| DB | SQLAlchemy async + SQLite（`storage/test_platform.db`） |
| Agent | `src/agent_runtime`（按 `role` 路由多后端 fallback） |
| 执行 | `execution_runtime`（Appium NL/DSL 双轨） |
| 配置 | `config/*.yaml` + Pydantic Settings |
| Python | >= 3.9（实际 3.10）；依赖 `requirements.txt` |

---

## 主线业务结构

```
config/
  settings.yaml              # testcase_generation / requirement_analysis / agent_runtime
  acn_modules.yaml           # ACN 一级模块、入口、页面锚点
  automation_lexicon.yaml    # 编译/lint 策略词表（非产品 UI 文案）
  execution_testdata.yaml    # 登录凭证、搜索作品、安全检测模型等
  llm_providers.yaml
execution_runtime/           # App 执行运行时（编译、Setup、导航、ToolGateway、Heal）
src/
  api/v1/
    requirement_analysis.py  # RA-xxxx
    testcase_generation.py   # TCG-xxxx
    execution_runs.py        # 执行任务
    case_library.py          # 用例库（TCG 落库可见）
  services/
    requirement_analysis_service.py
    testcase_generation_service.py / testcase_coverage.py
    testcase_contract_compiler.py / testcase_compile_advisor.py
    testcase_automation_lint.py / testcase_exec_heal.py
    execution_runtime_service.py / heal_loop.py / narrative_log.py
  agent_runtime/             # 统一 Agent 入口
  web/                       # 页面：requirement_analysis / testcase_generation / app_execution
.agents/skills/              # requirement.* / ui-testcase-from-testpoint / compile-advisor / execution-*
storage/                     # 运行时落盘（gitignore）：RA/TCG/执行日志与产物
```

---

## 常用命令

```bash
make run          # 或：python -m uvicorn src.main:app --reload --host 0.0.0.0 --port 8999
make init-db      # create_all（无迁移；改列需 ALTER 或重建）
make test
lsof -ti:8999 | xargs kill -9
PYTHONPATH=. .venv/bin/pytest tests/unit/test_testcase_generation.py tests/unit/test_execution_runtime.py -q
```

- 虚拟环境：`.venv/`；Key：`DEEPSEEK_API_KEY` / `OPENAI_API_KEY` / `ANTHROPIC_API_KEY` / `DASHSCOPE_API_KEY` 等（见 `deployments/.env.example`）
- **改 JS 必须递增模板 `?v=`**，否则浏览器缓存旧脚本

---

## 统一约定：agent_runtime

三条主线凡调用 LLM/CLI Agent，**必须** `agent_runtime.run(AgentTask(role=...))`，禁止业务内自建 Agent 类绕过。

| 域 | 典型 role |
|----|-----------|
| RA | `requirement.analyzer` / `requirement.reviewer` / `requirement.testpoint_designer` |
| TCG | `testcase.generator` / `testcase.compile_advisor` |
| EXE | 导航/治愈等（见 `settings.yaml` → `agent_runtime.roles` 与 execution skills） |

路由与 fallback：`config/settings.yaml` → `agent_runtime`；说明见 `docs/agent_runtime.md`。

API 信封：`{"success": bool, "data": ..., "error": null|string}`。

---

## 1. 需求分析（RA）

| 项 | 说明 |
|----|------|
| ID | `RA-xxxx` |
| 页面 | `/requirement-analysis` |
| 输入 | 上传需求文档 + 可选自定义要求 |
| 落盘 | `storage/requirement_analyses/{RA-id}/` |
| 配置 | `settings.yaml` → `requirement_analysis`（`fr_tp_batch_size` / `nfr_tp_batch_size` / token 预算） |

**产品约束（已确认）：**

- FR/NFR 只能来自**上传文档原文**；须有可核验的 `source_evidence`
- **禁止**固定章节格式（如必须第三章）；结构不足应报告缺失，不得编造
- 知识库只作方法/质量参考，**不是** FR/NFR 事实来源
- 「自定义要求」只进 Analyzer，不进 Reviewer 等其他角色
- TP 分批按配置，不把批大小写死回业务常量后忘记配置

核心：`src/services/requirement_analysis_service.py`、`src/api/v1/requirement_analysis.py`。

---

## 2. 用例生成（TCG）

| 项 | 说明 |
|----|------|
| ID | `TCG-xxxx` |
| 页面 | `/testcase-generation` |
| 输入 | 某 RA 中选中的 `test_type=ui` 测试点 |
| 落库 | `TestCase`（`project_id=NULL`），详情页 + 用例库可见 |
| 落盘 | `storage/testcase_generations/{TCG-id}/` |
| Skill | `.agents/skills/ui-testcase-from-testpoint/`（例句见 `examples.md`）；完整 Skill 只审计落盘，prompt 用 slim |

**产品约束（已确认）：**

- 必须带 `module`（ACN 一级模块）、`automation_level`、`precondition_spec`、`step_contracts`
- **模块入口由执行端负责**；`steps` 从已在模块页后的业务操作开始，禁止生成「进 tab / 点卡片进模块」前缀
- expected 关键可见文案必须用「」；负向写「不出现「xxx」」；编译**不**维护产品 UI 文案白名单
- **降本**：`pack_tp_batches_by_tokens` + slim 指令 + `Semaphore(max_concurrency)`；配置在 `testcase_generation`
- **明确不做**：一把梭全部 TP；绕过 `agent_runtime`；跨任务缓存
- 生成成功多为 `pending_review`（待审），不是立刻 `completed`；无同 ID 重跑 API 时失败需新建任务
- 入口前缀剥离不得把中途「外页→模块主状态」当入口删掉；剥光则保留原文
- 编译诊断：`testcase.compile_advisor`（suggestion/need）；禁止错误码→中文死表
- 断言质量评分：空 post 的 tap/wait 等动作步不参与 min，避免误杀整案

核心：`testcase_generation_service.py`、`testcase_coverage.py`、`testcase_contract_compiler.py`、`testcase_compile_advisor.py`、`testcase_exec_heal.py`。

---

## 3. 用例执行（EXE）

| 项 | 说明 |
|----|------|
| 页面 | `/app-execution`（及用例库发起执行） |
| 运行时 | `execution_runtime/` |
| 编排服务 | `src/services/execution_runtime_service.py` |
| 模块配置 | `config/acn_modules.yaml` |
| 测试数据 | `config/execution_testdata.yaml` |

**双轨标准：**

- 人审看 NL；机器跑 DSL（`exec_script` + `step_contracts`）
- 确定性 DSL 优先；定位漂移/歧义 → `AgentToolRunner`；**一切页面操作经 `ToolGateway`**
- `automation_level`：`ready` / `semi` / `manual`；执行默认只放行可执行用例，semi/manual/缺模块/编译失败须明确提示，禁止静默跳过
- `compile_status`：`ok` / `agent_required` / `failed` — 编译失败 ≠ 执行失败

**模块与导航：**

- 同模块复用 Appium Session；跨模块：页面锚点 + Agent 导航 + `NavigationPathCache`
- Setup：`ensure_login_state` / `ensure_entry_context`（含安全检测 Vision：`qwen3-vl-*`，坐标 0~1000）
- 禁止把固定入口步骤塞回 TCG 生成主路径

**跨页合同：**

- 真实状态链，例如开通会员：`reader_main -> external:会员页`
- 到达目标页是预期时，禁止当偏离并自动 `back`
- 旧合同执行前 `repair_step_contract_states` 按 NL 重算

**断言 / 词表：**

- 只认「」文案 → `text_visible` / `text_absent`
- 策略词：`config/automation_lexicon.yaml`（compiler + lint 共用）
- 自愈：弱编译走 Agent 改写/重生，不做白名单补「」

**已知锚点示例：** 阅读器 `.comic.creader.AcgCReaderActivity` + `reader_root`；会员页 `PhonePayActivity` + `vip_gold_page`。

---

## 编码与架构约束（主线相关）

### 编码

- Python：`from __future__ import annotations`；structlog；异步 DB 用独立 session
- 面向用户文案中文；代码标识英文
- 前端细则与 `rv-*` 见 `AGENTS.md`

### 约束

1. RA/TCG/EXE **不得绕过** `agent_runtime`
2. TCG **不得**退回固定 `TP_BATCH_SIZE=4` 串行；改 `testcase_generation` 配置
3. RA **不得**用固定章节或知识库编造 FR/NFR
4. 跨页步骤 **不得**强制回到模块主状态
5. 模块入口 **不得**重新塞回生成 steps；改 `acn_modules.yaml` / Setup
6. **禁止短视硬编码**（见北极星）；调试捷径不能晋升为机制则删除
7. JS 无构建步骤 — 改完递增 `?v=`；不引入新前端框架/npm
8. DB 无迁移 — 加列需 `ALTER TABLE` 或重建（丢数据）
9. `storage/` 不入库；执行 `make clean` 会删库与日志

### 测试入口

- TCG：`tests/unit/test_testcase_generation.py`、`test_testcase_contract_compiler.py`、`test_testcase_compile_advisor.py`、`test_testcase_automation_lint.py`
- EXE：`test_execution_runtime.py`、`test_execution_tools.py`、`test_agent_tool_runner.py`、`test_heal_loop.py` 等

---

## 非主线说明（勿当演进重点）

仓库仍含 Project / Pipeline FSM、仪表盘、报告等能力。除非任务明确要求，否则**不要**把文档与方案重心放在这些模块上；主线始终是 RA → TCG → EXE。
