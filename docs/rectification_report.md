## TestPlatform-wx 整改扫描报告（P0/P1）

本文为仓库级“体检输出”，用于后续整改逐项对照与验收。面向 2–4 周系统性重构，优先覆盖安全、稳定性、流水线幂等、LLM 治理与工程化。

### P0（必须优先处理）

#### 1) 生产安全基线缺失（鉴权/权限/CORS）
- **现状**：全站 API 默认无鉴权；CORS 默认 `["*"]`（见 `src/core/config.py`），`src/main.py` 直接放开跨域。
- **风险**：任意来源可调用写接口（创建流水线、上传文档、触发执行、下载文件等），存在数据泄露与滥用风险。
- **涉及文件**：
  - `src/core/config.py`
  - `src/main.py`
  - `src/api/v1/*.py`（所有写接口）

#### 2) 请求日志记录 body（可能落敏感信息）
- **现状**：`RequestLoggingMiddleware` 会记录 `/api/*` 的 body（非 multipart），并可能把 token/密码/隐私字段写入日志（见 `src/utils/middleware.py`）。
- **风险**：日志泄露即等同于凭据泄露；同时 body 过大也会造成日志膨胀与性能问题。
- **涉及文件**：`src/utils/middleware.py`

#### 3) 数据库 schema 漂移治理不足
- **现状**：启动时执行 `Base.metadata.create_all`（见 `src/main.py`），且模型中 `Pipeline.context_snapshot` 为 JSON（见 `src/core/models/models.py`），缺少迁移与版本治理策略。
- **风险**：schema 漂移不可追踪；线上升级不可控；新增字段/约束难以安全演进。
- **涉及文件**：
  - `src/main.py`
  - `src/core/models/*`
  - （建议新增）`alembic/` 迁移目录

#### 4) 流水线幂等与重试语义不明确（副作用重复风险）
- **现状**：`run_pipeline()` 会按 `current_stage` 继续执行；但 Stage 内可能写入 DB（例如生成用例、创建 execution、生成 report），缺少统一幂等键与 attempt 语义（见 `src/pipeline/orchestrator.py`、`src/pipeline/stages/*`）。
- **风险**：暂停/恢复/重试导致重复写入、重复执行、重复报告；“重试”可能产生不可预期副作用。
- **涉及文件**：
  - `src/pipeline/orchestrator.py`
  - `src/pipeline/context.py`
  - `src/pipeline/stages/*.py`
  - `src/core/models/models.py`

### P1（2–4 周内完成的系统性提升）

#### 5) API Handler 过重，缺少领域层边界
- **现状**：部分 API（例如 `src/api/v1/pipelines.py`）同时承担校验、编排、后台任务触发、状态推进。
- **风险**：可测试性差；耦合高；改动容易引发回归。
- **建议**：抽离 `src/services/*` 作为业务编排层，API 只保留参数校验与返回封装。

#### 6) LLM 调用重试/治理实现与注释不一致
- **现状**：`llm_call()` 注释声明会“超时/解析失败重试”，但函数默认 `max_retries=0`（见 `src/llm/caller.py`）。
- **风险**：线上可靠性不可控；失败定位困难；成本/预算规则难落地。
- **建议**：将默认重试与 `settings.llm.max_retries` 对齐，并补齐按 `pipeline_id/task_tag` 的计量日志字段。

#### 7) 前端规范一致性问题（emoji、错误提示）
- **现状**：页面/JS 中存在 emoji（例如 `src/web/templates/pages/pipeline.html`、`src/web/static/js/pipeline.js`），与前端规范（AGENTS.md：禁止 emoji，要求 Feather 风格 SVG）冲突。
- **风险**：风格不一致、可访问性差；后续页面难统一。
- **建议**：统一替换为 SVG 图标与 `rv-*` 组件体系，同时把 `alert`/`console.error` 改成统一 toast/inline error。

---

### 备注（验收关注点）
- **幂等**：同一 `pipeline_id` 在暂停/恢复/重试下，不应产生重复用例/重复执行/重复报告。\n
- **可观测性**：日志可用 `pipeline_id + stage_name + attempt` 一键定位到失败原因与输入/输出摘要。\n
