# 多类型测试用例自动执行方案

> 2026-05-22

## 一、test_type 重新定义

### 当前

```
test_type IN ("functional", "ui", "api", "performance", "integration")
```

"functional" 和 "ui" 有重叠（界面输入验证归到哪个？），"integration" 语义模糊。

### 改后

```
test_type IN ("ui", "api", "performance", "security", "compatibility")
```

| test_type | 覆盖范围 | 自动化执行 |
|-----------|---------|-----------|
| `ui` | 界面交互（点击、输入、滚动、拖拽）、输入值验证（格式/边界/异常）、页面元素存在性/可见性、前端校验逻辑、用户体验流程 | **是** — PlaywrightExecutor |
| `api` | HTTP 接口请求/响应、状态码、鉴权、数据格式、JSON Schema、错误码、超时/重试 | **是** — APIExecutor |
| `compatibility` | 浏览器矩阵（Chromium/Firefox/WebKit）、视口尺寸（Desktop/Tablet/Mobile）、OS 版本、设备型号 | **是** — BrowserMatrixExecutor |
| `performance` | 响应时间、并发量、吞吐量、资源消耗 | **否** — 需求分析阶段输出《性能测试方案》 |
| `security` | SQL 注入、XSS、CSRF、路径遍历、敏感信息泄露、权限绕过 | **否** — 需求分析阶段输出《安全测试方案》 |

### 归属规则

原来的 "functional"（功能测试）按以下规则分配到 ui 或 api：

- 描述涉及 "界面"/"页面"/"按钮"/"输入框"/"弹窗"/"表单"/"跳转"/"显示" → `ui`
- 描述涉及 "接口"/"请求"/"响应"/"状态码"/"header"/"参数"/"返回值" → `api`

---

## 二、涉及改动的文件清单

| 操作 | 文件 | 改动内容 |
|------|------|---------|
| 修改 | `.agents/skills/test-case-generator/SKILL.md` | test_type 输出范围改为 5 种；明确 ui/api/compatibility 的步骤格式 |
| 修改 | `.agents/skills/requirement-analyzer/SKILL.md` | 新增「性能测试方案」和「安全测试方案」两个输出章节 |
| 修改 | `src/core/models/models.py` | 更新 TestCase.test_type 注释 |
| 修改 | `src/llm/agents/testcase_generator_agent.py` | 更新默认值、test_type 校验 |
| 重写 | `src/pipeline/stages/execution.py` | NL→结构化翻译 + test_type 路由 |
| 修改 | `src/executor/registry.py` | 扩展为 `type[AbstractExecutor]` 的独立注册（不改接口，改调用方） |
| 新建 | `src/executor/compatibility_executor.py` | BrowserMatrixExecutor |
| 修改 | `src/pipeline/stages/analysis.py` | 透传 performance_plan / security_plan 到 context |
| 修改 | `src/pipeline/context.py` | 新增 `performance_plan`、`security_plan` 字段 |

---

## 三、各阶段改动详情

### 3.1 需求分析阶段

#### requirement-analyzer SKILL.md 新增章节

在现有输出格式的第 5 节之后，新增：

```markdown
## 8. 性能测试方案（独立文档）

当需求涉及以下场景时，必须输出此章节：
- 用户量 ≥ 1000 或提及并发
- 涉及支付、下单、搜索等响应敏感功能
- 涉及大数据量列表/报表

输出内容：
- 性能测试目标（响应时间、并发数、TPS）
- 压测场景设计（场景描述、模拟用户数、持续时间）
- 关键指标（P50/P95/P99 响应时间、错误率、吞吐量）
- 测试数据准备方案
- 推荐的压测工具（如 Locust/JMeter/k6）

## 9. 安全测试方案（独立文档）

当需求涉及以下场景时，必须输出此章节：
- 用户登录、注册、权限管理
- 涉及敏感数据（手机号、身份证、支付信息）
- 文件上传、URL 参数、富文本输入
- 第三方 API 集成

输出内容：
- 安全测试范围（认证、授权、数据保护、输入验证）
- OWASP Top 10 覆盖清单
- 具体测试用例（注入攻击、XSS、CSRF、越权访问）
- 安全检查工具建议（如 OWASP ZAP、sqlmap）
```

#### PipelineContext 新增字段

```python
# src/pipeline/context.py
performance_plan: dict | None = None    # 性能测试方案
security_plan: dict | None = None       # 安全测试方案
```

#### AnalysisStage 透传

```python
context.performance_plan = {
    "content": output.data.get("performance_plan", ""),
}
context.security_plan = {
    "content": output.data.get("security_plan", ""),
}
```

---

### 3.2 用例生成阶段

#### test-case-generator SKILL.md 改动

