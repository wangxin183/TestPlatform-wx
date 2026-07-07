/* Stage tools — standalone stage execution page */

var projectId = window.location.pathname.split('/')[2];
var currentStage = (function () {
  var m = window.location.search.match(/stage=(\w+)/);
  return m ? m[1] : 'ingestion';
})();

// ── Stage configuration ──

var STAGE_CONFIG = {
  ingestion: {
    label: '文档提取',
    uploadLabel: '上传文档',
    projectData: null,  // no project data selection
    description: '将上传的文档提取为统一的文本格式，供后续阶段使用。'
  },
  parsing: {
    label: '文档解析',
    uploadLabel: '上传文档',
    projectData: { type: 'documents' },
    description: '对文档进行分段解析，提取功能需求、非功能需求、风险点和测试点。'
  },
  analysis: {
    label: '需求分析',
    uploadLabel: '上传文档/切片数据',
    projectData: { type: 'parsed_requirements' },
    description: '基于解析出的需求数据，通过 LLM 生成完整的测试计划。'
  },
  generation: {
    label: '用例生成',
    uploadLabel: '上传文档/测试计划',
    projectData: { type: 'test_plan' },
    description: '根据需求分析和测试计划，生成详细的可执行测试用例。'
  },
  review: {
    label: '用例评审',
    uploadLabel: '上传文档',
    projectData: { type: 'test_cases_all' },
    description: '对生成的测试用例进行人工评审，逐个审批或驳回。'
  },
  execution: {
    label: '用例执行',
    uploadLabel: '上传文档',
    projectData: { type: 'test_cases_approved' },
    description: '将审批通过的测试用例分发到目标平台执行。（需要全部用例审批通过）'
  }
};

var uploadedFiles = [];  // { id, filename, file_path }
var selectedProjectData = {};  // context fields selected from project data

// ── Init ──

(function () {
  var cfg = STAGE_CONFIG[currentStage] || STAGE_CONFIG.ingestion;
  document.getElementById('tool-project-link').href = '/projects/' + projectId;
  document.getElementById('tool-project-link').textContent = '项目';
  document.getElementById('tool-stage-label').textContent = '— ' + cfg.label;

  renderStageTabs();
  switchInputMode();

  // Load project name for breadcrumb
  api.get('/projects/' + projectId).then(function (r) {
    document.getElementById('tool-project-link').textContent = r.data.name || '项目';
  }).catch(function () {});

  // Show project data option if stage supports it
  if (cfg.projectData) {
    document.getElementById('project-data-option').style.display = '';
  }
})();

// ── Stage tabs ──

function renderStageTabs() {
  var tabs = document.getElementById('stage-tabs');
  var stages = Object.keys(STAGE_CONFIG);
  tabs.innerHTML = stages.map(function (name) {
    var cfg = STAGE_CONFIG[name];
    var cls = (name === currentStage) ? 'tab active' : 'tab';
    return '<a class="' + cls + '" href="?stage=' + name + '">' + cfg.label + '</a>';
  }).join('');
}

// ── Input mode switch ──

function switchInputMode() {
  var mode = document.querySelector('input[name="input-mode"]:checked').value;
  document.getElementById('upload-mode').style.display = mode === 'upload' ? '' : 'none';
  document.getElementById('project-mode').style.display = mode === 'project' ? '' : 'none';

  if (mode === 'project') {
    loadProjectData();
  }

  updateRunButton();
}

// ── File upload ──

async function uploadToolFile() {
  var fileInput = document.getElementById('tool-file-input');
  var file = fileInput.files[0];
  if (!file) { showToast('请选择文件', 'error'); return; }

  var form = new FormData();
  form.append('file', file);
  try {
    var resp = await api.upload('/projects/' + projectId + '/documents', form);
    uploadedFiles.push(resp.data);
    fileInput.value = '';
    renderUploadedSources();
    updateRunButton();
    showToast('文件已上传', 'success');
  } catch (e) { showToast('上传失败: ' + e.message, 'error'); }
}

