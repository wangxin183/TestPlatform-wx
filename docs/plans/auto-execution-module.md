# 用例自动执行 & 自动提交缺陷模块方案

> 创建时间：2026-06-07
> 状态：方案阶段

---

## 一、目标

在 TestPlatform 中新增「用例自动执行」模块，支持独立触发用例执行，产出测试结果，并自动提交缺陷。

- 用例类型：App UI、Web UI、API
- UI 测试：优先使用视觉识别（AI 多模态模型 + 像素对比）
- 执行失败后：自动组装 Defect 并入库

---

## 二、现状

### 已有能力
- **Web UI 执行**：Playwright（`src/executor/web_executor.py`），导航、点击、输入、断言、截图
- **API 执行**：httpx（`src/executor/api_executor.py`），请求、JSON Schema 校验
- **移动端执行**：Appium（`src/executor/mobile_executor.py`），iOS + Android
- **执行调度**：Pipeline ExecutionStage 按 `test_type` 分组调度
- **缺陷模型**：`Defect` 表已有 `title/description/severity/evidence_paths/reproduction_steps/status`
- **截图存储**：`storage/screenshots/`

### 缺失能力
- ❌ 视觉识别（无 OCR、无截图对比、无 AI 视觉判断）
- ❌ 自动提缺陷（ExecutionStage 失败后不创建 Defect）
- ❌ 独立执行入口（目前执行绑定 Pipeline，没有独立触发）

---

## 三、前置条件

| 条件 | 说明 |
|------|------|
| 多模态 LLM API Key | GPT-4V / Claude Vision，用于截图语义判断 |
| Playwright 浏览器 | `playwright install chromium`，Web UI 必备 |
| Appium Server | 移动端执行，iOS 还需 Mac + Xcode |
| 被测应用 URL/包名 | 执行时需提供目标地址 |
| `storage/screenshots/` | 已有目录，截图空间需充足 |

---

## 四、技术方案

### 4.1 视觉识别：AI 视觉模型 + 像素对比混合

- **主力**：多模态 LLM，将执行截图 + 用例预期发给模型判断
- **辅助**：像素 diff（PIL）做快速白屏/崩溃检测
- **流程**：
  1. 执行步骤后截图
  2. 像素 diff 判断是否明显异常（白屏/崩溃）→ 有异常直接 fail
  3. 无异常 → AI 视觉模型对比截图与预期，输出 JSON：`{passed: true/false, reason: "判断依据"}`

### 4.2 自动提缺陷：每条失败用例提一个

- 执行失败后自动组装 Defect：
  - `title`：失败步骤自动生成（如「点击登录按钮后页面未跳转」）
  - `description`：执行摘要 + 步骤日志
  - `evidence_paths`：失败步骤截图路径
  - `reproduction_steps`：原用例步骤
  - `severity`：按用例优先级映射（严重→critical，高→high，中→medium，低→low）

### 4.3 执行触发：独立触发

- 用例管理页面新增「执行」按钮
- 选多条用例 → 弹窗配置（目标 URL / 设备 / 浏览器）→ 点执行
- 后端异步跑，前端轮询结果并展示

### 4.4 App UI：Appium + AI 视觉

- 复用现有 `mobile_executor.py`
- 每步执行后截图，发给 AI 视觉模型比对

---

## 五、实现变更

### 后端新增

| 文件 | 变更 |
|------|------|
| `src/executor/vision.py` | 新增 — 视觉识别模块：`pixel_diff()` + `ai_vision_check()` + `visual_assert()` |
| `src/api/v1/executions.py` | 增强 — `POST /api/v1/executions/run` 独立执行端点 |
| `src/services/defect_service.py` | 新增 — `auto_create_defect()` 从失败结果组装 Defect |

### 后端增强

