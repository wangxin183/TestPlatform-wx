/**
 * Review Module JS — Two-tab review (Pipeline Review + Upload Review)
 * v2 — matches rv-* class naming
 */

const STATE = {
  tab: 'pipeline',
  pipeProjectId: '',
  pipePipelineId: '',
  pipeCases: [],
  pipeSelectedIds: new Set(),
  pipeDetailId: null,
  uploadFormat: 'json',
  uploadPreviewCases: [],
  uploadBatchId: '',
  uploadCases: [],
  uploadSelectedIds: new Set(),
  uploadDetailId: null,
  reviewSource: '',
};

// ═══════════════════════════════════════════════════════
// Init
// ═══════════════════════════════════════════════════════

document.addEventListener('DOMContentLoaded', () => {
  loadProjects();
  loadUploadHistory();
  setupDragDrop();
  if (STATE.uploadFormat === 'json') ensureTextToggle();
});

// ═══════════════════════════════════════════════════════
// Tab Switching
// ═══════════════════════════════════════════════════════

function switchReviewTab(tab) {
  STATE.tab = tab;
  document.querySelectorAll('.rv-tab').forEach(el => {
    el.classList.toggle('active', el.dataset.tab === tab);
  });
  document.querySelectorAll('.rv-tab-panel').forEach(el => {
    el.classList.toggle('active', el.id === 'tab-' + tab);
  });
}

// ═══════════════════════════════════════════════════════
// Pipeline Review
// ═══════════════════════════════════════════════════════

async function loadProjects() {
  try {
    const res = await fetch('/api/v1/projects?size=100');
    const data = await res.json();
    if (!data.success) return;
    const sel = document.getElementById('pipeline-project-select');
    sel.innerHTML = '<option value="">-- 请选择项目 --</option>';
    data.data.forEach(p => {
      sel.innerHTML += `<option value="${p.id}">${esc(p.name)}</option>`;
    });
  } catch (e) { console.error('loadProjects', e); }
}

async function onPipelineProjectChange() { console.log("[review] onPipelineProjectChange called");
  const pid = document.getElementById('pipeline-project-select').value;
  STATE.pipeProjectId = pid;
  const pipeSel = document.getElementById('pipeline-select');
  pipeSel.innerHTML = '<option value="">-- 加载中 --</option>';
  if (!pid) { pipeSel.innerHTML = '<option value="">-- 请先选择项目 --</option>'; resetPipelineReview(); return; }

  try {
    const res = await fetch(`/api/v1/projects/${pid}/pipelines?size=100`);
    const data = await res.json();
    if (!data.success) { pipeSel.innerHTML = '<option value="">-- 加载失败 --</option>'; return; }
    const filtered = data.data.filter(p => p.current_stage === 'review');
    pipeSel.innerHTML = '<option value="">-- 请选择流水线 --</option>';
    if (filtered.length === 0) {
      pipeSel.innerHTML += '<option value="" disabled>暂无处于评审阶段的流水线</option>';
    } else {
      filtered.forEach(p => {
        pipeSel.innerHTML += `<option value="${p.id}">#${p.id.slice(0,8)} (${p.status})</option>`;
      });
    }
  } catch (e) { console.error('onPipelineProjectChange', e); }
}

function onPipelineSelect() { console.log("[review] onPipelineSelect called");
  const plid = document.getElementById('pipeline-select').value;
  STATE.pipePipelineId = plid;
  if (!plid) { resetPipelineReview(); return; }
  loadPipelineCases();
}

