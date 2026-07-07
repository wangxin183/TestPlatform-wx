/**
 * Case Library JS — import, create, auto-generate test cases
 * v1 — directory tree + 3-tab layout
 */

var CL = {
  tab: 'import',
  projectId: '',
  dirs: [],
  cases: [],
  selectedDirId: '',
  importFile: null
};

document.addEventListener('DOMContentLoaded', function() {
  loadCLProjects();
  loadCLDirectories();
  setupCLDropzone();
});

// ═══════════════════════ Tabs ═══════════════════

function switchCLTab(tab) {
  CL.tab = tab;
  document.querySelectorAll('.rv-tab').forEach(function(el) {
    el.classList.toggle('active', el.dataset.tab === tab);
  });
  document.querySelectorAll('.rv-tab-panel').forEach(function(el) {
    el.classList.toggle('active', el.id === 'tab-' + tab);
  });
}

// ═══════════════════════ Projects ═══════════════════

function loadCLProjects() {
  fetch('/api/v1/projects?size=100')
    .then(function(res) { return res.json(); })
    .then(function(data) {
      if (!data.success) return;
      var sel = document.getElementById('cl-project-select');
      sel.innerHTML = '<option value="">-- 请选择项目 --</option>';
      data.data.forEach(function(p) {
        sel.innerHTML += '<option value="' + p.id + '">' + esc(p.name) + '</option>';
      });
    });
}

function onCLProjectChange() {
  CL.projectId = document.getElementById('cl-project-select').value;
  loadCLDirectories();
  if (!CL.projectId) {
    document.getElementById('cl-case-list').innerHTML = '<tr><td colspan="6" class="rv-empty">请选择项目或目录查看用例</td></tr>';
    return;
  }
  loadCLCases('');
  loadCLTestPlans();
}

// ═══════════════════════ Directories ═══════════════════

function loadCLDirectories() {
  fetch('/api/v1/case-library/directories')
    .then(function(res) { return res.json(); })
    .then(function(data) {
      if (!data.success) return;
      CL.dirs = data.data || [];
      renderCLDirTree();
      renderCLDirSelects();
    });
}

function renderCLDirTree() {
  var el = document.getElementById('cl-dir-tree');
  var topDirs = CL.dirs.filter(function(d) { return !d.parent_id; });
  var subDirs = CL.dirs.filter(function(d) { return !!d.parent_id; });
  
  var html = '<div class="cl-dir-item' + (CL.selectedDirId === '' ? ' cl-dir-active' : '') + '" onclick="selectCLDir(\'\')">'
    + '<svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><path d="M22 19a2 2 0 0 1-2 2H4a2 2 0 0 1-2-2V5a2 2 0 0 1 2-2h5l2 3h9a2 2 0 0 1 2 2z"/></svg>'
    + ' 全部用例</div>';
  
  topDirs.forEach(function(d) {
    html += '<div class="cl-dir-item' + (CL.selectedDirId === d.id ? ' cl-dir-active' : '') + '" onclick="selectCLDir(\'' + d.id + '\')">'
      + '<svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><path d="M22 19a2 2 0 0 1-2 2H4a2 2 0 0 1-2-2V5a2 2 0 0 1 2-2h5l2 3h9a2 2 0 0 1 2 2z"/></svg>'
      + esc(d.name) + ' <span class="rv-group-n">' + (d.case_count || 0) + '</span>'
      + '<span class="cl-dir-actions">'
        + '<button class="rv-btn-icon" onclick="event.stopPropagation();editCLDir(\'' + d.id + '\',\'' + esc(d.name) + '\')" title="编辑"><svg width="12" height="12" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><path d="M11 4H4a2 2 0 0 0-2 2v14a2 2 0 0 0 2 2h14a2 2 0 0 0 2-2v-7"/><path d="M18.5 2.5a2.121 2.121 0 0 1 3 3L12 15l-4 1 1-4 9.5-9.5z"/></svg></button>'
        + '<button class="rv-btn-icon" onclick="event.stopPropagation();deleteCLDir(\'' + d.id + '\')" title="删除"><svg width="12" height="12" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><polyline points="3 6 5 6 21 6"/><path d="M19 6v14a2 2 0 0 1-2 2H7a2 2 0 0 1-2-2V6m3 0V4a2 2 0 0 1 2-2h4a2 2 0 0 1 2 2v2"/></svg></button>'
      + '</span>'
      + '</div>';
    subDirs.filter(function(s) { return s.parent_id === d.id; }).forEach(function(s) {
      html += '<div class="cl-dir-item cl-dir-sub' + (CL.selectedDirId === s.id ? ' cl-dir-active' : '') + '" onclick="selectCLDir(\'' + s.id + '\')">'
        + '<svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><path d="M22 19a2 2 0 0 1-2 2H4a2 2 0 0 1-2-2V5a2 2 0 0 1 2-2h5l2 3h9a2 2 0 0 1 2 2z"/></svg>'
        + esc(s.name) + ' <span class="rv-group-n">' + (s.case_count || 0) + '</span>'
        + '<span class="cl-dir-actions">'
          + '<button class="rv-btn-icon" onclick="event.stopPropagation();editCLDir(\'' + s.id + '\',\'' + esc(s.name) + '\')" title="编辑"><svg width="12" height="12" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><path d="M11 4H4a2 2 0 0 0-2 2v14a2 2 0 0 0 2 2h14a2 2 0 0 0 2-2v-7"/><path d="M18.5 2.5a2.121 2.121 0 0 1 3 3L12 15l-4 1 1-4 9.5-9.5z"/></svg></button>'
          + '<button class="rv-btn-icon" onclick="event.stopPropagation();deleteCLDir(\'' + s.id + '\')" title="删除"><svg width="12" height="12" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><polyline points="3 6 5 6 21 6"/><path d="M19 6v14a2 2 0 0 1-2 2H7a2 2 0 0 1-2-2V6m3 0V4a2 2 0 0 1 2-2h4a2 2 0 0 1 2 2v2"/></svg></button>'
        + '</span>'
        + '</div>';
    });
  });
  el.innerHTML = html || '<p class="rv-muted" style="font-size:12px;padding:8px">暂无目录</p>';
}

