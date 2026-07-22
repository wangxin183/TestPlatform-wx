---
name: executable-testcase-runtime
description: 模块化可执行用例、跨页面合同、登录 Setup 与 Agent 工具执行的长期约定
metadata:
  node_type: memory
  type: feedback
---

## 可执行用例双轨标准

UI 用例必须同时满足人工评审与自动执行：

- 前端展示自然语言（NL），执行端消费确定性 DSL
- 编译产物包含 `exec_script`、`step_contracts`、`compile_status`、`execution_mode`
- 确定性 DSL 优先；无法确定定位或 DSL 失败时切换 Agent 工具模式
- Agent 不直接操作 Appium，所有动作统一经过 `ToolGateway`
- `automation_level` 使用 `ready` / `semi` / `manual`；审批采用半硬门，执行默认只放行就绪用例

## 模块与智能入口

- 每条 UI 用例必须关联 `config/acn_modules.yaml` 中的 ACN 一级模块
- TCG 只生成模块内步骤，不生成“进入 tab、点击卡片进入模块”等入口前缀
- 执行端使用“页面锚点 + Agent 智能导航 + `NavigationPathCache`”，同模块用例复用 Appium Session
- 页面锚点支持 `package`、`activity`、`required_all`、`required_any`、`forbidden_any`
- 漫画阅读器：`.comic.creader.AcgCReaderActivity`，稳定 ID 为 `reader_root`、`fragment_read_real`
- 会员页：`com.iqiyi.vipcashier.activity.PhonePayActivity`，稳定 ID 为 `vip_gold_page`、`price_card`

## 登录 Setup / 安全检测 / 内容入口

`ensure_login_state` 可无人值守过「安全检测」。链路：截图 → MaaS Vision → **0~1000 相对坐标**映射整图像素 → `adb input tap` → page source「确定」bounds。

固化配置：

- 模型：`config/execution_testdata.yaml` → `security_check_model: qwen3-vl-plus`
- 路由：`task_tag=execution.security_check` → `dashscope_maas`
- 实现：`execution_runtime/setup/security_check.py`；Key：`DASHSCOPE_API_KEY`
- 凭证/选择器进 `execution_testdata.yaml`，禁止写死在步骤里

必记坑：

1. 游客页 `nameTv=小伙伴，戳我登录` ≠ 已登录。
2. Vision 慢时须 keepalive / 提高 `newCommandTimeout`（曾 120 断会话）。
3. Qwen-VL 坐标是 **0~1000**，不是像素；点选任务用 `*-vl-*`，勿用通用 chat 硬 grounding。
4. 白屏未加载完不要送 Vision；「确定」优先 page source bounds。
5. WebView 点击优先 `adb shell input tap`。

内容搜索入口（`ensure_entry_context`）：拉起 App → 搜索 → 按 `entry_context` 选漫画/动画 Tab → 点「立即阅读」/「开始阅读」。作品名来自 `execution_testdata.yaml` 的 `comics` / `animes` / `reader_page_mode`。

## 跨页面状态合同

步骤合同必须反映真实状态链，不能把所有步骤硬编码成模块主状态：

```text
点击「开通会员」：
reader_main -> external:会员页

确认会员页：
external:会员页 -> external:会员页
```

运行时规则：

1. 跨页动作执行后验证目标页面锚点；没有锚点时至少验证 Activity、package 或页面源发生变化。
2. 目标页是预期结果时，不能按模块主状态执行恢复 `back`。
3. 已落库旧合同由 `repair_step_contract_states` 按 NL 步骤重算，旧任务无需人工重编译。
4. Agent 只对配置中存在的状态做严格匹配；未知外部状态不得错误回退到模块首个状态。

## Agent 调用成本与稳定性

- 页面上下文使用 `PageObservation.as_agent_dict()`，只发送必要控件并限制数量
- `ToolGateway` 兼容 `resource-id:xxx` 等 locator 简写，避免因参数形状错误浪费一次 Agent 调用
- `assert_visible`、`assert_text`、`tap` 等确定性动作优先，不要让 Agent 重复观察已知信息
- 连续重复动作必须触发循环保护；成功导航路径写入缓存，失败缓存失效后再探索

## 断言抽取与词表（永久约定）

- **只认引号文案**：`objective_text_signals` 不维护产品 UI 白名单；无「」不猜 `text_visible`。
- **自愈**：弱编译走 Agent 改写 / 重生，不做白名单补「」。
- **词表**：主观/模糊/动作动词 → `config/automation_lexicon.yaml`。
- **narrate**：禁止 `narrate(name, **ev)`（`ev` 含 `event` 会 TypeError）；先剔掉 `event` 键。

## EXE-0013 根因与验证

失败用例：`428370c5-8702-475d-9dee-799e528c2413`。

根因：步骤“点击会员条「开通会员」”实际进入会员页，但旧合同写成 `reader_main -> reader_main`。运行时误判偏离并 `back`，随后在错误页触发 Agent 超时。

修复后合同为 `reader_main -> external:会员页`；同用例真实复跑约 2分05秒，`passed=1`、`broken=0`。复跑须满足前置测试数据（有会员条的漫画）。
