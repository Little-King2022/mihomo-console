import { $, e, empty, formActions } from '../core/dom.js';
import { state } from '../core/state.js';
import { api } from '../core/api.js';
import { act } from '../core/actions.js';
import { openModal, closeModal } from '../ui/modal.js';

let data = null;
let loading = null;
let error = '';

export function refreshRouter() {
  if (loading) return loading;
  loading = api('/api/router').then((r) => { data = r; error = ''; })
    .catch((err) => { error = err.message; })
    .finally(() => { loading = null; if (state.page === 'router') renderRouter(); });
  return loading;
}

async function save(config) {
  data = await api('/api/router', { method: 'PUT', body: JSON.stringify(config) });
  renderRouter();
}

function buttons(kind, index) {
  return `<button class="btn" data-router-edit="${kind}" data-index="${index}">编辑</button>
    <button class="btn subtle warn" data-router-delete="${kind}" data-index="${index}">删除</button>`;
}

function section(kind, title, headers, rows) {
  return `<section class="panel"><div class="panel-head"><h2>${title}</h2><button class="btn" data-router-add="${kind}">＋ 添加</button></div>
    <div class="campus-table-wrap"><table class="campus-table"><thead><tr>${headers.map((h) => `<th>${h}</th>`).join('')}<th>操作</th></tr></thead>
    <tbody>${rows.map((cells, i) => `<tr>${cells.map((v) => `<td>${e(v)}</td>`).join('')}<td><div class="row">${buttons(kind, i)}</div></td></tr>`).join('')}</tbody></table></div></section>`;
}

export function renderRouter() {
  const root = $('#page-router');
  if (!data) {
    root.innerHTML = error ? `<p class="err">${e(error)}</p><button class="btn" id="router-retry">重试</button>` : empty('正在读取路由器配置…');
    if (error) $('#router-retry').onclick = refreshRouter;
    else refreshRouter();
    return;
  }
  if (!data.configured) { root.innerHTML = empty('尚未导入本机路由器配置。'); return; }
  const c = data.config;
  root.innerHTML = `<div class="campus-stack">
    <section class="panel"><div class="panel-head"><div><h2>网络出口与透明代理</h2><p>主机、LAN 与容器统一使用 TUN 分流；校园和国内地址由系统直接转发。</p></div><button class="btn" id="router-refresh">刷新状态</button></div>
    <div class="campus-tags">${c.uplinks.map((u) => `<span class="tag">${e(u.name)} · ${e(u.interface)} · ${u.default ? '主默认出口' : '专用出口'}</span>`).join('')}</div>
    <p class="small muted">LAN ${e(c.lan.interface)} · ${e(c.lan.address)} · 网桥端口 ${e(c.lan.ports.join('、'))}</p>
    <label class="check"><input id="router-domestic" type="checkbox" ${c.domestic_bypass ? 'checked' : ''}>国内 IP 绕过代理内核</label>
    <p class="small muted">校园范围在下方系统路由及“校园网”页面管理；指定代理服务的域名规则优先。</p>
    </section>
    <section class="panel"><h2>DHCP 与真实 DNS</h2><form id="router-dhcp" class="campus-probe">
      <div><label for="router-start">地址池起点</label><input id="router-start" value="${e(c.lan.dhcp_start)}" required></div>
      <div><label for="router-end">地址池终点</label><input id="router-end" value="${e(c.lan.dhcp_end)}" required></div>
      <div><label for="router-lease">租期（小时）</label><input id="router-lease" type="number" min="1" max="720" value="${c.lan.lease_hours}" required></div>
      <div><label for="router-host-dns">主机真实 DNS，空格分隔</label><input id="router-host-dns" value="${e(c.host_dns.join(' '))}" required></div>
      <button class="btn primary" type="submit">保存 DHCP 与 DNS</button></form>
      <p class="small muted">LAN 客户端的网关与 DNS 为 ${e(c.lan.address.split('/')[0])}。域名解析例外与校园 DNS 在“校园网”页面编辑。</p></section>
    ${section('routes', '校园系统路由', ['目的网段', '网关', '出口', '优先级', '状态'], c.routes.map((r) => [r.destination, r.gateway, r.interface, r.metric, (r.enabled ? '启用' : '停用') + (r.bypass ? ' · 绕过 TUN' : '')]))}
    ${section('masquerade', '出口源地址转换', ['LAN 源网段', '目标范围', '出口', '状态'], c.masquerade.map((r) => [r.source, r.destination === '0.0.0.0/0' ? '全部' : r.destination, r.interface, r.enabled ? '启用' : '停用']))}
    ${section('forwards', '端口转发', ['入口', '协议', '外部端口', '内部目标', '状态'], c.forwards.map((r) => [r.interface, r.protocol.toUpperCase(), r.port, `${r.target_ip}:${r.target_port}`, r.enabled ? '启用' : '停用']))}
    ${section('reservations', '固定 DHCP 租约', ['MAC 地址', 'IPv4', '主机名'], c.lan.reservations.map((r) => [r.mac, r.ip, r.name || '—']))}
    <section class="panel"><h2>当前 DHCP 租约</h2><div class="campus-table-wrap"><table class="campus-table"><thead><tr><th>主机名</th><th>IPv4</th><th>MAC</th></tr></thead><tbody>${data.leases.map((r) => `<tr><td>${e(r.name)}</td><td>${e(r.ip)}</td><td>${e(r.mac)}</td></tr>`).join('')}</tbody></table></div></section>
    <p class="muted small">保存会校验配置并备份，失败自动恢复。网桥成员、主机地址和主默认出口保持当前拓扑；Docker 的端口映射继续由 Docker 管理。</p>
  </div>`;
  $('#router-refresh').onclick = act(refreshRouter);
  $('#router-domestic').onchange = act(async (event) => {
    const next = structuredClone(c); next.domestic_bypass = event.target.checked; await save(next);
  });
  $('#router-dhcp').onsubmit = act(async (event) => {
    event.preventDefault();
    const next = structuredClone(c);
    Object.assign(next.lan, { dhcp_start: $('#router-start').value, dhcp_end: $('#router-end').value, lease_hours: Number($('#router-lease').value) });
    next.host_dns = $('#router-host-dns').value.trim().split(/\s+/); await save(next);
  });
  root.querySelectorAll('[data-router-add]').forEach((b) => { b.onclick = () => entryForm(b.dataset.routerAdd); });
  root.querySelectorAll('[data-router-edit]').forEach((b) => { b.onclick = () => entryForm(b.dataset.routerEdit, Number(b.dataset.index)); });
  root.querySelectorAll('[data-router-delete]').forEach((b) => {
    b.onclick = act(async () => {
      if (!confirm('删除此条目并应用网络配置？')) return;
      const next = structuredClone(c);
      (b.dataset.routerDelete === 'reservations' ? next.lan.reservations : next[b.dataset.routerDelete]).splice(Number(b.dataset.index), 1);
      await save(next);
    });
  });
}

