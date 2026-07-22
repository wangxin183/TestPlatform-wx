# AGENTS.md — TestPlatform Agent 工作规范

本文件约束 **Agent 如何改本仓库**，并收录 **前端实现规范**。  
**产品主线**（需求分析 / 用例生成 / 用例执行）的架构与后端约束以 `CLAUDE.md` 为准；此处只写工作方式 + 前端必须遵守的展示侧边界。仪表盘等非主线页面暂不专项总结。

记忆索引：`memory-MEMORY-update.md`。

---

## 0. 工作准则（全栈，最高优先级）

1. **谨记北极星**  
   用 Agent 平衡**确定性高速路**与 **Harness 内自主决策**，解决「人盯日志改特例再跑」。  
   方案须说明落在哪一层，以及为何不是短视硬编码。详见 `CLAUDE.md` 文首、`memory-feedback-project-north-star.md`。

2. **先方案后执行**  
   任何任务先出方案/计划，**用户确认后再改代码**。禁止跳过方案直接改项目。

3. **禁止短视硬编码**  
   不为当前一个 case 堆产品文案白名单、错误码 suggestion 表、前后端各一份特例 map。  
   诊断/建议走后端 Agent；前端必要映射仅限 badge / event→中文展示。

4. **禁止凭经验猜测**  
   Bug 先定位根因（日志、堆栈、对比正常路径），一次只改一个变量并验证。

5. **控制 Token 消耗**  
   少重复读大文件、少无意义长输出；优先检索定位再精读。

6. **前端改动额外要求**  
   兼容 Safari/Chrome/Firefox；与现有 `rv-*` 体系一致；改 JS 必须递增模板 `?v=`。

---

## 1. 主线三页：前端约束（RA / TCG / EXE）

后端产品规则见 `CLAUDE.md` 对应章节。前端**只展示与触发**，不重复实现业务决策。

### 1.1 需求分析页

- 路由/脚本：需求分析页 + `requirement_analysis.js`
- 知识库可接线；「自定义要求」只应进入 Analyzer（勿在前端把自定义要求塞进 Reviewer 等其它角色请求）
- 展示 FR/NFR/TP 与证据；**不要**在前端编造或补全文档中不存在的需求

### 1.2 用例生成页（TCG）

- 路由：`/testcase-generation`；脚本：`testcase_generation.js`（改完递增 `?v=`）
- 任务 ID：`TCG-xxxx`；进度可能含「并发中」
- **不要**在前端拆批或重复调生成 API（组批/并发在后端）
- 步骤只展示**模块内**行为；禁止拼接「进 tab / 进模块」入口步骤
- 编译态区分 `ok` / `agent_required` / `failed`；展示错误码 + Agent 诊断（suggestion/need）
- 用户改用例后允许保存/重编译；编译失败不得伪装成执行失败；结果要有 toast/反馈，避免「假死」
- expected 关键文案须「」；前端不维护 UI 文案白名单或错误码文案表

### 1.3 用例执行页（EXE）

- 路由：执行页 + `app_execution.js`；改完递增 `?v=`
- 默认只跑自动化就绪用例；`semi` / `manual`、缺模块、编译失败须**明确提示**，禁止静默跳过
- `step_contracts`、页面锚点、导航缓存属执行端内部能力；前端只展示状态/诊断，不自维护状态机、不直调 Agent 导航
- 跨页到达目标（如阅读器→会员页）是正常业务迁移；不得展示为「偏离模块」或暗示自动返回
- 自然语言运行日志可展示 `message`；不要在前端再实现一套执行决策逻辑

### 1.4 用例库（与 TCG/EXE 衔接）

- 可见 TCG 落库用例；支持编辑/编译相关能力时同样遵守编译态与诊断展示规则
- XMind：**导入**已有；产品「一键导出」未做。勿把会话级 MCP 导出当成服务端能力

---

## 2. 设计系统（`rv-*`）

### 命名空间

- 新页面 CSS/JS 使用 `rv-*` 前缀
- 禁止复用旧的 `content-header`、`card-grid`、`panel`、`fade-in` 等
- 根容器：`<div class="rv-page">`

### 设计 Token

- 色：`--rv-accent` / `--rv-success` / `--rv-danger` / `--rv-info` / `--rv-warning`
- 间距：8px 节奏（4/8/12/16/20/24/28/32/40）
- 圆角：`--rv-radius-sm` / `--rv-radius` / `--rv-radius-lg` / `--rv-radius-xl`
- 阴影：`--rv-shadow-sm/md/lg/xl`
- 字体：`--rv-font`、`--rv-mono`（系统栈，无外部 CDN）

---

## 3. 组件体系

### 按钮

| 类 | 用途 |
|----|------|
| `rv-btn-accent` | 主操作 |
| `rv-btn-primary` | 次要主操作 |
| `rv-btn-outline` | 取消/返回 |
| `rv-btn-danger` | 驳回/删除 |
| `rv-btn-ghost` | 最轻操作 |
| `rv-btn-icon` | 表格内图标按钮 |
| `rv-btn-sm` / `rv-btn-xs` | 小尺寸 |

### 表格 / 弹窗 / 表单

- 表格：`rv-card > rv-table-wrap > table.rv-table`；空态 `rv-empty`
- 弹窗：`rv-modal-overlay > (rv-modal-glass + rv-modal)`；`Modal.open/close`
- 表单：`rv-select`、`rv-search-*`、`rv-form-input`、`rv-textarea`、`rv-checkbox`
- Badge：`rv-badge-success/danger/warning/info/neutral`

### 颜色语义

- 成功 Emerald / 危险 Rose / 信息 Indigo / 主操作 Amber / 文字 Warm Gray

---

## 4. 图标与兼容性

- 图标：Feather 风格内联 SVG，**禁止** emoji；颜色 `currentColor`
- `backdrop-filter` 必须带 `-webkit-backdrop-filter`
- JS 优先 `var` / `function`，避免 `?.` `??` `||=`（旧 Safari）
- flex `gap` 在旧 Safari 必要时改用 margin

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
        <p class="rv-header-sub">副标题</p>
      </div>
    </div>
  </header>
  <div class="rv-page-body">
    <!-- 内容 -->
  </div>
</div>
{% endblock %}
{% block scripts %}
<script src="/static/js/xxx.js?v=N"></script>
{% endblock %}
```

---

## 6. 前端完成检查清单

- [ ] CSS 类为 `rv-*`；图标 SVG 无 emoji
- [ ] 色彩用 `--rv-*` Token
- [ ] 弹窗 / 表格结构符合组件约定
- [ ] 按钮语义类正确；`backdrop-filter` 有 `-webkit-`
- [ ] JS 兼容语法；模板 `?v=` 已递增
- [ ] 无外部 CDN；空状态有 `rv-empty`
- [ ] 若动 RA/TCG/EXE：未在前端实现组批、入口导航、错误码死表或执行状态机
