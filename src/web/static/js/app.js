/* Common JavaScript utilities */

(function () {
  'use strict';

  // API base path
  window.API_BASE = '/api/v1';

  // Fetch wrapper
  window.api = {
    async get(path) {
      const resp = await fetch(API_BASE + path);
      const data = await resp.json();
      if (!data.success && data.error) throw new Error(data.error);
      return data;
    },
    async post(path, body) {
      const resp = await fetch(API_BASE + path, {
        method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(body)
      });
      const data = await resp.json();
      if (!data.success && data.error) throw new Error(data.error);
      return data;
    },
    async put(path, body) {
      const resp = await fetch(API_BASE + path, {
        method: 'PUT', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(body)
      });
      const data = await resp.json();
      if (!data.success && data.error) throw new Error(data.error);
      return data;
    },
    async del(path) {
      const resp = await fetch(API_BASE + path, { method: 'DELETE' });
      const data = await resp.json();
      if (!data.success && data.error) throw new Error(data.error);
      return data;
    },
    async upload(path, formData) {
      const resp = await fetch(API_BASE + path, { method: 'POST', body: formData });
      const data = await resp.json();
      if (!data.success && data.error) throw new Error(data.error);
      return data;
    }
  };

  // Format helpers
  window.fmt = {
    date(str) {
      if (!str) return '-';
      const d = new Date(str.endsWith('Z') ? str : str + 'Z');
      return d.toLocaleString('zh-CN', { timeZone: 'Asia/Shanghai' });
    },
    duration(ms) {
      if (ms < 1000) return ms + 'ms';
      return (ms / 1000).toFixed(1) + 's';
    },
    percent(val) {
      if (val == null) return '-';
      return Number(val).toFixed(1) + '%';
    },
    truncate(str, len) {
      if (!str) return '';
      return str.length > len ? str.slice(0, len) + '...' : str;
    }
  };

  // Modal helper
  window.Modal = {
    open(id) {
      document.getElementById(id).classList.add('open');
    },
    close(id) {
      document.getElementById(id).classList.remove('open');
    }
  };

  // Chinese i18n mappings
  window.i18n = {
    pipelineStatus: {
      pending: '待执行', running: '运行中', paused: '已暂停',
      completed: '已完成', failed: '失败', cancelled: '已取消'
    },
    stageName: {
      pending: '待开始', ingestion: '文档导入', parsing: '文档解析',
      analysis: '需求分析', generation: '用例生成', review: '用例评审',
      execution: '用例执行', reporting: '报告生成', regression: '回归测试',
      completed: '已完成', failed: '失败', cancelled: '已取消', paused: '已暂停'
    },
    stageStatus: {
      pending: '待执行', running: '运行中', completed: '已完成',
      failed: '失败', skipped: '已跳过'
    },
    executionStatus: {
      pending: '待执行', running: '运行中', completed: '已完成',
      failed: '失败', cancelled: '已取消'
    },
    resultStatus: {
      passed: '通过', failed: '失败', error: '错误', skipped: '跳过'
    },
    testCaseStatus: {
      draft: '草稿', pending_review: '待评审', approved: '已通过',
      rejected: '已驳回', deprecated: '已废弃'
    },
    defectStatus: {
      open: '待处理', confirmed: '已确认', in_progress: '处理中',
      fixed: '已修复', wont_fix: '不予修复'
    },
    documentStatus: {
      uploaded: '已上传', parsing: '解析中', parsed: '已解析', failed: '失败'
    },
    platformType: {
      web: 'Web', h5: 'H5', ios: 'iOS', android: 'Android',
      miniprogram: '微信小程序', api: 'API 接口'
    },
    priority: {
      critical: '严重', high: '高', medium: '中', low: '低'
    },
    testType: {
      ui: 'UI测试', api: 'API测试',
      performance: '性能测试', security: '安全测试', compatibility: '兼容性测试'
    },
    reportType: {
      execution: '执行报告', pipeline_summary: '流水线汇总', regression: '回归报告'
    },
    projectStatus: {
      active: '活跃'
    },
    t: function(category, key) {
      var map = window.i18n[category];
      return (map && map[key]) ? map[key] : (key || '');
    }
  };

  // Toast notification
  window.showToast = function (message, type) {
    type = type || 'info';
    var toast = document.createElement('div');
    toast.className = 'alert alert-' + type;
    toast.style.cssText = 'position:fixed;top:20px;right:20px;z-index:2000;min-width:300px;box-shadow:0 4px 12px rgba(0,0,0,0.15);';
    toast.textContent = message;
    document.body.appendChild(toast);
    setTimeout(function () { toast.style.opacity = '0'; toast.style.transition = 'opacity 0.3s'; setTimeout(function () { toast.remove(); }, 300); }, 3000);
  };

  // ── i18n helpers (kept for existing pages that reference i18n/formatting) ──
  // The stage-tools sidebar injection previously lived here and has been removed;
  // "测试执行" is now a top-level sidebar item in base.html.
})();