async function loadPipelineCases() {
  const pid = STATE.pipeProjectId, plid = STATE.pipePipelineId;
  console.log('[review] loadPipelineCases pid=', pid?.slice(0,8), 'plid=', plid?.slice(0,8));
  if (!pid || !plid) { console.log('[review] loadPipelineCases: missing pid or plid'); return; }
  const el = document.getElementById('pipeline-cases-list');
  if (!el) { console.error('[review] pipeline-cases-list element NOT FOUND!'); return; }
  el.innerHTML = '<tr><td colspan="7" class="rv-empty">加载中...</td></tr>';

  try {
    const url = `/api/v1/projects/${pid}/test-cases?pipeline_id=${plid}&size=500`;
    console.log('[review] fetch:', url);
    const res = await fetch(url);
    console.log('[review] fetch status:', res.status);
    const data = await res.json();
    console.log('[review] data.success:', data.success, 'cases:', data.data?.length);
    if (!data.success) { el.innerHTML = '<tr><td colspan="7" class="rv-empty">API返回失败</td></tr>'; return; }

    STATE.pipeCases = data.data;
    STATE._typeFilter = null;
    
    const layout = document.getElementById('pipeline-review-layout');
    const progress = document.getElementById('pipeline-review-progress');
    console.log('[review] layout el:', !!layout, 'progress el:', !!progress);
    if (layout) layout.style.display = '';
    if (progress) progress.style.display = '';
    
    console.log('[review] calling renderPipelineCaseList');
    renderPipelineCaseList();
    console.log('[review] calling renderPipelineGroupNav');
    renderPipelineGroupNav();
    console.log('[review] calling updatePipelineProgress');
    updatePipelineProgress();
    console.log('[review] loadPipelineCases DONE');
  } catch (e) {
    console.error('[review] loadPipelineCases ERROR:', e.message || e, 'stack:', e.stack?.substring(0,200));
    el.innerHTML = '<tr><td colspan="7" class="rv-empty">加载失败: ' + (e.message || e).substring(0,60) + '</td></tr>';
  }
}

function renderPipelineCaseList() {
  const cases = filterPipelineCases();
  const el = document.getElementById('pipeline-cases-list');
  if (cases.length === 0) { el.innerHTML = '<tr><td colspan="7" class="rv-empty">无匹配用例</td></tr>'; return; }
  el.innerHTML = cases.map(c => `
    <tr class="${rowClass(c.status)}">
      <td class="rv-td-check">
        <label class="rv-checkbox rv-checkbox-sm">
          <input type="checkbox" class="pipe-check" data-id="${c.id}" onchange="onPipeCheckChange()">
          <span class="rv-checkbox-mark"></span>
        </label>
      </td>
      <td>
        <a href="#" onclick="openPipelineDetail('${c.id}'); return false;" class="rv-case-link">${esc(c.title)}</a>
        ${renderFlags(c.ai_flags)}
      </td>
      <td>${rvBadge(c.priority, priorityBadge(c.priority))}</td>
      <td><span class="rv-type-tag">${esc(c.test_type || '')}</span></td>
      <td>${c.ai_score != null ? `<span class="rv-score">${c.ai_score}</span>` : '<span class="rv-muted">—</span>'}</td>
      <td>${rvBadge(statusLabel(c.status), statusBadge(c.status))}</td>
      <td class="rv-td-actions">
        <button class="rv-btn-icon rv-btn-icon-sm rv-btn-approve" onclick="quickReview('${c.id}','pipeline','approve'); event.stopPropagation();" title="通过" aria-label="通过">
          <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.5" stroke-linecap="round" stroke-linejoin="round"><polyline points="20 6 9 17 4 12"/></svg>
        </button>
        <button class="rv-btn-icon rv-btn-icon-sm rv-btn-reject" onclick="openPipelineDetail('${c.id}');" title="驳回" aria-label="驳回">
          <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.5" stroke-linecap="round" stroke-linejoin="round"><line x1="18" y1="6" x2="6" y2="18"/><line x1="6" y1="6" x2="18" y2="18"/></svg>
        </button>
      </td>
    </tr>
  `).join('');
}

