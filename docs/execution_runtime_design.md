# 独立测试执行运行时（execution_runtime）设计方案

> 本文保留 P0 落地背景（平台与执行解耦、task.json 契约、pytest/Appium）。  
> **Agent 工程与自愈的现行方案**以  
> [`docs/superpowers/plans/2026-07-22-agent-engineering-self-heal.md`](superpowers/plans/2026-07-22-agent-engineering-self-heal.md)  
> 为准（**StageAgentHarness**：确定性高速路 + HealLoop 围栏内自愈，而非「执行阶段禁止 LLM」）。

## 1. 核心思路

平台与执行逻辑**解耦**。平台只负责「出用例 + 触发 + 看结果」，执行逻辑做成一个**独立 pytest 工程**（`execution_runtime/`），能脱离平台用 CLI 单独跑。两者通过**文件契约**对接：

```
TestPlatform ──① 导出 task.json──▶ execution_runtime（子进程）
TestPlatform ◀─③ 读 allure-report/ + summary.json── execution_runtime
```

调用方式：平台 `spawn python -m execution_runtime.runner --task <task.json> --out <run_dir>`，跑完读回产物。

## 2. 执行流水线（运行时内部）

```
0. 环境/设备预检 gate
1. 编译层：本地规则优先；必要时 execution.compiler
2. pytest：每条用例一个 test
3. Setup（登录/入口）+ 模块会话 / 智能导航
4. 双轨执行：DSL 确定性路径 + Agent navigator（ToolGateway 围栏）
5. 阻塞时 StageAgentHarness / HealLoop + execution.diagnoser 自愈（分档预算）
6. 每步截图/source；失败记缺陷；heal_ledger 可审计
7. 产物：allure-report/ + summary.json + defects.json + heal_ledger.jsonl
```

## 3. 全局配置（被测 App / 设备，统一管理）

被测 App 与设备信息**用配置项全局管理**，不在前端每次手填。落在 `execution_runtime/config/settings.yaml`，平台导出 task.json 时从此读取默认值。

真实探测值（本机已连 iOS 真机、已装叭嗒）：

```yaml
target_app:
  name: "爱奇艺叭嗒"
  platform: ios
  bundle_id: "com.iqiyi.acg"      # 真机已装 v6.1.0
  # app_path: null                # 真机已装则无需重装

device:
  udid: "00008130-0004152902D1001C"   # 真机「黄桃」
  device_name: "黄桃"
  platform_version: "26.3"            # iPhone16,1 / iOS 26.3 / arm64e
  appium_url: "http://127.0.0.1:4723"
  automation_name: "XCUITest"
  wda_bundle_id: "com.iqiyi.wda.xctrunner.xctrunner"  # 真机已装 WDA

run:
  max_concurrency: 1               # 单真机，先串行跑通
  case_timeout_seconds: 120
  max_heal_attempts: 2
  self_heal_enabled: true
  ocr_enabled: true                # 缺失自动降级
```

## 4. 输入契约 task.json

平台只导出「**已评审通过(approved)**」的 TCG 用例（其它状态不允许进入编译/执行），App/设备段从全局配置带出：

```json
{
  "run_id": "EXE-0001",
  "app": {
    "platform": "ios",
    "bundle_id": "com.iqiyi.acg"
  },
  "device": {
    "udid": "00008130-0004152902D1001C",
    "device_name": "黄桃",
    "platform_version": "26.3",
    "appium_url": "http://127.0.0.1:4723",
    "automation_name": "XCUITest"
  },
  "cases": [
    {
      "case_id": "uuid",
      "title": "...",
      "status": "approved",
      "preconditions": "...",
      "platform_type": "ios",
      "test_point_id": "TP-001",
      "steps": [
        {"step": 1, "action": "点击搜索入口", "expected": "进入搜索页"}
      ]
    }
  ]
}
```

> 约束：runner 启动时二次校验每条 `status == approved`，非法状态直接拒绝（防止绕过平台注入未评审用例）。

## 5. 新 DSL（全新一套，不复用 src/executor 的 StepAction）

`execution_runtime/dsl/models.py`，pydantic 定义。老 `StepAction`、`src/executor`、`/execution` 页**原样保留不碰**，后续由你决定删除。

### 5.1 动作集（App UI 第一阶段）

| action | 参数 | 说明 |
|--------|------|------|
| `launch_app` | - | 启动/激活目标 App |
| `terminate_app` | - | 关闭 App |
| `tap` | locator | 点击元素 |
| `input` | locator, value | 输入文本 |
| `clear` | locator | 清空输入框 |
| `swipe` | direction, ratio, times?, until? | 手势滑动，可带终止条件 |
| `scroll` | direction, until? | 滚动到某标志出现 |
| `back` | - | 系统返回/侧滑返回 |
| `wait` | timeout / until(locator) | 显式等待 |
| `assert_visible` | locator | 断言元素存在 |
| `assert_text` | locator/ocr, value | 断言文本 |
| `screenshot` | - | 截图 |

### 5.2 locator（定位优先级：强 → 弱，iOS）

`accessibility_id > name/label > predicate/class_chain > xpath > ocr_text(兜底)`

```json
{"type": "accessibility_id", "value": "search_entry"}
```

### 5.3 DSL 示例（编译产物）

```json
{
  "case_id": "uuid",
  "name": "search_bada",
  "steps": [
    {"action": "launch_app"},
    {"action": "tap", "locator": {"type": "accessibility_id", "value": "搜索"}},
    {"action": "input", "locator": {"type": "class_chain", "value": "**/XCUIElementTypeSearchField"}, "value": "斗罗大陆"},
    {"action": "wait", "until": {"type": "name", "value": "搜索结果"}, "timeout": 10},
    {"action": "assert_visible", "locator": {"type": "name", "value": "斗罗大陆"}}
  ]
}
```

