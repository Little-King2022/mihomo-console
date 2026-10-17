import { $ } from './core/dom.js';
import { state } from './core/state.js';
import { api } from './core/api.js';
import { loadAll, refreshedAt } from './core/data.js';
import { isActing } from './core/actions.js';
import { historyTooltipActive } from './ui/history.js';
import { renderLive } from './pages/overview.js';

let polling = false;
async function poll() {
  if (!state.authenticated || polling || document.hidden) return;
  polling = true;
  try {
    if (state.page === 'overview') {
      state.telemetry = await api('/api/telemetry');
      renderLive();
    }
    if (
      !isActing() &&
      !historyTooltipActive() &&
      Date.now() - refreshedAt() > 15000 &&
      $('#modal').classList.contains('hidden') &&
      !['INPUT', 'SELECT', 'TEXTAREA'].includes(document.activeElement?.tagName)
    )
      await loadAll();
  } catch (error) {
    if (error.message !== 'unauthorized' && state.page === 'overview') {
      state.telemetry = { online: false, error: '控制台连接中断，正在重试' };
      renderLive();
    }
  } finally {
    polling = false;
  }
}
export function initPolling() {
  setInterval(poll, 2000);
  document.addEventListener('visibilitychange', () => {
    if (!document.hidden) poll();
  });
}

export { poll };
