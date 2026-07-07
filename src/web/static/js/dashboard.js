/* Dashboard page */

function renderStats(projects, pipelines) {
  var projectCount = (projects || []).length;
  var activePipelines = (pipelines || []).filter(function(p) { return p.status === 'running'; }).length;
  var completedPipelines = (pipelines || []).filter(function(p) { return p.status === 'completed'; }).length;
  var failedPipelines = (pipelines || []).filter(function(p) { return p.status === 'failed'; }).length;

  document.getElementById('stat-cards').innerHTML =
    '<div class="stat-card">' +
      '<div class="stat-icon blue">' +
        '<svg width="20" height="20" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M22 19a2 2 0 0 1-2 2H4a2 2 0 0 1-2-2V5a2 2 0 0 1 2-2h5l2 3h9a2 2 0 0 1 2 2z"/></svg>' +
      '</div>' +
      '<div class="stat-info"><div class="stat-label">项目总数</div><div class="stat-value">' + projectCount + '</div></div>' +
    '</div>' +
    '<div class="stat-card">' +
      '<div class="stat-icon green">' +
        '<svg width="20" height="20" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><polyline points="22 12 18 12 15 21 9 3 6 12 2 12"/></svg>' +
      '</div>' +
      '<div class="stat-info"><div class="stat-label">运行中流水线</div><div class="stat-value">' + activePipelines + '</div></div>' +
    '</div>' +
    '<div class="stat-card">' +
      '<div class="stat-icon purple">' +
        '<svg width="20" height="20" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M22 11.08V12a10 10 0 1 1-5.93-9.14"/><polyline points="22 4 12 14.01 9 11.01"/></svg>' +
      '</div>' +
      '<div class="stat-info"><div class="stat-label">已完成流水线</div><div class="stat-value">' + completedPipelines + '</div></div>' +
    '</div>' +
    '<div class="stat-card">' +
      '<div class="stat-icon orange">' +
        '<svg width="20" height="20" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><circle cx="12" cy="12" r="10"/><line x1="12" y1="8" x2="12" y2="12"/><line x1="12" y1="16" x2="12.01" y2="16"/></svg>' +
      '</div>' +
      '<div class="stat-info"><div class="stat-label">失败流水线</div><div class="stat-value">' + failedPipelines + '</div></div>' +
    '</div>';
}

function statusBadge(status) {
  var map = {completed:'badge-success',failed:'badge-danger',running:'badge-warning',paused:'badge-neutral',cancelled:'badge-neutral',pending:'badge-neutral'};
  var cls = map[status] || 'badge-neutral';
  var label = i18n.t('projectStatus', status) || status;
  return '<span class="badge ' + cls + '">' + label + '</span>';
}

// Load data
Promise.all([
  api.get('/projects?size=100'),
  api.get('/pipelines?size=100')
]).then(function(results) {
  var projects = results[0].data || [];
  var pipelines = results[1].data || [];

  renderStats(projects, pipelines);

  // Recent projects table
  var recentProjects = projects.slice(0, 5);
  var rows = recentProjects.map(function(p) {
    return '<tr>' +
      '<td><a href="/projects/' + p.id + '">' + escapeHtml(p.name) + '</a></td>' +
      '<td><span class="badge badge-info">' + i18n.t('platformType', p.platform_type) + '</span></td>' +
      '<td>' + statusBadge(p.status) + '</td>' +
    '</tr>';
  }).join('');
  document.getElementById('recent-projects').innerHTML = rows || '<tr><td colspan="3" class="text-muted">暂无项目</td></tr>';
}).catch(function(e) {
  console.error(e);
  document.getElementById('stat-cards').innerHTML = '<div class="stat-card" style="grid-column:1/-1"><div class="text-muted">加载统计失败</div></div>';
});

// Create project form handler
var createForm = document.getElementById('create-form');
if (createForm) {
  createForm.addEventListener('submit', function(e) {
    e.preventDefault();
    var form = e.target;
    var data = {
      name: form.name.value,
      description: form.description.value,
      platform_type: form.platform_type.value
    };
    api.post('/projects', data).then(function() {
      showToast('项目创建成功', 'success');
      Modal.close('create-modal');
      form.reset();
      // Reload stats
      return Promise.all([api.get('/projects?size=100'), api.get('/pipelines?size=100')]);
    }).then(function(results) {
      renderStats(results[0].data || [], results[1].data || []);
      // Reload to show new project in list
      location.reload();
    }).catch(function(err) {
      showToast('创建失败: ' + err.message, 'error');
    });
  });
}
