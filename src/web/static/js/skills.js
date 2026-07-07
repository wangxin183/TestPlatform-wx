/**
 * Skills Management JS — view, edit, create, AI-generate SKILL.md files
 * v1 — rv-* design system, modal overlay pattern
 */

var SK = {
  skills: [],
  currentSkill: null,
  editMode: false,
  generating: false
};

document.addEventListener('DOMContentLoaded', function() {
  loadSkills();
});

// ═══════════════════════ Load & Render ═══════════════════

function loadSkills() {
  fetch('/api/v1/skills')
    .then(function(res) { return res.json(); })
    .then(function(data) {
      if (!data.success) return;
      SK.skills = data.data || [];
      renderSkillList();
    })
    .catch(function() {
      document.getElementById('skills-list').innerHTML = '<tr><td colspan="5" class="rv-empty">加载失败，请刷新重试</td></tr>';
    });
}

function renderSkillList() {
  var el = document.getElementById('skills-list');
  if (SK.skills.length === 0) {
    el.innerHTML = '<tr><td colspan="5" class="rv-empty">暂无 Skills，点击「AI 生成」或「模板创建」添加</td></tr>';
    return;
  }
  el.innerHTML = SK.skills.map(function(s) {
    var desc = s.description || '';
    if (desc.length > 60) desc = desc.substring(0, 60) + '...';
    var time = s.updated_at ? s.updated_at.substring(0, 16).replace('T', ' ') : '-';
    var size = formatFileSize(s.size);
    return '<tr>'
      + '<td><span class="rv-case-link" onclick="viewSkill(\'' + escAttr(s.dir_name) + '\')">' + esc(s.name) + '</span></td>'
      + '<td class="rv-td-muted">' + esc(desc) + '</td>'
      + '<td class="rv-td-num">' + size + '</td>'
      + '<td class="rv-td-muted">' + time + '</td>'
      + '<td class="rv-td-actions">'
        + '<button class="rv-btn-icon rv-btn-icon-sm" onclick="viewSkill(\'' + escAttr(s.dir_name) + '\')" title="查看"><svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><path d="M1 12s4-8 11-8 11 8 11 8-4 8-11 8-11-8-11-8z"/><circle cx="12" cy="12" r="3"/></svg></button>'
        + '<button class="rv-btn-icon rv-btn-icon-sm" onclick="editSkill(\'' + escAttr(s.dir_name) + '\')" title="编辑"><svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><path d="M11 4H4a2 2 0 0 0-2 2v14a2 2 0 0 0 2 2h14a2 2 0 0 0 2-2v-7"/><path d="M18.5 2.5a2.121 2.121 0 0 1 3 3L12 15l-4 1 1-4 9.5-9.5z"/></svg></button>'
      + '</td>'
      + '</tr>';
  }).join('');
}

// ═══════════════════════ View / Edit Modal ═══════════════════

function viewSkill(dirName) {
  fetch('/api/v1/skills/' + encodeURIComponent(dirName))
    .then(function(res) { return res.json(); })
    .then(function(data) {
      if (!data.success) { alert(data.error || '加载失败'); return; }
      SK.currentSkill = data.data;
      SK.editMode = false;
      showSkillModal();
    });
}

function editSkill(dirName) {
  fetch('/api/v1/skills/' + encodeURIComponent(dirName))
    .then(function(res) { return res.json(); })
    .then(function(data) {
      if (!data.success) { alert(data.error || '加载失败'); return; }
      SK.currentSkill = data.data;
      SK.editMode = true;
      showSkillModal();
    });
}

