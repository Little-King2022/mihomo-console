import { $, e, empty } from '../core/dom.js';
import { stamp } from '../core/format.js';
import { state } from '../core/state.js';
import { api } from '../core/api.js';
import { act } from '../core/actions.js';
import { nodeHistory, hideHistoryTooltip } from '../ui/history.js';

function healthSubscriptionChoices() {
  return [
    { id: 'all', name: '全部订阅', count: state.nodes.length },
    ...state.subs.map((sub) => ({
      id: `sub${sub.id}`,
      name: sub.name,
      count: state.nodes.filter((n) => n.provider === `sub${sub.id}`).length,
      enabled: !!sub.enabled,
    })),
  ];
}

function resolveHealthSubscription() {
  if (state.nodeProvider === null)
    try {
      state.nodeProvider = sessionStorage.getItem('mihomo-node-subscription');
    } catch {}
  const choices = healthSubscriptionChoices();
  if (!choices.some((c) => c.id === state.nodeProvider))
    state.nodeProvider =
      choices.find((c) => c.enabled && c.count)?.id ||
      choices.find((c) => c.enabled)?.id ||
      choices[1]?.id ||
      'all';
}

function subscriptionHealthNodes() {
  return state.nodeProvider === 'all'
    ? state.nodes
    : state.nodes.filter((n) => n.provider === state.nodeProvider);
}

function selectHealthSubscription(provider) {
  if (!healthSubscriptionChoices().some((c) => c.id === provider)) return;
  state.nodeProvider = provider;
  try {
    sessionStorage.setItem('mihomo-node-subscription', provider);
  } catch {}
  renderNodes();
  document.getElementById('node-subtab-' + provider)?.focus({ preventScroll: true });
}

function renderNodes() {
  hideHistoryTooltip();
  resolveHealthSubscription();
  const choices = healthSubscriptionChoices(),
    scoped = subscriptionHealthNodes(),
    alive = scoped.filter((n) => n.alive).length;
  $('#page-nodes').innerHTML = /* HTML */ `<div class="split page-intro">
      <p class="muted">
        按订阅查看节点。最近 24 次测速中，绿色表示可用，红色表示失败；柱高表示延迟。
      </p>
      <div class="row">
        <button class="btn primary" id="check-nodes">检测节点</button
        ><button class="btn" id="clear-cool">清除停用</button>
      </div>
    </div>
    <div class="health-subscriptions" role="tablist" aria-label="按订阅查看节点">
      ${choices.map((c) => /* HTML */ `<button type="button" role="tab" class="health-subscription-tab ${state.nodeProvider === c.id ? 'active' : ''}" id="node-subtab-${c.id}" data-node-subscription="${c.id}" aria-controls="node-list" aria-selected="${state.nodeProvider === c.id}" tabindex="${state.nodeProvider === c.id ? 0 : -1}" title="${e(c.name)}"><span>${e(c.name)}</span><span class="subscription-node-count">${c.count}</span>${c.enabled === false ? '<span class="subscription-disabled">已停用</span>' : ''}</button>`).join('')}
    </div>
    <div class="health-toolbar">
      <div class="row" id="health-summary">
        <span class="tag ok">${alive} 可用</span
        ><span class="tag bad">${scoped.length - alive} 失败 / 未检测</span>
      </div>
      <div class="row">
        <input
          aria-label="搜索节点"
          id="node-search"
          placeholder="搜索当前订阅的节点…"
          value="${e(state.filter)}"
        /><select id="health-filter" aria-label="按健康状态筛选">
          <option value="all">全部状态</option>
          <option value="alive">可用</option>
          <option value="failed">失败</option>
          <option value="cooled">短暂停用</option>
        </select>
      </div>
    </div>
    <div
      id="node-list"
      class="node-grid"
      role="tabpanel"
      aria-labelledby="node-subtab-${state.nodeProvider}"
    ></div>`;
  $('#health-filter').value = state.health;
  renderNodeList();
  $('.health-subscriptions').onclick = (event) => {
    const tab = event.target.closest('[data-node-subscription]');
    if (tab) selectHealthSubscription(tab.dataset.nodeSubscription);
  };
  $('.health-subscriptions').onkeydown = (event) => {
    if (!['ArrowLeft', 'ArrowRight', 'Home', 'End'].includes(event.key)) return;
    const tabs = [...document.querySelectorAll('[data-node-subscription]')],
      index = tabs.indexOf(event.target);
    if (index < 0) return;
    event.preventDefault();
    const next =
      event.key === 'Home'
        ? 0
        : event.key === 'End'
          ? tabs.length - 1
          : (index + (event.key === 'ArrowRight' ? 1 : -1) + tabs.length) % tabs.length;
    selectHealthSubscription(tabs[next].dataset.nodeSubscription);
  };
  $('#node-search').oninput = (event) => {
    state.filter = event.target.value;
    renderNodeList();
  };
  $('#health-filter').onchange = (event) => {
    state.health = event.target.value;
    renderNodeList();
  };
  $('#check-nodes').onclick = act(() => api('/api/nodes/healthcheck', { method: 'POST' }));
  $('#clear-cool').onclick = act(() => api('/api/nodes/cooldowns/clear', { method: 'POST' }));
}