function renderCLDirSelects() {
  var selects = ['cl-import-dir', 'cl-create-dir', 'cl-gen-dir'];
  selects.forEach(function(sid) {
    var sel = document.getElementById(sid);
    if (!sel) return;
    var val = sel.value;
    sel.innerHTML = '<option value="">-- 根目录 --</option>';
    CL.dirs.forEach(function(d) {
      sel.innerHTML += '<option value="' + d.id + '"' + (val === d.id ? ' selected' : '') + '>' + esc(d.name) + '</option>';
    });
  });
}

function selectCLDir(dirId) {
  CL.selectedDirId = dirId;
  renderCLDirTree();
  loadCLCases(dirId);
}

function addCLDirectory() {
  showCLDirModal('新建目录', '', function(name) {
    if (!name) return;
    var parentId = CL.selectedDirId || '';
    var form = new FormData();
    form.append('name', name);
    if (parentId) form.append('parent_id', parentId);
    fetch('/api/v1/case-library/directories', { method: 'POST', body: form })
      .then(function(res) { return res.json(); })
      .then(function(data) {
        if (data.success) loadCLDirectories();
        else alert(data.error || '创建失败');
      });
  });
}

function editCLDir(id, oldName) {
  showCLDirModal('编辑目录', oldName, function(name) {
    if (!name || name === oldName) return;
    var form = new FormData();
    form.append('name', name);
    fetch('/api/v1/case-library/directories/' + id, { method: 'PUT', body: form })
      .then(function(res) { return res.json(); })
      .then(function(data) {
        if (data.success) loadCLDirectories();
        else alert(data.error || '编辑失败');
      });
  });
}

function deleteCLDir(id) {
  if (!confirm('删除目录后，目录中的用例将移至根目录。确定？')) return;
  fetch('/api/v1/case-library/directories/' + id, { method: 'DELETE' })
    .then(function(res) { return res.json(); })
    .then(function(data) {
      if (data.success) { loadCLDirectories(); loadCLCases(CL.selectedDirId); }
      else alert(data.error);
    });
}

// ═══════════════════════ Cases ═══════════════════

