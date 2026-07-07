const pipelineId = window.location.pathname.split('/')[2];
const STATUS_COLORS = {
  completed: 'completed', failed: 'failed', running: 'running',
  pending: 'pending', paused: 'paused', cancelled: 'cancelled',
};
const STATUS_LABELS = {
  completed: '已完成', failed: '失败', running: '运行中',
  pending: '等待中', paused: '已暂停', cancelled: '已取消',
};
const STAGE_LABELS = {
  ingestion: '文档摄入', parsing: '文档解析', analysis: '需求分析',
  generation: '用例生成', review: '人工评审', execution: '测试执行',
  reporting: '报告生成', regression: '回归筛选',
};
function svgIcon(pathD) {
  return '<svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" style="vertical-align:-2px; margin-right:6px;">' +
    pathD +
    '</svg>';
}
const STAGE_ICONS = {
  ingestion: svgIcon('<path d="M12 3v12"></path><path d="M7 10l5 5 5-5"></path><path d="M5 21h14"></path>'),
  parsing: svgIcon('<path d="M14 2H6a2 2 0 0 0-2 2v16a2 2 0 0 0 2 2h12a2 2 0 0 0 2-2V8z"></path><path d="M14 2v6h6"></path><path d="M8 13h8"></path><path d="M8 17h6"></path>'),
  analysis: svgIcon('<circle cx="11" cy="11" r="8"></circle><path d="M21 21l-4.35-4.35"></path>'),
  generation: svgIcon('<circle cx="12" cy="12" r="3"></circle><path d="M19.4 15a1.65 1.65 0 0 0 .33 1.82l.06.06a2 2 0 0 1-2.83 2.83l-.06-.06a1.65 1.65 0 0 0-1.82-.33 1.65 1.65 0 0 0-1 1.51V21a2 2 0 0 1-4 0v-.09A1.65 1.65 0 0 0 9 19.4a1.65 1.65 0 0 0-1.82.33l-.06.06a2 2 0 0 1-2.83-2.83l.06-.06A1.65 1.65 0 0 0 4.68 15a1.65 1.65 0 0 0-1.51-1H3a2 2 0 0 1 0-4h.09A1.65 1.65 0 0 0 4.6 9a1.65 1.65 0 0 0-.33-1.82l-.06-.06a2 2 0 0 1 2.83-2.83l.06.06A1.65 1.65 0 0 0 9 4.68a1.65 1.65 0 0 0 1-1.51V3a2 2 0 0 1 4 0v.09a1.65 1.65 0 0 0 1 1.51 1.65 1.65 0 0 0 1.82-.33l.06-.06a2 2 0 0 1 2.83 2.83l-.06.06A1.65 1.65 0 0 0 19.4 9a1.65 1.65 0 0 0 1.51 1H21a2 2 0 0 1 0 4h-.09a1.65 1.65 0 0 0-1.51 1z"></path>'),
  review: svgIcon('<path d="M14 2H6a2 2 0 0 0-2 2v16a2 2 0 0 0 2 2h12a2 2 0 0 0 2-2V8z"></path><path d="M14 2v6h6"></path><path d="M16 13H8"></path><path d="M16 17H8"></path>'),
  execution: svgIcon('<polygon points="5 3 19 12 5 21 5 3"></polygon>'),
  reporting: svgIcon('<path d="M3 3v18h18"></path><path d="M7 14l4-4 4 4 6-6"></path>'),
  regression: svgIcon('<path d="M21 12a9 9 0 0 1-9 9 9 9 0 0 1-9-9 9 9 0 0 1 9-9"></path><path d="M3 3v6h6"></path>'),
};

const UI_ICONS = {
  pause: svgIcon('<path d="M6 4h4v16H6z"></path><path d="M14 4h4v16h-4z"></path>'),
  stop: svgIcon('<rect x="6" y="6" width="12" height="12" rx="2"></rect>'),
  play: svgIcon('<polygon points="5 3 19 12 5 21 5 3"></polygon>'),
  x: svgIcon('<path d="M18 6L6 18"></path><path d="M6 6l12 12"></path>'),
  alert: svgIcon('<path d="M10.29 3.86L1.82 18a2 2 0 0 0 1.71 3h16.94a2 2 0 0 0 1.71-3L13.71 3.86a2 2 0 0 0-3.42 0z"></path><path d="M12 9v4"></path><path d="M12 17h.01"></path>'),
};