function renderPipelineGroupNav() {
  const groups = {};
  STATE.pipeCases.forEach(c => { const t = c.test_type || 'unknown'; if (!groups[t]) groups[t] = { total: 0, approved: 0 }; groups[t].total++; if (c.status === 'approved') groups[t].approved++; });
  const el = document.getElementById('pipe-group-nav');
  el.innerHTML = Object.entries(groups).map(([t, s]) =>
    `<div class="rv-group-row" onclick="filterByType('${t}')">
      <span class="rv-group-dot ${t}"></span>
      <span>${typeLabel(t)}</span>
      <span class="rv-group-n">${s.approved}/${s.total}</span>
    </div>`
  ).join('');
  // Restore checkbox state
  setTimeout(() => {
    document.querySelectorAll('.pipe-check').forEach(cb => {
      cb.checked = STATE.pipeSelectedIds.has(cb.dataset.id);
    });
    document.getElementById('pipe-select-all').checked = false;
  }, 0);
}

function updatePipelineProgress() {
  const c = STATE.pipeCases, t = c.length, a = c.filter(x => x.status === 'approved').length,
        r = c.filter(x => x.status === 'rejected').length, d = a + r, p = t - d;
  document.getElementById('pipe-stat-total').textContent = t;
  document.getElementById('pipe-stat-reviewed').textContent = d;
  document.getElementById('pipe-stat-approved').textContent = a;
  document.getElementById('pipe-stat-rejected').textContent = r;
  document.getElementById('pipe-stat-pending').textContent = p;
    // Progress is now shown via stat cards + toolbar fill
  const tf = document.getElementById('pipe-toolbar-fill');
  if (tf) tf.style.width = t ? (d / t * 100) + '%' : '0%';
}

function applyPipelineFilters() { STATE._typeFilter = null; renderPipelineCaseList(); }
function filterByType(type) { 
  STATE._typeFilter = type || null;
  renderPipelineCaseList(); 
  renderPipelineGroupNav();
}

function filterPipelineCases() {
  let cases = STATE.pipeCases;
  const pf = document.getElementById('pipe-filter-priority').value;
  const sf = document.getElementById('pipe-filter-status').value;
  const q = document.getElementById('pipe-filter-search').value.toLowerCase();
  if (pf) cases = cases.filter(c => c.priority === pf);
  if (sf) cases = cases.filter(c => c.status === sf);
  if (STATE._typeFilter) cases = cases.filter(c => c.test_type === STATE._typeFilter);
  if (q) cases = cases.filter(c => (c.title || '').toLowerCase().includes(q));
  return cases;
}

function resetPipelineReview() {
  STATE.pipeCases = []; STATE.pipeSelectedIds.clear();
  document.getElementById('pipeline-review-layout').style.display = 'none';
  document.getElementById('pipeline-review-progress').style.display = 'none';
  document.getElementById('pipeline-cases-list').innerHTML = '<tr><td colspan="7" class="rv-empty">请选择项目和流水线以查看用例</td></tr>';
}

function togglePipeSelectAll() {
  const checked = document.getElementById('pipe-select-all').checked;
  const visible = filterPipelineCases();
  STATE.pipeSelectedIds.clear();
  if (checked) visible.forEach(c => STATE.pipeSelectedIds.add(c.id));
  document.querySelectorAll('.pipe-check').forEach(cb => {
    cb.checked = STATE.pipeSelectedIds.has(cb.dataset.id);
  });
  updatePipeSelectedCount();
}

function onPipeCheckChange() {
  document.querySelectorAll('.pipe-check').forEach(cb => {
    if (cb.checked) STATE.pipeSelectedIds.add(cb.dataset.id);
    else STATE.pipeSelectedIds.delete(cb.dataset.id);
  });
  updatePipeSelectedCount();
}

function updatePipeSelectedCount() {
  document.getElementById('pipe-selected-count').textContent = `已选 ${STATE.pipeSelectedIds.size} 条`;
}

function openPipelineDetail(id) { STATE.reviewSource = 'pipeline'; STATE.pipeDetailId = id; showDetailModal(STATE.pipeCases.find(c => c.id === id)); }

