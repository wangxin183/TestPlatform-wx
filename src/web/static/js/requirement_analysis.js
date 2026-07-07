/**
 * 需求分析页面 — 独立的需求分析功能（与项目解耦）。
 *
 * 功能：
 *  - 拖拽/点击上传需求文档
 *  - 配置分析参数 → 启动分析 → 轮询进度
 *  - 可视化展示结构化分析 JSON（FR/NFR/测试点/风险）
 *  - 展示审查评分和意见
 *  - 人工审核表单（通过/驳回）
 *  - 历史记录列表
 */

(function () {
  'use strict';

  // ============================================================
  // 状态管理
  // ============================================================
  const state = {
    currentFile: null,          // 当前选中的文件 File 对象
    pollTimer: null,            // 轮询定时器 ID
    currentAnalysisId: null,    // 当前查看的分析 ID
    currentData: null,          // 当前分析的完整数据
    tab: 'fr',                  // 当前激活的 Tab
  };

  // ============================================================
  // 初始化
  // ============================================================
  function init() {
    bindUploadZone();
    loadHistory();
  }

  // ============================================================
  // 文件上传（拖拽 + 点击）
  // ============================================================

  function bindUploadZone() {
    const zone = document.getElementById('upload-zone');
    const fileInput = document.getElementById('file-input');

    fileInput.addEventListener('change', function () {
      if (this.files.length > 0) {
        handleFile(this.files[0]);
      }
    });

    zone.addEventListener('dragover', function (e) {
      e.preventDefault();
      zone.classList.add('dragover');
    });

    zone.addEventListener('dragleave', function () {
      zone.classList.remove('dragover');
    });

    zone.addEventListener('drop', function (e) {
      e.preventDefault();
      zone.classList.remove('dragover');
      if (e.dataTransfer.files.length > 0) {
        handleFile(e.dataTransfer.files[0]);
      }
    });
  }

  function handleFile(file) {
    const maxSize = 10 * 1024 * 1024; // 10MB
    if (file.size > maxSize) {
      showToast('文件过大（' + (file.size / 1024 / 1024).toFixed(1) + 'MB），最大支持 10MB', 'danger');
      return;
    }

    state.currentFile = file;

    document.getElementById('file-name').textContent = file.name;
    document.getElementById('file-size').textContent = formatFileSize(file.size);
    document.getElementById('file-info').style.display = 'flex';
    document.getElementById('upload-zone').style.display = 'none';
    document.getElementById('start-btn').disabled = false;
  }

  window.clearFile = function () {
    state.currentFile = null;
    document.getElementById('file-input').value = '';
    document.getElementById('file-info').style.display = 'none';
    document.getElementById('upload-zone').style.display = 'flex';
    document.getElementById('start-btn').disabled = true;
  };

  // ============================================================
  // 启动分析
  // ============================================================

  window.startAnalysis = async function () {
    if (!state.currentFile) {
      showToast('请先选择文件', 'warning');
      return;
    }

    const startBtn = document.getElementById('start-btn');
    startBtn.disabled = true;
    startBtn.textContent = '⏳ 提交中...';

    const form = new FormData();
    form.append('file', state.currentFile);
    form.append('platform_type', document.getElementById('platform-type').value);
    form.append('custom_prompt', document.getElementById('custom-prompt').value);
    form.append('obsidian_modules', document.getElementById('obsidian-modules').value);

    try {
      const resp = await api.upload('/requirement-analyses', form);
      const data = resp.data;

      if (!data || !data.analysis_id) {
        showToast('创建分析任务失败', 'danger');
        startBtn.disabled = false;
        startBtn.textContent = '🚀 开始分析';
        return;
      }

      state.currentAnalysisId = data.analysis_id;

      // 显示进度条
      document.getElementById('progress-panel').style.display = 'block';
      updateProgress(data.current_step || '准备中...', data.progress_pct || 0);

      // 开始轮询
      startPolling(data.analysis_id);

      // 添加 Loading 占位
      showLoading();

      showToast('分析任务已启动: ' + data.analysis_id, 'success');

    } catch (e) {
      console.error(e);
      showToast('启动失败: ' + (e.message || '未知错误'), 'danger');
      startBtn.disabled = false;
      startBtn.textContent = '🚀 开始分析';
    }
  };

  // ============================================================
  // 轮询状态
  // ============================================================

  function startPolling(analysisId) {
    if (state.pollTimer) {
      clearInterval(state.pollTimer);
    }

    state.pollTimer = setInterval(async () => {
      try {
        const resp = await api.get('/requirement-analyses/' + analysisId + '/status');
        const status = resp.data;

        updateProgress(status.current_step, status.progress_pct);

        // 检查终态
        const terminalStatuses = ['pending_review', 'approved', 'rejected', 'failed'];
        if (terminalStatuses.includes(status.status)) {
          stopPolling();
          // 获取完整数据
          await loadAnalysisDetail(analysisId);
        }
      } catch (e) {
        console.error('轮询错误:', e);
      }
    }, 2000);
  }

  function stopPolling() {
    if (state.pollTimer) {
      clearInterval(state.pollTimer);
      state.pollTimer = null;
    }
    document.getElementById('progress-panel').style.display = 'none';
  }

  function updateProgress(step, pct) {
    document.getElementById('progress-step').textContent = step;
    document.getElementById('progress-pct').textContent = pct + '%';
    document.getElementById('progress-fill').style.width = pct + '%';
  }

  function showLoading() {
    const content = document.getElementById('result-content');
    content.innerHTML = `
      <div class="loading-state">
        <div class="spinner"></div>
        <p>正在分析需求文档，请稍候...</p>
        <p class="rv-text-muted">此过程通常需要 2-5 分钟</p>
      </div>`;
  }

  // ============================================================
  // 加载分析详情
  // ============================================================

  async function loadAnalysisDetail(analysisId) {
    try {
      const resp = await api.get('/requirement-analyses/' + analysisId);
      state.currentData = resp.data;
      state.currentAnalysisId = analysisId;
      renderResult(resp.data);
      updateResultStatus(resp.data.status);
      loadHistory(); // 刷新历史列表
    } catch (e) {
      console.error('加载详情失败:', e);
      showToast('加载分析结果失败', 'danger');
    }
  }

  // ============================================================
  // 渲染分析结果
  // ============================================================

  function renderResult(data) {
    const content = document.getElementById('result-content');

    if (data.status === 'failed') {
      content.innerHTML = renderErrorState(data);
      return;
    }

    if (!data.analysis_json) {
      content.innerHTML = '<p class="rv-text-muted">分析结果尚未生成</p>';
      return;
    }

    const ajson = data.analysis_json;
    const rjson = data.review_json;

    let html = '';

    // ---- 审查评分摘要 ----
    if (rjson && rjson.score) {
      html += renderReviewSummary(rjson);
    }

    // ---- Tab 切换 ----
    html += renderTabs(data);

    // ---- Tab 内容 ----
    html += '<div class="tab-content" id="tab-content">';
    html += renderTabContent(state.tab, data);
    html += '</div>';

    // ---- 人工审核区域 ----
    if (data.status === 'pending_review') {
      html += renderHumanReviewForm(data);
    } else if (data.human_review) {
      html += renderHumanReviewResult(data.human_review);
    }

    content.innerHTML = html;

    // 绑定 Tab 切换事件
    bindTabEvents(data);
  }

  function renderReviewSummary(rjson) {
    const score = rjson.score || 0;
    const level = score >= 90 ? '优秀' : score >= 80 ? '良好' : score >= 70 ? '一般' : '需改进';
    const lc = score >= 80 ? 'score-good' : score >= 70 ? 'score-warn' : 'score-bad';

    const dims = rjson.dimensions || {};

    let dimsHtml = '';
    for (const [key, val] of Object.entries(dims)) {
      const label = {
        'completeness': '完整性',
        'clarity': '清晰度',
        'testability': '可测性',
        'risk_coverage': '风险覆盖',
        'boundary_coverage': '边界覆盖',
        'exception_coverage': '异常覆盖'
      }[key] || key;
      const ds = val.score || 0;
      const dc = ds >= 80 ? '' : ds >= 70 ? 'dim-warn' : 'dim-bad';
      dimsHtml += `<div class="dim-item ${dc}"><span class="dim-label">${label}</span><span class="dim-score">${ds}</span></div>`;
    }

    return `
      <div class="review-summary">
        <div class="review-score ${lc}">
          <span class="score-num">${score}</span>
          <span class="score-label">/100 ${level}</span>
        </div>
        <div class="review-dims">${dimsHtml}</div>
      </div>`;
  }

  function renderTabs(data) {
    const ajson = data.analysis_json || {};
    const frCount = (ajson.functional_requirements || []).length;
    const nfrCount = (ajson.non_functional_requirements || []).length;
    const tpCount = (ajson.test_points || []).length;
    const riskCount = (ajson.risks || []).length;

    const tabs = [
      { id: 'fr', label: '功能需求', count: frCount, cls: state.tab === 'fr' ? 'active' : '' },
      { id: 'nfr', label: '非功能需求', count: nfrCount, cls: state.tab === 'nfr' ? 'active' : '' },
      { id: 'tp', label: '测试点', count: tpCount, cls: state.tab === 'tp' ? 'active' : '' },
      { id: 'risk', label: '风险', count: riskCount, cls: state.tab === 'risk' ? 'active' : '' },
    ];

    if (data.review_json) {
      tabs.push({ id: 'review', label: '审查意见', count: 0, cls: state.tab === 'review' ? 'active' : '' });
    }

    if (data.logs && data.logs.length > 0) {
      tabs.push({ id: 'logs', label: '执行日志', count: data.logs.length, cls: state.tab === 'logs' ? 'active' : '' });
    }

    let html = '<div class="result-tabs">';
    for (const t of tabs) {
      html += `<button class="result-tab ${t.cls}" data-tab="${t.id}" onclick="switchTab('${t.id}')">${t.label} (${t.count})</button>`;
    }
    html += '</div>';
    return html;
  }

  function renderTabContent(tab, data) {
    const ajson = data.analysis_json || {};

    switch (tab) {
      case 'fr':
        return renderFRList(ajson.functional_requirements || []);
      case 'nfr':
        return renderNFRList(ajson.non_functional_requirements || []);
      case 'tp':
        return renderTPList(ajson.test_points || []);
      case 'risk':
        return renderRiskList(ajson.risks || []);
      case 'review':
        return renderReviewDetail(data.review_json);
      case 'logs':
        return renderLogs(data.logs || []);
      default:
        return '';
    }
  }

  // ---- FR 列表 ----
  function renderFRList(items) {
    if (!items.length) return '<p class="rv-text-muted">暂无功能需求</p>';

    let html = '';
    for (const fr of items) {
      const priorityCls = 'priority-' + (fr.priority || 'P2').toLowerCase().replace('p', 'p');
      const ambiguities = fr.ambiguities && fr.ambiguities.length
        ? `<div class="fr-ambiguities">⚠️ 歧义标记：${escHtmlArr(fr.ambiguities)}</div>`
        : '';
      const criteria = fr.acceptance_criteria && fr.acceptance_criteria.length
        ? `<ul class="fr-criteria">${fr.acceptance_criteria.map(c => '<li>' + escHtml(c) + '</li>').join('')}</ul>`
        : '';
      const deps = fr.dependent_fr && fr.dependent_fr.length
        ? `<span class="fr-deps">依赖: ${fr.dependent_fr.join(', ')}</span>`
        : '';

      html += `
        <details class="fr-item" open>
          <summary>
            <span class="fr-id">${escHtml(fr.id)}</span>
            <span class="priority-badge ${priorityCls}">${escHtml(fr.priority)}</span>
            <span class="fr-module">${escHtml(fr.module || '')}</span>
            <span class="fr-desc">${escHtml((fr.description || '').substring(0, 80))}</span>
            ${deps}
          </summary>
          <div class="fr-body">
            <p class="fr-full-desc">${escHtml(fr.description || '')}</p>
            ${criteria}
            ${ambiguities}
          </div>
        </details>`;
    }
    return html;
  }

  // ---- NFR 列表 ----
  function renderNFRList(items) {
    if (!items.length) return '<p class="rv-text-muted">暂无非功能需求</p>';

    let html = '<table class="rv-table"><thead><tr><th>ID</th><th>类别</th><th>描述</th><th>优先级</th><th>可量化标准</th></tr></thead><tbody>';
    for (const nfr of items) {
      const priorityCls = 'priority-' + (nfr.priority || 'P2').toLowerCase().replace('p', 'p');
      html += `
        <tr>
          <td><code>${escHtml(nfr.id)}</code></td>
          <td><span class="badge badge-info">${escHtml(nfr.category || '')}</span></td>
          <td>${escHtml(nfr.description || '')}</td>
          <td><span class="priority-badge ${priorityCls}">${escHtml(nfr.priority)}</span></td>
          <td class="rv-text-muted">${escHtml(nfr.measurable_criteria || '—')}</td>
        </tr>`;
    }
    html += '</tbody></table>';
    return html;
  }

  // ---- 测试点列表 ----
  function renderTPList(items) {
    if (!items.length) return '<p class="rv-text-muted">暂无测试点</p>';

    let html = '';
    for (const tp of items) {
      const testTypeCls = 'badge-' + (tp.test_type || 'ui');
      html += `
        <details class="fr-item">
          <summary>
            <span class="fr-id">${escHtml(tp.id)}</span>
            <span class="badge ${testTypeCls}">${escHtml(tp.test_type)}</span>
            <span class="fr-desc">${escHtml((tp.scenario || '').substring(0, 80))}</span>
            <span class="fr-deps">→ ${escHtml(tp.related_fr || '')}</span>
          </summary>
          <div class="fr-body">
            <p><strong>场景：</strong>${escHtml(tp.scenario || '')}</p>
            ${tp.positive_scenarios && tp.positive_scenarios.length ? '<p><strong>正常流程：</strong></p><ul>' + tp.positive_scenarios.map(s => '<li>' + escHtml(s) + '</li>').join('') + '</ul>' : ''}
            ${tp.boundary_conditions && tp.boundary_conditions.length ? '<p><strong>⚠️ 边界条件：</strong></p><ul>' + tp.boundary_conditions.map(s => '<li>' + escHtml(s) + '</li>').join('') + '</ul>' : ''}
            ${tp.negative_scenarios && tp.negative_scenarios.length ? '<p><strong>❌ 异常场景：</strong></p><ul>' + tp.negative_scenarios.map(s => '<li>' + escHtml(s) + '</li>').join('') + '</ul>' : ''}
            ${tp.permission_scenarios && tp.permission_scenarios.length ? '<p><strong>🔒 权限场景：</strong></p><ul>' + tp.permission_scenarios.map(s => '<li>' + escHtml(s) + '</li>').join('') + '</ul>' : ''}
          </div>
        </details>`;
    }
    return html;
  }

  // ---- 风险列表 ----
  function renderRiskList(items) {
    if (!items.length) return '<p class="rv-text-muted">暂无风险识别</p>';

    let html = '<div class="risk-grid">';
    for (const risk of items) {
      const sevCls = 'severity-' + (risk.severity || 'medium');
      html += `
        <div class="risk-card ${sevCls}">
          <div class="risk-header">
            <span class="fr-id">${escHtml(risk.id)}</span>
            <span class="badge badge-${risk.severity === 'high' ? 'danger' : risk.severity === 'medium' ? 'warning' : 'info'}">${escHtml(risk.severity)}</span>
          </div>
          <p>${escHtml(risk.description || '')}</p>
          <div class="risk-meta">
            <span>概率：${escHtml(risk.probability || '—')}</span>
            <span>影响：${escHtml(risk.impact || '—')}</span>
            <span>关联：${escHtml((risk.related_fr || []).join(', '))}</span>
          </div>
          <p class="risk-mitigation"><strong>缓解措施：</strong>${escHtml(risk.mitigation || '—')}</p>
        </div>`;
    }
    html += '</div>';
    return html;
  }

  // ---- 审查详情 ----
  function renderReviewDetail(rjson) {
    if (!rjson) return '<p class="rv-text-muted">暂无审查数据</p>';

    let html = '';

    // 遗漏项
    const missing = rjson.missing_items || [];
    if (missing.length) {
      html += '<h4>🔴 遗漏项 (' + missing.length + ')</h4>';
      for (const m of missing) {
        html += `<div class="review-item review-missing">
          <span class="badge badge-${m.severity === 'high' ? 'danger' : 'warning'}">${escHtml(m.severity)}</span>
          <strong>${escHtml(m.type)}</strong> — ${escHtml(m.description)}
          <span class="rv-text-muted">(${escHtml(m.location || '')})</span>
        </div>`;
      }
    }

    // 改进建议
    const suggestions = rjson.improvement_suggestions || [];
    if (suggestions.length) {
      html += '<h4>💡 改进建议 (' + suggestions.length + ')</h4>';
      for (const sug of suggestions) {
        const target = typeof sug === 'object' ? sug.target || '' : '';
        const issue = typeof sug === 'object' ? sug.issue || '' : '';
        const suggestion = typeof sug === 'object' ? sug.suggestion || '' : sug;
        html += `<div class="review-item review-suggestion">
          ${target ? '<code>' + escHtml(target) + '</code>' : ''}
          ${issue ? '<span class="rv-text-muted">' + escHtml(issue) + '</span>' : ''}
          <p><strong>建议：</strong>${escHtml(suggestion)}</p>
        </div>`;
      }
    }

    // 幻觉标记
    const hallucinations = rjson.hallucinations || [];
    if (hallucinations.length) {
      html += '<h4>⚠️ 疑似幻觉 (' + hallucinations.length + ')</h4>';
      for (const h of hallucinations) {
        html += `<div class="review-item review-hallucination">
          <code>${escHtml(h.item || '')}</code> — ${escHtml(h.reason || '')}
        </div>`;
      }
    }

    // 总体评语
    if (rjson.overall_comment) {
      html += '<h4>📝 总体评语</h4><p class="rv-text-muted">' + escHtml(rjson.overall_comment) + '</p>';
    }

    return html || '<p class="rv-text-muted">审查完成，无特别标记</p>';
  }

  // ---- 执行日志 ----
  function renderLogs(logs) {
    if (!logs.length) return '<p class="rv-text-muted">暂无日志</p>';

    let html = '<div class="log-list">';
    for (const log of logs) {
      const stepLabel = {
        'task_created': '📋 任务创建',
        'ingest_start': '📥 开始摄取文档',
        'ingest_done': '✅ 文档摄取完成',
        'knowledge_load': '📚 加载知识库',
        'skill_load': '🔧 加载 Skill',
        'claude_start': '🤖 Claude Code 开始分析',
        'claude_done': '✅ Claude Code 分析完成',
        'json_parse': '📊 JSON 解析',
        'codex_start': '🔍 Codex 开始审查',
        'codex_done': '✅ Codex 审查完成',
        'feishu_sent': '📨 飞书通知已发送',
        'human_review_submitted': '👤 人工审核已提交',
      }[log.step] || log.step;

      const extra = Object.entries(log)
        .filter(([k]) => !['seq', 'timestamp', 'step'].includes(k))
        .map(([k, v]) => k + '=' + JSON.stringify(v))
        .join(', ');

      html += `<div class="log-entry">
        <span class="log-time">${fmtDate(log.timestamp)}</span>
        <span class="log-step">${stepLabel}</span>
        ${extra ? '<span class="log-extra rv-text-muted">' + escHtml(extra) + '</span>' : ''}
      </div>`;
    }
    html += '</div>';
    return html;
  }

  // ---- 错误状态 ----
  function renderErrorState(data) {
    return `
      <div class="error-state">
        <div class="error-state-icon">❌</div>
        <h4>分析失败</h4>
        <p class="rv-text-muted">${escHtml(data.error_message || '未知错误')}</p>
        <button class="rv-btn rv-btn-primary" onclick="retryAnalysis('${data.analysis_id}')">🔄 重试</button>
        <button class="rv-btn" onclick="retryWithFeedback('${data.analysis_id}')">📝 补充意见后重试</button>
      </div>`;
  }

  // ---- 人工审核表单 ----
  function renderHumanReviewForm(data) {
    return `
      <div class="human-review-form">
        <h4>👤 人工审核</h4>
        <textarea id="review-comment" class="rv-textarea" rows="3" placeholder="请输入审查意见..."></textarea>
        <div class="review-actions">
          <button class="rv-btn rv-btn-success" onclick="submitReview('${data.analysis_id}', 'approved')">✅ 通过</button>
          <button class="rv-btn rv-btn-danger" onclick="submitReview('${data.analysis_id}', 'rejected')">❌ 驳回</button>
        </div>
      </div>`;
  }

  function renderHumanReviewResult(hr) {
    const decisionLabel = hr.decision === 'approved' ? '✅ 审核通过' : '❌ 已驳回';
    var retryBtn = '';
    if (hr.decision === 'rejected') {
      retryBtn = `
        <div class="review-actions" style="margin-top:12px">
          <button class="rv-btn rv-btn-primary" onclick="retryAnalysis('${state.currentAnalysisId}')">
            🔄 按审核意见重新分析
          </button>
        </div>`;
    }
    return `
      <div class="human-review-result">
        <h4>👤 人工审核结果</h4>
        <p><strong>决定：</strong>${decisionLabel}</p>
        ${hr.comment ? '<p><strong>意见：</strong>' + escHtml(hr.comment) + '</p>' : ''}
        <p class="rv-text-muted">审核时间：${fmtDate(hr.reviewed_at)}</p>
        ${retryBtn}
      </div>`;
  }

  // ============================================================
  // Tab 切换
  // ============================================================

  function bindTabEvents(data) {
    // Tab 事件通过 onclick 绑定在 HTML 中
  }

  window.switchTab = function (tabId) {
    state.tab = tabId;

    // 更新 Tab 按钮状态
    document.querySelectorAll('.result-tab').forEach(btn => {
      btn.classList.toggle('active', btn.dataset.tab === tabId);
    });

    // 更新内容
    const content = document.getElementById('tab-content');
    if (content && state.currentData) {
      content.innerHTML = renderTabContent(tabId, state.currentData);
    }
  };

  // ============================================================
  // 人工审核
  // ============================================================

  window.submitReview = async function (analysisId, decision) {
    const comment = document.getElementById('review-comment').value || '';

    if (decision === 'rejected' && !comment) {
      showToast('驳回时必须填写审查意见', 'warning');
      return;
    }

    try {
      await api.post('/requirement-analyses/' + analysisId + '/review', {
        decision: decision,
        comment: comment,
        corrections: [],
      });

      showToast(decision === 'approved' ? '审核通过 ✅' : '已驳回，审核意见已注入 🔄', 'success');
      await loadAnalysisDetail(analysisId);
      loadHistory();
    } catch (e) {
      console.error(e);
      showToast('提交审核失败: ' + (e.message || '未知错误'), 'danger');
    }
  };

  // ============================================================
  // 重试
  // ============================================================

  window.retryAnalysis = async function (analysisId) {
    // 读取人工审查意见作为 feedback
    var feedback = '';
    if (state.currentData && state.currentData.human_review) {
      feedback = state.currentData.human_review.comment || '';
    }
    try {
      await api.post('/requirement-analyses/' + analysisId + '/retry', { feedback: feedback });
      showToast('已触发重新分析' + (feedback ? '（含审核意见）' : ''), 'success');
      showLoading();
      startPolling(analysisId);
    } catch (e) {
      showToast('重试失败: ' + (e.message || '未知错误'), 'danger');
    }
  };

  window.retryWithFeedback = async function (analysisId) {
    // 预填已有的审核意见
    var existingFeedback = '';
    if (state.currentData && state.currentData.human_review) {
      existingFeedback = state.currentData.human_review.comment || '';
    }
    var additional = prompt('请输入补充意见（已有审核意见已自动注入）：', existingFeedback);
    if (additional === null) return;  // 用户点了取消
    var feedback = additional || existingFeedback;
    if (!feedback) {
      showToast('未提供任何意见，取消重试', 'warning');
      return;
    }

    try {
      await api.post('/requirement-analyses/' + analysisId + '/retry', { feedback: feedback });
      showToast('已触发带补充意见的重新分析', 'success');
      showLoading();
      startPolling(analysisId);
    } catch (e) {
      showToast('重试失败: ' + (e.message || '未知错误'), 'danger');
    }
  };

  // ============================================================
  // 历史记录列表
  // ============================================================

  async function loadHistory() {
    const list = document.getElementById('history-list');
    try {
      const resp = await api.get('/requirement-analyses?size=50');
      const items = resp.data || [];

      if (!items.length) {
        list.innerHTML = '<p class="rv-text-muted">暂无分析记录</p>';
        return;
      }

      const statusIcons = {
        'uploading': '⏳',
        'processing': '🔄',
        'reviewing': '🔍',
        'pending_review': '⏳',
        'approved': '✅',
        'rejected': '❌',
        'failed': '⚠️',
      };

      let html = '';
      for (const item of items) {
        const icon = statusIcons[item.status] || '📋';
        const activeCls = item.analysis_id === state.currentAnalysisId ? 'history-item-active' : '';
        html += `
          <div class="history-item ${activeCls}" onclick="viewAnalysis('${item.analysis_id}')">
            <span class="history-icon">${icon}</span>
            <div class="history-info">
              <div class="history-filename">${escHtml(item.filename)}</div>
              <div class="history-meta rv-text-muted">${item.analysis_id} · ${fmtDate(item.created_at)}</div>
            </div>
          </div>`;
      }
      list.innerHTML = html;
    } catch (e) {
      console.error(e);
      list.innerHTML = '<p class="rv-text-muted">加载失败</p>';
    }
  }

  window.viewAnalysis = async function (analysisId) {
    state.currentAnalysisId = analysisId;
    showLoading();
    await loadAnalysisDetail(analysisId);
    document.getElementById('upload-panel').style.display = 'block';
  };

  // ============================================================
  // 状态标签
  // ============================================================

  function updateResultStatus(status) {
    const badge = document.getElementById('result-status');
    const labels = {
      'uploading': '上传中',
      'processing': '分析中',
      'reviewing': '审查中',
      'pending_review': '待人工审核',
      'approved': '已通过',
      'rejected': '已驳回',
      'failed': '失败',
    };
    badge.textContent = labels[status] || status;
    badge.className = 'status-badge badge-' + (
      status === 'approved' ? 'success' :
      status === 'rejected' ? 'danger' :
      status === 'failed' ? 'danger' :
      'info'
    );
  }

  // ============================================================
  // 工具函数
  // ============================================================

  function escHtml(str) {
    if (!str) return '';
    return String(str)
      .replace(/&/g, '&amp;')
      .replace(/</g, '&lt;')
      .replace(/>/g, '&gt;')
      .replace(/"/g, '&quot;');
  }

  function escHtmlArr(arr) {
    return arr.map(escHtml).join('<br>');
  }

  function formatFileSize(bytes) {
    if (bytes < 1024) return bytes + ' B';
    if (bytes < 1024 * 1024) return (bytes / 1024).toFixed(1) + ' KB';
    return (bytes / 1024 / 1024).toFixed(1) + ' MB';
  }

  function fmtDate(iso) {
    if (!iso) return '';
    try {
      const d = new Date(iso.endsWith('Z') ? iso : iso + 'Z');
      return d.toLocaleString('zh-CN', { timeZone: 'Asia/Shanghai' });
    } catch (e) {
      return iso;
    }
  }

  // ============================================================
  // CSS 追加
  // ============================================================

  function injectStyles() {
    const style = document.createElement('style');
    style.textContent = `
      .analysis-layout { display: flex; gap: 20px; }
      .analysis-left { width: 380px; flex-shrink: 0; display: flex; flex-direction: column; gap: 16px; }
      .analysis-right { flex: 1; min-width: 0; }

      .upload-zone {
        border: 2px dashed var(--color-border); border-radius: var(--radius-lg);
        padding: 32px 16px; text-align: center; transition: all 0.2s;
      }
      .upload-zone.dragover { border-color: var(--color-primary); background: var(--color-primary-light); }
      .upload-zone-icon { font-size: 32px; margin-bottom: 8px; }
      .upload-zone-text { color: var(--color-text); margin-bottom: 4px; }
      .upload-zone-hint { color: var(--color-text-muted); font-size: 13px; margin-bottom: 12px; }

      .upload-file-info {
        display: flex; align-items: center; gap: 12px; padding: 12px;
        background: var(--color-primary-light); border-radius: var(--radius);
        margin-bottom: 12px;
      }
      .file-name { font-weight: 600; flex: 1; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
      .file-size { color: var(--color-text-muted); font-size: 13px; }

      .analysis-options { display: flex; flex-direction: column; gap: 12px; margin: 16px 0; }
      .opt-label { display: flex; flex-direction: column; gap: 4px; font-size: 13px; color: var(--color-text-secondary); }
      .rv-select, .rv-input, .rv-textarea {
        width: 100%; padding: 8px 12px; border: 1px solid var(--color-border);
        border-radius: var(--radius-sm); font-size: 14px; font-family: var(--font-stack);
      }
      .rv-textarea { resize: vertical; }

      .progress-header { display: flex; justify-content: space-between; margin-bottom: 8px; font-size: 13px; }
      .progress-bar { height: 6px; background: var(--color-border); border-radius: 3px; overflow: hidden; }
      .progress-bar-fill { height: 100%; background: var(--color-primary); transition: width 0.3s; border-radius: 3px; }

      .review-summary { display: flex; align-items: center; gap: 20px; padding: 16px; background: var(--color-bg); border-radius: var(--radius-lg); margin-bottom: 16px; }
      .review-score { text-align: center; padding: 12px 20px; border-radius: var(--radius); }
      .review-score.score-good { background: #dcfce7; }
      .review-score.score-warn { background: #fef3c7; }
      .review-score.score-bad { background: #fee2e2; }
      .score-num { font-size: 36px; font-weight: 700; color: var(--color-text); }
      .score-label { font-size: 13px; color: var(--color-text-secondary); }
      .review-dims { display: grid; grid-template-columns: 1fr 1fr; gap: 8px; flex: 1; }
      .dim-item { display: flex; justify-content: space-between; padding: 4px 8px; border-radius: var(--radius-sm); font-size: 13px; }
      .dim-item.dim-warn { background: #fef3c7; }
      .dim-item.dim-bad { background: #fee2e2; }
      .dim-score { font-weight: 600; }

      .result-tabs { display: flex; gap: 4px; margin-bottom: 16px; border-bottom: 2px solid var(--color-border); }
      .result-tab {
        padding: 8px 16px; border: none; background: none; cursor: pointer;
        font-size: 14px; color: var(--color-text-muted); border-bottom: 2px solid transparent;
        margin-bottom: -2px; transition: all 0.2s;
      }
      .result-tab.active { color: var(--color-primary); border-bottom-color: var(--color-primary); font-weight: 600; }

      .fr-item { border: 1px solid var(--color-border); border-radius: var(--radius-sm); margin-bottom: 8px; }
      .fr-item summary { padding: 12px; cursor: pointer; display: flex; align-items: center; gap: 8px; flex-wrap: wrap; }
      .fr-item summary:hover { background: var(--color-bg); }
      .fr-id { font-weight: 600; font-family: var(--font-mono); }
      .fr-module { color: var(--color-text-muted); font-size: 12px; }
      .fr-desc { color: var(--color-text-secondary); flex: 1; }
      .fr-deps { font-size: 12px; color: var(--color-primary); }
      .fr-body { padding: 12px; border-top: 1px solid var(--color-border); }
      .fr-criteria { margin: 8px 0; padding-left: 20px; }
      .fr-criteria li { margin-bottom: 4px; }
      .fr-ambiguities { background: #fef3c7; padding: 8px 12px; border-radius: var(--radius-sm); margin-top: 8px; font-size: 13px; }

      .priority-badge {
        display: inline-block; padding: 2px 8px; border-radius: 10px; font-size: 11px; font-weight: 600;
      }
      .priority-p0 { background: #fee2e2; color: #dc2626; }
      .priority-p1 { background: #fef3c7; color: #d97706; }
      .priority-p2 { background: var(--color-bg); color: var(--color-text-secondary); }
      .priority-p3 { background: var(--color-bg); color: var(--color-text-muted); }

      .risk-grid { display: grid; grid-template-columns: 1fr 1fr; gap: 12px; }
      .risk-card { border: 1px solid var(--color-border); border-radius: var(--radius); padding: 16px; }
      .risk-card.severity-high { border-left: 4px solid var(--color-danger); }
      .risk-card.severity-medium { border-left: 4px solid var(--color-warning); }
      .risk-card.severity-low { border-left: 4px solid var(--color-text-muted); }
      .risk-header { display: flex; justify-content: space-between; align-items: center; margin-bottom: 8px; }
      .risk-meta { display: flex; gap: 12px; font-size: 12px; color: var(--color-text-muted); margin: 8px 0; }
      .risk-mitigation { font-size: 13px; background: var(--color-bg); padding: 8px; border-radius: var(--radius-sm); }

      .review-item { padding: 8px 12px; border-radius: var(--radius-sm); margin-bottom: 8px; font-size: 13px; }
      .review-missing { background: #fee2e2; }
      .review-suggestion { background: var(--color-primary-light); }
      .review-hallucination { background: #fef3c7; }

      .log-list { max-height: 400px; overflow-y: auto; }
      .log-entry { padding: 6px 0; border-bottom: 1px solid var(--color-border); font-size: 13px; display: flex; gap: 8px; flex-wrap: wrap; }
      .log-time { color: var(--color-text-muted); font-family: var(--font-mono); white-space: nowrap; }

      .human-review-form { border: 2px solid var(--color-primary); border-radius: var(--radius-lg); padding: 16px; margin-top: 16px; }
      .human-review-form h4 { margin-bottom: 12px; }
      .review-actions { display: flex; gap: 8px; margin-top: 12px; }
      .human-review-result { background: var(--color-bg); padding: 16px; border-radius: var(--radius-lg); margin-top: 16px; }

      .history-item { display: flex; align-items: center; gap: 8px; padding: 10px 12px; border-radius: var(--radius-sm); cursor: pointer; }
      .history-item:hover { background: var(--color-bg); }
      .history-item-active { background: var(--color-primary-light); }
      .history-icon { font-size: 16px; }
      .history-filename { font-size: 13px; font-weight: 600; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; max-width: 220px; }
      .history-meta { font-size: 11px; }

      .empty-state, .loading-state, .error-state { text-align: center; padding: 60px 20px; }
      .empty-state-icon, .error-state-icon { font-size: 48px; margin-bottom: 12px; }

      .spinner {
        width: 32px; height: 32px; border: 3px solid var(--color-border);
        border-top-color: var(--color-primary); border-radius: 50%;
        animation: spin 0.8s linear infinite; margin: 0 auto 12px;
      }
      @keyframes spin { to { transform: rotate(360deg); } }

      #history-list { max-height: 400px; overflow-y: auto; }

      @media (max-width: 900px) {
        .analysis-layout { flex-direction: column; }
        .analysis-left { width: 100%; }
        .risk-grid { grid-template-columns: 1fr; }
      }
    `;
    document.head.appendChild(style);
  }

  // ============================================================
  // 启动
  // ============================================================
  injectStyles();
  init();
})();
