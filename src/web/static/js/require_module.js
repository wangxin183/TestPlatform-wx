/**
 * Requirement Management Module JS
 * Two-tab layout: Upload + List
 * v2 — auto-process + auto-poll
 */

var REQ_STATE = {
  tab: 'upload',
  reqFile: null,
  reqFormat: 'json',
  reqProjectId: '',
  listPage: 1,
  listSize: 50,
};

document.addEventListener('DOMContentLoaded', function() {
  loadReqProjects();
  setupReqDragDrop();
});

// ═══════════════════════ Tab Switching ═══════════════

function switchReqTab(tab) {
  REQ_STATE.tab = tab;
  document.querySelectorAll('.rv-tab').forEach(function(el) {
    el.classList.toggle('active', el.dataset.tab === tab);
  });
  document.querySelectorAll('.rv-tab-panel').forEach(function(el) {
    el.classList.toggle('active', el.id === 'tab-' + tab);
  });
  if (tab === 'list') {
    loadReqList();
  }
}

// ═══════════════════════ Projects ═══════════════════

function loadReqProjects() {
  fetch('/api/v1/projects?size=100')
    .then(function(res) { return res.json(); })
    .then(function(data) {
      if (!data.success) return;
      var projects = data.data;
      var sel = document.getElementById('req-project-select');
      if (sel) {
        sel.innerHTML = '<option value="">-- 请选择项目 --</option>';
        projects.forEach(function(p) {
          sel.innerHTML += '<option value="' + p.id + '">' + esc(p.name) + '</option>';
        });
      }
      var filter = document.getElementById('req-list-project-filter');
      if (filter) {
        filter.innerHTML = '<option value="">全部项目</option>';
        projects.forEach(function(p) {
          filter.innerHTML += '<option value="' + p.id + '">' + esc(p.name) + '</option>';
        });
      }
    })
    .catch(function(e) { console.error('loadReqProjects:', e); });
}

// ═══════════════════════ Format Switch ═══════════════

function onReqFormatChange() {
  var fmt = document.getElementById('req-format').value;
  REQ_STATE.reqFormat = fmt;
  var fileZone = document.getElementById('req-file-zone');
  var urlZone = document.getElementById('req-url-zone');
  if (fmt === 'url') {
    if (fileZone) fileZone.style.display = 'none';
    if (urlZone) urlZone.style.display = 'block';
  } else {
    if (fileZone) fileZone.style.display = 'block';
    if (urlZone) urlZone.style.display = 'none';
  }
  clearReqFile();
}

// ═══════════════════════ Drag & Drop ═══════════════

function setupReqDragDrop() {
  var dz = document.getElementById('req-dropzone');
  if (!dz) return;
  dz.addEventListener('dragover', function(e) {
    e.preventDefault(); e.stopPropagation();
    dz.classList.add('rv-dropzone--over');
  });
  dz.addEventListener('dragleave', function(e) {
    e.preventDefault(); e.stopPropagation();
    dz.classList.remove('rv-dropzone--over');
  });
  dz.addEventListener('drop', function(e) {
    e.preventDefault(); e.stopPropagation();
    dz.classList.remove('rv-dropzone--over');
    if (e.dataTransfer.files && e.dataTransfer.files.length > 0) {
      setReqFile(e.dataTransfer.files[0]);
    }
  });
}

function onReqFileSelected() {
  var input = document.getElementById('req-file-input');
  if (input.files && input.files.length > 0) setReqFile(input.files[0]);
}

function setReqFile(file) {
  REQ_STATE.reqFile = file;
  var info = document.getElementById('req-file-info');
  var dropzone = document.getElementById('req-dropzone');
  if (info) info.style.display = 'flex';
  if (dropzone) dropzone.style.display = 'none';
  var nameEl = document.getElementById('req-file-name');
  if (nameEl) nameEl.textContent = file.name;
  var sizeEl = document.getElementById('req-file-size');
  if (sizeEl) sizeEl.textContent = formatFileSize(file.size);
}

function clearReqFile() {
  REQ_STATE.reqFile = null;
  var info = document.getElementById('req-file-info');
  var dropzone = document.getElementById('req-dropzone');
  var input = document.getElementById('req-file-input');
  var urlInput = document.getElementById('req-url-input');
  if (info) info.style.display = 'none';
  if (dropzone) dropzone.style.display = 'block';
  if (input) input.value = '';
  if (urlInput) urlInput.value = '';
}

