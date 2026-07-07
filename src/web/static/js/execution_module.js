/**
 * 测试执行模块 — 独立页面（/execution）
 *
 * 两个用例来源：
 *   1. 目录用例 — 从用例库目录中选择
 *   2. 流水线用例 — 从流水线中选择已评审通过的用例
 */

// ── State ──
var currentSource = 'directory';   // 'directory' | 'pipeline'
var selectedCaseIds = [];         // [{id, title, test_type, platform_type, priority, ...}]
var allDirCases = [];             // loaded directory cases
var allPlCases = [];              // loaded pipeline cases
var currentExecutionId = null;
var ws = null;
var pollTimer = null;

// ── Init ──
document.addEventListener('DOMContentLoaded', function () {
    loadDirectories();
    loadProjectsForPipeline();
});

// ═══════════════════════════════════════════════════════
// Source switch
// ═══════════════════════════════════════════════════════

function switchSource(source) {
    currentSource = source;

    // Toggle panels
    document.getElementById('source-directory').style.display = (source === 'directory') ? '' : 'none';
    document.getElementById('source-pipeline').style.display = (source === 'pipeline') ? '' : 'none';

    // Toggle tabs
    var tabs = document.querySelectorAll('#source-tabs .tab');
    tabs[0].classList.toggle('active', source === 'directory');
    tabs[1].classList.toggle('active', source === 'pipeline');

    // Reset selection
    clearSelection();
}

function clearSelection() {
    selectedCaseIds = [];
    updateStartButton();
}

// ═══════════════════════════════════════════════════════
// Source 1: 目录用例
// ═══════════════════════════════════════════════════════

function loadDirectories() {
    api.get('/case-library/directories').then(function (res) {
        var dirs = res.data || [];
        var el = document.getElementById('dir-tree');
        if (dirs.length === 0) {
            el.innerHTML = '<span class="text-muted">暂无目录，请先在「用例管理」中创建目录并导入用例</span>';
            return;
        }
        el.innerHTML = dirs.map(function (d) {
            return '<div class="dir-item" style="padding:6px 8px;cursor:pointer;border-radius:6px;margin-bottom:2px" ' +
                'onclick="onDirClick(this, \'' + escHtml(d.id) + '\')" data-id="' + escHtml(d.id) + '">' +
                '<span style="margin-right:4px">📁</span>' +
                escHtml(d.name) +
                ' <span class="text-muted">(' + d.case_count + ')</span>' +
                '</div>';
        }).join('') + '<div class="dir-item" style="padding:6px 8px;cursor:pointer;border-radius:6px" onclick="onDirClick(this, \'\')" data-id="">' +
            '<span style="margin-right:4px">📂</span>全部用例（未归档）</div>';
    }).catch(function (err) {
        document.getElementById('dir-tree').textContent = '加载失败: ' + (err.message || '');
    });
}

function onDirClick(el, dirId) {
    // Highlight
    document.querySelectorAll('#dir-tree .dir-item').forEach(function (d) { d.style.background = ''; });
    el.style.background = '#f0f4ff';

    // Load cases
    var params = {};
    if (dirId) params.directory_id = dirId;

    api.get('/case-library/cases', params).then(function (res) {
        allDirCases = res.data || [];
        renderDirCases(allDirCases);
        clearSelection();
    }).catch(function (err) {
        showToast('加载用例失败: ' + (err.message || ''), 'error');
    });
}

function renderDirCases(cases) {
    var table = document.getElementById('dir-case-table');
    var tbody = document.getElementById('dir-case-tbody');
    var hint = document.getElementById('dir-case-hint');

    if (cases.length === 0) {
        table.style.display = 'none';
        hint.textContent = '该目录下无用例';
        hint.style.display = '';
        return;
    }

    hint.style.display = 'none';
    table.style.display = '';

    tbody.innerHTML = cases.map(function (tc) {
        var checked = selectedCaseIds.some(function (s) { return s.id === tc.id; }) ? 'checked' : '';
        var typeLabel = i18n.t('testType', tc.test_type) || tc.test_type || '-';
        var priorityLabel = i18n.t('priority', tc.priority) || tc.priority || '-';
        var priClass = tc.priority === 'critical' ? 'badge-danger' : (tc.priority === 'high' ? 'badge-warning' : 'badge-neutral');

        return '<tr>' +
            '<td><input type="checkbox" ' + checked + ' data-id="' + escHtml(tc.id) + '" onchange="toggleDirCase(this)"></td>' +
            '<td>' + escHtml(tc.title) + '</td>' +
            '<td><span class="badge badge-info">' + typeLabel + '</span></td>' +
            '<td><span class="badge ' + priClass + '">' + priorityLabel + '</span></td>' +
            '</tr>';
    }).join('');
}

