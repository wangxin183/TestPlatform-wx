# AGENTS.md — TestPlatform 前端开发规范

## 0. 工作准则（最高优先级）

0. **谨记项目北极星**：本项目要用 Agent **平衡确定性与自主决策**（确定性高速路 + Harness 内自愈），解决「人盯日志改特例再跑」的场景。前端只展示状态与 Agent/后端诊断，**禁止**把业务决策、错误码文案表、产品 UI 文案白名单做进 JS。详见 `memory-feedback-project-north-star.md`、`CLAUDE.md` 北极星节。
1. **先方案后执行**：接到任何任务时，必须先输出方案/计划给用户，获得确认后再开始动手修改代码。禁止跳过方案阶段直接修改项目。方案须说明落在「确定性」还是「Agent 围栏」，以及为何不是短视硬编码。
2. **前端质量准则**：每次对页面修改、重构或新增，都必须考虑浏览器兼容性（Safari/Chrome/Firefox）、页面风格一致性（与现有 `rv-*` 体系保持一致）、响应式表现。

3. **控制 Token 消耗**：避免重复读取文件、避免无意义的大段输出、精简代码注释和日志、优先用 `sed`/`grep` 定位而非 `cat` 全文件。
4. **禁止凭经验猜测，必须系统排查根因**：遇到 bug 时，先定位根因再动手修改。用 DevTools、日志、对比正常页面等方式找到确切原因，一次只改一个变量并即时验证。禁止反复试错式修改。
5. **禁止短视硬编码**：不为当前一个 case 在前端堆特例 map；诊断文案来自后端/Agent；必要映射仅限展示用 badge/event→中文。

本文件适用于所有涉及前端页面、样式、JS 交互的修改。每次改动前必须对照本规范。

### 与独立业务模块相关的前端约束

- **需求分析页**（`requirement_analysis`）：知识库模块已接线；「自定义要求」只进 Analyzer，不进其他角色。
- **用例生成页**（`/testcase-generation`，`testcase_generation.js`）：任务 ID `TCG-xxxx`；进度文案可能含「并发中」；修改 JS 后必须递增模板 `?v=`。
- 用例生成后端已降本（token 组批 + 有限并发），前端只展示进度/结果，**不要**在前端再拆批或重复调用生成 API。
- 用例步骤只展示模块内行为；模块入口由 `execution_runtime` 根据 `config/acn_modules.yaml` 的页面锚点智能处理，前端不得拼接或执行入口步骤。
- 编译结果需区分 `ok` / `agent_required` / `failed`，展示具体步骤、错误码和原因；用户修改后允许重新编译，不能把编译失败伪装成执行失败。
- 编译诊断文案由 `testcase.compile_advisor` 产出（suggestion/need），前端只展示；禁止再维护前端错误码文案表。
- expected 关键文案须「」包裹；无引号时编译不会再靠产品 UI 白名单猜测。
- 执行页默认只执行自动化就绪用例；`semi` / `manual`、缺少模块或编译失败的用例必须明确提示，不得静默跳过。
- `step_contracts`、页面锚点、导航路径缓存均属执行端内部能力；前端只展示状态和诊断信息，不维护状态迁移或自行调用 Agent 导航。
- 跨页结果（如阅读器进入会员页）是正常业务状态迁移；前端不得把目标页到达展示为“偏离模块”或自动返回。
- XMind：用例库/评审模块已支持**导入**；产品页「一键导出 XMind」尚未做。会话级可用 Cursor MCP `xmind-generator`，不可当作服务端能力。

---

## 1. 设计系统

### 命名空间
- 所有新页面 CSS/JS 统一使用 `rv-*` 前缀
- 禁止复用旧的 `content-header`、`card-grid`、`panel`、`fade-in` 等样式
- 页面根容器使用 `<div class="rv-page">`

### 设计 Token
- 色彩：`--rv-accent`（琥珀主色）、`--rv-success`、`--rv-danger`、`--rv-info`、`--rv-warning`
- 间距：8px 基准节奏（4/8/12/16/20/24/28/32/40）
- 圆角：`--rv-radius-sm`(6px) / `--rv-radius`(10px) / `--rv-radius-lg`(14px) / `--rv-radius-xl`(18px)
- 阴影：`--rv-shadow-sm/md/lg/xl`
- 字体：`--rv-font`（PingFang SC / Microsoft YaHei 系统栈）、`--rv-mono`（SF Mono / Fira Code 系统栈）
- 所有 Token 定义在 `:root` 中，页面内通过 `var()` 引用

---

## 2. 组件体系