async function quickReview(id, src, action) {
  await fetch(`/api/v1/test-cases/${id}/${action}`, { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: '{}' });
  loadPipelineCases();
}

async function reviewPipelineBatch(action) {
  if (!STATE.pipeSelectedIds.size) return;
  await fetch('/api/v1/test-cases/batch', { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ ids: [...STATE.pipeSelectedIds], action }) });
  loadPipelineCases();
}

// ═══════════════════════════════════════════════════════
// Upload Review
// ═══════════════════════════════════════════════════════

function onUploadFormatChange() {
  const fmt = document.getElementById('upload-format').value;
  STATE.uploadFormat = fmt;
  const extMap = { json: '.json', excel: '.xlsx', markdown: '.md', xmind: '.xmind' };
  document.getElementById('upload-format-hint').textContent = `支持格式: ${extMap[fmt] || ''}`;
  document.getElementById('upload-file-input').accept = extMap[fmt] || '.json';
  ensureTextToggle();
}

function ensureTextToggle() {
  const fmt = STATE.uploadFormat;
  const toggle = document.getElementById('text-input-toggle');
  if (fmt === 'json') { if (toggle) toggle.style.display = ''; }
  else { if (toggle) toggle.style.display = 'none'; }
}

function showTextInput() {
  document.getElementById('upload-dropzone').style.display = 'none';
  document.getElementById('upload-text-area').style.display = '';
}

function hideTextInput() {
  document.getElementById('upload-dropzone').style.display = '';
  document.getElementById('upload-text-area').style.display = 'none';
  document.getElementById('upload-text-input').value = '';
}

function setupDragDrop() {
  const dz = document.getElementById('upload-dropzone');
  dz.addEventListener('dragover', e => { e.preventDefault(); dz.classList.add('dragover'); });
  dz.addEventListener('dragleave', () => dz.classList.remove('dragover'));
  dz.addEventListener('drop', e => { e.preventDefault(); dz.classList.remove('dragover'); if (e.dataTransfer.files[0]) handleFile(e.dataTransfer.files[0]); });
}

function onFileSelect() {
  const file = document.getElementById('upload-file-input').files[0];
  if (file) handleFile(file);
}

async function handleFile(file) {
  const info = document.getElementById('upload-file-info');
  info.style.display = '';
  info.innerHTML = `<svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M14 2H6a2 2 0 0 0-2 2v16a2 2 0 0 0 2 2h12a2 2 0 0 0 2-2V8z"/><polyline points="14 2 14 8 20 8"/></svg> ${esc(file.name)} <span class="rv-muted">(${formatSize(file.size)})</span>`;
  await doUpload(file);
}

async function parseUploadContent() {
  const text = document.getElementById('upload-text-input').value.trim();
  if (!text) return;
  await doUpload(new Blob([text], { type: 'text/plain' }));
}

async function doUpload(fileOrBlob) {
  const form = new FormData();
  form.append('file', fileOrBlob);
  form.append('fmt', STATE.uploadFormat);

  try {
    const res = await fetch('/api/v1/review/upload', { method: 'POST', body: form });
    const data = await res.json();
    if (!data.success) { alert(data.error || '上传失败'); return; }
    STATE.uploadPreviewCases = data.data.cases;
    showPreview(data);
  } catch (e) { console.error('doUpload', e); alert('上传失败: ' + e.message); }
}

function showPreview(data) {
  const sec = document.getElementById('upload-preview');
  sec.style.display = '';
  document.getElementById('preview-count').textContent = data.data.cases.length;
  document.getElementById('preview-method').textContent = data.data.parse_method === 'ai_fallback' ? 'AI 解析' : '代码解析';
  document.getElementById('preview-hint').textContent = data.data.ai_hint || '';
  document.getElementById('upload-preview-table').innerHTML = data.data.cases.map(c => `
    <tr><td>${esc(c.title)}</td><td>${rvBadge(c.priority, priorityBadge(c.priority))}</td><td>${esc(c.test_type||'')}</td><td>${(c.steps||[]).length}</td></tr>
  `).join('');
}