let cachedLogs = [];
let selectedLogIdx = -1;

async function loadPipeline() {
  try {
    const resp = await api.get('/pipelines/' + pipelineId);
    const p = resp.data;
    document.getElementById('page-title').textContent = '流水线 #' + pipelineId.slice(0,8);
    document.getElementById('breadcrumb-name').textContent = '流水线 #' + pipelineId.slice(0,8);
    renderActions(p);
    loadDag();
    loadStageLogs();
  } catch(e) { console.error(e); }
}

function renderActions(p) {
  const c = document.getElementById('pipeline-actions');
  let h = '';
  if (p.status === 'running') {
    h += '<button class="rv-btn rv-btn-danger rv-btn-sm" onclick="pausePipeline()">' + UI_ICONS.pause + '暂停</button>';
    h += '<button class="rv-btn rv-btn-outline rv-btn-sm" onclick="cancelPipeline()">' + UI_ICONS.stop + '取消</button>';
  } else if (p.status === 'paused') {
    h += '<button class="rv-btn rv-btn-accent rv-btn-sm" onclick="resumePipeline()">' + UI_ICONS.play + '继续执行</button>';
    h += '<button class="rv-btn rv-btn-outline rv-btn-sm" onclick="cancelPipeline()">' + UI_ICONS.stop + '取消</button>';
  } else if (p.status === 'failed') {
    const failedLabel = STAGE_LABELS[p.current_stage] || p.current_stage || '';
    h += '<span style="font-size:13px; color:#f43f5e; margin-right:8px; font-weight:600">' + UI_ICONS.x + failedLabel + ' 失败</span>';
    h += '<button class="rv-btn rv-btn-accent" onclick="retryPipeline()" style="font-weight:600;">' + UI_ICONS.play + '继续执行（从 ' + failedLabel + ' 重试）</button>';
  } else if (p.status === 'completed') {
    h += '<span class="rv-badge rv-badge-success" style="font-size:13px; padding:5px 16px">已完成</span>';
  } else if (p.status === 'cancelled') {
    h += '<span class="rv-badge rv-badge-neutral" style="font-size:13px; padding:5px 16px">已取消</span>';
  }
  c.innerHTML = h;
}

async function loadDag() {
  try {
    const resp = await api.get('/pipelines/' + pipelineId + '/dag');
    const dag = resp.data;
    if (!dag) return;
    const c = document.getElementById('dag-container');
    c.innerHTML = dag.nodes.map((n, i) => {
      const cls = STATUS_COLORS[n.status] || 'pending';
      const latestIdx = cachedLogs.map((l, idx) => ({l, idx}))
        .filter(x => x.l.stage_name === n.id).pop();
      const logId = latestIdx ? latestIdx.idx : -1;
      return `
        ${i > 0 ? '<span style="color:#d6d3d1; margin:0 1px;">▸</span>' : ''}
        <span class="dag-node ${cls}" onclick="showStageDetail(${logId})" title="${n.label} — 点击查看详情">
          ${STAGE_ICONS[n.id] || '•'} ${n.label}
          ${n.duration_seconds !== null ? ` <small>${n.duration_seconds}s</small>` : ''}
        </span>`;
    }).join('');

    const pbar = document.getElementById('dag-progress');
    const completed = dag.completed_count || 0;
    const total = dag.total_count || dag.nodes.length;
    pbar.style.width = dag.progress + '%';
    pbar.textContent = dag.progress > 8 ? completed + '/' + total : '';
    if (dag.progress > 0 && dag.progress < 12) pbar.style.minWidth = '44px';

    const statusBg = {
      completed: 'var(--rv-success-gradient)',
      failed: 'var(--rv-danger-gradient)',
      running: 'var(--rv-accent-gradient)',
      paused: '#f59e0b',
      cancelled: '#a8a29e',
      pending: '#d6d3d1'
    };
    pbar.style.background = statusBg[dag.pipeline_status] || statusBg.pending;

    const plabel = document.getElementById('progress-label');
    if (plabel) plabel.textContent = '进度 ' + completed + '/' + total + ' (' + dag.progress + '%)';

    let statusText = (STAGE_LABELS[dag.current_stage] || dag.current_stage || '...') + ' | ';
    if (dag.pipeline_status === 'running') statusText += '运行中';
    else if (dag.pipeline_status === 'completed') statusText += '已完成';
    else if (dag.pipeline_status === 'failed') statusText += '失败';
    else if (dag.pipeline_status === 'paused') statusText += '已暂停';
    else statusText += dag.pipeline_status || '等待中';
    document.getElementById('pipeline-status-text').textContent = statusText;
    document.getElementById('pipeline-status-text').style.color = 'var(--rv-text-muted)';
  } catch(e) { console.error(e); }
}

