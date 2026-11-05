import { $, e, empty } from '../core/dom.js';
import { state } from '../core/state.js';
import { api } from '../core/api.js';
import { stamp } from '../core/format.js';

const query = { target: '', port: '443', probe: false };
let pending = false;
let report = null;
let error = '';

function fact(name, value) {
  return `<div><dt>${e(name)}</dt><dd>${e(value ?? '—')}</dd></div>`;
}

export function pathLabel(path) {
  return { kernel: 'Linux 内核直连', core: '进入 mihomo', system: 'TUN 已关闭 · 系统路由', unknown: '转发路径待确认' }[path] || '转发路径待确认';
}

function resultCard(result) {
  const rule = result.rule;
  const route = result.system_route;
  const probe = result.probe;
  const observed = probe?.observed;
  const bypass = result.kernel;
  const throughCore = result.path === 'core';
  return `<article class="panel routing-result">
    <div class="panel-head"><h2>${e(result.address)}</h2><span class="tag">${result.fake_ip ? 'Fake IP' : '真实 IP'}</span></div>
    <h3>${e(pathLabel(result.path))}</h3>
    <p class="muted">${result.path === 'kernel' ? '命中正在运行的 nftables 目标地址绕过规则，不由 mihomo 转发此目标流量。' : throughCore ? '运行中的 TUN 已启用，未命中已读取的地址绕过规则。实际规则与节点以连接实测为准。' : '当前状态不足以确认由 mihomo 接管。'}</p>
    <dl class="campus-facts">
      ${fact('LAN 绕过', bypass.lan_bypass === null ? '未知' : bypass.lan_bypass ? '命中 PREROUTING 绕过' : '未命中地址绕过')}
      ${fact('系统路由接口', route?.interface)}${fact('系统网关', route?.gateway || (route ? '同链路' : null))}${fact('本机源地址', route?.source)}
    </dl>
    <div class="routing-step"><strong>${throughCore ? '规则预测' : '显式代理时的规则预测'}</strong><p>${e(rule.proxy || '无法确定')} ${rule.certain ? '' : '（存在未能求值的前序规则，候选结果）'}</p><code>${e([rule.type, rule.payload].filter(Boolean).join(' · '))}</code><p class="muted">${e(result.chain?.join(' → ') || '未读取到当前节点')}</p>
    ${rule.unknown_before?.length ? `<p class="muted">待内核实测确认：${e(rule.unknown_before.map(r => `${r.type} ${r.payload}`).join('、'))}</p>` : ''}</div>
    ${Object.keys(bypass.evidence || {}).length ? `<div class="routing-step"><strong>内核绕过依据</strong>${Object.entries(bypass.evidence).map(([chain, proof]) => `<p><code>${e(chain)} #${e(proof.handle)}</code> · ${e(proof.set || '内联目标地址集合')} · <code>${e(typeof proof.match === 'string' ? proof.match : JSON.stringify(proof.match))}</code></p>`).join('')}</div>` : ''}
    <div class="routing-step"><strong>LAN 源地址转换</strong><p>${result.lan_nat.length ? result.lan_nat.map(n => `${e(n.source)} → ${e(n.interface)} · MASQUERADE`).join('<br>') : throughCore ? '由代理节点建立出口连接；本页不将 LAN 的直连 NAT 套用到节点连接。' : '未匹配到控制台管理的 LAN NAT 候选规则。'}</p><p class="muted">按保存的源网段、目的网段与出口推导，未抓取终端 NAT 会话。</p></div>
    ${probe ? `<div class="routing-step" role="status"><strong>连接实测 · ${probe.ok ? '成功' : '未完成'} · ${e(probe.latency_ms)} ms</strong><p>${e(probe.message)}</p>${observed ? `<p>内核连接记录：<code>${e(observed.rule)} ${e(observed.payload || '')}</code></p><p>${e(observed.chains.slice().reverse().join(' → '))}</p><p class="muted">入站 ${e(observed.inbound)} · ${e(observed.host || observed.destination)}</p>` : '<p class="muted">未捕获匹配本次源端口的内核连接；这本身不能证明已经绕过内核。</p>'}${probe.note ? `<p class="muted">${e(probe.note)}</p>` : ''}</div>` : ''}
  </article>`;
}