function loadCLCases(dirId) {
  var params = '?size=500';
  if (CL.projectId) params += '&project_id=' + encodeURIComponent(CL.projectId);
  if (dirId) params += '&directory_id=' + encodeURIComponent(dirId);
  
  fetch('/api/v1/case-library/cases' + params)
    .then(function(res) { return res.json(); })
    .then(function(data) {
      if (!data.success) return;
      CL.cases = data.data || [];
      renderCLCaseList();
    });
}

function renderCLCaseList() {
  var el = document.getElementById('cl-case-list');
  if (CL.cases.length === 0) {
    el.innerHTML = '<tr><td colspan="6" class="rv-empty">暂无用例</td></tr>';
    return;
  }
  el.innerHTML = CL.cases.map(function(c) {
    var sourceLabel = c.source === 'import' ? '导入' : (c.source === 'auto' ? '自动' : '手动');
    return '<tr>'
      + '<td><span class="rv-case-link" onclick="viewCLCase(\'' + c.id + '\')">' + esc(c.title) + '</span></td>'
      + '<td><span class="rv-type-tag">' + esc(c.test_type || '') + '</span></td>'
      + '<td>' + priorityBadge(c.priority) + '</td>'
      + '<td><span class="rv-badge rv-badge-neutral">' + sourceLabel + '</span></td>'
      + '<td class="rv-td-num">' + (c.steps ? c.steps.length : 0) + '</td>'
      + '<td class="rv-td-actions">'
        + '<button class="rv-btn-icon rv-btn-icon-sm" onclick="editCLCase(\'' + c.id + '\')" title="编辑"><svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><path d="M11 4H4a2 2 0 0 0-2 2v14a2 2 0 0 0 2 2h14a2 2 0 0 0 2-2v-7"/><path d="M18.5 2.5a2.121 2.121 0 0 1 3 3L12 15l-4 1 1-4 9.5-9.5z"/></svg></button>'
        + '<button class="rv-btn-icon rv-btn-icon-sm rv-btn-danger" onclick="deleteCLCase(\'' + c.id + '\')" title="删除"><svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><polyline points="3 6 5 6 21 6"/><path d="M19 6v14a2 2 0 0 1-2 2H7a2 2 0 0 1-2-2V6m3 0V4a2 2 0 0 1 2-2h4a2 2 0 0 1 2 2v2"/></svg></button>'
      + '</td>'
      + '</tr>';
  }).join('');
}

function viewCLCase(id) {
  editCLCase(id);
}