function addUrlSource() {
  var urlInput = document.getElementById('tool-url-input');
  var url = urlInput.value.trim();
  if (!url) { showToast('请输入 URL 链接', 'error'); return; }

  // Treat URL as a document source
  uploadedFiles.push({
    id: 'url_' + Date.now(),
    filename: url.split('/').pop() || url,
    file_path: url,
    is_url: true
  });
  urlInput.value = '';
  renderUploadedSources();
  updateRunButton();
  showToast('链接已添加', 'success');
}

function removeUploadedSource(idx) {
  uploadedFiles.splice(idx, 1);
  renderUploadedSources();
  updateRunButton();
}

function renderUploadedSources() {
  var list = document.getElementById('upload-source-list');
  if (!uploadedFiles.length) { list.innerHTML = ''; return; }
  list.innerHTML = '<div class="text-sm mb-sm" style="font-weight:500">已添加的数据源：</div>' +
    uploadedFiles.map(function (f, i) {
      var label = f.is_url ? (f.file_path.length > 60 ? f.file_path.slice(0, 60) + '...' : f.file_path) : f.filename;
      var badge = f.is_url ? '<span class="badge badge-info">URL</span>' : '<span class="badge badge-neutral">文件</span>';
      return '<div class="flex-between" style="padding:4px 0;border-bottom:1px solid var(--color-border)">' +
        '<span>' + badge + ' ' + esc(label) + '</span>' +
        '<button class="btn btn-sm btn-outline" onclick="removeUploadedSource(' + i + ')" style="color:#e53e3e">移除</button>' +
      '</div>';
    }).join('');
}

// ── Project data loading ──

async function loadProjectData() {
  var cfg = STAGE_CONFIG[currentStage];
  if (!cfg.projectData) return;

  var container = document.getElementById('project-data-list');
  container.innerHTML = '<span class="text-muted">加载中...</span>';

  try {
    switch (cfg.projectData.type) {
      case 'documents':
        await loadDocumentsData(container);
        break;
      case 'parsed_requirements':
        await loadParsedRequirementsData(container);
        break;
      case 'test_plan':
        await loadTestPlanData(container);
        break;
      case 'test_cases_all':
        await loadTestCasesData(container, null);
        break;
      case 'test_cases_approved':
        await loadTestCasesData(container, 'approved');
        break;
      default:
        container.innerHTML = '<span class="text-muted">无可选项目数据</span>';
    }
  } catch (e) {
    container.innerHTML = '<span class="text-muted">加载失败: ' + e.message + '</span>';
  }
}

async function loadDocumentsData(container) {
  var resp = await api.get('/projects/' + projectId + '/documents');
  var docs = resp.data || [];
  if (!docs.length) { container.innerHTML = '<span class="text-muted">暂无已上传文档</span>'; return; }
  container.innerHTML = docs.map(function (d) {
    return '<label class="checkbox-item">' +
      '<input type="checkbox" value="' + d.id + '" data-type="document" data-filepath="' + esc(d.file_path) + '" onchange="onProjectDataChange()">' +
      ' <span>' + esc(d.filename) + '</span> <span class="badge badge-neutral">' + i18n.t('documentStatus', d.status) + '</span>' +
    '</label>';
  }).join('');
}

async function loadParsedRequirementsData(container) {
  // Look for parsed_requirements in pipeline stage logs
  var pipelinesResp = await api.get('/projects/' + projectId + '/pipelines?size=20');
  var pipelines = pipelinesResp.data || [];
  var found = false;

  for (var i = 0; i < pipelines.length; i++) {
    var stagesResp = await api.get('/pipelines/' + pipelines[i].id + '/stages');
    var stages = stagesResp.data || [];
    for (var j = 0; j < stages.length; j++) {
      var log = stages[j];
      if (log.stage_name === 'parsing' && log.status === 'completed' && log.output_data) {
        found = true;
        var label = '流水线 #' + pipelines[i].id.slice(0, 8) + ' — 解析结果';
        container.innerHTML += '<label class="checkbox-item">' +
          '<input type="checkbox" value="' + pipelines[i].id + '" data-type="parsed_req" onchange="onProjectDataChange()">' +
          ' <span>' + label + '</span>' +
          ' <span class="badge badge-success">已完成</span>' +
        '</label>';
      }
    }
  }
  if (!found) { container.innerHTML = '<span class="text-muted">暂无解析结果，请先运行文档解析阶段</span>'; }
}