// ═══════ Compact Stage Row List ═══════
async function loadStageLogs() {
  try {
    const resp = await api.get('/pipelines/' + pipelineId + '/stages');
    cachedLogs = resp.data || [];
    const c = document.getElementById('stage-logs');
    if (!cachedLogs.length) {
      c.innerHTML = '<div class="rv-empty" style="padding:24px">暂无阶段日志</div>';
      return;
    }
    c.innerHTML = cachedLogs.map((log, idx) => {
      const cls = STATUS_COLORS[log.status] || 'pending';
      const icon = STAGE_ICONS[log.stage_name] || '•';
      const label = STAGE_LABELS[log.stage_name] || log.stage_name;
      const statusLabel = STATUS_LABELS[log.status] || log.status;

      let dur = '';
      if (log.started_at && log.completed_at) {
        const ms = new Date(log.completed_at) - new Date(log.started_at);
        dur = (ms / 1000).toFixed(1) + 's';
      }

      const attemptNum = cachedLogs.filter(l => l.stage_name === log.stage_name).length > 1
        ? ' #' + (cachedLogs.filter((l, i) => l.stage_name === log.stage_name && i <= idx).length)
        : '';
      const isSelected = selectedLogIdx === idx;
      return `
        <div class="stage-row ${cls} ${isSelected ? 'selected' : ''}"
             onclick="showStageDetail(${idx})">
          <span class="stage-row-icon">${icon}</span>
          <span class="stage-row-name">${label}${attemptNum}</span>
          ${dur ? `<span class="stage-row-dur">${dur}</span>` : ''}
          <span class="stage-row-status">${statusLabel}</span>
          ${log.error_message ? '<span class="stage-row-err" title="' + escHtml(log.error_message).slice(0, 100) + '">' + UI_ICONS.alert + '</span>' : ''}
        </div>`;
    }).join('');
  } catch(e) { console.error(e); }
}

