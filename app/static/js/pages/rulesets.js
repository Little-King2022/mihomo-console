import { $, e, empty, formActions } from '../core/dom.js';
import { state } from '../core/state.js';
import { api } from '../core/api.js';
import { act } from '../core/actions.js';
import { openModal, closeModal } from '../ui/modal.js';

export function renderRulesets() {
  $('#page-rulesets').innerHTML = `<div class="split page-intro"><p class="muted">管理 Mihomo 规则集；新增后可在「分流规则」中选择并指定出口。基础分流、DNS 与 TUN 使用的规则集受到删除保护。</p><button class="btn primary" id="add-ruleset">＋ 新增规则集</button></div>
    <div class="rule-list">${state.rulesets.map(item => `<article class="panel rule-card ruleset-card"><div>
      <span class="tag">${e(item.type)} · ${e(item.behavior)} · ${e(item.format)}</span>
      <h2>${e(item.name)}</h2><p class="small muted ruleset-source">${item.type === 'http' ? `更新周期 ${item.interval} 秒 · ${e(item.url)}` : `内联规则 ${item.payload.length} 条`}</p>
      <p class="small muted">${e(item.references.join('；') || '尚未引用，可在分流规则中选择')}</p></div>
      <div class="row"><button class="btn" data-set-entries="${e(item.name)}" ${item.format === 'mrs' ? 'disabled title="MRS 为二进制格式，请改用 YAML 或 text 来源"' : ''}>查看 / 编辑条目</button><button class="btn" data-set-edit="${e(item.name)}">编辑来源</button><button class="btn subtle warn" data-set-delete="${e(item.name)}" ${item.references.length ? 'disabled title="请先解除引用"' : ''}>删除</button></div></article>`).join('') || empty('尚无规则集')}
    </div>`;
  $('#add-ruleset').onclick = () => rulesetForm();
  document.querySelectorAll('[data-set-entries]').forEach(button => {
    button.onclick = () => rulesetEntries(button.dataset.setEntries);
  });
  document.querySelectorAll('[data-set-edit]').forEach(button => {
    button.onclick = () => rulesetForm(state.rulesets.find(x => x.name === button.dataset.setEdit));
  });
  document.querySelectorAll('[data-set-delete]').forEach(button => {
    button.onclick = act(async () => {
      if (confirm('删除此规则集？')) await api('/api/rulesets/' + encodeURIComponent(button.dataset.setDelete), { method: 'DELETE' });
    });
  });
}

function rulesetForm(item) {
  const value = item || { name: '', type: 'http', behavior: 'domain', format: 'yaml', url: '', interval: 86400, payload: [] };
  const select = (id, values, chosen) => `<select id="${id}" ${item?.protected ? 'disabled' : ''}>${values.map(v => `<option ${v === chosen ? 'selected' : ''}>${v}</option>`).join('')}</select>`;
  openModal(`<h2 id="dialog-title">${item ? '编辑' : '新增'}规则集</h2>
    <label for="rs-name">名称（创建后不可修改）</label><input id="rs-name" value="${e(value.name)}" ${item ? 'disabled' : ''} maxlength="64" placeholder="example-domains">
    <label for="rs-type">来源</label>${select('rs-type', ['http', 'inline'], value.type)}
    <label for="rs-behavior">规则行为</label>${select('rs-behavior', ['domain', 'ipcidr', 'classical'], value.behavior)}
    <label for="rs-format">文件格式</label>${select('rs-format', ['yaml', 'text', 'mrs'], value.format)}
    <p class="small muted">domain 为域名列表，ipcidr 为 IP 网段，classical 为完整规则。MRS 支持 domain / ipcidr；内联内容使用 YAML。</p>
    <div id="rs-http"><label for="rs-url">下载 URL</label><input id="rs-url" type="url" value="${e(value.url)}" placeholder="https://example.com/rules.yaml">
    <label for="rs-interval">更新周期（秒，至少 60）</label><input id="rs-interval" type="number" min="60" max="31536000" value="${value.interval}"></div>
    <div id="rs-inline"><label for="rs-payload">规则内容（每行一条，无需 payload 标头）</label><textarea id="rs-payload" rows="10" placeholder="+.example.com">${e(value.payload.join('\n'))}</textarea></div>
    ${item?.protected ? '<p class="small muted">此集合用于基础分流、DNS 或国内绕过；保留原来源类型、行为和格式。</p>' : ''}
    ${formActions('save-ruleset')}`);
  const showSource = () => {
    const inline = $('#rs-type').value === 'inline';
    $('#rs-http').classList.toggle('hidden', inline);
    $('#rs-inline').classList.toggle('hidden', !inline);
    if (inline) $('#rs-format').value = 'yaml';
    $('#rs-format').disabled = !!item?.protected || inline;
  };
  $('#rs-type').onchange = showSource;
  showSource();
  $('#cancel').onclick = closeModal;
  $('#save-ruleset').onclick = act(async () => {
    const body = { name: $('#rs-name').value.trim(), type: $('#rs-type').value,
      behavior: $('#rs-behavior').value, format: $('#rs-format').value,
      url: $('#rs-url').value.trim(), interval: Number($('#rs-interval').value), payload: $('#rs-payload').value };
    await api(item ? '/api/rulesets/' + encodeURIComponent(item.name) : '/api/rulesets', {
      method: item ? 'PUT' : 'POST', body: JSON.stringify(body),
    });
    closeModal();
  });
}