async function loadTestPlanData(container) {
  var pipelinesResp = await api.get('/projects/' + projectId + '/pipelines?size=20');
  var pipelines = pipelinesResp.data || [];
  var found = false;

  for (var i = 0; i < pipelines.length; i++) {
    var stagesResp = await api.get('/pipelines/' + pipelines[i].id + '/stages');
    var stages = stagesResp.data || [];
    for (var j = 0; j < stages.length; j++) {
      var log = stages[j];
      if (log.stage_name === 'analysis' && log.status === 'completed' && log.output_data) {
        found = true;
        var data = log.output_data;
        var planFile = data.test_plan_file || '';
        var label = '流水线 #' + pipelines[i].id.slice(0, 8) + ' — 测试计划';
        if (planFile) {
          label += ' <a href="/api/v1/files/download?path=' + encodeURIComponent(planFile) + '" download class="text-sm">下载</a>';
        }
        container.innerHTML += '<label class="checkbox-item">' +
          '<input type="checkbox" value="' + pipelines[i].id + '" data-type="test_plan" onchange="onProjectDataChange()">' +
          ' <span>' + label + '</span>' +
          ' <span class="badge badge-success">已完成</span>' +
        '</label>';
      }
    }
  }
  if (!found) { container.innerHTML = '<span class="text-muted">暂无测试计划，请先运行需求分析阶段</span>'; }
}

async function loadTestCasesData(container, requiredStatus) {
  var params = '?pipeline_id=&size=500';  // get all test cases for project
  var resp = await api.get('/projects/' + projectId + '/test-cases' + params);
  var cases = resp.data || [];

  if (requiredStatus) {
    cases = cases.filter(function (tc) { return tc.status === requiredStatus; });
  }

  if (!cases.length) {
    var hint = requiredStatus === 'approved' ? '暂无审批通过的用例，请先在用例评审阶段审批' : '暂无测试用例';
    container.innerHTML = '<span class="text-muted">' + hint + '</span>';
    return;
  }

  // For execution stage: check if ALL are approved
  var allApproved = true;
  if (STAGE_CONFIG[currentStage].projectData.type === 'test_cases_approved') {
    var allResp = await api.get('/projects/' + projectId + '/test-cases?size=500');
    var allCases = allResp.data || [];
    var notApproved = allCases.filter(function (tc) { return tc.status !== 'approved'; });
    if (notApproved.length > 0) {
      allApproved = false;
      container.innerHTML += '<div class="alert alert-warning mb-sm">' +
        '还有 ' + notApproved.length + ' 条用例未审批通过，需要全部审批通过才能执行</div>';
    }
  }

  container.innerHTML += cases.map(function (tc) {
    return '<label class="checkbox-item">' +
      '<input type="checkbox" value="' + tc.id + '" data-type="test_case"' +
        (allApproved ? '' : ' disabled') + ' onchange="onProjectDataChange()">' +
      ' <span>' + esc(tc.title) + '</span>' +
      ' <span class="badge ' + (tc.priority === 'critical' ? 'badge-danger' : tc.priority === 'high' ? 'badge-warning' : 'badge-neutral') + '">' + i18n.t('priority', tc.priority) + '</span>' +
      ' <span class="badge ' + (tc.status === 'approved' ? 'badge-success' : 'badge-neutral') + '">' + i18n.t('testCaseStatus', tc.status) + '</span>' +
    '</label>';
  }).join('');
}

function onProjectDataChange() {
  updateRunButton();
}

// ── Run button state ──