```diff
- "test_type": "功能/界面/接口/性能/集成",
+ "test_type": "ui/api/performance/security/compatibility",
```

新增 test_type 定义章节：

```markdown
## 测试类型定义

用例按 test_type 分为以下 5 类：

### ui — UI 测试
涵盖所有界面交互和输入验证：
- 页面元素交互（点击按钮、输入文本、滚动、切换标签页）
- 输入值验证（格式校验、边界值、空值、特殊字符）
- 界面状态变化（加载中、空数据、错误提示）
- 前端弹窗/提示/toast
- 页面跳转、路由切换
- 表单提交流程

step 的 action 格式：自然语言描述操作（如"在手机号输入框输入 13800138000"）

### api — 接口测试
涵盖所有 HTTP 接口验证：
- 请求方法（GET/POST/PUT/DELETE）
- 请求参数（必填/选填、类型、格式）
- 响应状态码（2xx/4xx/5xx）
- 响应体结构（JSON Schema、字段类型）
- 鉴权（Token 过期、无权限）
- 超时和重试

step 的 action 格式：自然语言描述请求（如"发送 POST 请求到 /api/user/login"）
expected 应包含预期状态码和关键响应字段。

### compatibility — 兼容性测试
涵盖跨环境验证：
- 浏览器兼容性（Chrome/Firefox/Safari/Edge）
- 视口兼容性（1920×1080 / 768×1024 / 375×812）
- OS 兼容性（Windows/macOS/iOS/Android）
- 依赖版本兼容性

step 的 action 格式：描述在不同环境下执行的操作。

### performance — 性能测试
此类型不自动执行，仅生成用例文档。步骤描述压测场景。

### security — 安全测试
此类型不自动执行，仅生成用例文档。步骤描述安全攻击场景。
```

#### TestCaseGeneratorAgent 改动

```python
# _save_testcases 中，test_type 默认值改为 "ui"
test_type=case_data.get("test_type", "ui"),

# 新增校验（_generate_testcases 返回后）
VALID_TYPES = {"ui", "api", "performance", "security", "compatibility"}
for c in cases_data:
    if c.get("test_type") not in VALID_TYPES:
        c["test_type"] = "ui"  # fallback
```

---

### 3.3 用例执行阶段

#### 整体流程

```
已审批用例（status=approved）
    │
    ├─ 按 test_type 分组
    │
    ├── test_type="ui" ──────────→ LLM NL→结构化翻译 ──→ PlaywrightExecutor
    ├── test_type="api" ─────────→ LLM NL→结构化翻译 ──→ APIExecutor
    ├── test_type="compatibility" → LLM NL→结构化翻译 ──→ BrowserMatrixExecutor(循环多配置)
    ├── test_type="performance" ─→ 跳过，记录"性能测试方案已输出"
    └── test_type="security" ────→ 跳过，记录"安全测试方案已输出"
```

#### NL→结构化翻译

在 `ExecutionStage` 中新增方法 `_translate_steps(test_case) -> list[StepAction]`：

```python
async def _translate_steps(self, test_case: TestCase) -> list[StepAction]:
    """用 LLM 将自然语言步骤翻译为结构化 StepAction"""
    
    if test_case.test_type == "ui":
        system_prompt = """你是 Web 自动化工程师。将自然语言操作的步骤翻译为 Playwright 可执行的结构化指令。
输出格式：[{"action_type": "...", "target": "...", "value": "...", "timeout_ms": 30000}]
action_type: navigate/click/input/assert/wait/scroll/screenshot"""
    
    elif test_case.test_type == "api":
        system_prompt = """你是接口测试工程师。将自然语言接口调用翻译为结构化指令。
输出格式：[{"action_type": "api_call", "target": "/path", "value": "GET|POST|PUT|DELETE", ...}]"""
    
    elif test_case.test_type == "compatibility":
        system_prompt = """同 UI 翻译，但步骤可跨浏览器/视口配置。"""
    
    user_prompt = f"""目标 URL: {self._target_url}
用例标题: {test_case.title}
用例描述: {test_case.description}
操作步骤:
{json.dumps(test_case.steps, ensure_ascii=False, indent=2)}

请翻译为结构化步骤数组。"""
    
    response = await llm_call(LLMRequest(
        system_prompt=system_prompt,
        user_prompt=user_prompt,
        task_tag="step_translation",
        complexity="medium",
        expect_json=True,
        max_tokens=4096,
    ))
    
    actions = []
    for item in response.parsed_json or []:
        actions.append(StepAction(
            step_number=item.get("step", len(actions) + 1),
            action_type=item.get("action_type", ""),
            target=item.get("target"),
            value=item.get("value"),
            timeout_ms=item.get("timeout_ms", 30000),
        ))
    return actions
```

