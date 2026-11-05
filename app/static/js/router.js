import { $, empty } from './core/dom.js';
import { state } from './core/state.js';
import { hideHistoryTooltip } from './ui/history.js';
import { renderOverview } from './pages/overview.js';
import { renderSubs } from './pages/subscriptions.js';
import { renderRules } from './pages/rules.js';
import { renderGroups } from './pages/groups.js';
import { renderNodes } from './pages/nodes.js';
import { renderPurity } from './pages/purity.js';
import { renderAdvanced } from './pages/advanced.js';
import { renderCampus } from './pages/campus.js';
import { renderRouter } from './pages/router.js';
import { renderRoutingDiagnostics } from './pages/routing-diagnostics.js';
import { renderRulesets } from './pages/rulesets.js';

const pages = ['overview', 'subs', 'rules', 'groups', 'rulesets', 'nodes', 'purity', 'campus', 'router', 'diagnostics', 'advanced'];

const titles = {
  overview: '概览',
  subs: '订阅',
  rules: '分流规则',
  groups: '代理组',
  rulesets: '规则集',
  nodes: '节点健康',
  purity: 'IP 纯净度',
  campus: '校园网',
  router: '路由器',
  diagnostics: '分流诊断',
  advanced: '高级',
};

const pageScroll = new Map();

function renderCurrent() {
  hideHistoryTooltip();
  const current = state.page;
  pages.forEach((name) => $('#page-' + name).classList.toggle('hidden', name !== current));
  $('#title').textContent = titles[current];
  if (!state.overview) {
    $('#page-' + current).innerHTML = empty('正在加载…');
    return;
  }
  ({
    overview: renderOverview,
    subs: renderSubs,
    rules: renderRules,
    groups: renderGroups,
    rulesets: renderRulesets,
    nodes: renderNodes,
    purity: renderPurity,
    campus: renderCampus,
    router: renderRouter,
    diagnostics: renderRoutingDiagnostics,
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
  const changed = state.page !== name;
  const content = $('#workspace-content');
  if (changed) pageScroll.set(state.page, content.scrollTop);
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
  if (changed) content.scrollTop = pageScroll.get(name) || 0;
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
