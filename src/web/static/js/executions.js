/**
 * 执行详情页 — 查看执行结果、进度、缺陷
 */

const executionId = window.EXECUTION_ID || new URLSearchParams(location.search).get('execution_id') || '';
let pollTimer = null;

document.addEventListener('DOMContentLoaded', () => {
    if (executionId) {
        loadExecutionDetail();
        startAutoRefresh();
    }
});

// ═══════════════════════════════════════════════════════
// Load execution detail
// ═══════════════════════════════════════════════════════

async function loadExecutionDetail() {
    try {
        const res = await api.get(`/executions/${executionId}/summary`);
        if (!res.success) {
            document.getElementById('execution-stats').innerHTML = '<p class="text-error">加载失败</p>';
            return;
        }

        const data = res.data;
        const ex = data.execution || {};
        const stats = data.stats || {};

        // Render stats cards
        document.getElementById('execution-stats').innerHTML = `
            <div class="stat-card">
                <div class="stat-value">${stats.total || 0}</div>
                <div class="stat-label">总计</div>
            </div>
            <div class="stat-card stat-passed">
                <div class="stat-value">${stats.passed || 0}</div>
                <div class="stat-label">通过</div>
            </div>
            <div class="stat-card stat-failed">
                <div class="stat-value">${stats.failed || 0}</div>
                <div class="stat-label">失败</div>
            </div>
            <div class="stat-card stat-error">
                <div class="stat-value">${stats.error || 0}</div>
                <div class="stat-label">错误</div>
            </div>
            ${stats.generated ? `
            <div class="stat-card stat-generated">
                <div class="stat-value">${stats.generated}</div>
                <div class="stat-label">脚本生成</div>
            </div>` : ''}
        `;

        // Progress bar
        const total = stats.total || 1;
        const completed = stats.passed + stats.failed + stats.error + (stats.generated || 0);
        const pct = Math.round((completed / Math.max(total, 1)) * 100);
        document.getElementById('progress-bar').style.width = pct + '%';
        document.getElementById('progress-text').textContent =
            `完成: ${completed}/${total} | 通过率: ${stats.pass_rate || 0}%`;

        // Results table
        renderResults(data.results || []);

        // Defects table
        renderDefects(data.defects || []);

        // Stop polling if completed
        if (ex.status === 'completed' || ex.status === 'failed') {
            if (pollTimer) clearInterval(pollTimer);
            document.getElementById('progress-text').textContent += ' ✅';
        }

    } catch (err) {
        console.error('Load execution detail failed:', err);
    }
}

function renderResults(results) {
    const tbody = document.querySelector('#execution-results tbody');
    if (!tbody) return;

    tbody.innerHTML = results.map(r => {
        let statusBadge = '';
        if (r.status === 'passed') statusBadge = '<span class="badge badge-success">通过</span>';
        else if (r.status === 'failed') statusBadge = '<span class="badge badge-danger">失败</span>';
        else if (r.status === 'error') statusBadge = '<span class="badge badge-error">错误</span>';
        else if (r.status === 'generated') statusBadge = '<span class="badge badge-info">已生成</span>';

        const duration = r.duration_ms ? (r.duration_ms / 1000).toFixed(1) + 's' : '-';
        const reason = r.failure_reason || r.error_message || '-';

        return `
            <tr>
                <td>${escapeHtml(r.test_case_id)}</td>
                <td>${r.attempt || 1}</td>
                <td>${statusBadge}</td>
                <td>${duration}</td>
                <td class="text-error">${escapeHtml(reason)}</td>
            </tr>
        `;
    }).join('');
}

function renderDefects(defects) {
    const defectsSection = document.getElementById('defects-section');
    const tbody = document.querySelector('#defects-section tbody');
    if (!defectsSection || !tbody) return;

    if (defects.length === 0) {
        defectsSection.style.display = 'none';
        return;
    }

    defectsSection.style.display = 'block';

    tbody.innerHTML = defects.map(d => {
        let sevBadge = '';
        if (d.severity === 'critical') sevBadge = '<span class="badge badge-danger">严重</span>';
        else if (d.severity === 'high') sevBadge = '<span class="badge badge-warning">高</span>';
        else if (d.severity === 'medium') sevBadge = '<span class="badge badge-info">中</span>';
        else sevBadge = '<span class="badge badge-secondary">低</span>';

        return `
            <tr>
                <td><strong>${escapeHtml(d.title)}</strong></td>
                <td>${sevBadge}</td>
                <td>${d.status === 'open' ? '待处理' : d.status}</td>
                <td>${escapeHtml(d.test_case_id || '-')}</td>
                <td>${fmt.date(d.created_at) || '-'}</td>
            </tr>
        `;
    }).join('');
}

function startAutoRefresh() {
    pollTimer = setInterval(() => {
        loadExecutionDetail();
    }, 5000);
}

function escapeHtml(str) {
    if (!str) return '';
    return String(str)
        .replace(/&/g, '&amp;')
        .replace(/</g, '&lt;')
        .replace(/>/g, '&gt;')
        .replace(/"/g, '&quot;');
}
