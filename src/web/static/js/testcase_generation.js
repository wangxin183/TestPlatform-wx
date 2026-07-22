/* 独立用例生成模块 — 数据层 + 渲染分离，便于后续 Vue2 迁移 */
(function () {
  'use strict';

  var state = {
    sources: [],
    selectedAnalysisId: '',
    testPoints: [],
    selectedTpIds: {},
    currentTask: null,
    currentData: null,
    tab: 'cases',
    pollTimer: null,
    editingCaseId: null,
    modules: []
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
      processing: 'rv-badge-info',
      pending_review: 'rv-badge-warning',
      completed: 'rv-badge-success',
      failed: 'rv-badge-danger'
    };
    var labels = {
      queued: '排队中',
      processing: '生成中',
      pending_review: '待评审',
      completed: '已完成',
      failed: '失败'
    };
    return '<span class="' + (map[status] || 'rv-badge-neutral') + '">' + esc(labels[status] || status) + '</span>';
  }

  function compileStatusLabel(status) {
    var labels = {
      ok: 'DSL 就绪',
      agent_required: 'Agent 执行',
      failed: '不可执行',
      pending: '待编译'
    };
    return labels[status] || status || '待编译';
  }

  function compileBadge(status) {
    var map = {
      ok: 'rv-badge-success',
      agent_required: 'rv-badge-warning',
      failed: 'rv-badge-danger',
      pending: 'rv-badge-neutral'
    };
    return '<span class="' + (map[status] || 'rv-badge-neutral') + '">' +
      esc(compileStatusLabel(status)) + '</span>';
  }

  function compileErrorSummary(c) {
    var errors = (c && c.compile_errors) || [];
    if (!errors.length) return c && c.automation_block_reason ? String(c.automation_block_reason) : '';
    return errors.map(function (e) {
      var reason = e.reason || e.message || '';
      var part = (e.code || 'ERROR') + ': ' + reason;
      if (e.step) part += '（步骤 ' + e.step + '）';
      if (e.suggestion) part += ' | 建议: ' + e.suggestion;
      return part;
    }).join('；');
  }

  function renderCompileDiagnostics(c) {
    var status = c.compile_status || '';
    if (status !== 'failed' && status !== 'agent_required') return '';
    var errors = c.compile_errors || [];
    var border = status === 'failed' ? 'var(--rv-danger)' : 'var(--rv-warning)';
    var title = status === 'failed'
      ? '编译诊断：不可执行'
      : '编译诊断：Agent 执行';
    var intro = status === 'failed'
      ? '当前无法稳定自动执行。下列原因/建议由编译诊断 Agent 即时生成，修改后请「保存并编译」。'
      : '可执行，但部分步骤需 Agent 运行时补定位。下列建议由编译诊断 Agent 即时生成。';
    var items = '';
    if (!errors.length) {
      items = '<li><div class="tcg-diag-reason">原因：' +
        esc(c.automation_block_reason || '未返回明细错误') +
        '</div><div class="tcg-diag-suggest">修改建议：请重新编译以获取 Agent 诊断。</div></li>';
    } else {
      items = errors.map(function (e) {
        var reason = e.reason || e.message || '';
        var head = '<code>' + esc(e.code || 'ERROR') + '</code> ';
        if (e.step) head += '步骤 ' + esc(e.step) + ' · ';
        head += esc(reason);
        return '<li>' +
          '<div class="tcg-diag-reason">原因：' + head + '</div>' +
          (e.suggestion ? '<div class="tcg-diag-suggest">修改建议：' + esc(e.suggestion) + '</div>' : '') +
          (e.need ? '<div class="tcg-diag-need">需补充：' + esc(e.need) + '</div>' : '') +
          '</li>';
      }).join('');
    }
    return '<div class="tcg-compile-diag" style="border-color:' + border + ';">' +
      '<div class="tcg-compile-diag-hd">' + esc(title) + '</div>' +
      '<p class="tcg-compile-diag-intro">' + esc(intro) + '</p>' +
      '<ul class="tcg-compile-diag-list">' + items + '</ul></div>';
  }

  function notifyCompileResult(actionLabel, caseData) {
    var status = (caseData && caseData.compile_status) || '';
    var label = compileStatusLabel(status);
    var type = status === 'ok' ? 'success' : (status === 'failed' ? 'danger' : 'warning');
    var msg = actionLabel + '：' + label;
    var errors = (caseData && caseData.compile_errors) || [];
    if (errors.length && (status === 'failed' || status === 'agent_required')) {
      var first = errors[0] || {};
      var reason = first.reason || first.message || '';
      msg += ' — ' + (first.code || 'ERROR') + ': ' + reason;
      if (first.suggestion) msg += '；建议: ' + first.suggestion;
    }
    if (typeof showToast === 'function') {
      showToast(msg, type);
    } else {
      alert(msg);
    }
  }

  // ---- Data API ----
  var DataAPI = {
    loadModules: function () {
      return api.get('/testcase-generations/modules').then(function (res) {
        state.modules = res.data || [];
        return state.modules;
      });
    },
    loadSources: function () {
      return api.get('/testcase-generations/sources').then(function (res) {
        state.sources = res.data || [];
        return state.sources;
      });
    },
    loadTestPoints: function (analysisId) {
      return api.get('/testcase-generations/sources/' + encodeURIComponent(analysisId) + '/test-points')
        .then(function (res) {
          state.testPoints = (res.data && res.data.ui_test_points) || [];
          return state.testPoints;
        });
    },
    loadHistory: function () {
      return api.get('/testcase-generations?size=50').then(function (res) {
        return res.data || [];
      });
    },
    start: function (payload) {
      return api.post('/testcase-generations', payload);
    },
    status: function (id) {
      return api.get('/testcase-generations/' + encodeURIComponent(id) + '/status');
    },
    detail: function (id) {
      return api.get('/testcase-generations/' + encodeURIComponent(id));
    },
    updateCase: function (gid, cid, body) {
      return api.put('/testcase-generations/' + encodeURIComponent(gid) + '/cases/' + encodeURIComponent(cid), body);
    },
    recompileCase: function (gid, cid) {
      return api.post('/testcase-generations/' + encodeURIComponent(gid) + '/cases/' + encodeURIComponent(cid) + '/recompile', {});
    },
    approveCase: function (gid, cid, comment) {
      return api.post('/testcase-generations/' + encodeURIComponent(gid) + '/cases/' + encodeURIComponent(cid) + '/approve', { comment: comment || '' });
    },
    rejectCase: function (gid, cid, comment, reason) {
      return api.post('/testcase-generations/' + encodeURIComponent(gid) + '/cases/' + encodeURIComponent(cid) + '/reject', {
        comment: comment || '',
        reject_reason: reason || ''
      });
    }
  };

  // ---- Render ----
  function renderSourceSelect() {
    var sel = document.getElementById('source-ra');
    if (!sel) return;
    var html = '<option value="">请选择需求分析任务</option>';
    state.sources.forEach(function (s) {
      html += '<option value="' + esc(s.analysis_id) + '">' +
        esc(s.analysis_id) + ' · UI ' + (s.ui_tp_count || 0) + ' · ' + esc(s.filename || '') +
        '</option>';
    });
    sel.innerHTML = html;
    if (state.selectedAnalysisId) {
      sel.value = state.selectedAnalysisId;
    }
  }

  function renderTpTable() {
    var tbody = document.getElementById('tp-tbody');
    var btn = document.getElementById('start-btn');
    if (!tbody) return;
    if (!state.testPoints.length) {
      tbody.innerHTML = '<tr><td colspan="3" class="rv-empty">该任务暂无 UI 测试点</td></tr>';
      if (btn) btn.disabled = true;
      return;
    }
    var html = '';
    state.testPoints.forEach(function (tp) {
      var id = tp.id || '';
      var checked = state.selectedTpIds[id] ? ' checked' : '';
      html += '<tr>' +
        '<td><input type="checkbox" class="rv-checkbox tp-check" data-id="' + esc(id) + '"' + checked + '></td>' +
        '<td><code>' + esc(id) + '</code></td>' +
        '<td title="' + esc(tp.scenario || '') + '">' + esc((tp.scenario || '').substring(0, 60)) + '</td>' +
        '</tr>';
    });
    tbody.innerHTML = html;
    Array.prototype.forEach.call(tbody.querySelectorAll('.tp-check'), function (el) {
      el.addEventListener('change', function () {
        var id = el.getAttribute('data-id');
        if (el.checked) state.selectedTpIds[id] = true;
        else delete state.selectedTpIds[id];
        updateStartEnabled();
      });
    });
    updateStartEnabled();
  }

  function updateStartEnabled() {
    var btn = document.getElementById('start-btn');
    if (!btn) return;
    btn.disabled = Object.keys(state.selectedTpIds).length === 0;
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
      var active = state.currentTask === t.generation_id ? ' active' : '';
      html += '<div class="tcg-history-item' + active + '" data-id="' + esc(t.generation_id) + '">' +
        '<div><strong>' + esc(t.generation_id) + '</strong><div class="rv-text-muted" style="font-size:12px;">' +
        esc(t.analysis_id) + ' · ' + (t.total_cases || 0) + ' 条</div></div>' +
        statusBadge(t.status) +
        '</div>';
    });
    el.innerHTML = html;
    Array.prototype.forEach.call(el.querySelectorAll('.tcg-history-item'), function (item) {
      item.addEventListener('click', function () {
        openTask(item.getAttribute('data-id'));
      });
    });
  }

  function renderResult(data) {
    state.currentData = data;
    var body = document.getElementById('result-body');
    if (!body) return;
    if (!data) {
      body.innerHTML = '<div class="rv-empty"><p>暂无数据</p></div>';
      return;
    }
    if (data.status === 'failed') {
      body.innerHTML = '<div class="rv-empty"><p>生成失败</p><p class="rv-text-muted">' +
        esc(data.error_message || '') + '</p></div>';
      return;
    }

    var stats = data.stats || { pending: 0, approved: 0, rejected: 0, total: 0 };
    var html = '';
    html += '<div class="tcg-stats">' +
      '<div class="tcg-stat"><div class="tcg-stat-num">' + stats.total + '</div><div class="tcg-stat-label">总计</div></div>' +
      '<div class="tcg-stat"><div class="tcg-stat-num" style="color:var(--rv-warning)">' + stats.pending + '</div><div class="tcg-stat-label">待审</div></div>' +
      '<div class="tcg-stat"><div class="tcg-stat-num" style="color:var(--rv-success)">' + stats.approved + '</div><div class="tcg-stat-label">已通过</div></div>' +
      '<div class="tcg-stat"><div class="tcg-stat-num" style="color:var(--rv-danger)">' + stats.rejected + '</div><div class="tcg-stat-label">已驳回</div></div>' +
      '</div>';

    html += '<div style="margin-bottom:12px;font-size:13px;color:var(--rv-text-secondary)">' +
      esc(data.generation_id) + ' · 来源 ' + esc(data.analysis_id) + ' · ' + statusBadge(data.status) +
      ' · ' + esc(data.current_step || '') +
      '</div>';

    html += '<div class="tcg-tabs">' +
      '<button type="button" class="tcg-tab' + (state.tab === 'cases' ? ' active' : '') + '" data-tab="cases">用例 (' + (data.cases || []).length + ')</button>' +
      '<button type="button" class="tcg-tab' + (state.tab === 'logs' ? ' active' : '') + '" data-tab="logs">日志 (' + (data.logs || []).length + ')</button>' +
      '</div>';

    html += '<div id="tcg-tab-content">' + renderTabContent(state.tab, data) + '</div>';
    body.innerHTML = html;

    Array.prototype.forEach.call(body.querySelectorAll('.tcg-tab'), function (btn) {
      btn.addEventListener('click', function () {
        state.tab = btn.getAttribute('data-tab');
        renderResult(state.currentData);
      });
    });
  }

  function renderTabContent(tab, data) {
    if (tab === 'logs') return renderLogs(data.logs || []);
    return renderCases(data.cases || [], data.generation_id);
  }

  function renderCases(cases, generationId) {
    if (!cases.length) return '<p class="rv-text-muted">暂无用例</p>';
    var html = '<div class="rv-table-wrap"><table class="rv-table"><thead><tr>' +
      '<th>标题</th><th>模块</th><th>TP</th><th>编译</th><th>状态</th><th class="rv-th-act">操作</th>' +
      '</tr></thead><tbody>';
    cases.forEach(function (c) {
      var tip = compileErrorSummary(c);
      html += '<tr>' +
        '<td>' + esc(c.title) + '</td>' +
        '<td><span class="rv-type-tag">' + esc(c.module || '未映射') + '</span></td>' +
        '<td><code>' + esc(c.test_point_id || '-') + '</code></td>' +
        '<td title="' + esc(tip) + '">' + compileBadge(c.compile_status) + '</td>' +
        '<td>' + statusBadge(c.status) + '</td>' +
        '<td><button type="button" class="rv-btn rv-btn-ghost rv-btn-xs btn-open-case" data-id="' + esc(c.id) + '">查看</button></td>' +
        '</tr>';
    });
    html += '</tbody></table></div>';
    setTimeout(function () {
      Array.prototype.forEach.call(document.querySelectorAll('.btn-open-case'), function (btn) {
        btn.addEventListener('click', function () {
          openCaseModal(btn.getAttribute('data-id'), generationId);
        });
      });
    }, 0);
    return html;
  }

  function renderLogs(logs) {
    if (!logs.length) return '<p class="rv-text-muted">暂无日志</p>';
    var html = '<div class="tcg-log-list">';
    logs.forEach(function (log) {
      var msg = log.message || '';
      if (!msg) {
        var labels = {
          task_created: '任务创建',
          source_loaded: '加载测试点',
          skill_load: '加载 Skill',
          batch_plan: '分批计划',
          agent_start: '智能体开始',
          agent_done: '智能体完成',
          agent_failed: '智能体失败',
          json_parse: 'JSON 解析',
          coverage_check: '覆盖校验',
          cases_persisted: '用例入库',
          pipeline_done: '进入待审',
          pipeline_error: '流水线错误',
          case_edited: '编辑用例',
          case_approved: '通过用例',
          case_rejected: '驳回用例',
          task_completed: '任务完成',
          self_heal_start: '自愈开始',
          self_heal_complete: '自愈完成',
          self_heal_exhausted: '自愈耗尽',
          exec_heal_deterministic: '可执行性定点加固',
          exec_heal_agent_patch: 'Agent 改写期望结果',
          exec_heal_regen: '整案重生兜底'
        };
        msg = labels[log.step] || log.step || '';
        var extra = Object.keys(log).filter(function (k) {
          return ['seq', 'timestamp', 'step', 'message'].indexOf(k) < 0;
        }).map(function (k) {
          return k + '=' + JSON.stringify(log[k]);
        }).join(', ');
        if (extra) msg = msg + ' — ' + extra.substring(0, 400);
      }
      html += '<div class="tcg-log-entry">' +
        '<span class="tcg-log-time">#' + log.seq + ' ' + esc(fmtDate(log.timestamp)) + '</span>' +
        '<span class="tcg-log-msg">' + esc(String(msg)) + '</span>' +
        '</div>';
    });
    html += '</div>';
    return html;
  }

  function openCaseModal(caseId, generationId) {
    var data = state.currentData;
    if (!data || !data.cases) return;
    var c = null;
    for (var i = 0; i < data.cases.length; i++) {
      if (data.cases[i].id === caseId) { c = data.cases[i]; break; }
    }
    if (!c) return;
    state.editingCaseId = caseId;
    document.getElementById('case-modal-title').textContent = c.title || caseId;
    var stepsText = (c.steps || []).map(function (s) {
      return (s.step || '') + '. ' + (s.action || '') + ' => ' + (s.expected || '');
    }).join('\n');
    var moduleOptions = '<option value="">请选择模块</option>';
    state.modules.forEach(function (m) {
      moduleOptions += '<option value="' + esc(m.name) + '"' +
        (m.name === c.module ? ' selected' : '') + '>' + esc(m.name) + '</option>';
    });
    var errorHtml = renderCompileDiagnostics(c);
    var body = document.getElementById('case-modal-body');
    body.innerHTML =
      errorHtml +
      '<label class="opt-label"><span>标题</span><input class="rv-form-input" id="edit-title" value="' + esc(c.title || '') + '"></label>' +
      '<label class="opt-label" style="display:block;margin-top:10px;"><span>所属模块</span><select class="rv-select" id="edit-module">' + moduleOptions + '</select></label>' +
      '<label class="opt-label" style="display:block;margin-top:10px;"><span>描述</span><textarea class="rv-textarea" id="edit-desc" rows="2">' + esc(c.description || '') + '</textarea></label>' +
      '<label class="opt-label" style="display:block;margin-top:10px;"><span>前置条件</span><textarea class="rv-textarea" id="edit-pre" rows="2">' + esc(c.preconditions || '') + '</textarea></label>' +
      '<label class="opt-label" style="display:block;margin-top:10px;"><span>步骤（每行：序号. 操作 => 预期）</span><textarea class="rv-textarea" id="edit-steps" rows="8">' + esc(stepsText) + '</textarea></label>' +
      '<label class="opt-label" style="display:block;margin-top:10px;"><span>驳回意见（驳回时填写）</span><textarea class="rv-textarea" id="edit-comment" rows="2"></textarea></label>' +
      '<p class="rv-text-muted" style="margin-top:8px;font-size:12px;">TP: ' + esc(c.test_point_id || '-') +
      ' · 状态: ' + esc(c.status) + ' · 编译: ' + compileBadge(c.compile_status) +
      ' · 执行模式: ' + esc(c.execution_mode || '-') + ' · 任务: ' + esc(generationId) + '</p>';

    document.getElementById('case-modal').style.display = 'flex';
    document.getElementById('btn-save-case').onclick = function () { saveCase(generationId, caseId); };
    document.getElementById('btn-recompile-case').onclick = function () { recompileCase(generationId, caseId); };
    document.getElementById('btn-approve-case').onclick = function () { approveCase(generationId, caseId); };
    document.getElementById('btn-reject-case').onclick = function () { rejectCase(generationId, caseId); };
  }

  function parseStepsText(text) {
    var lines = (text || '').split('\n').map(function (l) { return l.trim(); }).filter(Boolean);
    var steps = [];
    lines.forEach(function (line, idx) {
      var m = line.match(/^(\d+)\.\s*(.+?)\s*=>\s*(.+)$/);
      if (m) {
        steps.push({ step: parseInt(m[1], 10), action: m[2].trim(), expected: m[3].trim() });
      } else {
        var parts = line.split('=>');
        if (parts.length >= 2) {
          steps.push({ step: idx + 1, action: parts[0].replace(/^\d+\.\s*/, '').trim(), expected: parts.slice(1).join('=>').trim() });
        }
      }
    });
    return steps;
  }

  function saveCase(gid, cid) {
    var steps = parseStepsText(document.getElementById('edit-steps').value);
    DataAPI.updateCase(gid, cid, {
      title: document.getElementById('edit-title').value,
      description: document.getElementById('edit-desc').value,
      preconditions: document.getElementById('edit-pre').value,
      steps: steps,
      module: document.getElementById('edit-module').value
    }).then(function (res) {
      if (!res.success) throw new Error(res.error || '保存失败');
      notifyCompileResult('已保存并编译', res.data || {});
      return openTask(gid);
    }).then(function () {
      openCaseModal(cid, gid);
    }).catch(function (err) {
      alert(err.message || String(err));
    });
  }

  function recompileCase(gid, cid) {
    DataAPI.recompileCase(gid, cid).then(function (res) {
      if (!res.success) throw new Error(res.error || '重新编译失败');
      notifyCompileResult('已重新编译', res.data || {});
      return openTask(gid);
    }).then(function () {
      openCaseModal(cid, gid);
    }).catch(function (err) {
      alert(err.message || String(err));
    });
  }

  function approveCase(gid, cid) {
    var comment = document.getElementById('edit-comment').value;
    DataAPI.approveCase(gid, cid, comment).then(function () {
      return openTask(gid);
    }).then(function () {
      closeCaseModal();
    }).catch(function (err) {
      alert(err.message || String(err));
    });
  }

  function rejectCase(gid, cid) {
    var comment = document.getElementById('edit-comment').value;
    if (!comment) {
      alert('请填写驳回意见');
      return;
    }
    DataAPI.rejectCase(gid, cid, comment, 'other').then(function () {
      return openTask(gid);
    }).then(function () {
      closeCaseModal();
    }).catch(function (err) {
      alert(err.message || String(err));
    });
  }

  function closeCaseModal() {
    document.getElementById('case-modal').style.display = 'none';
    state.editingCaseId = null;
  }

  function showProgress(show, step, pct) {
    var panel = document.getElementById('progress-panel');
    if (!panel) return;
    panel.style.display = show ? 'block' : 'none';
    if (step != null) document.getElementById('progress-step').textContent = step;
    if (pct != null) {
      document.getElementById('progress-pct').textContent = pct + '%';
      document.getElementById('progress-fill').style.width = pct + '%';
    }
  }

  function stopPoll() {
    if (state.pollTimer) {
      clearInterval(state.pollTimer);
      state.pollTimer = null;
    }
  }

  function startPoll(generationId) {
    stopPoll();
    showProgress(true, '生成中...', 5);
    state.pollTimer = setInterval(function () {
      DataAPI.status(generationId).then(function (res) {
        var d = res.data || {};
        showProgress(true, d.current_step || '处理中', d.progress_pct || 0);
        if (['pending_review', 'completed', 'failed'].indexOf(d.status) >= 0) {
          stopPoll();
          if (d.status === 'failed') showProgress(false);
          openTask(generationId);
          refreshHistory();
        }
      }).catch(function () {});
    }, 2500);
  }

  function openTask(generationId) {
    state.currentTask = generationId;
    return DataAPI.detail(generationId).then(function (res) {
      renderResult(res.data);
      refreshHistory();
      if (res.data && (res.data.status === 'processing' || res.data.status === 'queued')) {
        startPoll(generationId);
      } else if (res.data && res.data.status !== 'failed') {
        showProgress(false);
      }
    }).catch(function (err) {
      alert(err.message || String(err));
    });
  }

  function refreshHistory() {
    return DataAPI.loadHistory().then(renderHistory);
  }

  function onSourceChange() {
    var sel = document.getElementById('source-ra');
    state.selectedAnalysisId = sel.value || '';
    state.selectedTpIds = {};
    if (!state.selectedAnalysisId) {
      state.testPoints = [];
      renderTpTable();
      return;
    }
    DataAPI.loadTestPoints(state.selectedAnalysisId).then(function () {
      // 默认全选
      state.testPoints.forEach(function (tp) {
        if (tp.id) state.selectedTpIds[tp.id] = true;
      });
      renderTpTable();
    }).catch(function (err) {
      alert(err.message || String(err));
    });
  }

  function startGeneration() {
    var ids = Object.keys(state.selectedTpIds);
    if (!state.selectedAnalysisId || !ids.length) return;
    var payload = {
      analysis_id: state.selectedAnalysisId,
      test_point_ids: ids,
      platform_type: document.getElementById('platform-type').value || '',
      custom_prompt: document.getElementById('custom-prompt').value || ''
    };
    document.getElementById('start-btn').disabled = true;
    DataAPI.start(payload).then(function (res) {
      var gid = res.data && res.data.generation_id;
      state.currentTask = gid;
      startPoll(gid);
      refreshHistory();
    }).catch(function (err) {
      alert(err.message || String(err));
      document.getElementById('start-btn').disabled = false;
    });
  }

  function initFromQuery() {
    var params = new URLSearchParams(window.location.search);
    var aid = params.get('analysis_id');
    if (aid) {
      state.selectedAnalysisId = aid;
    }
  }

  function bindEvents() {
    document.getElementById('source-ra').addEventListener('change', onSourceChange);
    document.getElementById('start-btn').addEventListener('click', startGeneration);
    document.getElementById('btn-select-all').addEventListener('click', function () {
      state.testPoints.forEach(function (tp) {
        if (tp.id) state.selectedTpIds[tp.id] = true;
      });
      renderTpTable();
    });
    document.getElementById('btn-select-none').addEventListener('click', function () {
      state.selectedTpIds = {};
      renderTpTable();
    });
  }

  window.TCG = {
    closeCaseModal: closeCaseModal,
    openTask: openTask
  };

  document.addEventListener('DOMContentLoaded', function () {
    initFromQuery();
    bindEvents();
    Promise.all([DataAPI.loadSources(), DataAPI.loadModules()]).then(function () {
      renderSourceSelect();
      if (state.selectedAnalysisId) {
        document.getElementById('source-ra').value = state.selectedAnalysisId;
        onSourceChange();
      }
    }).catch(function (err) {
      console.error(err);
    });
    refreshHistory();
  });
})();