function toggleDirSelectAll() {
    var checkAll = document.getElementById('dir-select-all');
    var checkboxes = document.querySelectorAll('#dir-case-tbody input[type=checkbox]');

    if (checkAll.checked) {
        checkboxes.forEach(function (cb) { cb.checked = true; });
        allDirCases.forEach(function (tc) {
            if (!selectedCaseIds.some(function (s) { return s.id === tc.id; })) {
                selectedCaseIds.push(tc);
            }
        });
    } else {
        checkboxes.forEach(function (cb) { cb.checked = false; });
        var dirIds = new Set(allDirCases.map(function (c) { return c.id; }));
        selectedCaseIds = selectedCaseIds.filter(function (s) { return !dirIds.has(s.id); });
    }
    updateStartButton();
}

function toggleDirCase(cb) {
    var id = cb.dataset.id;
    if (cb.checked) {
        var tc = allDirCases.find(function (c) { return c.id === id; });
        if (tc && !selectedCaseIds.some(function (s) { return s.id === id; })) {
            selectedCaseIds.push(tc);
        }
    } else {
        selectedCaseIds = selectedCaseIds.filter(function (s) { return s.id !== id; });
    }
    updateStartButton();
}

// ═══════════════════════════════════════════════════════
// Source 2: 流水线已审批用例
// ═══════════════════════════════════════════════════════

function loadProjectsForPipeline() {
    api.get('/projects').then(function (res) {
        var projects = res.data || [];
        var sel = document.getElementById('pl-project-select');
        projects.forEach(function (p) {
            var opt = document.createElement('option');
            opt.value = p.id;
            opt.textContent = p.name;
            sel.appendChild(opt);
        });
    }).catch(function () {});
}

function onPipelineProjectChange() {
    var projectId = document.getElementById('pl-project-select').value;
    var listEl = document.getElementById('pl-pipeline-list');
    var hint = document.getElementById('pl-hint');
    var table = document.getElementById('pl-case-table');

    table.style.display = 'none';
    allPlCases = [];
    clearSelection();

    if (!projectId) {
        listEl.innerHTML = '';
        hint.style.display = 'none';
        return;
    }

    hint.style.display = '';
    listEl.innerHTML = '<span class="text-muted">加载中...</span>';

    // Load pipelines for this project
    api.get('/projects/' + projectId + '/pipelines', { size: 50 }).then(function (res) {
        var pipelines = (res.data || []).filter(function (p) { return p.status !== 'cancelled'; });
        if (pipelines.length === 0) {
            listEl.innerHTML = '<span class="text-muted">该项目暂无流水线</span>';
            return;
        }

        // For each pipeline, fetch approved test case count
        var loaded = 0;
        var items = [];

        pipelines.forEach(function (p, idx) {
            api.get('/pipelines/' + p.id + '/test-cases').then(function (tcRes) {
                var cases = (tcRes.data || []).filter(function (c) { return c.status === 'approved'; });
                loaded++;
                items.push({ pipeline: p, cases: cases, idx: idx });
                if (loaded === pipelines.length) {
                    renderPipelineList(items);
                }
            }).catch(function () {
                loaded++;
                items.push({ pipeline: p, cases: [], idx: idx });
                if (loaded === pipelines.length) {
                    renderPipelineList(items);
                }
            });
        });
    }).catch(function (err) {
        listEl.innerHTML = '<span class="text-danger">加载失败: ' + (err.message || '') + '</span>';
    });
}

function renderPipelineList(items) {
    var listEl = document.getElementById('pl-pipeline-list');
    items.sort(function (a, b) { return a.idx - b.idx; });

    listEl.innerHTML = items.map(function (item) {
        var p = item.pipeline;
        var approvedCount = item.cases.length;
        var cls = approvedCount > 0 ? 'dir-item' : '';
        return '<div class="' + cls + '" style="padding:6px 8px;cursor:pointer;border-radius:6px;margin-bottom:2px" ' +
            'onclick="onPipelineClick(this, \'' + escHtml(p.id) + '\')" data-id="' + escHtml(p.id) + '">' +
            '<span style="margin-right:4px">🔁</span>' +
            escHtml(fmt.date(p.created_at)) + ' — ' + escHtml(i18n.t('pipelineStatus', p.status) || p.status) +
            ' <span class="' + (approvedCount > 0 ? 'text-success' : 'text-muted') + '">(' + approvedCount + ' 条已审批)</span>' +
            '</div>';
    }).join('');
}