function entryForm(kind, index) {
  const c = data.config;
  const entries = kind === 'reservations' ? c.lan.reservations : c[kind];
  const r = entries[index] || { enabled: true, bypass: true, metric: 500, protocol: 'both', source: c.lan.address, destination: '0.0.0.0/0' };
  const titles = { routes: '校园路由', masquerade: '源地址转换', forwards: '端口转发', reservations: '固定租约' };
  const fields = kind === 'routes' ? [['destination', '目的 CIDR'], ['gateway', '网关 IPv4'], ['metric', '优先级 metric', 'number']]
    : kind === 'masquerade' ? [['source', 'LAN 源 CIDR'], ['destination', '目的 CIDR，全部填 0.0.0.0/0']]
    : kind === 'forwards' ? [['port', '外部端口', 'number'], ['target_ip', '内部目标 IPv4'], ['target_port', '内部端口', 'number']]
    : [['mac', 'MAC 地址'], ['ip', '固定 IPv4'], ['name', '主机名（可选）']];
  const uplinks = kind === 'routes' ? c.uplinks.filter((u) => !u.default) : c.uplinks;
  openModal(`<h2 id="dialog-title">${index == null ? '添加' : '编辑'}${titles[kind]}</h2>
    ${fields.map(([key, title, type]) => `<label for="router-field-${key}">${title}</label><input id="router-field-${key}" type="${type || 'text'}" value="${e(r[key] ?? '')}" ${key === 'name' ? '' : 'required'}>`).join('')}
    ${kind !== 'reservations' ? `<label for="router-field-interface">出口 / 入口网卡</label><select id="router-field-interface">${uplinks.map((u) => `<option value="${e(u.interface)}" ${r.interface === u.interface ? 'selected' : ''}>${e(u.name)} · ${e(u.interface)}</option>`).join('')}</select>
    <label class="check"><input id="router-field-enabled" type="checkbox" ${r.enabled ? 'checked' : ''}>启用</label>` : ''}
    ${kind === 'routes' ? `<label class="check"><input id="router-field-bypass" type="checkbox" ${r.bypass ? 'checked' : ''}>此网段绕过 TUN</label>` : ''}
    ${kind === 'forwards' ? `<label for="router-field-protocol">协议</label><select id="router-field-protocol">${['both', 'tcp', 'udp'].map((v) => `<option value="${v}" ${r.protocol === v ? 'selected' : ''}>${v === 'both' ? 'TCP 与 UDP' : v.toUpperCase()}</option>`).join('')}</select>` : ''}
    ${formActions('router-save-entry')}`);
  $('#cancel').onclick = closeModal;
  $('#router-save-entry').onclick = act(async () => {
    const next = structuredClone(c);
    const list = kind === 'reservations' ? next.lan.reservations : next[kind];
    const row = { ...r, id: r.id || globalThis.crypto?.randomUUID?.() || Date.now().toString(36) + Math.random().toString(36).slice(2) };
    for (const [key, , type] of fields) row[key] = type === 'number' ? Number($('#router-field-' + key).value) : $('#router-field-' + key).value;
    if (kind !== 'reservations') Object.assign(row, { interface: $('#router-field-interface').value, enabled: $('#router-field-enabled').checked });
    if (kind === 'routes') row.bypass = $('#router-field-bypass').checked;
    if (kind === 'forwards') row.protocol = $('#router-field-protocol').value;
    if (index == null) list.push(row); else list[index] = row;
    await save(next); closeModal();
  });
}