function editCLCase(id) {
  var c = CL.cases.find(function(x) { return x.id === id; });
  if (!c) return;

  // Close any existing edit modal first
  closeCLCaseEditModal();

  // Build steps text from JSON array
  var stepsText = '';
  if (c.steps && Array.isArray(c.steps)) {
    stepsText = c.steps.map(function(s) {
      return (s.step || '') + '. ' + (s.action || '') + ' | ' + (s.expected || '');
    }).join('\n');
  }

  // Build directory options
  var dirOpts = '<option value="">-- 根目录 --</option>';
  CL.dirs.forEach(function(d) {
    var sel = c.directory_id === d.id ? ' selected' : '';
    dirOpts += '<option value="' + d.id + '"' + sel + '>' + esc(d.name) + '</option>';
  });

  var tagsVal = (c.tags && Array.isArray(c.tags)) ? c.tags.join(',') : '';

  var overlay = document.createElement('div');
  overlay.className = 'rv-modal-overlay';
  overlay.id = 'cl-case-edit-overlay';
  overlay.innerHTML =
    '<div class="rv-modal-glass" onclick="closeCLCaseEditModal()"></div>'
    + '<div class="rv-modal" style="max-width:680px">'
    // ── Header ──
    + '<div class="rv-modal-header">'
      + '<div style="display:flex;align-items:flex-start;gap:14px">'
        + '<div class="rv-header-icon-wrap" style="margin-top:2px">'
          + '<svg width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><path d="M11 4H4a2 2 0 0 0-2 2v14a2 2 0 0 0 2 2h14a2 2 0 0 0 2-2v-7"/><path d="M18.5 2.5a2.121 2.121 0 0 1 3 3L12 15l-4 1 1-4 9.5-9.5z"/></svg>'
        + '</div>'
        + '<div>'
          + '<span class="rv-modal-kicker">编辑用例</span>'
          + '<span class="rv-modal-title">' + esc(c.title) + '</span>'
        + '</div>'
      + '</div>'
      + '<button class="rv-btn rv-btn-icon" onclick="closeCLCaseEditModal()" style="flex-shrink:0">'
        + '<svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><line x1="18" y1="6" x2="6" y2="18"/><line x1="6" y1="6" x2="18" y2="18"/></svg>'
      + '</button>'
    + '</div>'
    // ── Body ──
    + '<div class="rv-modal-body" style="display:flex;flex-direction:column;gap:14px">'
      // Title
      + '<div><span class="rv-selector-label">用例标题 *</span>'
        + '<input class="rv-input" id="cle-title" value="' + esc(c.title || '') + '" placeholder="输入用例标题">'
      + '</div>'
      // Description
      + '<div><span class="rv-selector-label">描述</span>'
        + '<textarea class="rv-textarea" id="cle-desc" rows="2" placeholder="用例描述">' + esc(c.description || '') + '</textarea>'
      + '</div>'
      // Preconditions
      + '<div><span class="rv-selector-label">前置条件</span>'
        + '<textarea class="rv-textarea" id="cle-precond" rows="2" placeholder="前置条件">' + esc(c.preconditions || '') + '</textarea>'
      + '</div>'
      // Type + Priority row
      + '<div style="display:flex;gap:16px">'
        + '<div style="flex:1"><span class="rv-selector-label">测试类型</span>'
          + '<select class="rv-select" id="cle-type">'
            + '<option value="ui"' + (c.test_type === 'ui' ? ' selected' : '') + '>UI</option>'
            + '<option value="api"' + (c.test_type === 'api' ? ' selected' : '') + '>API</option>'
            + '<option value="performance"' + (c.test_type === 'performance' ? ' selected' : '') + '>性能</option>'
            + '<option value="security"' + (c.test_type === 'security' ? ' selected' : '') + '>安全</option>'
            + '<option value="compatibility"' + (c.test_type === 'compatibility' ? ' selected' : '') + '>兼容</option>'
          + '</select>'
        + '</div>'
        + '<div style="flex:1"><span class="rv-selector-label">优先级</span>'
          + '<select class="rv-select" id="cle-priority">'
            + '<option value="严重"' + (c.priority === '严重' ? ' selected' : '') + '>严重</option>'
            + '<option value="高"' + (c.priority === '高' ? ' selected' : '') + '>高</option>'
            + '<option value="中"' + (c.priority === '中' ? ' selected' : '') + '>中</option>'
            + '<option value="低"' + (c.priority === '低' ? ' selected' : '') + '>低</option>'
          + '</select>'
        + '</div>'
      + '</div>'
      // Directory
      + '<div><span class="rv-selector-label">归属目录</span>'
        + '<select class="rv-select" id="cle-dir">' + dirOpts + '</select>'
      + '</div>'
      // Steps
      + '<div><span class="rv-selector-label">测试步骤 (每行: "序号. 操作 | 预期结果")</span>'
        + '<textarea class="rv-textarea" id="cle-steps" rows="5" placeholder="1. 点击登录按钮 | 跳转到登录页\n2. 输入用户名密码 | 提示登录成功">' + esc(stepsText) + '</textarea>'
      + '</div>'
      // Tags
      + '<div><span class="rv-selector-label">标签 (逗号分隔)</span>'
        + '<input class="rv-input" id="cle-tags" value="' + esc(tagsVal) + '" placeholder="冒烟,回归">'
      + '</div>'
    + '</div>'
    // ── Footer ──
    + '<div class="rv-modal-footer">'
      + '<div class="rv-modal-footer-left"></div>'
      + '<div class="rv-modal-footer-right">'
        + '<button class="rv-btn rv-btn-ghost" onclick="closeCLCaseEditModal()">取消</button>'
        + '<button class="rv-btn rv-btn-accent" onclick="saveCLCaseEdit(\'' + id + '\')">'
          + '<svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><polyline points="20 6 9 17 4 12"/></svg>'
          + ' 保存修改'
        + '</button>'
      + '</div>'
    + '</div>'
    + '</div>';
  document.body.appendChild(overlay);
  overlay.classList.add('open');
}

function closeCLCaseEditModal() {
  var el = document.getElementById('cl-case-edit-overlay');
  if (el) el.remove();
}