function cancelPreview() { document.getElementById('upload-preview').style.display = 'none'; STATE.uploadPreviewCases = []; }

async function confirmUpload() {
  if (!STATE.uploadPreviewCases.length) return;
  STATE.uploadBatchId = STATE.uploadPreviewCases[0].batch_id;
  document.getElementById('upload-preview').style.display = 'none';
  document.getElementById('upload-review-section').style.display = '';
  document.getElementById('upload-batch-id').textContent = STATE.uploadBatchId.slice(0, 8);
  document.getElementById('upload-batch-format').textContent = STATE.uploadFormat;
  await loadUploadBatch(STATE.uploadBatchId);
  loadUploadHistory();
}

async function loadUploadBatch(id) {
  try {
    const res = await fetch(`/api/v1/review/batches/${id}`);
    const data = await res.json();
    if (!data.success) return;
    STATE.uploadCases = data.data;
    renderUploadCaseList();
  } catch (e) { console.error('loadUploadBatch', e); }
}

function renderUploadCaseList() {
  const cases = STATE.uploadCases, el = document.getElementById('upload-cases-list');
  if (!cases.length) { el.innerHTML = '<tr><td colspan="7" class="rv-empty">无用例</td></tr>'; return; }
  el.innerHTML = cases.map(c => `
    <tr class="${rowClass(c.status)}">
      <td class="rv-td-check">
        <label class="rv-checkbox rv-checkbox-sm">
          <input type="checkbox" class="upload-check" data-id="${c.id}" onchange="onUploadCheckChange()">
          <span class="rv-checkbox-mark"></span>
        </label>
      </td>
      <td><a href="#" onclick="openUploadDetail('${c.id}'); return false;" class="rv-case-link">${esc(c.title)}</a>${renderFlags(c.ai_flags)}</td>
      <td>${rvBadge(c.priority, priorityBadge(c.priority))}</td>
      <td><span class="rv-type-tag">${esc(c.test_type||'')}</span></td>
      <td>${c.ai_score != null ? `<span class="rv-score">${c.ai_score}</span>` : '<span class="rv-muted">—</span>'}</td>
      <td>${rvBadge(statusLabel(c.status), statusBadge(c.status))}</td>
      <td class="rv-td-actions">
        <button class="rv-btn-icon rv-btn-icon-sm rv-btn-approve" onclick="quickUploadReview('${c.id}','approve')" title="通过"><svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.5"><polyline points="20 6 9 17 4 12"/></svg></button>
        <button class="rv-btn-icon rv-btn-icon-sm rv-btn-reject" onclick="openUploadDetail('${c.id}')" title="驳回"><svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.5"><line x1="18" y1="6" x2="6" y2="18"/><line x1="6" y1="6" x2="18" y2="18"/></svg></button>
      </td>
    </tr>
  `).join('');
}

function toggleUploadSelectAll() {
  const checked = document.getElementById('upload-select-all').checked;
  STATE.uploadSelectedIds.clear();
  if (checked) STATE.uploadCases.forEach(c => STATE.uploadSelectedIds.add(c.id));
  document.querySelectorAll('.upload-check').forEach(cb => {
    cb.checked = STATE.uploadSelectedIds.has(cb.dataset.id);
  });
  updateUploadSelectedCount();
}

function onUploadCheckChange() {
  document.querySelectorAll('.upload-check').forEach(cb => { if (cb.checked) STATE.uploadSelectedIds.add(cb.dataset.id); else STATE.uploadSelectedIds.delete(cb.dataset.id); });
  updateUploadSelectedCount();
}

function updateUploadSelectedCount() { document.getElementById('upload-selected-count').textContent = `已选 ${STATE.uploadSelectedIds.size} 条`; }

