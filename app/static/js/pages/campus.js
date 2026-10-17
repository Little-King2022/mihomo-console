import { $, e, empty, formActions } from '../core/dom.js';
import { state } from '../core/state.js';
import { api } from '../core/api.js';
import { act } from '../core/actions.js';
import { stamp } from '../core/format.js';
import { openModal, closeModal } from '../ui/modal.js';

let snapshot = null;
let loading = null;
let loadError = '';
let checking = false;
let probeInitialized = false;
const probe = { kind: 'route', target: '', port: '443', domain: '' };

export function refreshCampus() {
  if (loading) return loading;
  loadError = '';
  loading = api('/api/campus')
    .then((data) => { state.campus = data; })
    .catch((error) => { loadError = error.message; })
    .finally(() => {
      loading = null;
      if (state.page === 'campus') renderCampus();
    });
  return loading;
}

function entryCard(entry, kind) {
  return `<article class="campus-entry">
    <div><div class="row"><strong>${e(entry.name)}</strong><span class="tag ${entry.enabled ? 'ok' : ''}">${entry.enabled ? '启用' : '停用'}</span></div>
    <p><code>${e(kind === 'networks' ? entry.cidr : entry.domain)}</code>${kind === 'dns' ? ` → <code>${e(entry.server || '默认解析器')}</code>` : ''}</p><p class="muted small">${entry.direct !== false ? '同时使用直连规则' : kind === 'networks' ? '仅排除 TUN' : '仅设置真实 IP 解析'}</p></div>
    <div class="row"><button class="btn" data-campus-edit="${e(entry.id)}" data-kind="${kind}">编辑</button>
    <button class="btn" data-campus-toggle="${e(entry.id)}" data-kind="${kind}">${entry.enabled ? '停用' : '启用'}</button>
    <button class="btn subtle warn" data-campus-delete="${e(entry.id)}" data-kind="${kind}">删除</button></div></article>`;
}

function diagnosticResult() {
  if (!snapshot) return '<p class="muted small">尚未检测。仅在点击检查时发起请求；Ping 无响应不一定代表断网。</p>';
  const r = snapshot.route;
  return `<div class="campus-result" role="status"><span class="tag ${snapshot.ok ? 'ok' : ''}">${snapshot.ok ? '检查通过' : '检查失败'}</span>
    <p>${e(snapshot.message)}</p><p class="small muted">${e(snapshot.target)}${snapshot.port ? `:${e(snapshot.port)}` : ''} · ${e(snapshot.domain || '')} · ${e(stamp(snapshot.checked_at))}</p>
    ${r ? `<dl class="campus-facts"><div><dt>出口网卡</dt><dd>${e(r.interface)}</dd></div><div><dt>校园源 IP</dt><dd>${e(r.source || '未知')}</dd></div><div><dt>网关</dt><dd>${e(r.gateway || '链路直达')}</dd></div><div><dt>路由表</dt><dd>${e(r.table)}</dd></div></dl>` : ''}
    ${snapshot.answers ? `<p>解析结果：<code>${e(snapshot.answers.join(' · '))}</code></p>` : ''}
    ${snapshot.latency_ms != null ? `<p class="muted small">${snapshot.kind === 'ping' ? 'ICMP 延迟' : '请求耗时'} ${e(snapshot.latency_ms)} ms</p>` : ''}
    <p class="muted small">此结果为检测时的快照；修改网段或网络后请重新检测。</p></div>`;
}

