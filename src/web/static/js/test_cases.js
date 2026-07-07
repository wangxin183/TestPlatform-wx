/* Test Cases Review Page — v3 */

const projectId = window.location.pathname.split('/')[2];
let selectedCaseId = null;
let selectedIds = new Set();
let allCases = [];
let currentEditMode = false;

(async function () {
  document.getElementById('back-link').href = '/projects/' + projectId;
  loadTestCases();
  loadReviewStats();
})();

// ═══════ Data Loading ═══════

async function loadTestCases() {
  const priority = document.getElementById('filter-priority').value;
  const status = document.getElementById('filter-status').value || 'pending_review';
  const search = document.getElementById('filter-search').value;

  let params = '?size=200';
  if (priority) params += '&priority=' + priority;
  if (status) params += '&status=' + status;
  if (search) params += '&search=' + encodeURIComponent(search);

  try {
    const resp = await api.get('/projects/' + projectId + '/test-cases' + params);
    allCases = resp.data || [];
    renderGroups();
    renderTable();
    renderHighRisk();
    updateProgress();
  } catch (e) { console.error(e); }
}

async function loadReviewStats() {
  try {
    const resp = await api.get('/pipelines/' + projectId + '/review-stats');
    // Stats shown in progress bar
  } catch (e) { /* optional */ }
}

function applyFilters() {
  selectedIds.clear();
  document.getElementById('select-all').checked = false;
  loadTestCases();
}

// ═══════ Group Navigation ═══════

function renderGroups() {
  const groups = {};
  allCases.forEach(tc => {
    const type = tc.test_type || 'unknown';
    if (!groups[type]) groups[type] = { total: 0, pending: 0, approved: 0, rejected: 0 };
    groups[type].total++;
    if (tc.status === 'pending_review') groups[type].pending++;
    if (tc.status === 'approved') groups[type].approved++;
    if (tc.status === 'rejected') groups[type].rejected++;
  });

  const typeLabels = { ui: '🖥 UI', api: '🔌 API', performance: '⚡ 性能', security: '🔒 安全', compatibility: '📱 兼容' };
  const nav = document.getElementById('group-nav');
  nav.innerHTML = Object.entries(groups).map(([type, g]) => {
    const label = typeLabels[type] || type;
    return `<div class="group-item" onclick="filterByType('${type}')">
      <span>${label}</span>
      <span class="group-count">${g.pending}/${g.total}</span>
    </div>`;
  }).join('');
}

// ═══════ Table Rendering ═══════

function renderTable() {
  const tbody = document.getElementById('test-cases-list');
  if (!allCases.length) {
    tbody.innerHTML = '<tr><td colspan="6" class="text-muted">暂无用例，可能是筛选条件太严格</td></tr>';
    return;
  }

  tbody.innerHTML = allCases.map(tc => {
    const isSelected = selectedIds.has(tc.id);
    const isHighRisk = tc.ai_score !== null && tc.ai_score !== undefined && tc.ai_score < 40;
    const rowClass = isHighRisk ? 'tr-high-risk' : '';
    const aiBadge = renderScoreBadge(tc.ai_score);
    const flags = tc.ai_flags || [];

    return `<tr class="${rowClass}">
      <td><input type="checkbox" ${isSelected ? 'checked' : ''} value="${tc.id}" onchange="toggleSelect('${tc.id}', this.checked)"></td>
      <td>
        <a href="#" onclick="showDetail('${tc.id}');return false" class="tc-link">${escapeHtml(tc.title)}</a>
        ${flags.length ? '<span class="flag-tags">' + flags.map(f => `<span class="flag-tag">${f}</span>`).join('') + '</span>' : ''}
        ${tc.reject_reason ? `<span class="badge badge-reason">${tc.reject_reason}</span>` : ''}
      </td>
      <td>${priorityBadge(tc.priority)}</td>
      <td>${aiBadge}</td>
      <td>${statusBadge(tc.status)}</td>
      <td>
        ${tc.status === 'pending_review' ? `
        <span class="action-icons">
          <span class="action-icon approve" onclick="quickApprove('${tc.id}')" title="快速通过">✓</span>
          <span class="action-icon reject" onclick="quickReject('${tc.id}')" title="快速驳回">✗</span>
        </span>` : `<span class="text-sm text-muted">—</span>`}
      </td>
    </tr>`;
  }).join('');
}

function renderScoreBadge(score) {
  if (score === null || score === undefined) return '<span class="score-na">N/A</span>';
  let cls = 'score-high';
  if (score < 40) cls = 'score-low';
  else if (score < 70) cls = 'score-mid';
  let icon = score < 40 ? '🔴' : score < 70 ? '🟡' : '🟢';
  return `<span class="score-badge ${cls}" title="AI评分 ${score}/100">${icon} ${score}</span>`;
}

