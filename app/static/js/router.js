import { $, e, empty } from './core/dom.js';
import { state } from './core/state.js';
import { hideHistoryTooltip } from './ui/history.js';
import { renderOverview } from './pages/overview.js';
import { renderSubs } from './pages/subscriptions.js';
import { renderRules } from './pages/rules.js';
import { renderGroups } from './pages/groups.js';
import { renderNodes } from './pages/nodes.js';
import { renderPurity } from './pages/purity.js';
import { renderAdvanced } from './pages/advanced.js';

const pages = ['overview', 'subs', 'rules', 'groups', 'nodes', 'purity', 'advanced'];

const titles = {
  overview: '概览',
  subs: '订阅',
  rules: '分流规则',
  groups: '代理组',
  nodes: '节点健康',
  purity: 'IP 纯净度',
  advanced: '高级',
};

const pageMeta = {
  overview: ['NETWORK OVERVIEW', '网络概览', '每一次连接，都有迹可循。'],
  subs: ['SUBSCRIPTIONS', '订阅管理', '从套餐用量到更新状态，一眼掌握。'],
  rules: ['TRAFFIC ROUTING', '分流规则', '给不同的服务，安排合适的出口。'],
  groups: ['PROXY GROUPS', '代理组', '用一组可靠的节点，连接更大的世界。'],
  nodes: ['NODE HEALTH', '节点健康', '用真实的测速记录，找到稳定的连接。'],
  purity: ['IP PURITY', 'IP 纯净度', '看清出口的身份，了解 IP 的风险。'],
  advanced: ['PREFERENCES', '高级设置', '更多控制，在你需要的时候。'],
};

function renderCurrent() {
  hideHistoryTooltip();
  const current = state.page;
  pages.forEach((name) => $('#page-' + name).classList.toggle('hidden', name !== current));
  const meta = pageMeta[current];
  $('#breadcrumb-page').textContent = titles[current];
  $('#page-eyebrow').textContent = meta[0];
  $('#title').innerHTML = e(meta[1]) + '<span class="heading-dot">.</span>';
  $('#page-description').textContent = meta[2];
  if (!state.overview) {
    $('#page-' + current).innerHTML = empty('正在加载…');
    return;
  }
  ({
    overview: renderOverview,
    subs: renderSubs,
    rules: renderRules,
    groups: renderGroups,
    nodes: renderNodes,
    purity: renderPurity,
    advanced: renderAdvanced,
  })[current]();
}

function restoredPage() {
  const hash = location.hash.slice(1);
  if (hash) return pages.includes(hash) ? hash : 'overview';
  try {
    const remembered = sessionStorage.getItem('mihomo-page');
    if (pages.includes(remembered)) return remembered;
  } catch {}
  return 'overview';
}

function navigate(name, { replace = false } = {}) {
  if (!pages.includes(name)) return;
  state.page = name;
  try {
    sessionStorage.setItem('mihomo-page', name);
  } catch {}
  if (location.hash !== '#' + name)
    history[replace ? 'replaceState' : 'pushState'](null, '', '#' + name);
  document.querySelectorAll('nav button').forEach((x) => {
    const on = x.dataset.page === name;
    x.classList.toggle('active', on);
    x.setAttribute('aria-current', on ? 'page' : 'false');
  });
  renderCurrent();
}
export function initRouter() {
  document
    .querySelectorAll('nav button')
    .forEach((b) => (b.onclick = () => navigate(b.dataset.page)));
  window.addEventListener('hashchange', () => navigate(restoredPage(), { replace: true }));
  document.addEventListener('click', (event) => {
    const button = event.target.closest('[data-go]');
    if (button) navigate(button.dataset.go);
  });
  navigate(restoredPage(), { replace: true });
}

export { renderCurrent, navigate };