export function renderCampus() {
  const root = $('#page-campus');
  const c = state.campus;
  if (!c) {
    root.innerHTML = loadError ? `<div class="panel"><p class="err">${e(loadError)}</p><button class="btn" id="campus-retry">重新加载</button></div>` : empty('正在读取校园网配置…');
    if (loadError) $('#campus-retry').onclick = () => refreshCampus();
    else refreshCampus();
    return;
  }
  if (!probeInitialized) {
    const dns = c.dns.find((d) => d.enabled && d.server);
    probe.target = dns?.server || '';
    probe.domain = dns?.domain.replace(/^\+\./, '') || '';
    probeInitialized = true;
  }
  const system = c.system;
  const physical = system.interfaces.filter((i) => i.type === 'ether' && /^(en|eth|wl)/.test(i.name));
  root.innerHTML = `<div class="campus-stack">
    <div class="panel campus-summary"><div><span class="eyebrow">CAMPUS ACCESS</span><h2>校园资源，使用校园出口。</h2><p class="muted">启用的网段绕过 TUN，沿用系统路由与 DHCP 网关。所有条目均可编辑、停用或删除，保存后校验并应用。</p></div>
    <div class="row"><span class="tag ${c.config_matches ? 'ok' : ''}">${c.config_matches === true ? '已写入生成配置' : c.config_matches === false ? '生成配置与设置不一致' : '尚无生成配置'}</span><span class="tag">${c.tun ? 'TUN 已开启' : 'TUN 已关闭'}</span></div></div>
    ${loadError ? `<p class="err">${e(loadError)}</p>` : ''}
    <div class="campus-grid"><section class="panel"><div class="panel-head"><div><h2>TUN 排除网段</h2><p>可填写单个 IP 或 CIDR 网段；停用后恢复后续路由策略。</p></div><button class="btn primary" id="campus-add-network">＋ 新增网段</button></div>
    ${c.networks.map((n) => entryCard(n, 'networks')).join('') || empty('暂无排除网段。可新增 IPv4 地址或 CIDR 网段。')}
    </section><section class="panel"><div class="panel-head"><div><h2>真实 IP 解析例外与 DNS</h2><p>每个域名独立编辑；DNS 留空使用默认解析器。</p></div><button class="btn primary" id="campus-add-dns">＋ 新增解析例外</button></div>
    ${c.dns.map((d) => entryCard(d, 'dns')).join('') || empty('暂无真实 IP 解析例外。')}</section></div>
    <section class="panel"><div class="panel-head"><div><h2>校园网连通性检查</h2><p>从本机校园物理网卡检查，不使用 HTTP 代理。DNS 检查须选择已配置的域名与服务器。</p></div></div>
      <form id="campus-probe" class="campus-probe"><div><label for="campus-kind">检查方式</label><select id="campus-kind">${[['route', '实际出口路由'], ['ping', 'Ping / ICMP'], ['tcp', 'TCP 端口'], ['dns', '校园 DNS 解析']].map(([v, t]) => `<option value="${v}" ${probe.kind === v ? 'selected' : ''}>${t}</option>`).join('')}</select></div>
      <div><label for="campus-target">${probe.kind === 'dns' ? 'DNS 服务器 IPv4' : '目标 IPv4'}</label><input id="campus-target" value="${e(probe.target)}" required></div>
      ${probe.kind === 'tcp' ? `<div><label for="campus-port">TCP 端口</label><input id="campus-port" type="number" min="1" max="65535" value="${e(probe.port)}" required></div>` : ''}
      ${probe.kind === 'dns' ? `<div><label for="campus-domain">具体域名</label><input id="campus-domain" value="${e(probe.domain)}" required></div>` : ''}
      <button class="btn primary" type="submit" ${checking ? 'disabled aria-busy="true"' : ''}>${checking ? '正在检查…' : '开始检查'}</button></form>
      <div id="campus-probe-result">${diagnosticResult()}</div></section>
    <section class="panel"><div class="panel-head"><div><h2>系统网卡与路由</h2><p>展示当前系统主路由表。网关和本机地址由系统网络管理器维护，页面管理上述 TUN 绕过规则。</p></div><button class="btn" id="campus-status-refresh">刷新状态</button></div>
    ${system.error ? `<p class="err">${e(system.error)}</p>` : ''}
    <div class="campus-tags">${physical.map((i) => `<span class="tag ${i.state === 'UP' ? 'ok' : ''}">${e(i.name)} · ${e(i.addresses.join(', ') || '未分配 IPv4')} · ${e(i.state)}</span>`).join('')}</div>
    <p class="muted small">系统下发 DNS：${e(system.dns_servers.join(' · ') || '未读取到')} · 默认网卡：${e(system.default || '未知')}</p>
    <div class="campus-table-wrap"><table class="campus-table"><thead><tr><th>目的网段</th><th>网关</th><th>网卡</th><th>源 IP</th><th>来源</th></tr></thead><tbody>${system.routes.map((r) => `<tr><td><code>${e(r.dst === 'default' ? '默认路由' : r.dst)}</code></td><td>${e(r.gateway || '链路直达')}</td><td>${e(r.dev || '—')}</td><td>${e(r.prefsrc || '—')}</td><td>${e(r.protocol || '—')}${r.flags?.includes('linkdown') ? ' · 链路断开' : ''}</td></tr>`).join('')}</tbody></table></div></section>
    <p class="muted small">TUN 排除适用于透明代理流量；显式使用 HTTP / SOCKS 代理的应用仍会进入内核。勾选“同时启用直连规则”时也会生成 DIRECT 规则。检查通过只代表当前项目可用，不表示校园认证一定完成。</p>
    </div>`;
  $('#campus-add-network').onclick = () => entryForm('networks');
  $('#campus-add-dns').onclick = () => entryForm('dns');
  $('#campus-status-refresh').onclick = act(refreshCampus);
  root.querySelectorAll('[data-campus-edit]').forEach((b) => {
    b.onclick = () => entryForm(b.dataset.kind, c[b.dataset.kind].find((n) => n.id === b.dataset.campusEdit));
  });
  root.querySelectorAll('[data-campus-toggle]').forEach((b) => {
    b.onclick = act(async () => {
      const entry = c[b.dataset.kind].find((n) => n.id === b.dataset.campusToggle);
      await saveEntry(b.dataset.kind, { enabled: !entry.enabled }, entry);
    });
  });
  root.querySelectorAll('[data-campus-delete]').forEach((b) => {
    b.onclick = act(async () => {
      if (!confirm('删除此条目并重新应用配置？删除后将按其余配置处理，不会自动恢复。')) return;
      await api(`/api/campus/${b.dataset.kind}/${b.dataset.campusDelete}`, { method: 'DELETE' });
      await refreshCampus();
    });
  });
  for (const key of ['target', 'port', 'domain']) {
    const input = $('#campus-' + key);
    if (input) input.oninput = () => { probe[key] = input.value; };
  }
  $('#campus-kind').onchange = (event) => { probe.kind = event.target.value; renderCampus(); };
  $('#campus-probe').onsubmit = async (event) => {
    event.preventDefault();
    if (checking) return;
    checking = true;
    const body = { ...probe, port: Number(probe.port) };
    renderCampus();
    try {
      snapshot = await api('/api/campus/diagnostics', { method: 'POST', body: JSON.stringify(body) });
    } catch (error) {
      snapshot = { ...body, ok: false, message: error.message, checked_at: new Date().toISOString() };
    } finally {
      checking = false;
      if (state.page === 'campus') renderCampus();
    }
  };
}