// ═══════ Stage Detail Panel ═══════
function showStageDetail(logIdx) {
  selectedLogIdx = logIdx;
  loadStageLogs();

  const log = cachedLogs[logIdx];
  const panel = document.getElementById('stage-detail-content');

  if (!log) {
    panel.innerHTML = '<div class="rv-empty" style="padding:40px"><div>未找到日志</div></div>';
    return;
  }

  const cls = STATUS_COLORS[log.status] || 'pending';
  const statusLabel = STATUS_LABELS[log.status] || log.status;
  const label = STAGE_LABELS[log.stage_name] || log.stage_name;
  const icon = STAGE_ICONS[log.stage_name] || '';

  let dur = '-';
  if (log.started_at && log.completed_at) {
    dur = ((new Date(log.completed_at) - new Date(log.started_at)) / 1000).toFixed(1) + ' 秒';
  }

  const fmt = (s, fallback) => {
    const v = s || fallback || null;
    if (!v) return '-';
    const d = new Date(v.endsWith('Z') ? v : v + 'Z');
    const bj = new Date(d.getTime() + 8 * 3600000);
    return bj.toISOString().replace('T', ' ').slice(0, 19);
  };

  let outputHtml = '';
  if (log.output_data) {
    const data = typeof log.output_data === 'string' ? tryParse(log.output_data) : log.output_data;
    outputHtml = renderOutput(data);
  }

  let errHtml = '';
  if (log.error_message) {
    errHtml = `
      <div class="detail-block">
        <div class="detail-block-title error-title">错误信息</div>
        <pre class="detail-pre detail-error">${escHtml(log.error_message)}</pre>
      </div>`;
  }

  let inputHtml = '';
  if (log.input_summary) {
    const inp = typeof log.input_summary === 'string' ? tryParse(log.input_summary) : log.input_summary;
    inputHtml = `
      <div class="detail-block">
        <div class="detail-block-title">输入</div>
        ${renderOutput(inp)}
      </div>`;
  }

  const badgeCls = log.status === 'completed' ? 'rv-badge-success' : log.status === 'failed' ? 'rv-badge-danger' : log.status === 'running' ? 'rv-badge-warning' : 'rv-badge-neutral';

  panel.innerHTML = `
    <div style="display:flex; align-items:center; gap:10px; margin-bottom:14px; padding:4px 20px">
      <span class="dag-node ${cls}" style="font-size:13px; padding:4px 12px">${icon} ${label}</span>
      <span class="rv-badge ${badgeCls}">${statusLabel}</span>
      ${log.status === 'failed' ? '<button class="rv-btn rv-btn-accent rv-btn-sm" onclick="retryPipeline()" style="margin-left:auto">▶️ 重试此阶段</button>' : ''}
    </div>

    <div class="detail-meta-compact" style="padding:0 20px">
      <div><span>开始</span><span>${fmt(log.started_at, log.created_at)}</span></div>
      <div><span>结束</span><span>${fmt(log.completed_at, log.created_at)}</span></div>
      <div><span>耗时</span><span>${dur}</span></div>
    </div>

    ${errHtml}
    ${inputHtml}

    <div class="detail-block">
      <div class="detail-block-title">输出</div>
      ${log.stage_name === 'ingestion' ? renderIngestionOutput(log) : (outputHtml || '<div style="color:var(--rv-text-muted); font-size:13px; padding:12px 0;">无输出数据</div>')}
    </div>
    ${log.stage_name === 'generation' ? '<div class="detail-block"><div class="detail-block-title">已生成用例</div><div id="testcase-preview">加载中...</div></div>' : ''}
    ${log.stage_name === 'review' ? '<div class="detail-block"><div class="detail-block-title">评审统计</div><div id="review-stats-preview">加载中...</div></div>' : ''}
  `;

  if (log.stage_name === 'generation') {
    loadTestCasesForStage();
  }
  if (log.stage_name === 'review') {
    loadReviewStatsForStage();
  }
}

function renderOutput(data, depth) {
  depth = depth || 0;
  if (data === null || data === undefined) return '<span class="json-null">null</span>';
  if (typeof data === 'string') {
    if (data.length > 100 || data.includes('\n')) {
      return `<pre class="detail-pre">${escHtml(data.slice(0, 10000))}${data.length > 10000 ? '\n…(已截断)' : ''}</pre>`;
    }
    return `<span>${escHtml(data)}</span>`;
  }
  if (typeof data === 'number') return `<span class="json-num">${data}</span>`;
  if (typeof data === 'boolean') return `<span class="json-bool">${data}</span>`;
  if (Array.isArray(data)) {
    if (!data.length) return '<span class="json-empty">[]</span>';
    return '<div class="json-array">' + data.map((v, i) =>
      `<div class="json-array-row"><span class="json-idx">${i}</span>${renderOutput(v, depth + 1)}</div>`
    ).join('') + '</div>';
  }
  if (typeof data === 'object') {
    const keys = Object.keys(data);
    if (!keys.length) return '<span class="json-empty">{}</span>';
    return '<table class="detail-table">' + keys.map(k =>
      `<tr><td class="detail-table-key">${cnLabel(escHtml(k))}</td><td>${renderOutput(data[k], depth + 1)}</td></tr>`
    ).join('') + '</table>';
  }
  return `<span>${escHtml(String(data))}</span>`;
}

function tryParse(s) {
  try { return JSON.parse(s); } catch(e) { return s; }
}