// ═══════ High Risk Section ═══════

function renderHighRisk() {
  const highRisk = allCases.filter(tc => tc.ai_score !== null && tc.ai_score !== undefined && tc.ai_score < 40);
  const section = document.getElementById('high-risk-alert');
  const list = document.getElementById('high-risk-list');
  const count = document.getElementById('high-risk-count');

  if (!highRisk.length) { section.style.display = 'none'; return; }
  section.style.display = 'block';
  count.textContent = highRisk.length;

  list.innerHTML = highRisk.map(tc => `
    <div class="high-risk-card" onclick="showDetail('${tc.id}')">
      <span class="hr-score">🔴 ${tc.ai_score}</span>
      <span class="hr-title">${escapeHtml(tc.title)}</span>
      <span class="hr-priority">${priorityBadge(tc.priority)}</span>
      <span class="hr-actions">
        <button class="btn btn-success btn-xs" onclick="event.stopPropagation();quickApprove('${tc.id}')">通过</button>
        <button class="btn btn-danger btn-xs" onclick="event.stopPropagation();quickReject('${tc.id}')">驳回</button>
      </span>
    </div>
  `).join('');
}

// ═══════ Progress Bar ═══════

function updateProgress() {
  const total = allCases.length;
  const approved = allCases.filter(c => c.status === 'approved').length;
  const rejected = allCases.filter(c => c.status === 'rejected').length;
  const reviewed = approved + rejected;
  const pending = total - reviewed;

  document.getElementById('stat-total').textContent = total;
  document.getElementById('stat-reviewed').textContent = reviewed;
  document.getElementById('stat-approved').textContent = approved;
  document.getElementById('stat-rejected').textContent = rejected;
  document.getElementById('stat-pending').textContent = pending;

  const pct = total > 0 ? (reviewed / total * 100) : 0;
  document.getElementById('review-fill').style.width = pct + '%';
  document.getElementById('review-fill').className = 'progress-fill ' + (pct >= 100 ? 'success' : 'warning');
}

// ═══════ Detail Modal ═══════

async function showDetail(id) {
  selectedCaseId = id;
  currentEditMode = false;
  try {
    const resp = await api.get('/test-cases/' + id);
    const tc = resp.data;
    document.getElementById('case-detail-title').textContent = tc.title;

    const steps = (tc.steps || []).map(s => `
      <tr>
        <td>${s.step}</td>
        <td class="editable-field" data-field="steps">${escapeHtml(s.action)}</td>
        <td class="editable-field" data-field="steps">→ ${escapeHtml(s.expected)}</td>
      </tr>`).join('');

    const aiInfo = tc.ai_score !== null && tc.ai_score !== undefined ? `
      <div class="ai-score-card">
        <span class="ai-score-value ${tc.ai_score < 40 ? 'danger' : tc.ai_score < 70 ? 'warning' : 'success'}">${tc.ai_score}/100</span>
        ${(tc.ai_flags || []).map(f => `<span class="flag-tag">${f}</span>`).join('')}
      </div>` : '';

    document.getElementById('case-detail-body').innerHTML = `
      ${aiInfo}
      <div class="detail-field"><label>描述</label><p class="editable-field" data-field="description">${escapeHtml(tc.description || '—')}</p></div>
      <div class="detail-field"><label>前置条件</label><p class="editable-field" data-field="preconditions">${escapeHtml(tc.preconditions || '—')}</p></div>
      <div class="detail-field"><label>优先级</label><p>${priorityBadge(tc.priority)}</p></div>
      <div class="detail-field"><label>标签</label><p>${(tc.tags || []).join(', ') || '—'}</p></div>
      <div class="detail-field"><label>测试步骤</label>
        <div class="table-wrap"><table><thead><tr><th style="width:40px">#</th><th>操作</th><th>预期结果</th></tr></thead><tbody>${steps}</tbody></table></div>
      </div>
    `;

    document.getElementById('review-comment').value = tc.review_comment || '';
    document.getElementById('reject-reason-select').value = tc.reject_reason || '';
    document.getElementById('btn-edit-mode').style.display = 'inline-block';
    Modal.open('case-detail-modal');
  } catch (e) { console.error(e); }
}

// ═══════ Review Actions ═══════

async function reviewCase(action) {
  if (!selectedCaseId) return;
  const comment = document.getElementById('review-comment').value;
  const reason = document.getElementById('reject-reason-select').value;
  try {
    const url = '/test-cases/' + selectedCaseId + '/' + action;
    await api.post(url, { comment, reason });
    showToast(action === 'approve' ? '已通过' : '已驳回', 'success');
    Modal.close('case-detail-modal');
    selectedIds.clear();
    loadTestCases();
  } catch (e) { showToast('操作失败: ' + e.message, 'error'); }
}

