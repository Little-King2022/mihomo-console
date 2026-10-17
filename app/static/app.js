// Application bootstrap: compose modules and wire session-level events only.
import { $ } from './js/core/dom.js';
import { state } from './js/core/state.js';
import { api } from './js/core/api.js';
import { authenticate, consumeLoginToken } from './js/core/session.js';
import { loadAll } from './js/core/data.js';
import { act } from './js/core/actions.js';
import { events } from './js/core/events.js';
import { initRouter, navigate, renderCurrent } from './js/router.js';
import { initShell, renderMode } from './js/ui/shell.js';
import { initModal, closeModal } from './js/ui/modal.js';
import { initClipboard } from './js/ui/clipboard.js';
import { initHistoryTooltip } from './js/ui/history.js';
import { initProxyGuide } from './js/features/proxy-guide.js';
import { refreshPurity } from './js/pages/purity.js';
import { refreshCampus } from './js/pages/campus.js';
import { renderLive } from './js/pages/overview.js';
import { refreshGroupDiagnostics } from './js/features/group-diagnostics.js';
import { initPolling, poll } from './js/polling.js';
const loginToken = consumeLoginToken();
function showLogin() {
  state.authenticated = false;
  $('#login').classList.remove('hidden');
  $('#shell').classList.add('hidden');
  closeModal();
}

function showApp() {
  state.authenticated = true;
  $('#login').classList.add('hidden');
  $('#shell').classList.remove('hidden');
}
events.addEventListener('session:expired', showLogin);
events.addEventListener('data:updated', () => {
  renderMode();
  renderCurrent();
});
initModal();
initHistoryTooltip();
initClipboard();
initProxyGuide();
initShell(navigate);
initRouter();
$('#refresh').onclick = act(async () => {
  await loadAll();
  if (state.page === 'overview') {
    const [telemetry] = await Promise.all([api('/api/telemetry'), refreshGroupDiagnostics()]);
    state.telemetry = telemetry;
    renderLive();
  }
  if (state.page === 'purity') await refreshPurity();
  if (state.page === 'campus') await refreshCampus();
});
$('#modes').onclick = async (event) => {
  if (event.target.dataset.mode)
    await act(() =>
      api('/api/mode', {
        method: 'POST',
        body: JSON.stringify({ mode: event.target.dataset.mode }),
      }),
    )(event);
};
$('#tun').onclick = act(() =>
  api('/api/tun', { method: 'POST', body: JSON.stringify({ enabled: !state.overview.tun }) }),
);
$('#logout').onclick = act(async () => {
  await api('/api/logout', { method: 'POST' });
  showLogin();
});
$('#login-form').onsubmit = async (event) => {
  event.preventDefault();
  $('#login-err').textContent = '';
  try {
    await authenticate($('#token').value);
    $('#token').value = '';
    showApp();
    await loadAll();
    await poll();
  } catch (error) {
    $('#login-err').textContent = error.message === 'unauthorized' ? '令牌不正确' : error.message;
  }
};

initPolling();
authenticate(loginToken)
  .then(async () => {
    showApp();
    await loadAll();
    await poll();
  })
  .catch((error) => {
    showLogin();
    if (loginToken !== null)
      $('#login-err').textContent = error.message === 'unauthorized' ? '令牌不正确' : error.message;
  });