// ═══════ Load Review Stats ═══════
async function loadReviewStatsForStage() {
  const rsDiv = document.getElementById('review-stats-preview');
  if (!rsDiv) return;
  try {
    const resp = await api.get('/pipelines/' + pipelineId + '/review-stats');
    const s = resp.data || {};
    if (!s.total) { rsDiv.innerHTML = '<div style="color:var(--rv-text-muted); font-size:13px;">暂无统计数据</div>'; return; }
    const pct = s.total ? Math.round(s.approved / s.total * 100) : 0;
    const types = Object.entries(s.by_type || {}).map(([t, d]) =>
      `<div class="rs-type-row"><span>${t}</span><span>${d.approved}/${d.total}</span></div>`
    ).join('');
    const reasons = Object.entries(s.reject_reasons || {}).map(([r, c]) =>
      `<div class="rs-reason-row"><span class="rs-reason-label">${r}</span><div class="rs-reason-bar"><div class="rs-reason-fill" style="width:${Math.round(c/s.rejected*100)}%"></div><span style="font-size:11px; margin-left:4px;">${c}</span></div></div>`
    ).join('') || '<div style="color:var(--rv-text-muted); font-size:12px;">无驳回记录</div>';

    rsDiv.innerHTML = `
      <div class="rs-summary">
        <div class="rs-stat"><span class="rs-stat-val">${s.total}</span><span>总计</span></div>
        <div class="rs-stat success"><span class="rs-stat-val">${s.approved}</span><span>通过</span></div>
        <div class="rs-stat fail"><span class="rs-stat-val">${s.rejected}</span><span>驳回</span></div>
        <div class="rs-stat"><span class="rs-stat-val">${s.avg_ai_score}</span><span>均分</span></div>
      </div>
      <div class="rs-progress-bar"><div class="rs-progress-fill" style="width:${pct}%"></div></div>
      <div style="font-size:12px; color:var(--rv-text-muted); margin-top:8px;">按类型</div>
      <div class="rs-types">${types}</div>
      <div style="font-size:12px; color:var(--rv-text-muted); margin-top:8px;">驳回原因</div>
      <div class="rs-reasons">${reasons}</div>
      ${s.high_risk_count ? '<div class="rs-high-risk">⚠️ ' + s.high_risk_count + ' 条高风险用例 (' + s.high_risk_approved + ' 已通过)</div>' : ''}
      ${s.rejected ? '<button class="rv-btn rv-btn-accent rv-btn-sm" style="margin-top:8px" onclick="retryRejected()">🔄 重新生成驳回用例</button>' : ''}
    `;
  } catch(e) { rsDiv.innerHTML = '<div style="color:var(--rv-text-muted); font-size:13px;">加载失败</div>'; }
}

async function retryRejected() {
  if (!confirm('将重新生成所有被驳回的用例，旧用例将标记为已废弃。确定继续？')) return;
  try {
    await api.post('/pipelines/' + pipelineId + '/retry-rejected', {});
    showToast('已触发重新生成，请等待完成', 'success');
    setTimeout(loadPipeline, 2000);
  } catch(e) { showToast('操作失败: ' + e.message, 'error'); }
}