function saveCLCaseEdit(id) {
  var title = document.getElementById('cle-title').value.trim();
  if (!title) { alert('请输入用例标题'); return; }

  var stepsRaw = document.getElementById('cle-steps').value.trim();
  var stepsArr = [];
  if (stepsRaw) {
    var lines = stepsRaw.split('\n');
    lines.forEach(function(line) {
      var parts = line.split('|');
      var action = (parts[0] || '').replace(/^\d+\.\s*/, '').trim();
      var expected = (parts[1] || '').trim();
      if (action || expected) {
        stepsArr.push({ step: stepsArr.length + 1, action: action, expected: expected });
      }
    });
  }

  var form = new FormData();
  form.append('title', title);
  form.append('description', document.getElementById('cle-desc').value.trim());
  form.append('preconditions', document.getElementById('cle-precond').value.trim());
  form.append('test_type', document.getElementById('cle-type').value);
  form.append('priority', document.getElementById('cle-priority').value);
  form.append('directory_id', document.getElementById('cle-dir').value);
  form.append('steps', JSON.stringify(stepsArr));
  form.append('tags', JSON.stringify(document.getElementById('cle-tags').value.split(',').map(function(s) { return s.trim(); }).filter(Boolean)));

  fetch('/api/v1/case-library/cases/' + id, { method: 'PUT', body: form })
    .then(function(res) { return res.json(); })
    .then(function(data) {
      if (data.success) {
        closeCLCaseEditModal();
        loadCLCases(CL.selectedDirId);
        loadCLDirectories();
      } else {
        alert(data.error || '保存失败');
      }
    });
}

function deleteCLCase(id) {
  if (!confirm('确定删除？')) return;
  fetch('/api/v1/case-library/cases/' + id, { method: 'DELETE' })
    .then(function(res) { return res.json(); })
    .then(function(data) {
      if (data.success) loadCLCases(CL.selectedDirId);
      else alert(data.error);
    });
}

// ═══════════════════════ Import ═══════════════════

function setupCLDropzone() {
  var dz = document.getElementById('cl-dropzone');
  if (!dz) return;
  dz.addEventListener('dragover', function(e) { e.preventDefault(); dz.classList.add('rv-dropzone--over'); });
  dz.addEventListener('dragleave', function() { dz.classList.remove('rv-dropzone--over'); });
  dz.addEventListener('drop', function(e) {
    e.preventDefault(); dz.classList.remove('rv-dropzone--over');
    if (e.dataTransfer.files.length > 0) setCLFile(e.dataTransfer.files[0]);
  });
}

function onCLFileSelected() {
  var input = document.getElementById('cl-file-input');
  if (input.files && input.files.length > 0) setCLFile(input.files[0]);
}

function setCLFile(file) {
  CL.importFile = file;
  document.getElementById('cl-file-info').style.display = 'flex';
  document.getElementById('cl-dropzone').style.display = 'none';
  document.getElementById('cl-file-name').textContent = file.name;
  document.getElementById('cl-file-size').textContent = formatFileSize(file.size);
}

function clearCLFile() {
  CL.importFile = null;
  document.getElementById('cl-file-info').style.display = 'none';
  document.getElementById('cl-dropzone').style.display = 'block';
  document.getElementById('cl-file-input').value = '';
}

function importCases() {
  if (!CL.importFile) { showMsg('cl-import-msg', '请选择文件', true); return; }
  
  var form = new FormData();
  form.append('file', CL.importFile);
  // Don't pass directory_id here - save to root first, user selects dir after
  
  showMsg('cl-import-msg', '解析中...');
  fetch('/api/v1/case-library/cases/import', { method: 'POST', body: form })
    .then(function(res) { return res.json(); })
    .then(function(data) {
      if (data.success) {
        showMsg('cl-import-msg', '已解析 ' + data.data.length + ' 条用例，选择目录保存', false);
        clearCLFile();
        // Show save-to-directory dialog
        showCLSaveDirModal(data.data);
      } else {
        showMsg('cl-import-msg', data.error || '导入失败', true);
      }
    })
    .catch(function(e) { showMsg('cl-import-msg', '网络错误', true); });
}