function showSkillModal() {
  var s = SK.currentSkill;
  closeSkillModal();

  var title = SK.editMode ? '编辑 Skill' : '查看 Skill';
  var kicker = SK.editMode ? '编辑 Skill' : '查看 Skill';
  var bodyContent = '';
  var footerContent = '';

  if (SK.editMode) {
    bodyContent = '<textarea class="rv-textarea" id="skill-edit-content" rows="22" style="font-family:var(--rv-mono);font-size:12px;line-height:1.6">' + esc(s.content || '') + '</textarea>';
    footerContent =
      '<div class="rv-modal-footer-left"></div>'
      + '<div class="rv-modal-footer-right">'
        + '<button class="rv-btn rv-btn-ghost" onclick="closeSkillModal()">取消</button>'
        + '<button class="rv-btn rv-btn-accent" onclick="saveSkill(\'' + escAttr(s.dir_name) + '\')">'
          + '<svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><polyline points="20 6 9 17 4 12"/></svg>'
          + ' 保存修改'
        + '</button>'
      + '</div>';
  } else {
    bodyContent = '<pre style="background:#fafaf9;border:1px solid var(--rv-border);border-radius:var(--rv-radius);padding:16px;overflow:auto;max-height:60vh;font-family:var(--rv-mono);font-size:12px;line-height:1.6;white-space:pre-wrap">' + esc(s.content || '') + '</pre>';
    footerContent =
      '<div class="rv-modal-footer-left"></div>'
      + '<div class="rv-modal-footer-right">'
        + '<button class="rv-btn rv-btn-outline" onclick="editSkill(\'' + escAttr(s.dir_name) + '\')">'
          + '<svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><path d="M11 4H4a2 2 0 0 0-2 2v14a2 2 0 0 0 2 2h14a2 2 0 0 0 2-2v-7"/><path d="M18.5 2.5a2.121 2.121 0 0 1 3 3L12 15l-4 1 1-4 9.5-9.5z"/></svg>'
          + ' 编辑'
        + '</button>'
        + '<button class="rv-btn rv-btn-ghost" onclick="closeSkillModal()">关闭</button>'
      + '</div>';
  }

  var overlay = document.createElement('div');
  overlay.className = 'rv-modal-overlay';
  overlay.id = 'skill-detail-overlay';
  overlay.innerHTML =
    '<div class="rv-modal-glass" onclick="closeSkillModal()"></div>'
    + '<div class="rv-modal" style="max-width:800px">'
    + '<div class="rv-modal-header">'
      + '<div style="display:flex;align-items:flex-start;gap:14px">'
        + '<div class="rv-header-icon-wrap" style="margin-top:2px">'
          + '<svg width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><circle cx="12" cy="12" r="3"/><path d="M19.4 15a1.65 1.65 0 0 0 .33 1.82l.06.06a2 2 0 0 1-2.83 2.83l-.06-.06a1.65 1.65 0 0 0-1.82-.33 1.65 1.65 0 0 0-1 1.51V21a2 2 0 0 1-4 0v-.09A1.65 1.65 0 0 0 9 19.4a1.65 1.65 0 0 0-1.82.33l-.06.06a2 2 0 0 1-2.83-2.83l.06-.06A1.65 1.65 0 0 0 4.68 15a1.65 1.65 0 0 0-1.51-1H3a2 2 0 0 1 0-4h.09A1.65 1.65 0 0 0 4.6 9a1.65 1.65 0 0 0-.33-1.82l-.06-.06a2 2 0 0 1 2.83-2.83l.06.06A1.65 1.65 0 0 0 9 4.68a1.65 1.65 0 0 0 1-1.51V3a2 2 0 0 1 4 0v.09a1.65 1.65 0 0 0 1 1.51 1.65 1.65 0 0 0 1.82-.33l.06-.06a2 2 0 0 1 2.83 2.83l-.06.06A1.65 1.65 0 0 0 19.4 9a1.65 1.65 0 0 0 1.51 1H21a2 2 0 0 1 0 4h-.09a1.65 1.65 0 0 0-1.51 1z"/></svg>'
        + '</div>'
        + '<div>'
          + '<span class="rv-modal-kicker">' + esc(title) + '</span>'
          + '<span class="rv-modal-title">' + esc(s.name) + '</span>'
        + '</div>'
      + '</div>'
      + '<button class="rv-btn rv-btn-icon" onclick="closeSkillModal()" style="flex-shrink:0">'
        + '<svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><line x1="18" y1="6" x2="6" y2="18"/><line x1="6" y1="6" x2="18" y2="18"/></svg>'
      + '</button>'
    + '</div>'
    + '<div class="rv-modal-body">' + bodyContent + '</div>'
    + '<div class="rv-modal-footer">' + footerContent + '</div>'
    + '</div>';
  document.body.appendChild(overlay);
  overlay.classList.add('open');
}

function closeSkillModal() {
  var el = document.getElementById('skill-detail-overlay');
  if (el) el.remove();
  SK.editMode = false;
}

function saveSkill(dirName) {
  var content = document.getElementById('skill-edit-content').value;
  if (!content.trim()) { alert('内容不能为空'); return; }

  var form = new FormData();
  form.append('content', content);

  fetch('/api/v1/skills/' + encodeURIComponent(dirName), { method: 'PUT', body: form })
    .then(function(res) { return res.json(); })
    .then(function(data) {
      if (data.success) {
        closeSkillModal();
        loadSkills();
      } else {
        alert(data.error || '保存失败');
      }
    });
}