function updateRunButton() {
  var btn = document.getElementById('btn-run-stage');
  var hint = document.getElementById('run-hint');
  var mode = document.querySelector('input[name="input-mode"]:checked').value;

  if (mode === 'upload') {
    if (uploadedFiles.length > 0) {
      btn.disabled = false;
      hint.textContent = '已选择 ' + uploadedFiles.length + ' 个数据源';
    } else {
      btn.disabled = true;
      hint.textContent = '请上传文件或添加 URL 链接';
    }
  } else {
    // Project data mode
    var checked = document.querySelectorAll('#project-data-list input[type="checkbox"]:checked');
    if (checked.length > 0) {
      btn.disabled = false;
      hint.textContent = '已选择 ' + checked.length + ' 项数据';
    } else {
      btn.disabled = true;
      hint.textContent = '请选择项目数据';
    }
  }
}

// ── Run stage ──

async function runStage() {
  var cfg = STAGE_CONFIG[currentStage];
  var mode = document.querySelector('input[name="input-mode"]:checked').value;

  // Build context based on input mode and stage
  var context = {};
  if (mode === 'upload') {
    // Build raw_texts from uploaded files
    var rawTexts = {};
    for (var i = 0; i < uploadedFiles.length; i++) {
      var f = uploadedFiles[i];
      if (f.is_url) {
        rawTexts[f.id] = f.file_path;  // URL passed as raw_text
      } else {
        rawTexts[f.id] = f.file_path;  // stored file path
      }
    }
    context.raw_texts = rawTexts;
    context.document_ids = uploadedFiles.filter(function (f) { return !f.is_url; }).map(function (f) { return f.id; });
  } else {
    // Project data mode — collect selected data
    var checked = document.querySelectorAll('#project-data-list input[type="checkbox"]:checked');
    var types = {};
    for (var i = 0; i < checked.length; i++) {
      var type = checked[i].getAttribute('data-type');
      if (!types[type]) types[type] = [];
      types[type].push(checked[i].value);
    }

    switch (cfg.projectData.type) {
      case 'documents':
        context.document_ids = types.document || [];
        break;
      case 'parsed_requirements':
        // Will be auto-loaded from pipeline context when running
        context.document_ids = [];
        break;
      case 'test_plan':
        // test plan is a file path in context
        break;
      case 'test_cases_all':
      case 'test_cases_approved':
        context.approved_test_case_ids = types.test_case || [];
        break;
    }
  }

  var btn = document.getElementById('btn-run-stage');
  btn.disabled = true;
  btn.textContent = '运行中...';
  hint.textContent = '正在执行 ' + cfg.label + '...';

  // Hide previous output
  document.getElementById('output-panel').style.display = 'none';

  try {
    var resp = await api.post('/stages/' + currentStage + '/run', {
      project_id: projectId,
      context: context
    });

    var outputPanel = document.getElementById('output-panel');
    outputPanel.style.display = '';

    if (resp.success) {
      document.getElementById('output-status').innerHTML =
        '<span class="badge badge-success" style="font-size:14px">执行成功</span>';
    } else {
      document.getElementById('output-status').innerHTML =
        '<span class="badge badge-danger" style="font-size:14px">执行失败</span>' +
        (resp.data.error ? '<p class="text-sm" style="color:#e53e3e;margin-top:8px">' + esc(resp.data.error) + '</p>' : '');
    }

    // Render output data
    var output = resp.data.output || {};
    var ctx = resp.data.context || {};
    document.getElementById('output-data').innerHTML = renderStageOutput(output, ctx);

    showToast(cfg.label + ' 执行完成', resp.success ? 'success' : 'error');
  } catch (e) {
    document.getElementById('output-panel').style.display = '';
    document.getElementById('output-status').innerHTML =
      '<span class="badge badge-danger" style="font-size:14px">请求失败</span>';
    document.getElementById('output-data').innerHTML =
      '<p style="color:#e53e3e">' + esc(e.message) + '</p>';
    showToast('运行失败: ' + e.message, 'error');
  } finally {
    btn.disabled = false;
    btn.textContent = '▶ 运行阶段';
  }
}

// ── Output rendering (stage-specific) ──