function onPipelineClick(el, pipelineId) {
    // Highlight
    document.querySelectorAll('#pl-pipeline-list .dir-item').forEach(function (d) { d.style.background = ''; });
    el.style.background = '#f0f4ff';

    // Load approved cases
    var projectId = document.getElementById('pl-project-select').value;
    api.get('/projects/' + projectId + '/test-cases', { pipeline_id: pipelineId, status: 'approved', size: 500 }).then(function (res) {
        allPlCases = res.data || [];
        renderPlCases(allPlCases);
        clearSelection();
    }).catch(function (err) {
        showToast('加载用例失败: ' + (err.message || ''), 'error');
    });
}

function renderPlCases(cases) {
    var table = document.getElementById('pl-case-table');
    var tbody = document.getElementById('pl-case-tbody');
    var hint = document.getElementById('pl-case-hint');

    if (cases.length === 0) {
        table.style.display = 'none';
        hint.textContent = '该流水线无已审批用例';
        hint.style.display = '';
        return;
    }

    hint.style.display = 'none';
    table.style.display = '';

    tbody.innerHTML = cases.map(function (tc) {
        var checked = selectedCaseIds.some(function (s) { return s.id === tc.id; }) ? 'checked' : '';
        var typeLabel = i18n.t('testType', tc.test_type) || tc.test_type || '-';
        var priorityLabel = i18n.t('priority', tc.priority) || tc.priority || '-';
        var priClass = tc.priority === 'critical' ? 'badge-danger' : (tc.priority === 'high' ? 'badge-warning' : 'badge-neutral');

        return '<tr>' +
            '<td><input type="checkbox" ' + checked + ' data-id="' + escHtml(tc.id) + '" onchange="togglePlCase(this)"></td>' +
            '<td>' + escHtml(tc.title) + '</td>' +
            '<td><span class="badge badge-info">' + typeLabel + '</span></td>' +
            '<td><span class="badge ' + priClass + '">' + priorityLabel + '</span></td>' +
            '</tr>';
    }).join('');
}

function togglePlSelectAll() {
    var checkAll = document.getElementById('pl-select-all');
    var checkboxes = document.querySelectorAll('#pl-case-tbody input[type=checkbox]');

    if (checkAll.checked) {
        checkboxes.forEach(function (cb) { cb.checked = true; });
        allPlCases.forEach(function (tc) {
            if (!selectedCaseIds.some(function (s) { return s.id === tc.id; })) {
                selectedCaseIds.push(tc);
            }
        });
    } else {
        checkboxes.forEach(function (cb) { cb.checked = false; });
        var plIds = new Set(allPlCases.map(function (c) { return c.id; }));
        selectedCaseIds = selectedCaseIds.filter(function (s) { return !plIds.has(s.id); });
    }
    updateStartButton();
}

function togglePlCase(cb) {
    var id = cb.dataset.id;
    if (cb.checked) {
        var tc = allPlCases.find(function (c) { return c.id === id; });
        if (tc && !selectedCaseIds.some(function (s) { return s.id === id; })) {
            selectedCaseIds.push(tc);
        }
    } else {
        selectedCaseIds = selectedCaseIds.filter(function (s) { return s.id !== id; });
    }
    updateStartButton();
}

// ═══════════════════════════════════════════════════════
// Start execution
// ═══════════════════════════════════════════════════════

function updateStartButton() {
    var btn = document.getElementById('btn-start');
    var textEl = document.getElementById('selected-count-text');
    var count = selectedCaseIds.length;

    btn.disabled = count === 0;
    btn.textContent = count > 0 ? '▶ 开始执行 (' + count + ')' : '▶ 开始执行';
    textEl.textContent = count > 0 ? '已选择 ' + count + ' 条用例' : '未选择用例';
}