export function renderRoutingDiagnostics() {
  if (state.page !== 'diagnostics') return;
  const container = $('#page-diagnostics');
  container.innerHTML = `<div class="campus-stack">
    <div class="panel"><div class="panel-head"><h2>检查一个目标</h2></div>
      <form id="routing-diagnostic-form" class="campus-probe">
        <div><label for="routing-target">域名或 IPv4 地址</label><input id="routing-target" placeholder="example.com / 223.5.5.5" value="${e(query.target)}" maxlength="253" required ${pending ? 'disabled' : ''}></div>
        <div class="routing-port"><label for="routing-port">TCP 端口</label><input id="routing-port" type="number" min="1" max="65535" value="${e(query.port)}" required ${pending ? 'disabled' : ''}></div>
        <button class="btn primary" type="submit" ${pending ? 'disabled' : ''}>${pending ? '诊断中…' : '开始诊断'}</button>
      </form>
      <label class="routing-probe-option"><input id="routing-probe" type="checkbox" ${query.probe ? 'checked' : ''} ${pending ? 'disabled' : ''}>同时连接实测（首个 IP；443 端口验证 TLS 证书）</label>
      <p class="muted small">仅手动执行。读取 LAN DNS、运行中的分流规则与 nftables；不改变模式、规则或节点。系统路由查询不包含透明代理重定向，不能单独用它判断出口。</p>
      ${error ? `<p class="err" role="alert">${e(error)}</p>` : ''}
    </div>
    <div id="routing-report" aria-live="polite" aria-busy="${pending}">${report ? `<div class="panel"><div class="panel-head"><h2>${e(report.target)}:${e(report.port)}</h2><span class="small muted">${e(stamp(report.checked_at))}</span></div><dl class="campus-facts">${fact('运行模式', report.mode)}${fact('TUN', report.tun ? '启用' : '关闭')}${fact('DNS 入口', report.dns.server || '直接输入 IP')}${fact('DNS 状态', report.dns.status)}</dl>${report.dns.server ? `<p class="small muted">DNS 策略：${e(report.dns.policy || '默认解析')} · 配置上游 ${e([report.dns.upstream].flat().join('、'))}<br>${e(report.dns.upstream_note)}</p>` : ''}<p class="small muted">${e(report.scope)}</p>${report.warnings.map(w => `<p class="err">${e(w)}</p>`).join('')}</div><div class="routing-results">${report.results.map(resultCard).join('')}</div>` : `<div class="panel">${empty('输入需要排查的目标，查看 DNS → 内核绕过 → 分流规则 → 出口与 NAT。')}</div>`}</div>
  </div>`;
  $('#routing-target').oninput = event => { query.target = event.target.value; };
  $('#routing-port').oninput = event => { query.port = event.target.value; };
  $('#routing-probe').onchange = event => { query.probe = event.target.checked; };
  $('#routing-diagnostic-form').onsubmit = event => { event.preventDefault(); refreshRoutingDiagnostics(); };
}

export async function refreshRoutingDiagnostics() {
  if (pending || !query.target.trim()) return;
  pending = true;
  error = '';
  report = null;
  renderRoutingDiagnostics();
  try {
    report = await api('/api/routing/diagnostics', { method: 'POST', body: JSON.stringify({ target: query.target, port: Number(query.port), probe: query.probe }) });
  } catch (failure) {
    error = failure.message === 'unauthorized' ? '请重新登录后诊断' : failure.message;
  } finally {
    pending = false;
    renderRoutingDiagnostics();
  }
}