function renderStageOutput(output, context) {
  var html = '';

  switch (currentStage) {
    case 'ingestion':
      var texts = context.raw_texts || output.raw_texts || output;
      var docCount = Object.keys(texts).length;
      html += '<div class="stat-row"><span>文档数：</span><strong>' + docCount + '</strong></div>';
      html += '<h4>提取内容预览</h4>';
      Object.keys(texts).forEach(function (k) {
        var preview = String(texts[k]).slice(0, 300);
        html += '<details class="mb-sm"><summary>' + esc(k.split('/').pop()) + '</summary>' +
          '<pre class="pre-block">' + esc(preview) + (texts[k].length > 300 ? '...' : '') + '</pre></details>';
      });
      break;

    case 'parsing':
      html += '<div class="stat-row"><span>解析文档数：</span><strong>' + (output.documents_parsed || '-') + '</strong></div>';
      html += '<div class="stat-row"><span>需求数：</span><strong>' + (output.requirements_count || '-') + '</strong></div>';
      html += '<div class="stat-row"><span>风险点：</span><strong>' + (output.risks_count || '-') + '</strong></div>';
      if (output.chunk_files && output.chunk_files.length) {
        html += '<h4>切片文件</h4><ul>';
        output.chunk_files.forEach(function (f) {
          html += '<li><a href="/api/v1/files/download?path=' + encodeURIComponent(f) + '" download>' + esc(f.split('/').pop()) + '</a></li>';
        });
        html += '</ul>';
      }
      break;

    case 'analysis':
      if (output.skill_loaded) {
        html += '<div class="stat-row"><span>Skill 状态：</span><strong>' + (output.skill_source || '已加载') + '</strong></div>';
      }
      if (context.test_plan_file || output.test_plan_file) {
        var f = context.test_plan_file || output.test_plan_file;
        html += '<div class="stat-row"><span>测试计划文件：</span>' +
          '<a href="/api/v1/files/download?path=' + encodeURIComponent(f) + '" download class="btn btn-sm btn-outline">下载测试计划</a></div>';
      }
      if (context.test_plan_md) {
        html += '<h4>测试计划预览</h4>';
        html += '<div class="markdown-preview">' + esc(context.test_plan_md).slice(0, 500) + '...</div>';
      }
      break;

    case 'generation':
      html += '<div class="stat-row"><span>生成用例数：</span><strong>' + (output.test_case_count || output.cases_count || '-') + '</strong></div>';
      if (output.generated_test_cases || context.generated_test_cases) {
        var cases = output.generated_test_cases || context.generated_test_cases;
        html += '<h4>用例列表</h4>';
        html += '<div class="table-wrap"><table><thead><tr><th>标题</th><th>优先级</th><th>类型</th></tr></thead><tbody>';
        cases.forEach(function (tc) {
          html += '<tr><td>' + esc(tc.title || '') + '</td>' +
            '<td><span class="badge ' + (tc.priority === 'critical' ? 'badge-danger' : 'badge-neutral') + '">' + i18n.t('priority', tc.priority) + '</span></td>' +
            '<td>' + i18n.t('testType', tc.test_type) + '</td></tr>';
        });
        html += '</tbody></table></div>';
      }
      break;

    case 'review':
      html += '<div class="stat-row"><span>评审状态：</span><strong>' + (output.status || '-') + '</strong></div>';
      html += '<p class="text-sm text-muted">用例评审完成后，在流水线页面查看详细的评审状态和用例内容。</p>';
      break;

    case 'execution':
      html += '<div class="stat-row"><span>执行记录数：</span><strong>' + ((output.execution_ids || []).length || '-') + '</strong></div>';
      if (output.execution_ids && output.execution_ids.length) {
        html += '<h4>执行 ID</h4><ul>';
        output.execution_ids.forEach(function (eid) {
          html += '<li><a href="/executions/' + eid + '">' + eid.slice(0, 8) + '</a></li>';
        });
        html += '</ul>';
      }
      break;

    default:
      html += '<pre class="pre-block">' + esc(JSON.stringify(output, null, 2)) + '</pre>';
  }

  return html || '<p class="text-muted">无输出数据</p>';
}

function esc(s) {
  return (s || '').toString().replace(/[&<>"']/g, function (m) {
    return ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' })[m];
  });
}