#### test_type 路由

```python
# ExecutionStage.execute() 中替换现有按 platform_type 分组

EXECUTOR_MAP = {
    "ui": "playwright",             # → PlaywrightExecutor via registry
    "api": "api",                    # → APIExecutor
    "compatibility": "compatibility", # → BrowserMatrixExecutor
}

# 分组改为按 test_type
grouped: dict[str, list[TestCase]] = {}
for tc in test_cases:
    grouped.setdefault(tc.test_type, []).append(tc)

for test_type, cases in grouped.items():
    if test_type in ("performance", "security"):
        # 跳过，记录
        logger.info("execution_skipped", test_type=test_type, 
                    reason=f"{test_type} 方案已在需求分析阶段输出")
        continue
    
    executor_name = EXECUTOR_MAP.get(test_type, test_case.platform_type)
    executor = ExecutorRegistry.get(executor_name)
    # ... setup → execute → teardown
```

---

### 3.4 新增 BrowserMatrixExecutor

```python
# src/executor/compatibility_executor.py

class BrowserMatrixExecutor(AbstractExecutor):
    """跨浏览器/视口兼容性执行器。
    
    基于 PlaywrightExecutor，对每个用例循环执行多套配置：
    1. Chromium + 1920×1080 (Desktop)
    2. Chromium + 375×812  (Mobile)
    3. Firefox + 1920×1080 (Desktop)
    4. WebKit + 375×812   (Mobile)
    """
    
    MATRIX = [
        {"browser": "chromium", "viewport": {"width": 1920, "height": 1080}, "label": "Chrome/Desktop"},
        {"browser": "chromium", "viewport": {"width": 375, "height": 812},  "label": "Chrome/Mobile"},
        {"browser": "firefox",  "viewport": {"width": 1920, "height": 1080}, "label": "Firefox/Desktop"},
        {"browser": "webkit",   "viewport": {"width": 375, "height": 812},  "label": "Safari/Mobile"},
    ]
    
    async def execute_steps(self, actions):
        results = []
        for config in self.MATRIX:
            # 为每个配置创建新的浏览器上下文
            context = await self._browser.new_context(viewport=config["viewport"])
            page = await context.new_page()
            for action in actions:
                result = await self._execute_step_on_page(page, action)
                result.browser_config = config["label"]
                results.append(result)
            await context.close()
        return results
```

---

## 四、执行流程总览

```
需求分析阶段 (AnalysisStage)
    │
    ├──→ 测试计划（含功能测试点 + 接口测试点 + 兼容性测试点）
    ├──→ 性能测试方案（独立文档，跳过自动执行）
    └──→ 安全测试方案（独立文档，跳过自动执行）
    
用例生成阶段 (GenerationStage)
    │
    ├──→ ui 用例（界面交互 + 输入验证）
    ├──→ api 用例（HTTP 接口）
    ├──→ compatibility 用例（跨浏览器/设备）
    ├──→ performance 用例（文档型，不自动执行）
    └──→ security 用例（文档型，不自动执行）

用例评审阶段 (ReviewStage)
    └──→ 人工审批（所有 test_type 一样处理）

用例执行阶段 (ExecutionStage)
    │
    ├── ui 用例 ──→ LLM 翻译 NL→结构化 ──→ PlaywrightExecutor ──→ pass/fail/error
    ├── api 用例 ──→ LLM 翻译 NL→结构化 ──→ APIExecutor ──→ pass/fail/error
    ├── compatibility 用例 ──→ LLM 翻译 NL→结构化 ──→ BrowserMatrixExecutor(4 配置) ──→ 矩阵结果
    ├── performance 用例 ──→ 跳过（标注"方案已输出"）
    └── security 用例 ──→ 跳过（标注"方案已输出"）
```

## 五、实施步骤

| 步骤 | 内容 | 文件 | 工作量 |
|------|------|------|--------|
| 1 | 更新 SKILL.md（requirement-analyzer + test-case-generator） | `.agents/skills/` × 2 | 0.5 天 |
| 2 | 更新 TestCase.test_type 注释 + PipelineContext + AnalysisStage | `models.py`, `context.py`, `analysis.py` | 0.5 天 |
| 3 | 更新 TestCaseGeneratorAgent（test_type 默认值 + 校验） | `testcase_generator_agent.py` | 0.5 天 |
| 4 | 新增 BrowserMatrixExecutor | `compatibility_executor.py` | 0.5 天 |
| 5 | 重写 ExecutionStage（NL→结构化翻译 + test_type 路由） | `execution.py` | 1.5 天 |
| 6 | 端到端验证 | — | 1 天 |
| **合计** | | | **4.5 天** |
