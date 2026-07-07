/**
 * Projects Module — Table List with Search & Edit
 * v6 — Precision Panel table design
 */

// ═══════════════════════════════════════════════════
// Platform config
// ═══════════════════════════════════════════════════

var PLATFORM = {
  web:         { label: 'Web',        color: '#3b82f6' },
  h5:          { label: 'H5',         color: '#8b5cf6' },
  ios:         { label: 'iOS',        color: '#06b6d4' },
  android:     { label: 'Android',    color: '#10b981' },
  miniprogram: { label: '小程序',      color: '#f59e0b' },
  api:         { label: 'API 接口',    color: '#ef4444' },
};

// ═══════════════════════════════════════════════════
// State
// ═══════════════════════════════════════════════════

var allProjects = [];

// ═══════════════════════════════════════════════════
// Helpers
// ═══════════════════════════════════════════════════

function esc(s) {
  if (!s) return '';
  var d = document.createElement('div');
  d.textContent = s;
  return d.innerHTML;
}

function fmtDate(iso) {
  if (!iso) return '';
  var d = new Date(iso);
  return d.toLocaleDateString('zh-CN', { month: '2-digit', day: '2-digit' });
}

// ═══════════════════════════════════════════════════
// Render Table
// ═══════════════════════════════════════════════════

function renderTable(projects) {
  var el = document.getElementById('projects-list');
  document.getElementById('project-count').textContent = projects.length + ' 个项目';

  if (!projects.length) {
    el.innerHTML = '<tr><td colspan="5" class="rv-empty">暂无匹配项目</td></tr>';
    return;
  }

  el.innerHTML = projects.map(function(p) {
    var pf = PLATFORM[p.platform_type] || PLATFORM.web;
    return '<tr class="rv-project-row" onclick="goDetail(\'' + p.id + '\')">'
      + '<td><a href="/projects/' + p.id + '" class="rv-case-link" onclick="event.stopPropagation()">' + esc(p.name) + '</a></td>'
      + '<td><span class="rv-muted">' + esc(p.description || '—') + '</span></td>'
      + '<td><span class="rv-badge rv-badge-info" style="background:' + pf.color + '15;color:' + pf.color + '">' + esc(pf.label) + '</span></td>'
      + '<td><span class="rv-muted">' + fmtDate(p.created_at) + '</span></td>'
      + '<td class="rv-td-actions">'
        + '<button class="rv-btn-icon rv-btn-icon-sm" onclick="event.stopPropagation(); openEditModal(\'' + p.id + '\')" title="编辑">'
          + '<svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><path d="M11 4H4a2 2 0 0 0-2 2v14a2 2 0 0 0 2 2h14a2 2 0 0 0 2-2v-7"/><path d="M18.5 2.5a2.121 2.121 0 0 1 3 3L12 15l-4 1 1-4 9.5-9.5z"/></svg>'
        + '</button>'
      + '</td>'
    + '</tr>';
  }).join('');
}

function goDetail(id) {
  window.location.href = '/projects/' + id;
}

// ═══════════════════════════════════════════════════
// Search
// ═══════════════════════════════════════════════════

function applySearch() {
  var q = (document.getElementById('project-search').value || '').toLowerCase().trim();
  if (!q) { renderTable(allProjects); return; }
  var filtered = allProjects.filter(function(p) {
    return (p.name || '').toLowerCase().indexOf(q) !== -1;
  });
  renderTable(filtered);
}

// ═══════════════════════════════════════════════════
// Load
// ═══════════════════════════════════════════════════

function loadProjects() {
  var el = document.getElementById('projects-list');
  el.innerHTML = '<tr><td colspan="5" class="rv-empty">加载中...</td></tr>';
  fetch('/api/v1/projects?size=100')
    .then(function(r) { return r.json(); })
    .then(function(data) {
      if (data.success) {
        allProjects = data.data || [];
        applySearch();
      } else {
        el.innerHTML = '<tr><td colspan="5" class="rv-empty">加载失败</td></tr>';
      }
    })
    .catch(function() {
      el.innerHTML = '<tr><td colspan="5" class="rv-empty">加载失败</td></tr>';
    });
}

// ═══════════════════════════════════════════════════
// Modal: Create / Edit
// ═══════════════════════════════════════════════════

function openCreateModal() {
  document.getElementById('modal-kicker').textContent = '新建项目';
  document.getElementById('modal-title').textContent = '创建新项目';
  document.getElementById('modal-submit-btn').innerHTML = '<svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.5"><polyline points="20 6 9 17 4 12"/></svg> 创建';
  document.getElementById('project-id').value = '';
  document.getElementById('project-name').value = '';
  document.getElementById('project-desc').value = '';
  document.getElementById('project-platform').value = 'web';
  document.getElementById('project-modal').classList.add('open');
}

function openEditModal(id) {
  fetch('/api/v1/projects/' + id)
    .then(function(r) { return r.json(); })
    .then(function(data) {
      if (!data.success) return;
      var p = data.data;
      document.getElementById('modal-kicker').textContent = '编辑项目';
      document.getElementById('modal-title').textContent = p.name;
      document.getElementById('modal-submit-btn').innerHTML = '<svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.5"><polyline points="20 6 9 17 4 12"/></svg> 保存';
      document.getElementById('project-id').value = p.id;
      document.getElementById('project-name').value = p.name;
      document.getElementById('project-desc').value = p.description || '';
      document.getElementById('project-platform').value = p.platform_type || 'web';
      document.getElementById('project-modal').classList.add('open');
    });
}

function closeProjectModal() {
  document.getElementById('project-modal').classList.remove('open');
}

// ═══════════════════════════════════════════════════
// Form Submit
// ═══════════════════════════════════════════════════

document.addEventListener('DOMContentLoaded', function() {
  document.getElementById('project-form').addEventListener('submit', function(e) {
    e.preventDefault();
    var id = document.getElementById('project-id').value;
    var body = {
      name: document.getElementById('project-name').value,
      description: document.getElementById('project-desc').value,
      platform_type: document.getElementById('project-platform').value,
    };
    var isEdit = !!id;
    fetch(isEdit ? '/api/v1/projects/' + id : '/api/v1/projects', {
      method: isEdit ? 'PUT' : 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(body)
    })
    .then(function(r) { return r.json(); })
    .then(function(data) {
      if (data.success) { closeProjectModal(); loadProjects(); }
      else { alert(data.error || '操作失败'); }
    })
    .catch(function(err) { alert('请求失败: ' + err.message); });
  });

  loadProjects();
});
