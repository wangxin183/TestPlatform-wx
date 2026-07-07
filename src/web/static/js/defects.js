/* Defects page */

(function() {
  loadDefects();
})();

async function loadDefects() {
  const severity = document.getElementById('filter-severity').value;
  const status = document.getElementById('filter-status').value;

  let params = '?size=100';
  if (severity) params += '&severity=' + severity;
  if (status) params += '&status=' + status;

  try {
    const resp = await api.get('/defects' + params);
    const defects = resp.data || [];
    const tbody = document.getElementById('defects-list');

    if (!defects.length) {
      tbody.innerHTML = '<tr><td colspan="5" class="text-muted">暂无缺陷</td></tr>';
      return;
    }

    tbody.innerHTML = defects.map(d => `
      <tr>
        <td>${escapeHtml(d.title)}</td>
        <td>${severityBadge(d.severity)}</td>
        <td>${statusBadge(d.status)}</td>
        <td><span class="text-sm text-muted">${d.test_case_id ? '#' + d.test_case_id.slice(0,8) : '-'}</span></td>
        <td>${fmt.date(d.created_at)}</td>
      </tr>
    `).join('');
  } catch (e) {
    console.error(e);
  }
}

function severityBadge(s) {
  const m = {critical:'badge-danger',high:'badge-warning',medium:'badge-info',low:'badge-neutral'};
  const label = i18n.t('priority', s);
  return `<span class="badge ${m[s] || 'badge-neutral'}">${label}</span>`;
}

function statusBadge(s) {
  const m = {open:'badge-danger',confirmed:'badge-warning',in_progress:'badge-info',fixed:'badge-success',wont_fix:'badge-neutral'};
  const label = i18n.t('defectStatus', s);
  return `<span class="badge ${m[s] || 'badge-neutral'}">${label}</span>`;
}

function escapeHtml(str) { return (str||'').replace(/[&<>]/g, m => ({'&':'&amp;','<':'&lt;','>':'&gt;'})[m]); }