// ═══════════════════════ Upload ═══════════════════

function uploadRequirement() {
  var fmt = document.getElementById('req-format').value;
  var pid = document.getElementById('req-project-select').value;
  var msgEl = document.getElementById('req-upload-msg');
  var btn = document.getElementById('req-upload-btn');

  if (!pid) {
    if (msgEl) { msgEl.textContent = '请先选择项目'; msgEl.className = 'rv-upload-msg rv-upload-msg--error'; }
    return;
  }

  var formData = new FormData();
  formData.append('project_id', pid);

  if (fmt === 'url') {
    var urlVal = document.getElementById('req-url-input').value.trim();
    if (!urlVal) {
      if (msgEl) { msgEl.textContent = '请输入URL链接'; msgEl.className = 'rv-upload-msg rv-upload-msg--error'; }
      return;
    }
    formData.append('url', urlVal);
    formData.append('name', urlVal.substring(0, 80));
  } else {
    if (!REQ_STATE.reqFile) {
      if (msgEl) { msgEl.textContent = '请选择文件'; msgEl.className = 'rv-upload-msg rv-upload-msg--error'; }
      return;
    }
    formData.append('file', REQ_STATE.reqFile);
    formData.append('name', REQ_STATE.reqFile.name);
  }

  if (msgEl) { msgEl.textContent = '上传中...'; msgEl.className = 'rv-upload-msg'; }
  if (btn) { btn.disabled = true; btn.style.opacity = '0.6'; }

  fetch('/api/v1/requirements/upload', { method: 'POST', body: formData })
    .then(function(res) { return res.json(); })
    .then(function(data) {
      if (btn) { btn.disabled = false; btn.style.opacity = '1'; }
      if (data.success) {
        if (msgEl) { msgEl.textContent = '上传成功，正在自动处理...'; msgEl.className = 'rv-upload-msg rv-upload-msg--success'; }
        clearReqFile();
        setTimeout(function() {
          if (msgEl) { msgEl.textContent = ''; msgEl.className = 'rv-upload-msg'; }
          switchReqTab('list');
          loadReqList();
        }, 1200);
      } else {
        if (msgEl) { msgEl.textContent = data.error || '上传失败'; msgEl.className = 'rv-upload-msg rv-upload-msg--error'; }
      }
    })
    .catch(function(e) {
      if (btn) { btn.disabled = false; btn.style.opacity = '1'; }
      if (msgEl) { msgEl.textContent = '网络错误: ' + (e.message || '未知'); msgEl.className = 'rv-upload-msg rv-upload-msg--error'; }
      console.error('uploadRequirement:', e);
    });
}

// ═══════════════════════ List ═══════════════════

function loadReqList() {
  var pid = document.getElementById('req-list-project-filter').value;
  var status = document.getElementById('req-list-status-filter').value;
  var search = document.getElementById('req-list-search').value.trim();
  var tbody = document.getElementById('req-list-tbody');

  var params = 'page=' + REQ_STATE.listPage + '&size=' + REQ_STATE.listSize;
  if (pid) params += '&project_id=' + encodeURIComponent(pid);
  if (status) params += '&status=' + encodeURIComponent(status);

  if (tbody) tbody.innerHTML = '';

  fetch('/api/v1/requirements?' + params)
    .then(function(res) { return res.json(); })
    .then(function(data) {
      if (!data.success) {
        if (tbody) tbody.innerHTML = '<tr><td colspan="10" class="rv-empty">加载失败</td></tr>';
        return;
      }
      var tasks = data.data || [];
      if (search) {
        var q = search.toLowerCase();
        tasks = tasks.filter(function(t) { return (t.name || '').toLowerCase().indexOf(q) >= 0; });
      }
      renderReqList(tasks);
    })
    .catch(function(e) {
      console.error('loadReqList:', e);
      if (tbody) tbody.innerHTML = '<tr><td colspan="10" class="rv-empty">加载失败: ' + (e.message || '未知错误') + '</td></tr>';
    });
}