| 文件 | 变更 |
|------|------|
| `src/pipeline/stages/execution.py` | 增强 — 执行失败后调用 `auto_create_defect()`；UI 执行步骤集成 `visual_assert()` |
| `src/executor/web_executor.py` | 增强 — 截图后调用视觉识别判断 |
| `src/executor/mobile_executor.py` | 增强 — 同上 |

### 前端新增

| 文件 | 变更 |
|------|------|
| `src/web/templates/pages/case_library.html` | 增强 — 表格新增「执行」按钮列 |
| `src/web/static/js/case_library.js` | 增强 — 执行弹窗 + 轮询逻辑 |
| `src/web/templates/pages/execution_result.html` | 新增 — 执行结果页 |
| `src/web/static/js/execution_result.js` | 新增 — 结果渲染 |

---

## 六、执行流程（Web UI）

```
用户选用例 → 点「执行」
  → 弹窗：输入目标 URL + 选浏览器
  → 后端 POST /api/v1/executions/run
    → 创建 Execution 记录
    → 遍历每个用例：
      1. LLM 翻译自然语言步骤 → 结构化 StepAction
      2. Playwright 逐步执行
      3. 每步截图
      4. pixel_diff() 检测白屏/崩溃
      5. 无异常 → ai_vision_check() 对比截图与预期
      6. 记录 StepResult（状态 + 判断理由 + 截图路径）
    → 汇总 ExecutionResult
    → 如有失败 → auto_create_defect()
  → 前端轮询完成后跳转结果页
```

---

## 七、关键技术实现

### 7.1 视觉识别 `vision.py`

```python
async def pixel_diff(screenshot: Path, baseline: Path | None) -> float:
    """PIL 逐像素对比，返回差异百分比。>30% 判定为异常。"""

async def ai_vision_check(
    screenshot: Path, 
    expected: str, 
    step_context: str
) -> dict:
    """调用多模态 LLM，返回 {passed: bool, reason: str}。"""

async def visual_assert(
    screenshot: Path, 
    expected: str, 
    step: dict
) -> StepResult:
    """串联 pixel_diff + ai_vision_check。"""
```

### 7.2 自动提单 `defect_service.py`

```python
async def auto_create_defect(
    execution_result: ExecutionResult,
    test_case: TestCase,
    db: AsyncSession,
) -> Defect:
    """从失败执行结果组装 Defect 并入库。"""
    defect = Defect(
        execution_result_id=execution_result.id,
        execution_id=execution_result.execution_id,
        project_id=test_case.project_id,
        title=f"【自动】{test_case.title} - {failure_summary}",
        description=_build_description(execution_result),
        severity=_map_priority(test_case.priority),
        reproduction_steps=test_case.steps,
        evidence_paths=_collect_failure_screenshots(execution_result),
        status="open",
    )
    db.add(defect)
    await db.commit()
    return defect
```

### 7.3 AI 视觉 Prompt

```
你是一个 UI 测试断言器。下面是一张截图和预期结果。
请判断截图中的 UI 状态是否满足预期。

操作步骤：{step_action}
预期结果：{expected}

请输出 JSON：
{"passed": true/false, "reason": "简述判断依据，中文"}
```

---

## 八、独立执行 API

### `POST /api/v1/executions/run`

```json
// Request
{
  "case_ids": ["uuid1", "uuid2"],
  "project_id": "uuid",
  "config": {
    "platform_type": "web",
    "target_url": "https://example.com",
    "browser": "chromium",
    "headless": true
  }
}

// Response (立即返回)
{
  "success": true,
  "data": {
    "execution_id": "uuid",
    "status": "running"
  }
}
```

### `GET /api/v1/executions/{execution_id}/results`

轮询此端点获取实时结果，包含每步状态 + 截图 + 判断理由。

---

## 九、假设与约束

- AI 视觉模型 API 已可用（GPT-4V / Claude Vision）
- 视觉判断延迟：每次调用约 1-3 秒
- App UI 执行需 Appium Server 已启动
- 自动提单创建后状态为 `open`，由人工后续跟踪
- 前端遵循 `rv-*` 设计系统
