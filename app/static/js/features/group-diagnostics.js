import { $, e, empty } from '../core/dom.js';
import { state } from '../core/state.js';
import { api } from '../core/api.js';
import { icon } from '../ui/icons.js';

const sources = { ipsb: 'IP.SB', ipwhois: 'ipwho.is', ipapi: 'ipapi.is' };
const records = new Map();
let selected = null;
let source = 'ipsb';
let restored = false;
let initialized = false;

function diagnosticKey(group, kind, provider = source) {
  // A node change must not discard the snapshot or trigger another probe.
  return JSON.stringify([group.id, kind, kind === 'ip' ? provider : '']);
}

function latencyTone(delay) {
  return delay == null ? 'unavailable' : delay < 300 ? 'fast' : delay < 800 ? 'moderate' : 'slow';
}

function timeLabel(result) {
  if (!result?.checked_at) return '尚未检测';
  return '检测于 ' + new Date(result.checked_at).toLocaleTimeString([], { hour12: false });
}

function footer(record, group) {
  if (record?.pending) return '<span class="diagnostic-working">正在通过此代理组检测…</span>';
  const result = record?.data;
  const changed = result && (result.node_changed || (group.now && result.node !== group.now));
  return `<span>${e(result ? timeLabel(result) : '点击刷新开始检测')}</span>${result ? `<span class="diagnostic-node" title="检测时的出口节点">${e(result.node)}</span>${changed ? '<span class="diagnostic-warning">出口已发生变化，请手动刷新确认</span>' : ''}` : ''}`;
}

function ipPanel(group) {
  const record = records.get(diagnosticKey(group, 'ip'));
  const result = record?.pending ? null : record?.data;
  const info = result?.ip;
  const error = record?.error || result?.error;
  return `<section class="panel diagnostic-panel" aria-labelledby="ip-heading">
    <div class="panel-head"><h2 id="ip-heading">${icon('globe')} 出口 IP</h2><div class="diagnostic-actions">
      <select id="ip-source" aria-label="IP 查询来源">${Object.entries(sources)
        .map(
          ([key, label]) =>
            `<option value="${key}" ${source === key ? 'selected' : ''}>${label}</option>`,
        )
        .join('')}</select>
      <button class="icon-btn ${record?.pending ? 'diagnostic-spinning' : ''}" data-diagnostic-refresh="ip" aria-label="刷新出口 IP" title="刷新出口 IP" ${record?.pending ? 'disabled' : ''}>${icon('refresh')}</button>
    </div></div>
    <div class="diagnostic-ip"><span>IP 地址</span><strong id="diagnostic-ip-value">${e(info?.ip || (record?.pending ? '检测中…' : '—'))}</strong>${info ? `<button class="icon-btn" data-copy="diagnostic-ip-value" title="复制 IP 地址" aria-label="复制 IP 地址">${icon('copy')}</button>` : ''}</div>
    ${error ? `<p class="diagnostic-error" role="status">${e(error)}</p>` : ''}
    <dl class="diagnostic-details">${[
      ['国家 / 地区', info?.country],
      ['城市', info?.city],
      ['组织 / ISP', info?.organization],
      ['ASN', info?.asn],
    ]
      .map(([label, value]) => `<div><dt>${label}</dt><dd>${e(value || '—')}</dd></div>`)
      .join('')}</dl>
    <div class="diagnostic-footer" aria-live="polite">${footer(record, group)}</div>
  </section>`;
}