async function rulesetEntries(name) {
  let page = 1, query = '', generation = 0, closed = false;
  openModal(`<h2 id="dialog-title">${e(name)} · 编辑条目</h2>
    <p class="small muted">HTTP 规则的删除保存为本地排除，上游更新后仍然有效；可在下方恢复。删除只影响当前规则集，不会强制指定直连或代理出口。</p>
    <form id="rs-entry-search" class="row"><input id="rs-entry-query" type="search" aria-label="搜索规则条目" placeholder="搜索域名、网段或规则…" maxlength="253"><button class="btn" type="submit">搜索</button></form>
    <p id="rs-entry-count" class="small muted" aria-live="polite"></p><div id="rs-entry-list" class="ruleset-entries"></div>
    <div class="row"><button class="btn" id="rs-entry-prev">上一页</button><button class="btn" id="rs-entry-next">下一页</button></div>
    <h3>已删除的上游条目</h3><div id="rs-entry-excluded" class="ruleset-entries"></div>
    <div class="actions"><button class="btn" id="cancel">关闭</button></div>`, {onClose: () => {closed = true; generation++;}});
  $('#cancel').onclick = closeModal;
  const load = async () => {
    const version = ++generation;
    $('#rs-entry-count').textContent = '正在读取规则…';
    try {
      const report = await api(`/api/rulesets/${encodeURIComponent(name)}/entries?query=${encodeURIComponent(query)}&page=${page}`);
      if (closed || version !== generation) return;
      $('#rs-entry-count').textContent = `有效 ${report.total} 条 · 搜索匹配 ${report.matched} 条 · 第 ${page} 页（每页 ${report.page_size} 条）`;
      $('#rs-entry-list').innerHTML = report.entries.map((entry, i) => `<div class="ruleset-entry"><code>${e(entry)}</code><button class="btn subtle warn" data-entry-remove="${i}">删除</button></div>`).join('') || empty('没有匹配的规则');
      const excluded = report.excluded.filter(entry => entry.toLowerCase().includes(query.toLowerCase()));
      $('#rs-entry-excluded').innerHTML = excluded.map((entry,i) => `<div class="ruleset-entry"><code>${e(entry)}</code><button class="btn" data-entry-restore="${i}">恢复</button></div>`).join('') || empty('没有已删除的上游条目');
      $('#rs-entry-prev').disabled = page <= 1;
      $('#rs-entry-next').disabled = page * report.page_size >= report.matched;
      const change = (entry, restore) => act(async () => {
        if (!restore && !confirm(`从 ${name} 删除此条目？\n${entry}`)) return;
        await api(`/api/rulesets/${encodeURIComponent(name)}/entries`, {method: 'POST', body: JSON.stringify({entry, restore})});
        await load();
      });
      document.querySelectorAll('[data-entry-remove]').forEach(button => {button.onclick = change(report.entries[Number(button.dataset.entryRemove)], false);});
      document.querySelectorAll('[data-entry-restore]').forEach(button => {button.onclick = change(excluded[Number(button.dataset.entryRestore)], true);});
    } catch (error) {
      if (!closed && version === generation) $('#rs-entry-count').textContent = error.message;
    }
  };
  $('#rs-entry-search').onsubmit = event => {event.preventDefault(); page = 1; query = $('#rs-entry-query').value.trim(); load();};
  $('#rs-entry-prev').onclick = () => {if (page > 1) {page--; load();}};
  $('#rs-entry-next').onclick = () => {page++; load();};
  await load();
}