function renderReqList(tasks) {
  var tbody = document.getElementById('req-list-tbody');
  if (!tbody) return;
  if (tasks.length === 0) {
    tbody.innerHTML = '<tr><td colspan="10" class="rv-empty">暂无需求记录</td></tr>';
    return;
  }
  tbody.innerHTML = tasks.map(function(t) {
    return '<tr>'
      + '<td class="rv-td-title" title="' + esc(t.name) + '">' + esc(truncate(t.name, 40)) + '</td>'
      + '<td><span class="rv-tag rv-tag--fmt">' + esc(t.source_format) + '</span></td>'
      + '<td class="rv-td-num">' + (t.char_count || 0).toLocaleString() + '</td>'
      + '<td class="rv-td-num">' + (t.chunk_count || 0) + '</td>'
      + '<td>' + renderStatusBadge(t.chunk_status) + '</td>'
      + '<td class="rv-td-num">' + (t.req_count || 0) + '</td>'
      + '<td>' + renderDownloadLink(t, 'structured') + '</td>'
      + '<td>' + renderDownloadLink(t, 'testplan') + '</td>'
      + '<td>' + renderDownloadLink(t, 'original') + '</td>'
      + '<td class="rv-td-actions">'
        + '<button class="rv-btn-icon rv-btn-icon-sm rv-btn-danger" onclick="deleteRequirement(\'' + t.id + '\')" title="删除">'
          + '<svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><polyline points="3 6 5 6 21 6"/><path d="M19 6v14a2 2 0 0 1-2 2H7a2 2 0 0 1-2-2V6m3 0V4a2 2 0 0 1 2-2h4a2 2 0 0 1 2 2v2"/></svg>'
        + '</button>'
      + '</td>'
      + '</tr>';
  }).join('');
}

function renderDownloadLink(t, type) {
  if (type === 'original' && !t.file_path) return '<span class="rv-muted">—</span>';
  if (type === 'structured' && !t.structured_file) return '<span class="rv-muted">—</span>';
  if (type === 'testplan' && !t.test_plan_file) return '<span class="rv-muted">—</span>';
  var label = type === 'original' ? '源文件' : (type === 'structured' ? '结构化' : '测试计划');
  return '<a href="/api/v1/requirements/' + t.id + '/download/' + type + '" class="rv-link-download">'
    + '<svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><path d="M21 15v4a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2v-4"/><polyline points="7 10 12 15 17 10"/><line x1="12" y1="15" x2="12" y2="3"/></svg> '
    + label + '</a>';
}

function renderStatusBadge(status) {
  var map = {
    'pending':    ['rv-badge rv-badge-warning', '待处理'],
    'processing': ['rv-badge rv-badge-info', '处理中'],
    'completed':  ['rv-badge rv-badge-success', '已完成'],
    'failed':     ['rv-badge rv-badge-danger', '失败']
  };
  var m = map[status] || ['rv-badge rv-badge-neutral', status || '未知'];
  return '<span class="' + m[0] + '">' + m[1] + '</span>';
}

function deleteRequirement(id) {
  if (!confirm('确定要删除此需求记录吗？')) return;
  fetch('/api/v1/requirements/' + id, { method: 'DELETE' })
    .then(function(res) { return res.json(); })
    .then(function(data) {
      if (data.success) loadReqList();
      else alert('删除失败: ' + (data.error || '未知错误'));
    })
    .catch(function(e) { alert('网络错误: ' + (e.message || '')); });
}

// ═══════════════════════ Utils ═══════════════════

function esc(s) {
  if (!s) return '';
  return String(s).replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;').replace(/"/g, '&quot;');
}

function truncate(s, maxLen) {
  if (!s) return '';
  s = String(s);
  return s.length > maxLen ? s.substring(0, maxLen - 1) + '\u2026' : s;
}

function formatFileSize(bytes) {
  if (!bytes || bytes === 0) return '0 B';
  var k = 1024, sizes = ['B', 'KB', 'MB', 'GB'];
  var i = Math.floor(Math.log(bytes) / Math.log(k));
  i = Math.min(i, sizes.length - 1);
  return parseFloat((bytes / Math.pow(k, i)).toFixed(1)) + ' ' + sizes[i];
}
