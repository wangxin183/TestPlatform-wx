# AGENTS.md — TestPlatform 前端开发规范

## 0. 工作准则（最高优先级）

1. **先方案后执行**：接到任何任务时，必须先输出方案/计划给用户，获得确认后再开始动手修改代码。禁止跳过方案阶段直接修改项目。
2. **前端质量准则**：每次对页面修改、重构或新增，都必须考虑浏览器兼容性（Safari/Chrome/Firefox）、页面风格一致性（与现有 `rv-*` 体系保持一致）、响应式表现。

3. **控制 Token 消耗**：避免重复读取文件、避免无意义的大段输出、精简代码注释和日志、优先用 `sed`/`grep` 定位而非 `cat` 全文件。
4. **禁止凭经验猜测，必须系统排查根因**：遇到 bug 时，先定位根因再动手修改。用 DevTools、日志、对比正常页面等方式找到确切原因，一次只改一个变量并即时验证。禁止反复试错式修改。

本文件适用于所有涉及前端页面、样式、JS 交互的修改。每次改动前必须对照本规范。

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