function startExecution() {
    if (selectedCaseIds.length === 0) {
        showToast('请至少选择一个用例', 'warn');
        return;
    }

    var btn = document.getElementById('btn-start');
    btn.disabled = true;
    btn.textContent = '⏳ 启动中...';

    var ids = selectedCaseIds.map(function (s) { return s.id; });

    api.post('/executions/run', {
        test_case_ids: ids
    }).then(function (res) {
        var data = res.data;

        // Show stats
        document.getElementById('exec-stats-bar').style.display = '';
        document.getElementById('stat-total').textContent = data.total_cases;

        // Show progress
        document.getElementById('progress-panel').style.display = '';
        document.getElementById('results-panel').style.display = '';

        // Reset
        document.getElementById('stat-passed').textContent = '0';
        document.getElementById('stat-failed').textContent = '0';
        document.getElementById('stat-generated').textContent = '0';
        document.getElementById('live-passed').textContent = '0';
        document.getElementById('live-failed').textContent = '0';
        document.getElementById('live-error').textContent = '0';
        document.getElementById('progress-fill').style.width = '0%';
        document.getElementById('progress-fill').classList.remove('success', 'warning');

        showToast('已启动执行，共 ' + data.total_cases + ' 条用例', 'success');

        if (data.execution_ids && data.execution_ids.length > 0) {
            currentExecutionId = data.execution_ids[0];
            connectWebSocket(currentExecutionId);
            startPolling(currentExecutionId);
        }

    }).catch(function (err) {
        showToast('执行失败: ' + (err.message || '网络错误'), 'error');
        btn.disabled = false;
        updateStartButton();
    });
}

// ═══════════════════════════════════════════════════════
// WebSocket
// ═══════════════════════════════════════════════════════

function connectWebSocket(executionId) {
    var protocol = location.protocol === 'https:' ? 'wss:' : 'ws:';
    var wsUrl = protocol + '//' + location.host + '/ws/executions/' + executionId + '/live';

    try {
        ws = new WebSocket(wsUrl);
        ws.onopen = function () {
            window._wsPing = setInterval(function () {
                if (ws && ws.readyState === WebSocket.OPEN) ws.send('ping');
            }, 30000);
        };
        ws.onmessage = function (event) {
            try {
                var msg = JSON.parse(event.data);
                if (msg.type === 'pong') return;
                handleProgressMessage(msg);
            } catch (e) {}
        };
        ws.onclose = function () { if (window._wsPing) clearInterval(window._wsPing); };
    } catch (e) {}
}

function handleProgressMessage(msg) {
    if (msg.type === 'execution_progress') {
        if (msg.current && msg.total) {
            var pct = Math.round((msg.current / msg.total) * 100);
            document.getElementById('progress-fill').style.width = pct + '%';
            if (pct > 0 && pct < 100) document.getElementById('progress-fill').classList.add('warning');
            document.getElementById('progress-text').textContent = msg.current + '/' + msg.total;
            document.getElementById('current-case-name').textContent = msg.case_title || '';
        }
        if (msg.status === 'passed') {
            inc('live-passed'); inc('stat-passed');
        } else if (msg.status === 'failed') {
            inc('live-failed'); inc('stat-failed');
        } else if (msg.status === 'error') {
            inc('live-error');
        } else if (msg.status === 'generated') {
            inc('stat-generated');
        }
    }
    if (msg.type === 'execution_complete') {
        document.getElementById('progress-text').textContent = '✅ 执行完成';
        document.getElementById('progress-fill').style.width = '100%';
        document.getElementById('progress-fill').classList.remove('warning');
        document.getElementById('progress-fill').classList.add('success');
        loadResults(currentExecutionId);
        loadDefects(currentExecutionId);
        document.getElementById('btn-start').disabled = false;
        updateStartButton();
    }
}

function inc(elId) {
    var el = document.getElementById(elId);
    if (el) el.textContent = parseInt(el.textContent || '0') + 1;
}

// ═══════════════════════════════════════════════════════
// Polling fallback
// ═══════════════════════════════════════════════════════

function startPolling(executionId) {
    if (pollTimer) clearInterval(pollTimer);
    pollTimer = setInterval(function () {
        api.get('/executions/' + executionId + '/progress').then(function (res) {
            if (!res.success) return;
            var d = res.data;
            if (d.status === 'completed' || d.status === 'failed') {
                clearInterval(pollTimer);
                document.getElementById('progress-text').textContent = '✅ 执行完成';
                document.getElementById('progress-fill').style.width = '100%';
                document.getElementById('progress-fill').classList.remove('warning');
                document.getElementById('progress-fill').classList.add('success');
                loadResults(executionId);
                loadDefects(executionId);
                document.getElementById('btn-start').disabled = false;
                updateStartButton();
            } else {
                var s = d.by_status || {};
                document.getElementById('live-passed').textContent = s.passed || 0;
                document.getElementById('live-failed').textContent = s.failed || 0;
                document.getElementById('live-error').textContent = s.error || 0;
                var done = d.completed || 0;
                var total = d.total_cases || 1;
                var pct = Math.round((done / Math.max(total, 1)) * 100);
                document.getElementById('progress-fill').style.width = pct + '%';
                document.getElementById('progress-text').textContent = done + '/' + total;
                if (pct > 0 && pct < 100) {
                    document.getElementById('progress-fill').classList.remove('success');
                    document.getElementById('progress-fill').classList.add('warning');
                }
            }
        }).catch(function () {});
    }, 3000);
}