function showCLSaveDirModal(cases) {
  var overlay = document.createElement('div');
  overlay.className = 'rv-modal-overlay';
  var dirs = CL.dirs.map(function(d) { return '<option value="' + d.id + '">' + esc(d.name) + '</option>'; }).join('');
  overlay.innerHTML = '<div class="rv-modal-glass" onclick="this.parentElement.remove()"></div>'
    + '<div class="rv-modal" style="max-width:400px">'
    + '<div class="rv-modal-header"><span class="rv-modal-kicker">选择目录</span></div>'
    + '<p style="font-size:13px;margin:8px 0">将 ' + cases.length + ' 条用例保存到:</p>'
    + '<select class="rv-select" id="cl-save-dir-sel"><option value="">-- 根目录 --</option>' + dirs + '</select>'
    + '<div class="rv-modal-footer">'
    + '<div class="rv-modal-footer-left"></div>'
    + '<div class="rv-modal-footer-right">'
    + '<button class="rv-btn rv-btn-ghost" onclick="this.closest(\'.rv-modal-overlay\').remove()">取消</button>'
    + '<button class="rv-btn rv-btn-accent" id="cl-save-dir-ok">保存</button>'
    + '</div></div></div>';
  document.body.appendChild(overlay);
  overlay.querySelector('#cl-save-dir-ok').onclick = function() {
    var dirId = overlay.querySelector('#cl-save-dir-sel').value;
    overlay.remove();
    saveImportedCases(cases, dirId);
  };
}

function saveImportedCases(cases, dirId) {
  if (!dirId) { loadCLDirectories(); loadCLCases(CL.selectedDirId); return; }
  var done = 0;
  cases.forEach(function(c) {
    var form = new FormData();
    form.append('directory_id', dirId);
    fetch('/api/v1/case-library/cases/' + c.id, { method: 'PUT', body: form })
      .then(function() { done++; if (done === cases.length) { loadCLDirectories(); loadCLCases(CL.selectedDirId); } });
  });
}

// ═══════════════════════ Create ═══════════════════

function createCase() {
  var title = document.getElementById('cl-create-title').value.trim();
  if (!title) { showMsg('cl-create-msg', '请输入标题', true); return; }
  
  var stepsText = document.getElementById('cl-create-steps').value.trim();
  var steps = [];
  if (stepsText) {
    stepsText.split('\\n').forEach(function(line) {
      var m = line.match(/^\\d+[.、]\\s*(.+)/);
      if (m) {
        var parts = m[1].split('|');
        steps.push({ step: steps.length + 1, action: (parts[0] || '').trim(), expected: (parts[1] || '').trim() });
      }
    });
  }
  if (steps.length === 0) steps = [{ step: 1, action: title, expected: '' }];

  var form = new FormData();
  form.append('title', title);
  form.append('project_id', CL.projectId);
  form.append('description', document.getElementById('cl-create-desc').value);
  form.append('preconditions', document.getElementById('cl-create-precond').value);
  form.append('steps', JSON.stringify(steps));
  form.append('test_type', document.getElementById('cl-create-type').value);
  form.append('priority', document.getElementById('cl-create-priority').value);
  var dirId = document.getElementById('cl-create-dir').value;
  if (dirId) form.append('directory_id', dirId);

  fetch('/api/v1/case-library/cases', { method: 'POST', body: form })
    .then(function(res) { return res.json(); })
    .then(function(data) {
      if (data.success) {
        showMsg('cl-create-msg', '创建成功', false);
        document.getElementById('cl-create-title').value = '';
        document.getElementById('cl-create-steps').value = '';
        loadCLDirectories();
        loadCLCases(CL.selectedDirId);
      } else {
        showMsg('cl-create-msg', data.error || '创建失败', true);
      }
    });
}

// ═══════════════════════ Auto Generate ═══════════════════

function loadCLTestPlans() {
  if (!CL.projectId) return;
  fetch('/api/v1/requirements?project_id=' + encodeURIComponent(CL.projectId) + '&status=completed&size=50')
    .then(function(res) { return res.json(); })
    .then(function(data) {
      var sel = document.getElementById('cl-gen-plan');
      sel.innerHTML = '<option value="">-- 请选择测试计划 --</option>';
      (data.data || []).forEach(function(t) {
        if (t.test_plan_file) {
          var planName = t.test_plan_file.split('/').pop();
          sel.innerHTML += '<option value="' + t.id + '">' + esc(planName) + '</option>';
        }
      });
      if (sel.options.length <= 1) sel.innerHTML += '<option value="" disabled>暂无已完成的测试计划</option>';
    });
}