async function saveEntry(kind, body, entry) {
  await api(`/api/campus/${kind}${entry ? '/' + entry.id : ''}`, {
    method: entry ? 'PUT' : 'POST', body: JSON.stringify(body),
  });
  await refreshCampus();
}

function entryForm(kind, entry) {
  const network = kind === 'networks';
  openModal(`<h2 id="dialog-title">${entry ? '编辑' : '新增'}${network ? 'TUN 排除网段' : '真实 IP 解析例外'}</h2>
    <label for="campus-name">名称</label><input id="campus-name" maxlength="80" value="${e(entry?.name || '')}" placeholder="例如：图书馆资源">
    ${network ? `<label for="campus-cidr">IPv4 地址或 CIDR 网段</label><input id="campus-cidr" value="${e(entry?.cidr || '')}" placeholder="192.0.2.0/24"><p class="muted small">填写学校提供的真实网段。保存后绕过 TUN，沿用系统出口；删除或停用后不再排除此网段；其他重叠的启用条目仍会生效。</p>` : `<label for="campus-dns-domain">域名</label><input id="campus-dns-domain" value="${e(entry?.domain || '')}" placeholder="+.example.edu.cn"><label for="campus-server">DNS 服务器 IPv4（可选）</label><input id="campus-server" value="${e(entry?.server || '')}" placeholder="留空使用默认解析器"><p class="muted small">+. 前缀包含子域名；域名使用真实 IP 解析。指定 DNS 服务器时，该服务器会自动排除 TUN。</p>`}
    <label class="check"><input id="campus-direct" type="checkbox" ${entry?.direct !== false ? 'checked' : ''}>同时为此${network ? '网段' : '域名'}启用直连规则（含显式代理流量）</label>
    <label class="check"><input id="campus-enabled" type="checkbox" ${!entry || entry.enabled ? 'checked' : ''}>启用此条目</label>
    ${formActions('campus-save')}`);
  $('#cancel').onclick = closeModal;
  $('#campus-save').onclick = act(async () => {
    const body = { name: $('#campus-name').value, enabled: $('#campus-enabled').checked, direct: $('#campus-direct').checked };
    if (network) body.cidr = $('#campus-cidr').value;
    else Object.assign(body, { domain: $('#campus-dns-domain').value, server: $('#campus-server').value });
    await saveEntry(kind, body, entry);
    closeModal();
  });
}