// ═══════════════════════════════════════════════════════
// Load results / defects
// ═══════════════════════════════════════════════════════

function loadResults(executionId) {
    if (!executionId) return;
    api.get('/executions/' + executionId + '/summary').then(function (res) {
        if (!res.success) return;
        var data = res.data, results = data.results || [], stats = data.stats || {};
        document.getElementById('stat-total').textContent = stats.total || 0;
        document.getElementById('stat-passed').textContent = stats.passed || 0;
        document.getElementById('stat-failed').textContent = stats.failed || 0;
        document.getElementById('stat-generated').textContent = stats.generated || 0;
        document.getElementById('results-panel').style.display = '';

        var tbody = document.getElementById('results-tbody');
        tbody.innerHTML = results.map(function (r) {
            var badge = '';
            if (r.status === 'passed') badge = '<span class="badge badge-success">通过</span>';
            else if (r.status === 'failed') badge = '<span class="badge badge-danger">失败</span>';
            else if (r.status === 'error') badge = '<span class="badge badge-warning">错误</span>';
            else badge = '<span class="badge badge-info">已生成</span>';
            var dur = r.duration_ms ? fmt.duration(r.duration_ms) : '-';
            return '<tr><td class="text-ellipsis" style="max-width:280px">' + escHtml(r.test_case_id) + '</td>' +
                '<td>' + badge + '</td><td>' + dur + '</td>' +
                '<td class="text-sm">' + escHtml(r.failure_reason || r.error_message || '-') + '</td>' +
                '<td>' + (r.screenshot_path ? '<a href="/' + escHtml(r.screenshot_path) + '" target="_blank" class="btn btn-sm btn-outline">📷</a>' : '-') + '</td></tr>';
        }).join('');

        document.getElementById('progress-text').textContent = '✅ 执行完成  通过率: ' + (stats.pass_rate || 0) + '%';
    }).catch(function () {});
}

function loadDefects(executionId) {
    if (!executionId) return;
    api.get('/executions/' + executionId + '/defects').then(function (res) {
        if (!res.success) return;
        var defects = res.data || [];
        if (defects.length === 0) return;
        document.getElementById('defects-panel').style.display = '';

        document.getElementById('defects-tbody').innerHTML = defects.map(function (d) {
            var sev = '';
            if (d.severity === 'critical') sev = '<span class="badge badge-danger">严重</span>';
            else if (d.severity === 'high') sev = '<span class="badge badge-warning">高</span>';
            else if (d.severity === 'medium') sev = '<span class="badge badge-info">中</span>';
            else sev = '<span class="badge badge-neutral">低</span>';
            return '<tr><td><strong>' + escHtml(d.title) + '</strong></td><td>' + sev + '</td>' +
                '<td>' + (d.status === 'open' ? '待处理' : d.status) + '</td>' +
                '<td class="text-sm text-muted">' + escHtml((d.description || '').substring(0, 120)) + '</td></tr>';
        }).join('');
    }).catch(function () {});
}

// ═══════════════════════════════════════════════════════
// Retry
// ═══════════════════════════════════════════════════════

function retryFailed() {
    if (!currentExecutionId) { showToast('没有可重试的执行记录', 'warn'); return; }
    api.post('/executions/' + currentExecutionId + '/retry').then(function (res) {
        showToast(res.success ? '已重试 ' + res.data.retried_cases + ' 条用例' : (res.error || '重试失败'), res.success ? 'success' : 'error');
        if (res.success) { loadResults(currentExecutionId); loadDefects(currentExecutionId); }
    }).catch(function (err) { showToast('重试失败: ' + (err.message || ''), 'error'); });
}

// ═══════════════════════════════════════════════════════
// Helpers
// ═══════════════════════════════════════════════════════

function escHtml(str) {
    if (!str) return '';
    return String(str).replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;').replace(/"/g, '&quot;');
}