function openUploadDetail(id) { STATE.reviewSource = 'upload'; STATE.uploadDetailId = id; showDetailModal(STATE.uploadCases.find(c => c.id === id)); }

async function quickUploadReview(id, action) {
  await fetch(`/api/v1/review/submissions/${id}/${action}`, { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: '{}' });
  if (STATE.uploadBatchId) loadUploadBatch(STATE.uploadBatchId);
}

async function reviewUploadBatch(action) {
  if (!STATE.uploadSelectedIds.size) return;
  await fetch('/api/v1/review/submissions/batch', { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ ids: [...STATE.uploadSelectedIds], action }) });
  if (STATE.uploadBatchId) loadUploadBatch(STATE.uploadBatchId);
}

async function refreshUploadReview() { if (STATE.uploadBatchId) loadUploadBatch(STATE.uploadBatchId); }

async function loadUploadHistory() {
  const el = document.getElementById('upload-history-list');
  try {
    const res = await fetch('/api/v1/review/batches?size=50');
    const data = await res.json();
    if (!data.success) return;
    if (!data.data.length) { el.innerHTML = '<tr><td colspan="7" class="rv-empty">暂无上传记录</td></tr>'; return; }
    el.innerHTML = data.data.map(b => `
      <tr>
        <td><code class="rv-code">${esc(b.batch_id.slice(0,8))}</code></td>
        <td>${esc(b.source_format)}</td>
        <td>${b.total}</td>
        <td class="rv-text-success">${b.approved}</td>
        <td class="rv-text-danger">${b.rejected}</td>
        <td><span class="rv-muted">${b.created_at ? formatTime(b.created_at) : '-'}</span></td>
        <td><button class="rv-btn rv-btn-xs rv-btn-ghost" onclick="loadHistoryBatch('${b.batch_id}')">查看</button></td>
      </tr>
    `).join('');
  } catch (e) { console.error('loadUploadHistory', e); }
}

async function loadHistoryBatch(id) {
  STATE.uploadBatchId = id;
  document.getElementById('upload-review-section').style.display = '';
  document.getElementById('upload-batch-id').textContent = id.slice(0, 8);
  await loadUploadBatch(id);
  switchReviewTab('upload');
  document.getElementById('upload-review-section').scrollIntoView({ behavior: 'smooth' });
}

// ═══════════════════════════════════════════════════════
// Shared: Detail Modal
// ═══════════════════════════════════════════════════════

function showDetailModal(c) {
  if (!c) return;
  document.getElementById('case-detail-title').textContent = c.title || '用例详情';
  document.getElementById('case-detail-body').innerHTML = renderDetailBody(c);
  document.getElementById('review-comment').value = c.review_comment || '';
  document.getElementById('reject-reason-select').value = c.reject_reason || '';
  const editBtn = document.getElementById('btn-edit-mode');
  editBtn.innerHTML = `<svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><path d="M11 4H4a2 2 0 0 0-2 2v14a2 2 0 0 0 2 2h14a2 2 0 0 0 2-2v-7"/><path d="M18.5 2.5a2.121 2.121 0 0 1 3 3L12 15l-4 1 1-4 9.5-9.5z"/></svg> 编辑`;
  editBtn.dataset.mode = 'view';
  Modal.open('case-detail-modal');
}

