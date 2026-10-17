import { e } from '../core/dom.js';
import { bytes, stamp } from '../core/format.js';
import { icon } from './icons.js';

function usage(raw) {
  let info = raw;
  try {
    if (typeof info === 'string') info = JSON.parse(info);
  } catch {
    info = null;
  }
  if (!info || typeof info !== 'object') return '<p class="muted">订阅未提供流量信息</p>';
  const used =
    Number(info.Upload ?? info.upload ?? 0) + Number(info.Download ?? info.download ?? 0);
  const total = Number(info.Total ?? info.total ?? 0),
    expiry = Number(info.Expire ?? info.expire ?? 0);
  const percent = total > 0 ? Math.max(0, (used / total) * 100) : null;
  return /* HTML */ `<div class="usage">
    <div class="split">
      <span>已用 <strong>${bytes(used)}</strong> / ${total > 0 ? bytes(total) : '未提供总量'}</span
      ><strong>${percent == null ? '—' : percent.toFixed(1) + '%'}</strong>
    </div>
    ${percent == null ? '' : `<progress class="${percent > 90 ? 'danger' : ''}" max="100" value="${Math.min(percent, 100)}" aria-label="订阅流量已用 ${percent.toFixed(1)}%"></progress>`}
    <div class="split muted small">
      <span>${total > 0 ? '剩余 ' + bytes(Math.max(total - used, 0)) : '流量总额未知'}</span
      ><span
        >${expiry > 0 ? (expiry * 1000 < Date.now() ? '已到期 · ' : '到期 ') + stamp(expiry * 1000) : '未提供到期时间'}</span
      >
    </div>
  </div>`;
}

function subCard(sub, compact = false) {
  let host = '';
  try {
    host = new URL(sub.url).hostname;
  } catch {}
  return /* HTML */ `<article class="card subscription">
    <div class="split">
      <div>
        <div class="eyebrow">${icon('layers')} 订阅 ${String(sub.id).padStart(2, '0')}</div>
        <h2>${e(sub.name)}</h2>
      </div>
      <span class="tag ${sub.enabled ? 'ok' : ''}">${sub.enabled ? '启用中' : '已停用'}</span>
    </div>
    ${usage(sub.last_info)}
    <div class="split small muted">
      <span>${e(host)}</span><span>每 ${sub.interval_hours} 小时更新</span>
    </div>
    <p class="small muted">上次更新 ${stamp(sub.last_update)}</p>
    ${sub.last_error ? '<p class="error-text">更新失败，请检查订阅或重试</p>' : ''}
    ${compact ? '' : `<div class="actions"><button class="btn" data-sedit="${sub.id}">编辑</button><button class="btn" data-supdate="${sub.id}" ${sub.enabled ? '' : 'disabled'}>立即更新</button><button class="btn subtle warn" data-sdel="${sub.id}">删除</button></div>`}
  </article>`;
}

export { subCard };