// ═══════════════════════ AI Generate Modal ═══════════════════

function showAIGenerateModal() {
  closeAIGenerateModal();

  var overlay = document.createElement('div');
  overlay.className = 'rv-modal-overlay';
  overlay.id = 'skill-generate-overlay';
  overlay.innerHTML =
    '<div class="rv-modal-glass" onclick="closeAIGenerateModal()"></div>'
    + '<div class="rv-modal" style="max-width:520px">'
    + '<div class="rv-modal-header">'
      + '<div style="display:flex;align-items:flex-start;gap:14px">'
        + '<div class="rv-header-icon-wrap" style="margin-top:2px">'
          + '<svg width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><polygon points="13 2 3 14 12 14 11 22 21 10 12 10 13 2"/></svg>'
        + '</div>'
        + '<div>'
          + '<span class="rv-modal-kicker">AI 生成</span>'
          + '<span class="rv-modal-title">用自然语言描述生成 Skill</span>'
        + '</div>'
      + '</div>'
      + '<button class="rv-btn rv-btn-icon" onclick="closeAIGenerateModal()" style="flex-shrink:0">'
        + '<svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><line x1="18" y1="6" x2="6" y2="18"/><line x1="6" y1="6" x2="18" y2="18"/></svg>'
      + '</button>'
    + '</div>'
    + '<div class="rv-modal-body">'
      + '<textarea class="rv-textarea" id="skill-gen-desc" rows="6" placeholder="描述你想要的 Skill，例如：一个用于 H5 页面兼容性测试的 skill，覆盖 iOS/Android 主流机型的 UI 适配和性能测试"></textarea>'
      + '<p id="skill-gen-msg" style="margin-top:8px;font-size:12px;color:var(--rv-text-muted)"></p>'
    + '</div>'
    + '<div class="rv-modal-footer">'
      + '<div class="rv-modal-footer-left"></div>'
      + '<div class="rv-modal-footer-right">'
        + '<button class="rv-btn rv-btn-ghost" onclick="closeAIGenerateModal()">取消</button>'
        + '<button class="rv-btn rv-btn-accent" id="skill-gen-btn" onclick="generateSkill()">'
          + '<svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><polygon points="13 2 3 14 12 14 11 22 21 10 12 10 13 2"/></svg>'
          + ' 生成'
        + '</button>'
      + '</div>'
    + '</div>'
    + '</div>';
  document.body.appendChild(overlay);
  overlay.classList.add('open');
}

function closeAIGenerateModal() {
  var el = document.getElementById('skill-generate-overlay');
  if (el) el.remove();
  SK.generating = false;
}

function generateSkill() {
  if (SK.generating) return;
  var desc = document.getElementById('skill-gen-desc').value.trim();
  if (!desc) { alert('请输入 Skill 描述'); return; }

  SK.generating = true;
  var btn = document.getElementById('skill-gen-btn');
  var msg = document.getElementById('skill-gen-msg');
  btn.disabled = true;
  btn.innerHTML = '<svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><circle cx="12" cy="12" r="10"/><polyline points="12 6 12 12 16 14"/></svg> 生成中...';
  msg.textContent = 'AI 正在生成，约需 5-15 秒...';
  msg.style.color = 'var(--rv-text-muted)';

  var form = new FormData();
  form.append('description', desc);

  fetch('/api/v1/skills/generate', { method: 'POST', body: form })
    .then(function(res) { return res.json(); })
    .then(function(data) {
      SK.generating = false;
      if (data.success) {
        closeAIGenerateModal();
        loadSkills();
        // Auto-open the new skill
        setTimeout(function() { viewSkill(data.data.dir_name); }, 500);
      } else {
        msg.textContent = (data.detail || data.error || '生成失败，请重试');
        msg.style.color = 'var(--rv-danger)';
        btn.disabled = false;
        btn.innerHTML = '<svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><polygon points="13 2 3 14 12 14 11 22 21 10 12 10 13 2"/></svg> 重试';
      }
    })
    .catch(function(err) {
      SK.generating = false;
      msg.textContent = '网络错误，请重试';
      msg.style.color = 'var(--rv-danger)';
      btn.disabled = false;
      btn.innerHTML = '<svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><polygon points="13 2 3 14 12 14 11 22 21 10 12 10 13 2"/></svg> 重试';
    });
}

// ═══════════════════════ Template Create Modal ═══════════════════