function renderDetailBody(c) {
  const steps = (c.steps || []).map((s, i) =>
    `<tr><td class="rv-td-step">${i+1}</td><td>${esc(s.action)}</td><td>${esc(s.expected)}</td></tr>`
  ).join('');
  return `
    <div class="rv-detail-grid">
      <div class="rv-detail-field">
        <span class="rv-detail-label">优先级</span>
        <span>${rvBadge(c.priority, priorityBadge(c.priority))}</span>
      </div>
      <div class="rv-detail-field">
        <span class="rv-detail-label">类型</span>
        <span class="rv-type-tag">${esc(c.test_type||'')}</span>
      </div>
      <div class="rv-detail-field">
        <span class="rv-detail-label">平台</span>
        <span>${esc(c.platform_type||'未指定')}</span>
      </div>
      ${c.ai_score != null ? `<div class="rv-detail-field"><span class="rv-detail-label">AI 评分</span><span class="rv-score">${c.ai_score} 分</span>${renderFlags(c.ai_flags)}</div>` : ''}
      ${(c.tags||[]).length ? `<div class="rv-detail-field rv-detail-full"><span class="rv-detail-label">标签</span><span>${(c.tags||[]).map(t => `<span class="rv-tag">${esc(t)}</span>`).join(' ')}</span></div>` : ''}
    </div>
    <div class="rv-detail-section">
      <h4>描述</h4>
      <p class="rv-editable" id="detail-description" data-field="description">${esc(c.description||'无')}</p>
    </div>
    <div class="rv-detail-section">
      <h4>前置条件</h4>
      <p class="rv-editable" id="detail-preconditions" data-field="preconditions">${esc(c.preconditions||'无')}</p>
    </div>
    <div class="rv-detail-section">
      <h4>测试步骤</h4>
      <table class="rv-detail-steps">
        <thead><tr><th class="rv-th-step">#</th><th>操作</th><th>预期结果</th></tr></thead>
        <tbody>${steps || '<tr><td colspan="3" class="rv-empty">无步骤</td></tr>'}</tbody>
      </table>
    </div>
  `;
}

function toggleReviewEditMode() {
  const btn = document.getElementById('btn-edit-mode');
  const isEdit = btn.dataset.mode === 'edit';
  if (isEdit) {
    saveDetailEdits();
    btn.innerHTML = `<svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><path d="M11 4H4a2 2 0 0 0-2 2v14a2 2 0 0 0 2 2h14a2 2 0 0 0 2-2v-7"/><path d="M18.5 2.5a2.121 2.121 0 0 1 3 3L12 15l-4 1 1-4 9.5-9.5z"/></svg> 编辑`;
    btn.dataset.mode = 'view';
    document.querySelectorAll('.rv-detail-edit').forEach(el => {
      const p = document.createElement('p');
      p.className = 'rv-editable'; p.dataset.field = el.dataset.field; p.textContent = el.value || '无'; el.replaceWith(p);
    });
  } else {
    btn.innerHTML = `<svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><rect x="3" y="3" width="18" height="18" rx="2" ry="2"/><line x1="3" y1="9" x2="21" y2="9"/><line x1="9" y1="21" x2="9" y2="9"/></svg> 保存`;
    btn.dataset.mode = 'edit';
    document.querySelectorAll('.rv-editable').forEach(p => {
      if (!['description','preconditions'].includes(p.dataset.field)) return;
      const ta = document.createElement('textarea');
      ta.className = 'rv-textarea rv-textarea-sm rv-detail-edit'; ta.dataset.field = p.dataset.field;
      ta.value = p.textContent === '无' ? '' : p.textContent; ta.rows = 3; p.replaceWith(ta);
    });
  }
}

async function saveDetailEdits() {
  const caseId = STATE.reviewSource === 'pipeline' ? STATE.pipeDetailId : STATE.uploadDetailId;
  if (!caseId) return;
  const body = {}; document.querySelectorAll('.rv-detail-edit').forEach(el => { body[el.dataset.field] = el.value; });
  const url = STATE.reviewSource === 'pipeline' ? `/api/v1/test-cases/${caseId}` : `/api/v1/review/submissions/${caseId}`;
  try {
    await fetch(url, { method: 'PUT', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(body) });
    STATE.reviewSource === 'pipeline' ? loadPipelineCases() : loadUploadBatch(STATE.uploadBatchId);
  } catch (e) { console.error('saveDetailEdits', e); }
}

