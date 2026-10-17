import { $, e, empty, formActions } from '../core/dom.js';
import { state } from '../core/state.js';
import { api } from '../core/api.js';
import { act } from '../core/actions.js';
import { openModal, closeModal } from '../ui/modal.js';
import { toast } from '../ui/toast.js';

const templates = {
  ai: {
    name: 'AI 服务',
    domains:
      'chatgpt.com\nopenai.com\noaistatic.com\noaiusercontent.com\nclaude.ai\nclaude.com\nanthropic.com\ngemini.google.com\ngenerativelanguage.googleapis.com\nai.google.dev\ngrok.com\nx.ai',
    rule_sets: 'openai,claude,gemini',
    priority: 10,
  },
  google: {
    name: 'Google 服务',
    domains:
      'google.com\ngoogleapis.com\ngstatic.com\ngoogleusercontent.com\ngooglevideo.com\nyoutube.com\nytimg.com\nyoutu.be',
    rule_sets: '',
    priority: 30,
  },
  custom: { name: '', domains: '', rule_sets: '', priority: 50 },
};

function groupOptions(id) {
  return state.groups
    .filter((g) => g.enabled)
    .map((g) => `<option value="${g.id}" ${g.id == id ? 'selected' : ''}>${e(g.name)}</option>`)
    .join('');
}

function renderRules() {
  $('#page-rules').innerHTML = /* HTML */ `<div class="split page-intro">
      <p class="muted">
        先定义服务域名，再选择出口。AI 规则优先于 Google，未命中的境外服务使用默认出口。
      </p>
      <button class="btn primary" id="add-rule">＋ 添加规则</button>
    </div>
    <div class="template-grid">
      ${[
        ['ai', 'AI 服务', 'ChatGPT · Claude · Gemini · Grok'],
        ['google', 'Google 服务', '搜索 · YouTube · Google APIs'],
        ['custom', '自定义服务', '为常用服务指定专属出口'],
      ]
        .map(
          ([id, title, desc]) =>
            `<button class="template" data-template="${id}"><strong>${title}</strong><span>${desc}</span><span class="accent">＋ 使用模板</span></button>`,
        )
        .join('')}
    </div>
    <div class="rule-list">
      ${
        state.rules
          .map((r) => {
            const g = state.groups.find((g) => g.id === r.group_id);
            return /* HTML */ `<article class="panel rule-card">
              <div>
                <span class="tag">优先级 ${r.priority}</span>
                <span class="tag ${r.enabled ? 'ok' : ''}">${r.enabled ? '启用' : '停用'}</span>
                <h2>${e(r.name)}</h2>
                <p class="muted small domain-preview">
                  ${e(r.rule_sets.split(',').filter(Boolean).join(' · '))}
                  ${e(r.domains.split('\n').slice(0, 5).join(' · '))}${r.domains.split('\n').length > 5 ? ' …' : ''}
                </p>
              </div>
              <div class="rule-target">
                <span class="muted small">转发至</span
                ><select aria-label="${e(r.name)}的出口代理组" data-target="${r.id}">
                  ${groupOptions(r.group_id)}</select
                ><span class="small muted">当前 ${e(g?.now || '等待内核')}</span>
              </div>
              <div class="row">
                <button class="btn" data-redit="${r.id}">编辑</button
                ><button class="btn subtle warn" data-rdel="${r.id}">删除</button>
              </div>
            </article>`;
          })
          .join('') || empty('添加一条规则，为特定服务选择出口。')
      }
    </div>`;
  $('#add-rule').onclick = () => ruleForm();
  document
    .querySelectorAll('[data-template]')
    .forEach((b) => (b.onclick = () => ruleForm(null, b.dataset.template)));
  document
    .querySelectorAll('[data-redit]')
    .forEach((b) => (b.onclick = () => ruleForm(state.rules.find((r) => r.id == b.dataset.redit))));
  document.querySelectorAll('[data-rdel]').forEach(
    (b) =>
      (b.onclick = act(async () => {
        if (confirm('删除规则后，该服务会交给后续规则或默认出口，继续？'))
          await api(`/api/rules/${b.dataset.rdel}`, { method: 'DELETE' });
      })),
  );
  document.querySelectorAll('[data-target]').forEach(
    (b) =>
      (b.onchange = act(() =>
        api(`/api/rules/${b.dataset.target}`, {
          method: 'PUT',
          body: JSON.stringify({ group_id: Number(b.value) }),
        }),
      )),
  );
}

function ruleForm(rule, template = 'custom') {
  if (!state.groups.some((g) => g.enabled)) {
    toast('请先创建一个启用的代理组', true);
    return;
  }
  const r = rule || templates[template],
    sets = new Set((r.rule_sets || '').split(','));
  const target =
    rule?.group_id ||
    (template === 'ai'
      ? state.rules.find((r) => (r.rule_sets || '').split(',').includes('openai'))?.group_id ||
        state.groups.find((g) => g.name === '台湾家宽')?.id
      : state.groups.find((g) => g.is_default)?.id);
  openModal(
    /* HTML */ `<h2 id="dialog-title">${rule ? '编辑' : '添加'}分流规则</h2>
      <label for="r-name">服务名称</label><input id="r-name" value="${e(r.name)}" /><label
        for="r-domains"
        >域名，每行一个（包含其子域名）</label
      ><textarea id="r-domains" placeholder="example.com">${e(r.domains)}</textarea>
      <p class="small muted">
        如需只匹配完整域名，填写 DOMAIN,example.com。AI 与 Google 重叠的域名由更小的优先级先匹配。
      </p>
      <label>补充规则集</label>
      <div class="row">
        ${['openai', 'claude', 'gemini'].map((n) => `<label class="check"><input data-rset value="${n}" type="checkbox" ${sets.has(n) ? 'checked' : ''}>${n}</label>`).join('')}
      </div>
      <label for="r-group">出口代理组</label
      ><select id="r-group">
        ${groupOptions(target)}</select
      ><label for="r-priority">匹配优先级（越小越先）</label
      ><input id="r-priority" type="number" value="${r.priority}" /><label class="check"
        ><input
          id="r-enabled"
          type="checkbox"
          ${!rule || rule.enabled ? 'checked' : ''}
        />启用规则</label
      >${formActions('save-rule')}`,
  );
  $('#cancel').onclick = closeModal;
  $('#save-rule').onclick = act(async () => {
    const body = {
      name: $('#r-name').value,
      domains: $('#r-domains').value,
      rule_sets: [...document.querySelectorAll('[data-rset]:checked')].map((x) => x.value),
      group_id: Number($('#r-group').value),
      priority: Number($('#r-priority').value),
      enabled: $('#r-enabled').checked,
    };
    await api(rule ? `/api/rules/${rule.id}` : '/api/rules', {
      method: rule ? 'PUT' : 'POST',
      body: JSON.stringify(body),
    });
    closeModal();
  });
}

export { renderRules };
