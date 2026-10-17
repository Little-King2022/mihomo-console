import { $, e, empty } from '../core/dom.js';
import { state } from '../core/state.js';
import { api } from '../core/api.js';
import { icon } from '../ui/icons.js';

const snapshots = new Map();
let selected = null;
let restored = false;
let detailsOpen = false;

const riskBands = [
  [15, '极度纯净', '#166534'],
  [25, '纯净', '#22c55e'],
  [40, '中性', '#84cc16'],
  [50, '轻度风险', '#eab308'],
  [70, '中度风险', '#f97316'],
  [100, '极度风险', '#dc2626'],
];

function riskBand(value) {
  if (typeof value !== 'number' || !Number.isFinite(value) || value < 0 || value > 100)
    return { label: '暂未提供', color: 'var(--muted)', value: null };
  const [, label, color] = riskBands.find(([end]) => value <= end);
  return { label, color, value };
}

function riskPosition(value) {
  if (riskBand(value).value === null) return 50;
  const index = riskBands.findIndex(([end]) => value <= end);
  const start = index ? riskBands[index - 1][0] : 0;
  return ((index + (value - start) / (riskBands[index][0] - start)) / riskBands.length) * 100;
}

function ipAttribute(type) {
  const names = {
    isp: '住宅 IP',
    hosting: '机房 IP',
    business: '机房 IP',
    enterprise: '商业宽带',
    education: '教育 IP',
    government: '政府 IP',
  };
  return Object.hasOwn(names, type) ? names[type] : '暂未提供';
}

function flag(code) {
  return /^[A-Z]{2}$/.test(code || '')
    ? String.fromCodePoint(...[...code].map((letter) => letter.charCodeAt(0) + 127397))
    : '';
}

function locationText(location) {
  let country = location.country_code;
  try {
    if (/^[A-Z]{2}$/.test(country))
      country = new Intl.DisplayNames(['zh-CN'], { type: 'region' }).of(country);
  } catch {}
  return (
    [...new Set([country, location.region, location.city].filter(Boolean))].join('，') || '暂未提供'
  );
}

function riskScale(value, pending) {
  const band = riskBand(value);
  const known = band.value !== null;
  return `<div class="purity-scale ${known ? '' : 'purity-scale-unknown'}" style="--risk-color:${band.color};--risk-position:${riskPosition(band.value)}%">
    <span class="purity-score-badge">${pending ? '检测中…' : known ? `${e(band.value)}% ${band.label}` : '暂未提供系数'}</span>
    <div class="purity-scale-bar" ${known ? `role="meter" aria-label="IPPure 风险系数，数值越低越纯净" aria-valuemin="0" aria-valuemax="100" aria-valuenow="${band.value}" aria-valuetext="${e(band.value)}%，${band.label}"` : 'aria-hidden="true"'}>${riskBands.map(([, , color]) => `<i class="purity-scale-segment" style="background:${color}"></i>`).join('')}${known ? '<i class="purity-scale-pointer"></i>' : ''}</div>
    <div class="purity-scale-ticks" aria-hidden="true">${[0, 15, 25, 40, 50, 70, 100].map((tick, index) => `<span style="left:${(index / 6) * 100}%">${tick}</span>`).join('')}</div>
  </div>`;
}

function tag(label, tone = '') {
  return `<span class="purity-tag ${tone}">${e(label)}</span>`;
}

function reportCard(group, snapshot) {
  const result = snapshot?.data;
  const report = snapshot?.pending ? null : result?.report;
  const pending = snapshot?.pending;
  const broadcast = report?.is_broadcast;
  const changed = result && (result.node_changed || (group.now && result.node !== group.now));
  const locations = report?.locations || [];
  const details = [
    ['ASN 域名', report?.asn_domain],
    ['IP 网段', report?.network],
    ['IP 范围', [report?.network_start, report?.network_end].filter(Boolean).join(' – ')],
  ];
  return `<article class="purity-card" aria-label="IP 纯净度报告" aria-busy="${!!pending}">
    <div class="purity-row"><span class="purity-label">IP</span><div class="purity-value purity-ip-line"><strong id="purity-ip">${e(report?.ip || (pending ? '检测中…' : '—'))}</strong>${report ? `<button class="icon-btn" data-copy="purity-ip" title="复制 IP" aria-label="复制 IP">${icon('copy')}</button>` : ''}</div></div>
    <div class="purity-row"><span class="purity-label">ASN</span><div class="purity-value purity-asn"><span>${e(report?.asn || '—')}</span><span class="muted">${report?.organization ? `– ${e(report.organization)}` : ''}</span></div></div>
    <div class="purity-row purity-location-row"><span class="purity-label">位置</span><div class="purity-locations">${locations.length ? locations.map((location) => `<div><span class="purity-location-source">${e(location.source)}</span><span class="purity-location-value">${e(flag(location.country_code))} ${e(locationText(location))}</span></div>`).join('') : `<span class="muted">${pending ? '正在读取定位信息…' : '检测后展示多家定位信息'}</span>`}</div></div>
    <div class="purity-row"><span class="purity-label">IP 来源</span><div class="purity-value">${tag(broadcast === false ? '原生 IP' : broadcast === true ? '广播 IP' : '暂未提供', broadcast === false ? 'good' : broadcast === true ? 'caution' : '')}</div></div>
    <div class="purity-row"><span class="purity-label">IP 属性</span><div class="purity-value">${tag(ipAttribute(report?.asn_type), ['isp', 'education'].includes(report?.asn_type) ? 'good' : ['hosting', 'business'].includes(report?.asn_type) ? 'caution' : '')}</div></div>
    <div class="purity-score-row"><div class="purity-score-heading"><span class="purity-label">IPPure 系数</span><span class="muted">越低越纯净</span></div>${riskScale(report?.fraud_score, pending)}</div>
    ${snapshot?.error || result?.error ? `<p class="purity-error" role="alert">${e(snapshot?.error || result.error)}</p>` : ''}
    ${report?.warnings?.length ? `<div class="purity-warnings" role="status">${report.warnings.map((message) => `<p>${e(message)}</p>`).join('')}</div>` : ''}
    ${changed ? '<p class="purity-warnings" role="status">出口节点已发生变化；此处保留上次检测结果，请手动重新检测。</p>' : ''}
    <details class="purity-details" id="purity-details" ${detailsOpen ? 'open' : ''}><summary><span>更多信息与评分说明</span>${icon('down')}</summary><div class="purity-details-body">${details.map(([name, value]) => `<div><span>${name}</span><strong>${e(value || '—')}</strong></div>`).join('')}<p>IPPure 系数表示 IP 的风险程度。0–15 极度纯净，15–25 纯净，25–40 中性，40–50 轻度风险，50–70 中度风险，70–100 极度风险；分界值归前一档。未返回的数据保留为未知。</p><a href="https://ippure.com/faq" target="_blank" rel="noreferrer">查看 IPPure 评分说明 ↗</a></div></details>
  </article>`;
}

