import { $, e } from '../core/dom.js';
import { stamp } from '../core/format.js';

let historyTooltipTarget = null;
export const historyTooltipActive = () => !!historyTooltipTarget;
function nodeHistory(node) {
  const history = node.history || [],
    max = Math.max(1, ...history.map((h) => h.delay));
  return /* HTML */ `<div
    class="health-history"
    role="group"
    aria-label="最近 ${history.length} 次检测，从左至右由旧到新；方向键查看记录"
  >
    ${
      history
        .map((h, i) => {
          const time = stamp(h.time),
            result = h.delay > 0 ? '延迟 ' + h.delay + ' ms' : '连接失败';
          return `<span class="health-bar ${h.delay > 0 ? 'ok' : 'bad'}" style="--height:${h.delay > 0 ? Math.max(20, (h.delay / max) * 100) : 100}%" tabindex="${i === 0 ? 0 : -1}" role="img" aria-label="测速时间 ${e(time)}，${e(result)}" data-health-time="${e(time)}" data-health-result="${e(result)}"></span>`;
        })
        .join('') || '<span class="muted small">等待测速记录</span>'
    }
  </div>`;
}

function hideHistoryTooltip() {
  historyTooltipTarget?.removeAttribute('aria-describedby');
  historyTooltipTarget = null;
  $('#history-tooltip')?.classList.add('hidden');
}

function showHistoryTooltip(bar) {
  const tooltip = $('#history-tooltip');
  if (!tooltip || !bar?.isConnected) return;
  if (historyTooltipTarget !== bar) historyTooltipTarget?.removeAttribute('aria-describedby');
  historyTooltipTarget = bar;
  bar.setAttribute('aria-describedby', 'history-tooltip');
  tooltip.innerHTML = `<span class="history-tooltip-label">测速时间</span><span class="history-tooltip-time">${e(bar.dataset.healthTime)}</span><strong class="${bar.classList.contains('ok') ? 'success' : 'failure'}">${e(bar.dataset.healthResult)}</strong>`;
  tooltip.classList.remove('hidden');
  const rect = bar.getBoundingClientRect(),
    box = tooltip.getBoundingClientRect();
  const left = Math.max(
    8,
    Math.min(innerWidth - box.width - 8, rect.left + rect.width / 2 - box.width / 2),
  );
  const top =
    rect.top - box.height - 10 >= 8
      ? rect.top - box.height - 10
      : Math.min(innerHeight - box.height - 8, rect.bottom + 10);
  tooltip.style.left = left + 'px';
  tooltip.style.top = Math.max(8, top) + 'px';
}
export function initHistoryTooltip() {
  document.addEventListener('pointerover', (event) => {
    const bar = event.target.closest('[data-health-time]');
    if (bar) showHistoryTooltip(bar);
  });
  document.addEventListener('pointerout', (event) => {
    const bar = event.target.closest('[data-health-time]');
    if (
      bar &&
      historyTooltipTarget === bar &&
      !bar.contains(event.relatedTarget) &&
      document.activeElement !== bar
    )
      hideHistoryTooltip();
  });
  document.addEventListener('focusin', (event) => {
    const bar = event.target.closest('[data-health-time]');
    if (bar) showHistoryTooltip(bar);
  });
  document.addEventListener('focusout', (event) => {
    if (event.target === historyTooltipTarget) hideHistoryTooltip();
  });
  document.addEventListener('click', (event) => {
    const bar = event.target.closest('[data-health-time]');
    if (bar) {
      event.preventDefault();
      showHistoryTooltip(bar);
    } else hideHistoryTooltip();
  });
  document.addEventListener('keydown', (event) => {
    const bar = event.target.closest('[data-health-time]');
    if (!bar || !['ArrowLeft', 'ArrowRight', 'Home', 'End'].includes(event.key)) return;
    event.preventDefault();
    const bars = [...bar.parentElement.querySelectorAll('[data-health-time]')],
      index = bars.indexOf(bar);
    const next =
      event.key === 'Home'
        ? 0
        : event.key === 'End'
          ? bars.length - 1
          : (index + (event.key === 'ArrowRight' ? 1 : -1) + bars.length) % bars.length;
    bars.forEach((item, i) => (item.tabIndex = i === next ? 0 : -1));
    bars[next].focus({ preventScroll: true });
    showHistoryTooltip(bars[next]);
  });
  window.addEventListener('resize', hideHistoryTooltip);
  document.addEventListener('scroll', hideHistoryTooltip, true);
  document.addEventListener('visibilitychange', () => {
    if (document.hidden) hideHistoryTooltip();
  });
}

export { nodeHistory, hideHistoryTooltip };
