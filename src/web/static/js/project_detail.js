const projectId = window.location.pathname.split('/')[2];

(async function () {
  try {
    const resp = await api.get('/projects/' + projectId);
    const p = resp.data;
    document.getElementById('breadcrumb-name').textContent = p.name;
    document.getElementById('page-title').textContent = p.name;
    renderProjectInfo(p);
    loadDocuments();
    loadPipelines();
  } catch (e) { console.error(e); }
})();

function renderProjectInfo(p) {
  const platformLabel = i18n.t('platformType', p.platform_type) || p.platform_type;
  const statusLabel = i18n.t('projectStatus', p.status) || p.status;
  const created = fmt.date(p.created_at);
  const updated = fmt.date(p.updated_at);
  const desc = p.description || '（无描述）';
  const configStr = p.platform_config ? JSON.stringify(p.platform_config) : null;

  const fields = [
    { label: '项目名称', value: esc(p.name) },
    { label: '描述', value: esc(desc) },
    {
      label: '平台类型',
      value: `<span class="rv-badge rv-badge-info">${esc(platformLabel)}</span>`
    },
    {
      label: '状态',
      value: `<span class="rv-badge ${p.status === 'active' ? 'rv-badge-success' : 'rv-badge-neutral'}">${esc(statusLabel)}</span>`
    },
    {
      label: '平台配置',
      value: configStr
        ? `<code class="rv-code" style="word-break:break-all">${esc(configStr)}</code>`
        : '<span class="rv-muted">未配置</span>'
    },
    { label: '创建时间', value: esc(created) },
    { label: '更新时间', value: esc(updated) },
  ];

  document.getElementById('project-info').innerHTML = fields.map(f => `
    <div class="rv-detail-field">
      <span class="rv-detail-label">${f.label}</span>
      <span>${f.value}</span>
    </div>
  `).join('');
}

async function uploadDocument() {
  const fileInput = document.getElementById('doc-file-input');
  const file = fileInput.files[0];
  if (!file) return showToast('请选择文件', 'error');
  const form = new FormData();
  form.append('file', file);
  try {
    await api.upload('/projects/' + projectId + '/documents', form);
    showToast('上传成功', 'success');
    fileInput.value = '';
    loadDocuments();
  } catch (e) { showToast('上传失败: ' + e.message, 'error'); }
}

async function loadDocuments() {
  try {
    const resp = await api.get('/projects/' + projectId + '/documents');
    const docs = resp.data || [];
    const list = document.getElementById('doc-list');
    if (!docs.length) {
      list.innerHTML = '<span class="rv-muted" style="font-size:12px">暂无文档</span>';
      return;
    }
    list.innerHTML = docs.map(d => `
      <div class="rv-file-info" style="margin-top:6px; justify-content:space-between">
        <span>
          <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" style="margin-right:8px;color:var(--rv-accent)"><path d="M14 2H6a2 2 0 0 0-2 2v16a2 2 0 0 0 2 2h12a2 2 0 0 0 2-2V8z"/><polyline points="14 2 14 8 20 8"/></svg>
          ${esc(d.filename)}
        </span>
        <span class="rv-muted" style="font-size:11px">${fmt.date(d.created_at)}</span>
      </div>
    `).join('');
  } catch (e) { console.error(e); }
}

async function loadPipelines() {
  try {
    const resp = await api.get('/projects/' + projectId + '/pipelines?size=20');
    const pipelines = resp.data || [];
    const tbody = document.getElementById('pipeline-list');
    if (!pipelines.length) {
      tbody.innerHTML = '<tr><td colspan="5" class="rv-empty">暂无流水线</td></tr>';
      return;
    }
    tbody.innerHTML = pipelines.map(p => `
      <tr>
        <td><a href="/pipelines/${p.id}" class="rv-code">#${p.id.slice(0,8)}</a></td>
        <td><span class="rv-badge rv-badge-info">${i18n.t('stageName', p.current_stage)}</span></td>
        <td>${statusBadge(p.status)}</td>
        <td class="rv-muted" style="font-size:12px">${fmt.date(p.started_at)}</td>
        <td><a href="/pipelines/${p.id}" class="rv-btn rv-btn-outline rv-btn-xs">查看</a></td>
      </tr>
    `).join('');
  } catch (e) { console.error(e); }
}

async function startPipeline() {
  try {
    const docs = await api.get('/projects/' + projectId + '/documents');
    const validStatuses = ['uploaded', 'parsed'];
    const docIds = (docs.data || []).filter(d => validStatuses.includes(d.status)).map(d => d.id);
    if (!docIds.length) return showToast('请先上传文档', 'error');
    const resp = await api.post('/projects/' + projectId + '/pipelines', {document_ids: docIds});
    showToast('流水线已启动', 'success');
    window.location.href = '/pipelines/' + resp.data.id;
  } catch (e) { showToast('启动失败: ' + e.message, 'error'); }
}

function statusBadge(s) {
  const m = {completed:'rv-badge-success',failed:'rv-badge-danger',running:'rv-badge-warning',paused:'rv-badge-info',cancelled:'rv-badge-neutral'};
  var label = i18n.t('pipelineStatus', s);
  return `<span class="rv-badge ${m[s]||'rv-badge-neutral'}">${label}</span>`;
}
function esc(s) { return (s||'').replace(/[&<>]/g, m=>({'&':'&amp;','<':'&lt;','>':'&gt;'})[m]); }