function generateCases() {
  if (!CL.projectId) { showMsg('cl-gen-msg', '请先选择项目', true); return; }
  var planId = document.getElementById('cl-gen-plan').value;
  if (!planId) { showMsg('cl-gen-msg', '请选择测试计划', true); return; }
  
  var form = new FormData();
  form.append('project_id', CL.projectId);
  form.append('test_plan_id', planId);
  var dirId = document.getElementById('cl-gen-dir').value;
  if (dirId) form.append('directory_id', dirId);
  
  showMsg('cl-gen-msg', '生成中（约1-2分钟）...');
  fetch('/api/v1/case-library/cases/generate', { method: 'POST', body: form })
    .then(function(res) { return res.json(); })
    .then(function(data) {
      if (data.success) {
        showMsg('cl-gen-msg', '已启动，请稍后刷新列表查看', false);
      } else {
        showMsg('cl-gen-msg', data.error || '启动失败', true);
      }
    });
}


// ═══════════════════════ Directory Modal ═══════════════════

function showCLDirModal(title, defaultName, onConfirm) {
  var overlay = document.createElement('div');
  overlay.className = 'rv-modal-overlay';
  overlay.innerHTML = '<div class="rv-modal-glass" onclick="this.parentElement.remove()"></div>'
    + '<div class="rv-modal" style="max-width:360px">'
    + '<div class="rv-modal-header"><span class="rv-modal-kicker">' + title + '</span></div>'
    + '<input class="rv-input" id="cl-dir-modal-input" value="' + esc(defaultName) + '" placeholder="目录名称" style="margin:16px 0">'
    + '<div class="rv-modal-footer">'
    + '<div class="rv-modal-footer-left"></div>'
    + '<div class="rv-modal-footer-right">'
    + '<button class="rv-btn rv-btn-ghost" onclick="this.closest(\'.rv-modal-overlay\').remove()">取消</button>'
    + '<button class="rv-btn rv-btn-accent" id="cl-dir-modal-ok">确定</button>'
    + '</div></div></div>';
  document.body.appendChild(overlay);
  overlay.classList.add('open');
  var input = overlay.querySelector('#cl-dir-modal-input');
  input.focus(); input.select();
  overlay.querySelector('#cl-dir-modal-ok').onclick = function() {
    var v = input.value.trim();
    overlay.remove();
    onConfirm(v);
  };
  input.addEventListener('keydown', function(e) { if (e.key === 'Enter') { overlay.querySelector('#cl-dir-modal-ok').click(); } });
}

// ═══════════════════════ Utils ═══════════════════

function resetCL() {
  CL.dirs = []; CL.cases = []; CL.selectedDirId = '';
  document.getElementById('cl-dir-tree').innerHTML = '';
  document.getElementById('cl-case-list').innerHTML = '<tr><td colspan="6" class="rv-empty">请选择项目</td></tr>';
}

function showMsg(elId, text, isError) {
  var el = document.getElementById(elId);
  if (!el) return;
  el.textContent = text;
  el.className = 'rv-upload-msg' + (isError ? ' rv-upload-msg--error' : ' rv-upload-msg--success');
  if (!isError) setTimeout(function() { el.textContent = ''; el.className = 'rv-upload-msg'; }, 3000);
}

function priorityBadge(p) {
  var map = { '严重': 'rv-badge-danger', '高': 'rv-badge-warning', '中': 'rv-badge-info', '低': 'rv-badge-neutral' };
  return '<span class="rv-badge ' + (map[p] || 'rv-badge-neutral') + '">' + (p || '中') + '</span>';
}

function esc(s) {
  if (!s) return '';
  return String(s).replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;').replace(/"/g, '&quot;');
}

function formatFileSize(bytes) {
  if (!bytes || bytes === 0) return '0 B';
  var k = 1024, sizes = ['B', 'KB', 'MB', 'GB'];
  var i = Math.floor(Math.log(bytes) / Math.log(k));
  return parseFloat((bytes / Math.pow(k, i)).toFixed(1)) + ' ' + sizes[Math.min(i, sizes.length - 1)];
}