function showTemplateModal() {
  closeTemplateModal();

  var overlay = document.createElement('div');
  overlay.className = 'rv-modal-overlay';
  overlay.id = 'skill-template-overlay';
  overlay.innerHTML =
    '<div class="rv-modal-glass" onclick="closeTemplateModal()"></div>'
    + '<div class="rv-modal" style="max-width:480px">'
    + '<div class="rv-modal-header">'
      + '<div style="display:flex;align-items:flex-start;gap:14px">'
        + '<div class="rv-header-icon-wrap" style="margin-top:2px">'
          + '<svg width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><line x1="12" y1="5" x2="12" y2="19"/><line x1="5" y1="12" x2="19" y2="12"/></svg>'
        + '</div>'
        + '<div>'
          + '<span class="rv-modal-kicker">模板创建</span>'
          + '<span class="rv-modal-title">从骨架模板创建 Skill</span>'
        + '</div>'
      + '</div>'
      + '<button class="rv-btn rv-btn-icon" onclick="closeTemplateModal()" style="flex-shrink:0">'
        + '<svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><line x1="18" y1="6" x2="6" y2="18"/><line x1="6" y1="6" x2="18" y2="18"/></svg>'
      + '</button>'
    + '</div>'
    + '<div class="rv-modal-body" style="display:flex;flex-direction:column;gap:14px">'
      + '<div><span class="rv-selector-label">Skill 名称 *</span>'
        + '<input class="rv-input" id="skill-tmpl-name" placeholder="my-custom-skill（英文 kebab-case）">'
      + '</div>'
      + '<div><span class="rv-selector-label">描述</span>'
        + '<textarea class="rv-textarea" id="skill-tmpl-desc" rows="3" placeholder="简洁描述 skill 用途"></textarea>'
      + '</div>'
      + '<p id="skill-tmpl-msg" style="font-size:12px;color:var(--rv-text-muted)"></p>'
    + '</div>'
    + '<div class="rv-modal-footer">'
      + '<div class="rv-modal-footer-left"></div>'
      + '<div class="rv-modal-footer-right">'
        + '<button class="rv-btn rv-btn-ghost" onclick="closeTemplateModal()">取消</button>'
        + '<button class="rv-btn rv-btn-accent" onclick="createSkill()">创建</button>'
      + '</div>'
    + '</div>'
    + '</div>';
  document.body.appendChild(overlay);
  overlay.classList.add('open');
}

function closeTemplateModal() {
  var el = document.getElementById('skill-template-overlay');
  if (el) el.remove();
}

function createSkill() {
  var name = document.getElementById('skill-tmpl-name').value.trim();
  var desc = document.getElementById('skill-tmpl-desc').value.trim();
  var msg = document.getElementById('skill-tmpl-msg');

  // Client-side kebab-case validation
  var nameRe = /^[a-z0-9]+(-[a-z0-9]+)*$/;
  if (!name) {
    msg.textContent = '请输入 Skill 名称';
    msg.style.color = 'var(--rv-danger)';
    return;
  }
  if (!nameRe.test(name)) {
    msg.textContent = '名称只能包含小写字母、数字和连字符（kebab-case）';
    msg.style.color = 'var(--rv-danger)';
    return;
  }

  var form = new FormData();
  form.append('name', name);
  form.append('description', desc);

  fetch('/api/v1/skills', { method: 'POST', body: form })
    .then(function(res) { return res.json(); })
    .then(function(data) {
      if (data.success) {
        closeTemplateModal();
        loadSkills();
        setTimeout(function() { editSkill(data.data.dir_name); }, 400);
      } else {
        msg.textContent = data.detail || data.error || '创建失败';
        msg.style.color = 'var(--rv-danger)';
      }
    })
    .catch(function() {
      msg.textContent = '网络错误，请重试';
      msg.style.color = 'var(--rv-danger)';
    });
}

// ═══════════════════════ Utils ═══════════════════

function esc(s) {
  if (!s) return '';
  return String(s).replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;').replace(/"/g, '&quot;');
}

function escAttr(s) {
  if (!s) return '';
  return String(s).replace(/'/g, "\\'").replace(/"/g, '&quot;');
}

function formatFileSize(bytes) {
  if (!bytes || bytes === 0) return '0 B';
  var k = 1024, sizes = ['B', 'KB', 'MB', 'GB'];
  var i = Math.floor(Math.log(bytes) / Math.log(k));
  return parseFloat((bytes / Math.pow(k, i)).toFixed(1)) + ' ' + sizes[Math.min(i, sizes.length - 1)];
}