// ═══════ Load Test Cases ═══════
async function loadTestCasesForStage() {
  const tcDiv = document.getElementById('testcase-preview');
  if (!tcDiv) return;
  try {
    const resp = await api.get('/pipelines/' + pipelineId + '/test-cases');
    const cases = resp.data || [];
    if (!cases.length) {
      tcDiv.innerHTML = '<div style="color:var(--rv-text-muted); font-size:13px; padding:8px 0;">暂无用例</div>';
      return;
    }
    tcDiv.innerHTML = cases.map((tc, i) => {
      const badgeCls = tc.status === 'approved' ? 'rv-badge-success' : tc.status === 'rejected' ? 'rv-badge-danger' : 'rv-badge-neutral';
      const statusLabel = {approved:'已通过', rejected:'已驳回', pending_review:'待评审', draft:'草稿'}[tc.status] || tc.status;
      return `<div class="tc-row" onclick="toggleTcSteps(${i})">
        <span class="tc-idx">#${i + 1}</span>
        <span class="tc-priority priority-${tc.priority || 'medium'}">${tc.priority || '中'}</span>
        <span class="tc-title">${escHtml(tc.title)}</span>
        <span class="rv-badge ${badgeCls}" style="margin-left:auto; flex-shrink:0;">${statusLabel}</span>
      </div>
      <div class="tc-steps" id="tc-steps-${i}" style="display:none;">
        ${(tc.steps || []).map(s => '<div class="tc-step"><span class="tc-step-num">' + (s.step || '') + '</span><span>' + escHtml(s.action || s.description || '') + '</span><span style="color:var(--rv-text-muted); margin-left:8px;">→ ' + escHtml(s.expected || '') + '</span></div>').join('')}
        ${tc.preconditions ? '<div class="tc-precond">前置条件: ' + escHtml(tc.preconditions) + '</div>' : ''}
        ${tc.description ? '<div class="tc-desc">' + escHtml(tc.description) + '</div>' : ''}
      </div>`;
    }).join('');
    tcDiv.innerHTML = '<div style="max-height:400px; overflow-y:auto;">' + tcDiv.innerHTML + '</div>';
  } catch(e) {
    tcDiv.innerHTML = '<div style="color:var(--rv-danger); font-size:13px;">加载失败: ' + escHtml(e.message) + '</div>';
  }
}

function renderIngestionOutput(log) {
  if (!log.output_data) return '<div style="color:var(--rv-text-muted); font-size:13px; padding:12px 0;">无输出数据</div>';
  const data = typeof log.output_data === 'string' ? tryParse(log.output_data) : log.output_data;

  let html = '<div class="ingest-summary">';
  html += '<div class="ingest-stat"><span class="ingest-stat-val">' + (data.document_count || 0) + '</span><span class="ingest-stat-label">文档总数</span></div>';
  html += '<div class="ingest-stat success"><span class="ingest-stat-val">' + (data.success_count || 0) + '</span><span class="ingest-stat-label">成功</span></div>';
  html += '<div class="ingest-stat fail"><span class="ingest-stat-val">' + (data.error_count || 0) + '</span><span class="ingest-stat-label">失败</span></div>';
  html += '</div>';

  const docs = data.documents || [];
  if (docs.length) {
    html += '<div class="ingest-docs">';
    docs.forEach((doc, i) => {
      const stCls = doc.status === 'success' ? 'success' : 'fail';
      const stLabel = doc.status === 'success' ? 'OK' : 'ERR';
      html += '<div class="ingest-doc-row">';
      html += '<span class="ingest-doc-idx">#' + (i + 1) + '</span>';
      html += '<span class="ingest-doc-status ' + stCls + '">' + stLabel + '</span>';
      html += '<div class="ingest-doc-info">';
      html += '<div class="ingest-doc-name">' + escHtml(doc.filename || '未知文件') + '</div>';
      html += '<div class="ingest-doc-meta">';
      html += '<span>类型 ' + escHtml(doc.file_type || '-') + '</span>';
      html += '<span>长度 ' + (doc.content_length || 0) + ' 字符</span>';
      html += '<span>ID ' + escHtml((doc.doc_id || '').slice(0, 8)) + '...</span>';
      html += '</div></div></div>';
    });
    html += '</div>';
  }

  return html;
}

function toggleTcSteps(idx) {
  const el = document.getElementById('tc-steps-' + idx);
  if (el) el.style.display = el.style.display === 'none' ? 'block' : 'none';
}

// ═══════ WebSocket ═══════
function connectWS() {
  const proto = location.protocol === 'https:' ? 'wss:' : 'ws:';
  const ws = new WebSocket(proto + '//' + location.host + '/ws/pipelines/' + pipelineId + '/live');
  ws.onmessage = (e) => {
    try {
      const m = JSON.parse(e.data);
      if (m.type === 'pong') return;
      if (m.type === 'stage_change') { loadDag(); loadStageLogs(); loadPipeline(); }
    } catch(_) {}
  };
  ws.onclose = () => setTimeout(connectWS, 5000);
  ws.onerror = () => ws.close();
  setInterval(() => { if (ws.readyState === WebSocket.OPEN) ws.send('ping'); }, 30000);

  let pollTimer = setInterval(async () => {
    try {
      const r = await api.get('/pipelines/' + pipelineId);
      const p = r.data;
      if (p.status === 'completed' || p.status === 'failed' || p.status === 'cancelled') {
        clearInterval(pollTimer);
      }
      loadDag();
      loadStageLogs();
      renderActions(p);
    } catch(_) {}
  }, 2000);
}