async function quickApprove(id) {
  try {
    await api.post('/test-cases/' + id + '/approve', {});
    showToast('已通过', 'success');
    loadTestCases();
  } catch (e) { showToast('操作失败', 'error'); }
}

async function quickReject(id) {
  try {
    await api.post('/test-cases/' + id + '/reject', {});
    showToast('已驳回', 'success');
    loadTestCases();
  } catch (e) { showToast('操作失败', 'error'); }
}

// ═══════ Batch Actions ═══════

function toggleSelect(id, checked) {
  if (checked) selectedIds.add(id); else selectedIds.delete(id);
  document.getElementById('selected-count').textContent = '已选 ' + selectedIds.size + ' 条';
}

function toggleSelectAll() {
  const all = document.getElementById('select-all').checked;
  const boxes = document.querySelectorAll('#test-cases-list input[type=checkbox]');
  boxes.forEach(b => { b.checked = all; toggleSelect(b.value, all); });
}

async function batchAction(action) {
  if (!selectedIds.size) return showToast('请先选择用例', 'error');
  try {
    await api.post('/test-cases/batch', { ids: Array.from(selectedIds), action });
    showToast('批量操作完成', 'success');
    selectedIds.clear();
    document.getElementById('selected-count').textContent = '已选 0 条';
    document.getElementById('select-all').checked = false;
    loadTestCases();
  } catch (e) { showToast('操作失败: ' + e.message, 'error'); }
}

async function batchByFilter(action, priority) {
  try {
    const resp = await api.post('/test-cases/batch-by-filter', {
      filters: { priority, status: 'pending_review' },
      action,
      pipeline_id: null,
    });
    showToast(`已批量${action === 'approve' ? '通过' : '驳回'} ${resp.data.updated} 条`, 'success');
    loadTestCases();
  } catch (e) { showToast('操作失败: ' + e.message, 'error'); }
}

function filterByType(type) {
  // Quick filter by type — update table
  document.getElementById('filter-search').value = '';
  document.getElementById('filter-priority').value = '';
  allCases = allCases.filter(c => (c.test_type || '') === type);
  renderTable();
  updateProgress();
}

// ═══════ Edit Mode ═══════

function toggleEditMode() {
  currentEditMode = !currentEditMode;
  const fields = document.querySelectorAll('.editable-field');
  const btn = document.getElementById('btn-edit-mode');

  if (currentEditMode) {
    fields.forEach(f => {
      f.contentEditable = true;
      f.classList.add('editing');
    });
    btn.textContent = '💾 保存';
  } else {
    fields.forEach(f => {
      f.contentEditable = false;
      f.classList.remove('editing');
    });
    // Save edits
    saveEdits();
    btn.textContent = '✏️ 编辑';
  }
}

async function saveEdits() {
  if (!selectedCaseId) return;
  const desc = document.querySelector('[data-field="description"]')?.textContent || '';
  const precond = document.querySelector('[data-field="preconditions"]')?.textContent || '';

  try {
    await api.put('/test-cases/' + selectedCaseId, {
      description: desc,
      preconditions: precond,
      status: 'approved',
    });
    showToast('已保存并批准', 'success');
  } catch (e) { showToast('保存失败: ' + e.message, 'error'); }
}

// ═══════ Pipeline Advance ═══════

async function advancePipeline() {
  const pending = allCases.filter(c => c.status === 'pending_review');
  if (pending.length) {
    if (!confirm(`还有 ${pending.length} 条用例未评审，确定继续吗？`)) return;
  }
  // Resume pipeline to advance from review to execution
  try {
    await api.post('/pipelines/' + projectId + '/resume', {});
    showToast('流水线已推进', 'success');
  } catch (e) { showToast('操作失败: ' + e.message, 'error'); }
}

// ═══════ Helpers ═══════

function priorityBadge(p) {
  const m = { '严重': 'badge-critical', '高': 'badge-warning', '中': 'badge-info', '低': 'badge-neutral' };
  const label = p || '中';
  const cls = m[label] || 'badge-neutral';
  return `<span class="badge ${cls}">${label}</span>`;
}

function statusBadge(s) {
  const m = { approved: 'badge-success', pending_review: 'badge-warning', rejected: 'badge-danger', draft: 'badge-neutral', deprecated: 'badge-neutral' };
  const labels = { approved: '✓ 已通过', rejected: '✗ 已驳回', pending_review: '待评审', draft: '草稿', deprecated: '已废弃' };
  const cls = m[s] || 'badge-neutral';
  return `<span class="badge ${cls}">${labels[s] || s}</span>`;
}

function escapeHtml(str) {
  if (!str) return '';
  return String(str).replace(/[&<>]/g, m => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;' })[m]);
}