### 按钮
| 类 | 用途 | 外观 |
|----|------|------|
| `rv-btn-accent` | 主操作（创建/确认/通过） | 琥珀渐变 + 光晕阴影 |
| `rv-btn-primary` | 次要主操作 | 深色背景 |
| `rv-btn-outline` | 取消/返回 | 透明 + 边框 |
| `rv-btn-danger` | 驳回/删除 | 红色渐变 |
| `rv-btn-ghost` | 最轻操作 | 透明无边框 |
| `rv-btn-icon` | 图标按钮（表格中） | 32×32 方形 |
| `rv-btn-sm` / `rv-btn-xs` | 小尺寸 | 配合使用 |

### 表格
- 必须包裹在 `rv-card > rv-table-wrap > table.rv-table` 中
- 表头使用 `rv-th-check / rv-th-tag / rv-th-num / rv-th-act` 固定宽度类
- 表格必须有 `table-layout: fixed`（CSS 中已全局设置）
- 空状态：`<tr><td colspan="N" class="rv-empty">提示文字</td></tr>`

### 弹窗
- 结构：`rv-modal-overlay > (rv-modal-glass + rv-modal)`
- Header：`rv-modal-header` > `rv-modal-kicker` + `rv-modal-title`
- Footer：`rv-modal-footer` 分左右（`rv-modal-footer-left` / `rv-modal-footer-right`）
- 打开/关闭：`Modal.open('id')` / `Modal.close('id')`（定义在 `app.js`）

### 选择器 / 表单
- Select：`rv-select` + `rv-select-sm`
- 搜索框：`rv-search-wrap > rv-search-icon + rv-search-input`
- 表单输入：`rv-form-input`
- 多行文本：`rv-textarea` + `rv-textarea-sm`
- 自定义复选框：`rv-checkbox`

### 标签 / Badge
- `rv-badge-success / danger / warning / info / neutral`
- `rv-flag` / `rv-flag-danger` / `rv-flag-warning`（风险标记）
- `rv-type-tag`（测试类型标签）
- `rv-score`（评分数字）

### 卡片
- `rv-card` + `rv-card-hd` (header)
- 统计卡片：`rv-stats-grid > rv-stat-card--approved/rejected/pending/reviewed`
- 侧边栏卡片：`rv-sidebar-card` + `rv-sidebar-title`

### 空状态 / 加载
- 空状态：`<div class="rv-empty">` 配合 SVG 图标
- 加载中：淡色文字 + 居中

### 颜色语义
- 通过/成功 → Emerald (`--rv-success: #10b981`)
- 驳回/危险 → Rose (`--rv-danger: #f43f5e`)
- 待审/信息 → Indigo (`--rv-info: #6366f1`)
- 主操作 → Amber (`--rv-accent: #f59e0b`)
- 文字 → Warm Gray (#1c1917 / #57534e / #a8a29e)

---

## 3. 图标

- **必须**使用 Feather 风格内联 SVG，**禁止** emoji
- 尺寸：14×14 (按钮内) / 16×16 (导航 tab) / 20×20 (header icon)
- 颜色继承 `currentColor`

---

## 4. 浏览器兼容性

| 属性 | 处理 |
|------|------|
| `backdrop-filter` | 必须加 `-webkit-backdrop-filter` |
| `position: sticky` | 表头固定务必测试 |
| JS 语法 | 优先 `var` / `function`，避免 `?.` `??` `||=` |
| `gap` in flex | 旧 Safari 不支持，必要时用 `margin` |
| SVG | 自闭合标签在 HTML5 中写完整闭合 |

---

## 5. 页面结构模板

```html
{% extends "base.html" %}
{% block head_extra %}{% endblock %}
{% block content %}
<div class="rv-page">
  <header class="rv-header">
    <div class="rv-header-left">
      <div class="rv-header-icon-wrap"><svg>...</svg></div>
      <div>
        <h1 class="rv-header-title">页面标题</h1>
        <p class="rv-header-sub">英文副标题</p>
      </div>
    </div>
    <!-- 右侧操作按钮 -->
  </header>
  <div class="rv-page-body">
    <!-- 页面内容 -->
  </div>
</div>
{% endblock %}
{% block scripts %}
<script src="/static/js/xxx.js?v=N"></script>
{% endblock %}
```

---

## 6. 检查清单

每次前端修改完成前，逐项确认：

- [ ] 所有 CSS 类使用 `rv-*` 前缀
- [ ] 图标为 SVG，无 emoji
- [ ] 色彩使用 `--rv-*` Token，无裸 hex 值
- [ ] 弹窗使用 `rv-modal-overlay` 结构
- [ ] 表格包裹 `rv-card > rv-table-wrap > table.rv-table`
- [ ] 按钮使用正确的语义类（accent / danger / outline / ghost）
- [ ] `backdrop-filter` 有 `-webkit-` 前缀
- [ ] JS 兼容语法（var/function）
- [ ] 字体来自 `--rv-font` / `--rv-mono`，无外部 CDN 引用
- [ ] CSS 括号平衡（`{` = `}`）
- [ ] 页面加载无外部网络依赖
- [ ] 空状态有 `rv-empty` 处理