// ═══════ Actions ═══════
async function pausePipeline() {
  try { await api.post('/pipelines/' + pipelineId + '/pause', {}); loadPipeline(); }
  catch(e) { showToast('暂停失败: ' + e.message, 'error'); }
}
async function resumePipeline() {
  try { await api.post('/pipelines/' + pipelineId + '/resume', {}); loadPipeline(); }
  catch(e) { showToast('继续失败: ' + e.message, 'error'); }
}
async function cancelPipeline() {
  if (!confirm('确认取消流水线？')) return;
  try { await api.post('/pipelines/' + pipelineId + '/cancel', {}); loadPipeline(); }
  catch(e) { showToast('取消失败: ' + e.message, 'error'); }
}
async function retryPipeline() {
  const btn = event.target;
  btn.disabled = true;
  btn.textContent = '重试中...';
  try {
    const resp = await api.post('/pipelines/' + pipelineId + '/retry', {});
    if (resp.success) {
      setTimeout(() => { loadPipeline(); loadDag(); loadStageLogs(); }, 2000);
    } else {
      showToast('重试失败: ' + (resp.error || '未知错误'), 'error');
      btn.disabled = false;
      btn.textContent = '▶️ 继续执行（重试）';
    }
  } catch(e) {
    showToast('重试失败: ' + e.message, 'error');
    btn.disabled = false;
    btn.textContent = '▶️ 继续执行（重试）';
  }
}

function escHtml(s) { return String(s).replace(/&/g,'&amp;').replace(/</g,'&lt;').replace(/>/g,'&gt;'); }

// ═══════ 字段名中文映射 ═══════
const FIELD_CN = {
  pipeline_id:'流水线ID',project_id:'项目ID',document_ids:'文档ID列表',
  raw_texts:'原始文本',parsed_requirements:'解析后的需求',analysis_report:'分析报告',
  generated_test_cases:'生成的用例',approved_test_case_ids:'已审批用例ID',
  execution_ids:'执行记录ID',report_ids:'报告ID',regression_case_ids:'回归用例ID',
  custom_prompt:'自定义提示词',test_plan_md:'测试计划',test_plan_file:'测试计划文件',
  performance_plan:'性能测试方案',security_plan:'安全测试方案',review_feedback:'评审反馈',
  project_config:'项目配置',platform_type:'平台类型',title:'标题',description:'描述',
  actors:'角色',functional_requirements:'功能需求',non_functional_requirements:'非功能需求',
  constraints:'约束条件',data_entities:'数据实体',id:'编号',name:'名称',priority:'优先级',
  category:'分类',fields:'字段',filename:'文件名',file_type:'文件类型',status:'状态',
  gaps:'需求缺口',ambiguities:'模糊需求',contradictions:'需求矛盾',
  missing_edge_cases:'遗漏的边界情况',testability_issues:'可测试性问题',
  missing_error_handling:'遗漏的错误处理',overall_quality_score:'整体质量评分',
  summary:'分析总结',suggestion:'建议',issue:'问题',step:'步骤序号',action:'操作',
  expected:'预期结果',steps:'操作步骤',preconditions:'前置条件',tags:'标签',
  test_type:'测试类型',total_cases:'总用例数',passed:'通过',failed:'失败',
  errors:'错误',pass_rate:'通过率',total:'总计',duration_ms:'耗时(毫秒)',
  error_message:'错误信息',message:'消息',generated_at:'生成时间',report_type:'报告类型',
  format:'格式',selection_reason:'选择原因',severity:'严重程度',
  total_duration_ms:'总耗时(毫秒)',total_duration_s:'总耗时(秒)',
};
function cnLabel(key) {
  if (FIELD_CN[key]) return FIELD_CN[key];
  const stem = key.toLowerCase().replace(/(_json|_md|_path|_ids|_id|s)$/, '');
  return FIELD_CN[stem] || key;
}

loadPipeline();
connectWS();