function renderPurity() {
  if (state.page !== 'purity' || !$('#page-purity')) return;
  if (!restored) {
    restored = true;
    try {
      selected = Number(sessionStorage.getItem('mc-purity-group'));
    } catch {}
  }
  const container = $('#page-purity');
  const groups = state.groups.filter((group) => group.enabled);
  if (!groups.length) {
    container.innerHTML = `<div class="panel">${empty('先启用一个代理组，再检测它的出口 IP 纯净度。')}</div>`;
    return;
  }
  if (!groups.some((group) => group.id === selected)) selected = groups[0].id;
  const group = groups.find((item) => item.id === selected);
  const snapshot = snapshots.get(group.id);
  const result = snapshot?.data;
  const pending = snapshot?.pending;
  const report = pending ? null : result?.report;
  container.innerHTML = `<div class="purity-workspace">
    <div class="purity-toolbar"><div><strong>选择检测出口</strong><p>只在点击检测时查询，各代理组独立保存结果。</p></div><div class="diagnostic-groups" role="group" aria-label="选择纯净度检测代理组">${groups.map((item) => `<button class="diagnostic-group ${item.id === group.id ? 'active' : ''}" data-purity-group="${item.id}" aria-pressed="${item.id === group.id}">${icon(item.is_default ? 'globe' : 'shield')}${e(item.name)}</button>`).join('')}</div></div>
    <div class="purity-report-wrap"><div class="purity-query"><span>${e(report?.ip || (pending ? '正在检测所选代理组的出口…' : '检测所选代理组的出口 IP'))}</span><button id="purity-run" class="btn" ${pending ? 'disabled' : ''}>${icon('refresh')}${pending ? '检测中…' : report ? '重新检测' : '开始检测'}</button></div>
    <div class="purity-meta"><span>${e(group.name)}${report ? ` <span class="purity-ip-version">IPv${report.version}</span>` : ''}</span><a href="https://ippure.com/" target="_blank" rel="noreferrer">数据来源 IPPure ↗</a></div>
    ${reportCard(group, snapshot)}
    <div class="purity-provenance" aria-live="polite">${pending ? '正在通过所选代理组查询，请稍候…' : result ? `<span>检测于 ${e(new Date(result.checked_at).toLocaleString('zh-CN', { hour12: false }))}</span><span>出口节点：${e(result.node)}</span>` : '<span>尚未检测 · 点击“开始检测”获取真实报告</span>'}</div>
    </div></div>`;
  container.querySelectorAll('[data-purity-group]').forEach((button) => {
    button.onclick = () => {
      selected = Number(button.dataset.purityGroup);
      try {
        sessionStorage.setItem('mc-purity-group', String(selected));
      } catch {}
      renderPurity();
    };
  });
  $('#purity-run').onclick = refreshPurity;
  $('#purity-details').ontoggle = (event) => {
    detailsOpen = event.target.open;
  };
}

async function refreshPurity() {
  const group = state.groups.find((item) => item.id === selected && item.enabled);
  if (!group || snapshots.get(group.id)?.pending) return;
  const snapshot = { pending: true };
  snapshots.set(group.id, snapshot);
  if (snapshots.size > 128) {
    for (const [id, old] of snapshots) {
      if (!old.pending && id !== group.id) {
        snapshots.delete(id);
        break;
      }
    }
  }
  renderPurity();
  try {
    snapshot.data = await api(`/api/groups/${group.id}/diagnostics/purity?refresh=true`, {
      method: 'POST',
    });
  } catch (error) {
    snapshot.error = error.message === 'unauthorized' ? '请重新登录后检测' : error.message;
  } finally {
    snapshot.pending = false;
    renderPurity();
  }
}

export { renderPurity, refreshPurity, riskBand, riskPosition, ipAttribute, locationText };
