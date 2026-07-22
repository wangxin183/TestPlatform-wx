(function () {
  'use strict';

  var state = {
    config: null,
    cases: [],
    selectedCaseIds: {},
    includeSemi: false,
    currentRunId: null,
    currentData: null,
    tab: 'overview',
    pollTimer: null
  };

  function esc(s) {
    return (typeof escapeHtml === 'function') ? escapeHtml(String(s == null ? '' : s)) : String(s == null ? '' : s)
      .replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;').replace(/"/g, '&quot;');
  }

  function fmtDate(str) {
    return (window.fmt && fmt.date) ? fmt.date(str) : (str || '-');
  }

  function statusBadge(status) {
    var map = {
      queued: 'rv-badge-neutral',
      running: 'rv-badge-info',
      completed: 'rv-badge-success',
      failed: 'rv-badge-danger',
      aborted: 'rv-badge-danger',
      unknown: 'rv-badge-neutral'
    };
    var labels = {
      queued: '排队中',
      running: '执行中',
      completed: '已完成',
      failed: '失败',
      aborted: '已中止'
    };
    return '<span class="' + (map[status] || 'rv-badge-neutral') + '">' + esc(labels[status] || status) + '</span>';
  }

  var DataAPI = {
    config: function () {
      return api.get('/execution-runs/config');
    },
    cases: function (includeSemi) {
      var q = '/execution-runs/cases?test_type=ui&limit=200';
      if (includeSemi) q += '&include_semi=true';
      return api.get(q);
    },
    history: function () {
      return api.get('/execution-runs?size=50');
    },
    start: function (caseIds, includeSemi) {
      return api.post('/execution-runs', {
        case_ids: caseIds,
        include_semi: !!includeSemi
      });
    },
    status: function (runId) {
      return api.get('/execution-runs/' + encodeURIComponent(runId) + '/status');
    },
    detail: function (runId) {
      return api.get('/execution-runs/' + encodeURIComponent(runId));
    }
  };

  function levelBadge(level) {
    var map = {
      ready: 'rv-badge-success',
      semi: 'rv-badge-warning',
      manual: 'rv-badge-neutral'
    };
    var labels = { ready: 'ready', semi: 'semi', manual: 'manual' };
    var lv = level || 'ready';
    return '<span class="' + (map[lv] || 'rv-badge-neutral') + '">' + esc(labels[lv] || lv) + '</span>';
  }

  function executionBadge(c) {
    if (c.compile_status === 'agent_required' || c.execution_mode === 'agent') {
      return '<span class="rv-badge-warning">Agent</span>';
    }
    return '<span class="rv-badge-success">DSL 优先</span>';
  }

  function renderConfig(cfg) {
    var el = document.getElementById('runtime-config');
    if (!el || !cfg) return;
    var app = cfg.target_app || {};
    var dev = cfg.device || {};
    el.innerHTML =
      '<div><strong>被测 App</strong> · ' + esc(app.name) + '</div>' +
      '<div>包名 <code>' + esc(app.bundle_id) + '</code></div>' +
      (app.app_activity ? '<div>Activity <code>' + esc(app.app_activity) + '</code></div>' : '') +
      '<div style="margin-top:8px;"><strong>设备</strong> · ' + esc(dev.device_name) + '</div>' +
      '<div>UDID <code>' + esc(dev.udid) + '</code> · Android/iOS ' + esc(dev.platform_version) + '</div>' +
      '<div>驱动 <code>' + esc(dev.automation_name) + '</code></div>' +
      '<div>Appium <code>' + esc(dev.appium_url) + '</code></div>' +
      '<p style="margin-top:8px;color:var(--rv-text-muted);">修改 <code>execution_runtime/config/settings.yaml</code></p>';
  }

  function renderCaseTable() {
    var tbody = document.getElementById('case-tbody');
    var btn = document.getElementById('start-btn');
    if (!tbody) return;
    if (!state.cases.length) {
      tbody.innerHTML = '<tr><td colspan="4" class="rv-empty">暂无符合条件的 approved UI 用例（默认仅 ready）</td></tr>';
      if (btn) btn.disabled = true;
      return;
    }
    var html = '';
    state.cases.forEach(function (c) {
      var id = c.case_id || '';
      var checked = state.selectedCaseIds[id] ? ' checked' : '';
      html += '<tr>' +
        '<td><input type="checkbox" class="rv-checkbox case-check" data-id="' + esc(id) + '"' + checked + '></td>' +
        '<td title="' + esc(c.title) + '">' + esc((c.title || '').substring(0, 48)) + '</td>' +
        '<td><span class="rv-type-tag">' + esc(c.module || '-') + '</span></td>' +
        '<td>' + executionBadge(c) + '</td>' +
        '</tr>';
    });
    tbody.innerHTML = html;
    Array.prototype.forEach.call(tbody.querySelectorAll('.case-check'), function (el) {
      el.addEventListener('change', function () {
        var id = el.getAttribute('data-id');
        if (el.checked) state.selectedCaseIds[id] = true;
        else delete state.selectedCaseIds[id];
        updateStartEnabled();
      });
    });
    updateStartEnabled();
  }

  function updateStartEnabled() {
    var btn = document.getElementById('start-btn');
    if (!btn) return;
    btn.disabled = Object.keys(state.selectedCaseIds).length === 0;
  }

  function renderHistory(list) {
    var el = document.getElementById('history-list');
    if (!el) return;
    if (!list.length) {
      el.innerHTML = '<p class="rv-text-muted" style="padding:16px;">暂无历史任务</p>';
      return;
    }
    var html = '';
    list.forEach(function (t) {
      var rid = t.run_id || '';
      var active = state.currentRunId === rid ? ' active' : '';
      var sum = t.summary || {};
      html += '<div class="aex-history-item' + active + '" data-id="' + esc(rid) + '">' +
        '<div><strong>' + esc(rid) + '</strong>' +
        '<div class="rv-text-muted" style="font-size:12px;">' +
        (t.case_count || 0) + ' 条 · 通过 ' + (sum.passed != null ? sum.passed : (t.passed != null ? t.passed : '-')) +
        '</div></div>' + statusBadge(t.status) + '</div>';
    });
    el.innerHTML = html;
    Array.prototype.forEach.call(el.querySelectorAll('.aex-history-item'), function (item) {
      item.addEventListener('click', function () {
        openRun(item.getAttribute('data-id'));
      });
    });
  }

  var SOURCE_LABEL = {
    runtime: '运行时',
    heal: '自愈',
    agent: 'Agent',
    session: '会话',
    platform: '平台'
  };

  function formatLogMessage(l) {
    if (l && l.message && /[\u4e00-\u9fff]/.test(String(l.message))) {
      return String(l.message);
    }
    var ev = (l && (l.event || l.step || l.raw)) || '';
    if (typeof ev !== 'string') ev = JSON.stringify(ev);
    var map = {
      task_loaded: '已加载执行任务',
      precheck_start: '开始环境预检',
      precheck_done: '环境预检完成',
      cases_accepted: '用例校验通过',
      cases_rejected: '部分用例被拒绝',
      compile_start: '开始编译用例 DSL',
      compile_done: '用例编译完成',
      compile_failed: '用例编译失败',
      pytest_start: '启动真机/模拟器执行',
      pytest_done: 'pytest 执行结束',
      allure_generated: '已生成 Allure 报告',
      run_completed: '本轮执行完成',
      run_aborted: '执行中止',
      export_start: '导出执行任务',
      export_done: '任务文件已导出',
      subprocess_start: '执行子进程已启动',
      subprocess_done: '执行子进程结束',
      db_import_done: '结果已写回数据库',
      run_failed: '执行任务失败',
      heal_plan: '已生成自愈方案',
      heal_success: '自愈后重试成功',
      heal_exhausted: '自愈次数用尽',
      tool_call: 'Agent 调用工具',
      module_navigation_succeeded: '已进入目标模块',
      skip_setup: '复用同模块会话'
    };
    return map[ev] || (l && l.message) || ev || '（无说明）';
  }

  function renderTabContent(tab, data) {
    if (tab === 'logs') {
      var logs = data.narrative_logs || [];
      if (!logs.length) {
        logs = (data.runtime_logs || []).concat(data.logs || []);
      }
      if (!logs.length) return '<p class="rv-empty">暂无日志</p>';
      var html = '<div class="aex-log-list">';
      logs.forEach(function (l) {
        var ts = l.ts || l.timestamp || '';
        var msg = formatLogMessage(l);
        var srcKey = l.source || '';
        var src = srcKey
          ? '<span class="aex-log-source">' + esc(SOURCE_LABEL[srcKey] || srcKey) + '</span> '
          : '';
        html += '<div class="aex-log-entry">' +
          '<div class="aex-log-meta"><span class="rv-text-muted">' + esc(String(ts)) + '</span> ' + src + '</div>' +
          '<div class="aex-log-msg">' + esc(String(msg)) + '</div></div>';
      });
      return html + '</div>';
    }
    if (tab === 'env') {
      var env = data.env_check;
      if (!env || !env.items) return '<p class="rv-empty">暂无预检结果</p>';
      var eh = '';
      env.items.forEach(function (i) {
        var mark = i.ok ? '✅' : '❌';
        eh += '<div class="aex-env-item">' + mark + ' <strong>' + esc(i.name) + '</strong> — ' + esc(i.detail || '') + '</div>';
      });
      return eh;
    }
    if (tab === 'defects') {
      var defects = data.defects || [];
      if (!defects.length) return '<p class="rv-empty">无缺陷记录</p>';
      var dh = '<div class="rv-table-wrap"><table class="rv-table"><thead><tr><th>标题</th><th>严重度</th><th>用例</th></tr></thead><tbody>';
      defects.forEach(function (d) {
        dh += '<tr><td>' + esc(d.title) + '</td><td>' + esc(d.severity) + '</td><td><code>' + esc(d.case_id) + '</code></td></tr>';
      });
      return dh + '</tbody></table></div>';
    }
    if (tab === 'cases') {
      var results = data.case_results || [];
      if (!results.length) return '<p class="rv-empty">暂无用例结果</p>';
      var ch = '<div class="rv-table-wrap"><table class="rv-table"><thead><tr><th>用例</th><th>结果</th><th>耗时</th></tr></thead><tbody>';
      results.forEach(function (r) {
        var badge = r.outcome === 'passed' ? 'rv-badge-success' : 'rv-badge-danger';
        ch += '<tr><td title="' + esc(r.title) + '">' + esc((r.title || r.case_id || '').substring(0, 40)) +
          '</td><td><span class="' + badge + '">' + esc(r.outcome) + '</span></td><td>' +
          (r.duration_ms || 0) + 'ms</td></tr>';
      });
      return ch + '</tbody></table></div>';
    }
    return '';
  }

  function renderResult(data) {
    state.currentData = data;
    var body = document.getElementById('result-body');
    var title = document.getElementById('result-title');
    if (!body) return;
    if (!data) {
      body.innerHTML = '<div class="rv-empty"><p>暂无数据</p></div>';
      return;
    }
    if (title) title.textContent = data.run_id + ' · 执行详情';

    var sum = data.summary || {};
    var html = '';
    html += '<div style="display:flex;justify-content:space-between;align-items:center;margin-bottom:8px;">' +
      statusBadge(data.status) +
      '<span class="rv-text-muted" style="font-size:12px;">' + esc(data.current_step || '') + '</span></div>';

    html += '<div class="aex-progress-wrap"><div class="aex-progress-bar">' +
      '<div class="aex-progress-fill" style="width:' + (data.progress_pct || 0) + '%"></div></div>' +
      '<div style="font-size:12px;color:var(--rv-text-muted);margin-top:4px;">进度 ' + (data.progress_pct || 0) + '%</div></div>';

    html += '<div class="aex-stats">' +
      '<div class="aex-stat"><div class="aex-stat-num">' + (sum.total != null ? sum.total : '-') + '</div><div class="aex-stat-label">总计</div></div>' +
      '<div class="aex-stat"><div class="aex-stat-num" style="color:var(--rv-success)">' + (sum.passed != null ? sum.passed : '-') + '</div><div class="aex-stat-label">通过</div></div>' +
      '<div class="aex-stat"><div class="aex-stat-num" style="color:var(--rv-danger)">' + (sum.failed != null ? sum.failed : '-') + '</div><div class="aex-stat-label">失败</div></div>' +
      '<div class="aex-stat"><div class="aex-stat-num" style="color:var(--rv-warning)">' + (sum.defects != null ? sum.defects : '-') + '</div><div class="aex-stat-label">缺陷</div></div>' +
      '</div>';

    if (data.error_message) {
      html += '<div class="rv-flag-warning" style="margin-bottom:12px;">' + esc(data.error_message) + '</div>';
    }

    if (data.allure_url && (data.status === 'completed' || data.status === 'failed')) {
      html += '<p style="margin-bottom:12px;"><a class="rv-btn rv-btn-outline rv-btn-sm" href="' +
        esc(data.allure_url) + '" target="_blank" rel="noopener">打开 Allure 报告</a></p>';
    }
    if (data.execution_id) {
      html += '<p class="rv-text-muted" style="font-size:12px;margin-bottom:12px;">已落库 Execution: <code>' +
        esc(data.execution_id) + '</code></p>';
    }

    html += '<div class="aex-tabs">' +
      '<button type="button" class="aex-tab' + (state.tab === 'overview' ? ' active' : '') + '" data-tab="overview">概览</button>' +
      '<button type="button" class="aex-tab' + (state.tab === 'cases' ? ' active' : '') + '" data-tab="cases">用例结果</button>' +
      '<button type="button" class="aex-tab' + (state.tab === 'defects' ? ' active' : '') + '" data-tab="defects">缺陷</button>' +
      '<button type="button" class="aex-tab' + (state.tab === 'env' ? ' active' : '') + '" data-tab="env">环境预检</button>' +
      '<button type="button" class="aex-tab' + (state.tab === 'logs' ? ' active' : '') + '" data-tab="logs">日志</button>' +
      '</div>';

    if (state.tab === 'overview') {
      html += '<p class="rv-text-muted" style="font-size:13px;">创建 ' + fmtDate(data.created_at) +
        ' · 完成 ' + fmtDate(data.completed_at) + ' · ' + (data.case_count || 0) + ' 条用例</p>';
    } else {
      html += renderTabContent(state.tab, data);
    }

    body.innerHTML = html;
    Array.prototype.forEach.call(body.querySelectorAll('.aex-tab'), function (btn) {
      btn.addEventListener('click', function () {
        state.tab = btn.getAttribute('data-tab');
        renderResult(state.currentData);
      });
    });
  }

  function stopPoll() {
    if (state.pollTimer) {
      clearInterval(state.pollTimer);
      state.pollTimer = null;
    }
  }

  function startPoll(runId) {
    stopPoll();
    state.pollTimer = setInterval(function () {
      DataAPI.status(runId).then(function (res) {
        if (!res.success) return;
        var st = res.data;
        if (state.currentRunId !== runId) return;
        if (state.currentData) {
          state.currentData.status = st.status;
          state.currentData.progress_pct = st.progress_pct;
          state.currentData.current_step = st.current_step;
          state.currentData.summary = st.summary;
          state.currentData.error_message = st.error_message;
          if (st.narrative_logs && st.narrative_logs.length) {
            state.currentData.narrative_logs = st.narrative_logs;
          }
          if (st.logs) {
            state.currentData.logs = st.logs;
          }
        }
        renderResult(state.currentData);
        if (st.status !== 'running' && st.status !== 'queued') {
          stopPoll();
          DataAPI.detail(runId).then(function (dres) {
            if (dres.success) {
              state.currentData = dres.data;
              renderResult(dres.data);
            }
            refreshHistory();
          });
        }
      });
    }, 2500);
  }

  function openRun(runId) {
    state.currentRunId = runId;
    state.tab = 'logs';
    stopPoll();
    DataAPI.detail(runId).then(function (res) {
      if (!res.success) {
        alert(res.error || '加载失败');
        return;
      }
      renderResult(res.data);
      refreshHistory();
      if (res.data.status === 'running' || res.data.status === 'queued') {
        startPoll(runId);
      }
    });
  }

  function refreshHistory() {
    DataAPI.history().then(function (res) {
      renderHistory(res.data || []);
    });
  }

  function loadCases() {
    DataAPI.cases(state.includeSemi).then(function (res) {
      state.cases = res.data || [];
      var visible = {};
      state.cases.forEach(function (c) { visible[c.case_id] = true; });
      Object.keys(state.selectedCaseIds).forEach(function (id) {
        if (!visible[id]) delete state.selectedCaseIds[id];
      });
      renderCaseTable();
    });
  }

  function bindEvents() {
    var startBtn = document.getElementById('start-btn');
    if (startBtn) {
      startBtn.addEventListener('click', function () {
        var ids = Object.keys(state.selectedCaseIds);
        if (!ids.length) return;
        startBtn.disabled = true;
        DataAPI.start(ids, state.includeSemi).then(function (res) {
          startBtn.disabled = false;
          if (!res.success) {
            alert(res.error || '启动失败');
            return;
          }
          openRun(res.data.run_id);
        }).catch(function () {
          startBtn.disabled = false;
        });
      });
    }
    var semiEl = document.getElementById('include-semi');
    if (semiEl) {
      semiEl.addEventListener('change', function () {
        state.includeSemi = !!semiEl.checked;
        loadCases();
      });
    }
    var selAll = document.getElementById('btn-select-all');
    if (selAll) {
      selAll.addEventListener('click', function () {
        state.cases.forEach(function (c) {
          state.selectedCaseIds[c.case_id] = true;
        });
        renderCaseTable();
      });
    }
    var selNone = document.getElementById('btn-select-none');
    if (selNone) {
      selNone.addEventListener('click', function () {
        state.selectedCaseIds = {};
        renderCaseTable();
      });
    }
  }

  function init() {
    bindEvents();
    DataAPI.config().then(function (res) {
      state.config = res.data;
      renderConfig(res.data);
    });
    loadCases();
    refreshHistory();
    var params = new URLSearchParams(window.location.search);
    var runId = params.get('run_id');
    if (runId) openRun(runId);
  }

  if (document.readyState === 'loading') {
    document.addEventListener('DOMContentLoaded', init);
  } else {
    init();
  }
})();