function latencyPanel(group) {
  const record = records.get(diagnosticKey(group, 'latency'));
  const result = record?.pending ? null : record?.data;
  const targets =
    result?.targets || ['Google', 'Cloudflare', 'GitHub'].map((name) => ({ name, delay: null }));
  const average = result?.average;
  return `<section class="panel diagnostic-panel" aria-labelledby="latency-heading">
    <div class="panel-head"><h2 id="latency-heading">${icon('pulse')} 网络延迟</h2><div class="diagnostic-actions">
      <span class="diagnostic-average ${latencyTone(average)}">平均 ${average == null ? '—' : `${e(average)} ms`}</span>
      <button class="icon-btn ${record?.pending ? 'diagnostic-spinning' : ''}" data-diagnostic-refresh="latency" aria-label="刷新网络延迟" title="刷新网络延迟" ${record?.pending ? 'disabled' : ''}>${icon('refresh')}</button>
    </div></div>
    <div class="diagnostic-latencies">${targets.map((target) => `<div class="diagnostic-latency ${latencyTone(target.delay)}"><span>${e(target.name)}</span><div class="diagnostic-track" aria-hidden="true"><i style="width:${target.delay == null ? 0 : Math.min(100, Math.max(3, target.delay / 10))}%"></i></div><strong>${record?.pending ? '检测中' : target.delay == null ? (target.error ? '连接失败' : '—') : `${e(target.delay)} ms`}</strong></div>`).join('')}</div>
    ${record?.error ? `<p class="diagnostic-error" role="status">${e(record.error)}</p>` : ''}
    <p class="diagnostic-explainer">HTTPS 请求耗时，包含代理建连；平均值仅计算成功的检测。</p>
    <div class="diagnostic-footer" aria-live="polite">${footer(record, group)}</div>
  </section>`;
}

async function check(group, kind) {
  const key = diagnosticKey(group, kind);
  const previous = records.get(key);
  if (previous?.pending) return;
  const provider = source;
  const record = { pending: true };
  records.set(key, record);
  // Bound browser memory as group names, selections and subscriptions change.
  if (records.size > 128) {
    for (const [oldKey, old] of records) {
      if (!old.pending && oldKey !== key) {
        records.delete(oldKey);
        break;
      }
    }
  }
  paint();
  try {
    record.data = await api(
      `/api/groups/${group.id}/diagnostics/${kind}?source=${provider}&refresh=true`,
      { method: 'POST' },
    );
  } catch (error) {
    record.error = error.message === 'unauthorized' ? '请重新登录' : error.message;
  } finally {
    record.pending = false;
    paint();
  }
}

function paint() {
  if (state.page !== 'overview' || !$('#group-diagnostics')) return;
  const container = $('#group-diagnostics');
  const groups = state.groups.filter((group) => group.enabled);
  if (!groups.length) {
    container.innerHTML = `<div class="panel">${empty('启用代理组后，可查看各组的出口 IP 和网络延迟。')}</div>`;
    return;
  }
  if (!groups.some((group) => group.id === selected)) selected = groups[0].id;
  const group = groups.find((group) => group.id === selected);
  container.innerHTML = `<div class="diagnostics-heading"><div><h2>出口检测</h2><p>仅页面重载或手动刷新时检测；切换代理组查看已保存的结果</p></div><div class="diagnostic-groups" role="group" aria-label="选择检测的代理组">${groups.map((item) => `<button class="diagnostic-group ${item.id === selected ? 'active' : ''}" data-diagnostic-group="${item.id}" aria-pressed="${item.id === selected}">${icon(item.is_default ? 'globe' : 'shield')}${e(item.name)}</button>`).join('')}</div></div><div class="diagnostics-grid">${ipPanel(group)}${latencyPanel(group)}</div>`;
  container.querySelectorAll('[data-diagnostic-group]').forEach((button) => {
    button.onclick = () => {
      selected = Number(button.dataset.diagnosticGroup);
      savePreferences();
      renderGroupDiagnostics();
    };
  });
  container.querySelector('#ip-source').onchange = (event) => {
    source = event.target.value;
    savePreferences();
    renderGroupDiagnostics();
  };
  container.querySelectorAll('[data-diagnostic-refresh]').forEach((button) => {
    button.onclick = () => check(group, button.dataset.diagnosticRefresh);
  });
}

function savePreferences() {
  try {
    sessionStorage.setItem('mc-diagnostics', JSON.stringify({ selected, source }));
  } catch {}
}

function renderGroupDiagnostics() {
  if (!restored) {
    restored = true;
    try {
      const saved = JSON.parse(sessionStorage.getItem('mc-diagnostics'));
      if (saved) {
        selected = Number(saved.selected);
        if (sources[saved.source]) source = saved.source;
      }
    } catch {}
  }
  paint();
  if (initialized) return;
  initialized = true;
  refreshGroupDiagnostics();
}

async function refreshGroupDiagnostics() {
  const group = state.groups.find((item) => item.id === selected && item.enabled);
  if (group) {
    await Promise.all([check(group, 'ip'), check(group, 'latency')]);
  }
}

export { renderGroupDiagnostics, refreshGroupDiagnostics, diagnosticKey, latencyTone };