async function submitReview(action) {
  const caseId = STATE.reviewSource === 'pipeline' ? STATE.pipeDetailId : STATE.uploadDetailId;
  if (!caseId) return;
  const body = JSON.stringify({ comment: document.getElementById('review-comment').value, reason: document.getElementById('reject-reason-select').value });
  const url = STATE.reviewSource === 'pipeline' ? `/api/v1/test-cases/${caseId}/${action}` : `/api/v1/review/submissions/${caseId}/${action}`;
  try {
    await fetch(url, { method: 'POST', headers: { 'Content-Type': 'application/json' }, body });
    Modal.close('case-detail-modal');
    STATE.reviewSource === 'pipeline' ? loadPipelineCases() : loadUploadBatch(STATE.uploadBatchId);
  } catch (e) { console.error('submitReview', e); }
}

// ═══════════════════════════════════════════════════════
// Helpers
// ═══════════════════════════════════════════════════════

function esc(s) { if (!s) return ''; const d = document.createElement('div'); d.textContent = s; return d.innerHTML; }

function priorityBadge(p) { return ({'严重':'rv-badge-danger','高':'rv-badge-warning','中':'rv-badge-info','低':'rv-badge-neutral'})[p] || 'rv-badge-neutral'; }
function statusBadge(s) { return ({'approved':'rv-badge-success','rejected':'rv-badge-danger','pending_review':'rv-badge-info'})[s] || 'rv-badge-neutral'; }
function statusLabel(s) { return ({'approved':'已通过','rejected':'已驳回','pending_review':'待评审'})[s] || s; }
function typeLabel(t) { return ({'ui':'UI测试','api':'接口测试','performance':'性能测试','security':'安全测试','compatibility':'兼容性'})[t] || t; }
function rowClass(s) { return s === 'rejected' ? 'rv-row-rejected' : s === 'approved' ? 'rv-row-approved' : ''; }
function formatSize(b) { return b < 1024 ? b+' B' : b < 1024*1024 ? (b/1024).toFixed(1)+' KB' : (b/(1024*1024)).toFixed(1)+' MB'; }
function formatTime(iso) { if (!iso) return '-'; const d = new Date(iso); return d.toLocaleString('zh-CN', { month:'2-digit', day:'2-digit', hour:'2-digit', minute:'2-digit' }); }

function rvBadge(text, cls) { return `<span class="rv-badge ${cls}">${esc(text)}</span>`; }

const FLAG_MAP = {
  'high_risk':            { label: '高风险',    desc: '综合评分低于40分，用例质量存在严重问题，建议重写', color: 'rv-flag rv-flag-danger' },
  'insufficient_steps':   { label: '步骤不足',  desc: '测试步骤不完整，步骤数≤1或缺少预期结果', color: 'rv-flag rv-flag-warning' },
  'vague_description':    { label: '描述模糊',  desc: '标题或描述过于笼统，无法明确测试意图和场景', color: 'rv-flag rv-flag-warning' },
  'wrong_test_type':      { label: '类型错误',  desc: 'test_type 与实际测试内容不匹配（如API测试标为UI）', color: 'rv-flag rv-flag-danger' },
  'vague_expected':       { label: '预期模糊',  desc: '预期结果含“正常/成功/正确”等模糊词汇，应改为具体可观察的描述', color: 'rv-flag rv-flag-warning' },
  'overvalued_priority':  { label: '优先级偏高',  desc: '不严重的用例（如边界/文案）被标为“严重”，与实际影响不匹配', color: 'rv-flag rv-flag-warning' },
  'missing_preconditions':{ label: '缺少前置条件', desc: '未描述执行用例前需要的账号状态、数据准备或环境配置', color: 'rv-flag rv-flag-warning' },
};

function renderFlags(flags) {
  if (!flags || !flags.length) return '';
  return ' ' + flags.map(f => {
    var info = FLAG_MAP[f];
    if (info) {
      return '<span class="' + info.color + '" title="' + esc(f) + '">' + esc(info.desc) + '</span>';
    }
    return '<span class="rv-flag">' + esc(f) + '</span>';
  }).join('');
}