## 6. 编译层 Compiler（NL 用例 → DSL）

- role：`execution.compiler`（走 `agent_runtime`，config 里新增）。
- 输入：单条 approved TestCase（`title/preconditions/steps[].action/expected`）+ App 上下文（platform=ios、bundle_id）。
- 输出：上面的 DSL JSON。
- 关键补齐（对应之前讨论的四类缺口）：
  - **定位**：把「章节末尾」这类意图编译成带终止条件的手势循环（`swipe until <marker> / max_times`）。
  - **数据**：`input` 缺值时补默认测试数据。
  - **断言分级**：可判定 → `assert_visible/assert_text`；不可判定（"流畅无卡顿"）→ 降级为截图留证，不作硬门禁。
- 缓存：DSL 落盘 `run_dir/compiled/<case_id>.json`，可复查。

## 7. 执行引擎

`engine/executor.py`：DSL → Appium/XCUITest 确定性执行，逐动作映射。定位按 5.2 优先级依次尝试。执行阶段**不调 LLM**。

## 8. 全过程可视化（日志 + 快照，越详细越好）

每一步都留痕，产物结构化落盘，平台可回放：

- **步骤日志**：JSONL（`run_dir/<case_id>/steps.jsonl`），每步含 `step_no / action / locator / 命中策略 / 开始·结束时间 / 耗时 / 状态 / 错误 / 自愈信息`。
- **快照**：每步前后各一张截图（`screenshots/<case_id>_<step>_{before,after}.png`）+ page_source（`source/<case_id>_<step>.xml`）。
- **运行总日志**：`run_dir/run.log`（复用平台既有 JSONL logger 风格），阶段级事件（预检/编译/执行/自愈/报告）。
- **Allure**：把上述日志、截图、page_source、自愈记录全部 attach 到对应 test/step，报告里点开即见。
- 敏感字段（手机号/密码/验证码）日志与截图按需脱敏。

## 9. 缺陷记录（执行中发现 bug 自动记录）

- 触发条件：断言失败、App crash、经自愈仍无法通过的定位失败 → 判定为 bug。
- 产物：`run_dir/defects.json`，每条含 `case_id / title / 复现步骤 / 期望 vs 实际 / 失败类型 / 关键截图·page_source 路径 / 严重级别`。
- 平台对接（P3）：读 `defects.json` 落库到现有 `Defect` 表（`project_id` 可空，沿用解耦模块惯例），在执行结果页展示缺陷列表。

## 10. 环境/设备预检 gate（自动执行）

`env/precheck.py`，**运行时启动即自动执行**，不需要在平台前端手动点触发；不过直接拦停并给出可执行修复指引（或自动修复）。检查项（iOS 真机）：

- Appium-Python-Client / appium CLI + `xcuitest` driver 已装
- Appium Server 可达（不可达尝试拉起）
- iOS 真机在线：`idevice_id -l` 含目标 UDID；`ideviceinfo` 可读
- **目标 App 已安装**：`ideviceinstaller list` 含 `com.iqiyi.acg`（缺失则报缺陷/阻断，第一阶段不自动安装）
- WebDriverAgent 就绪（真机已装 `com.iqiyi.wda...`）
- PaddleOCR 可用（缺失降级，不阻断）

## 11. pytest + allure 组织

- runner 读 task.json → 自动预检 → 逐条编译 → 动态生成 pytest 用例（每条一个 test）。
- `conftest.py` fixture：设备(真机) / Appium driver / ocr / healer / 步骤记录器。
- allure-pytest 原生出报告；每步 attach 截图、page_source、自愈记录。
- 另写 `summary.json`（总数/通过/失败/自愈次数/缺陷数）+ `defects.json`，供平台快速展示。

## 12. 目录结构

```
execution_runtime/               # 独立 pytest 工程，可 CLI 单跑
├── runner.py                    # 入口：task.json → 预检 → 编译 → pytest → 收产物
├── conftest.py
├── env/precheck.py              # 自动预检（iOS 真机 + 叭嗒 app-exists）
├── compiler/{compiler.py, prompts/compile.txt}
├── dsl/models.py                # 全新 DSL
├── engine/{appium_driver.py, executor.py}
├── recorder.py                  # 步骤日志 + 截图 + page_source 快照
├── defect.py                    # 缺陷记录
├── ocr/ocr_service.py
├── heal/healer.py
├── report/{allure_adapter.py, summary.py}
├── models/result.py
└── config/settings.yaml         # 被测 app / 设备 全局配置
```

## 13. 平台侧改动（P3 已实现）

- **页面**：`/app-execution`（rv-* 设计体系）
- **API**：`/api/v1/execution-runs/*`
- **服务**：`execution_runtime_service.py` — task.json 导出 → 子进程 runner → 进度轮询 → summary/defects/allure 回读 → Execution/Defect 落库
- **Allure**：`/execution-runs/{EXE-id}/allure/index.html`
- 侧栏「App UI 执行」；旧 `/execution` 保留不动

## 14. 分阶段落地

1. **P0**：✅ Android 模拟器 + 叭嗒冒烟（EXE-ANDROID-001）
2. **P1 缺陷 + 自愈**：defects.json 已写盘；自愈待续
3. **P2 OCR 增强**：待续
4. **P3 平台对接**：✅ 前后端 + bridge + 落库