function renderNodeList() {
  hideHistoryTooltip();
  const scoped = subscriptionHealthNodes();
  const items = scoped.filter((n) => {
    const sub = state.subs.find((s) => `sub${s.id}` === n.provider)?.name || n.provider;
    return (
      (n.node_name + ' ' + sub).toLowerCase().includes(state.filter.toLowerCase()) &&
      (state.health === 'all' ||
        (state.health === 'alive' && n.alive) ||
        (state.health === 'failed' && !n.alive) ||
        (state.health === 'cooled' && n.cooldown_until))
    );
  });
  $('#node-list').innerHTML =
    items
      .map((n) => {
        const sub = state.subs.find((s) => `sub${s.id}` === n.provider);
        const history = n.history || [],
          successful = history.filter((h) => h.delay > 0).length;
        return /* HTML */ `<article class="card node-card" data-node-provider="${e(n.provider)}">
          <div class="split">
            <span class="muted small">${e(sub?.name || n.provider)}</span
            ><span class="tag ${n.cooldown_until ? 'warn' : n.alive ? 'ok' : 'bad'}"
              >${n.cooldown_until ? '短暂停用' : n.alive ? '可用' : n.checked_at ? '失败' : '等待检测'}</span
            >
          </div>
          <h3>${e(n.node_name)}</h3>
          <div class="split">
            <strong class="latency ${n.alive ? 'ok' : 'bad'}"
              >${n.alive ? n.delay + ' <small>ms</small>' : '—'}</strong
            ><span class="muted small"
              >最近成功
              ${history.length ? Math.round((successful / history.length) * 100) + '%' : '—'}</span
            >
          </div>
          ${nodeHistory(n)}
          <div class="split small muted">
            <span>旧 ← 最近 ${history.length} 次 → 新</span><span>连续失败 ${n.fails}</span>
          </div>
          <p class="small muted">
            检测
            ${stamp(n.checked_at)}${n.cooldown_until ? '<br>停用至 ' + stamp(n.cooldown_until) : ''}
          </p>
        </article>`;
      })
      .join('') ||
    empty(
      scoped.length
        ? '没有符合条件的节点'
        : state.nodeProvider === 'all'
          ? '内核尚未回报节点，请更新订阅后检测。'
          : state.subs.find((s) => `sub${s.id}` === state.nodeProvider)?.enabled
            ? '此订阅尚无节点健康记录，请更新订阅后检测。'
            : '此订阅已停用，启用后可检测节点。',
    );
}

export {
  renderNodes,
  healthSubscriptionChoices,
  resolveHealthSubscription,
  subscriptionHealthNodes,
};
