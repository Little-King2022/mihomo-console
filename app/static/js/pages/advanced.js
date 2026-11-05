import { $, e } from '../core/dom.js';
import { state } from '../core/state.js';
import { api } from '../core/api.js';
import { toast } from '../ui/toast.js';

let maintenance = null, pending = null, failure = '', working = false, lastStatusAt = 0;
const names = {kernel: 'Mihomo 内核', panel: 'MetaCube XD 控制面板'};
const running = () => ['queued', 'running'].includes(maintenance?.job?.state);

function componentCard(kind) {
  const item = maintenance?.components[kind];
  const current = item?.version || (item?.installed ? '已安装，版本未记录' : maintenance ? '未安装或无法识别' : kind === 'kernel' ? state.overview.version : '读取中…');
  return `<section class="panel maintenance-card"><h2>${names[kind]}</h2>
    <dl class="maintenance-versions"><div><dt>当前版本</dt><dd>${e(current)}</dd></div><div><dt>最新稳定版</dt><dd>${e(item?.latest || '尚未检查')}</dd></div></dl>
    <p class="small muted">${kind === 'kernel' ? '下载与配置自检通过后切换；会短暂中断代理连接。启动、TUN 或国内绕过检查失败时自动恢复。' : '更新静态面板，保留现有接入配置，无需重启代理内核。首次纳入版本管理前，旧面板版本可能无法识别。'}</p>
    ${item?.checked_at ? `<p class="small muted">上次检查：${e(new Date(item.checked_at).toLocaleString())}${item.release_url ? ` · <a href="${e(item.release_url)}" target="_blank" rel="noreferrer">官方发布说明 ↗</a>` : ''}</p>` : ''}
    <div class="row"><button class="btn" data-maintenance="check" data-component="${kind}" ${running() || working || pending ? 'disabled' : ''}>检查更新</button>
    <button class="btn primary" data-maintenance="upgrade" data-component="${kind}" ${running() || working || !item?.update_available ? 'disabled' : ''}>${item?.latest ? '升级至 ' + e(item.latest) : '升级'}</button>
    <button class="btn" data-maintenance="rollback" data-component="${kind}" ${running() || working || !item?.rollback_available ? 'disabled' : ''}>回滚${item?.rollback_version ? '至 ' + e(item.rollback_version) : '上一版本'}</button></div>
    ${item?.latest && !item.update_available ? '<p class="small muted">当前已是该稳定版本或更新版本。</p>' : ''}
    ${!item?.rollback_available ? '<p class="small muted">成功升级后保留上一版本，供回滚使用。</p>' : ''}</section>`;
}

function renderAdvanced() {
  const job = maintenance?.job;
  $('#page-advanced').innerHTML = `    <div class="maintenance-grid">${componentCard('kernel')}${componentCard('panel')}</div>
    <section class="panel" aria-live="polite"><h2>维护状态</h2>
      <p class="${job?.state === 'failed' ? 'warn' : 'muted'}">${e(job ? `${names[job.kind] || ''}：${job.message}` : '尚无维护任务。检查更新后可选择升级。')}</p>
      ${running() ? '<p class="small muted">任务在后台运行，关闭页面后仍会继续。完成前无法启动另一项维护。</p>' : ''}
      ${failure ? `<p class="warn">${e(failure)}</p>` : ''}<button class="btn" id="maintenance-refresh">刷新维护状态</button></section>`;
  $('#maintenance-refresh').onclick = () => refreshMaintenance();
  document.querySelectorAll('[data-maintenance]').forEach(button => {
    button.onclick = async () => {
      if (working || running()) return;
      const kind = button.dataset.component, action = button.dataset.maintenance;
      const target = maintenance?.components[kind]?.latest;
      if (action !== 'check') {
        const text = action === 'rollback' ? `将${names[kind]}恢复至上一版本？` : `将${names[kind]}升级至 ${target}？`;
        if (!confirm(text + (kind === 'kernel' ? '\n切换时会短暂中断代理连接。' : ''))) return;
      }
      working = true; failure = ''; renderAdvanced();
      try {
        maintenance = await api(`/api/maintenance/${kind}/${action}`, {method:'POST', body:JSON.stringify({version:target})});
        failure = maintenance.warning || '';
        lastStatusAt = Date.now();
      } catch (error) {
        failure = error.message;
        if (error.message !== 'unauthorized') toast(error.message, true);
      } finally {
        working = false;
        if (state.authenticated && state.page === 'advanced') renderAdvanced();
      }
    };
  });
  if (!maintenance && !pending && !failure) refreshMaintenance();
}

export function refreshMaintenance() {
  if (pending) return pending;
  pending = api('/api/maintenance').then(data => {
    maintenance = data; failure = ''; lastStatusAt = Date.now();
    if (data.components.kernel.version && state.overview) state.overview.version = data.components.kernel.version;
  }).catch(error => {failure = error.message;}).finally(() => {
    pending = null;
    if (state.authenticated && state.page === 'advanced') renderAdvanced();
  });
  return pending;
}

export function pollMaintenance() {
  if (running() || Date.now() - lastStatusAt >= 15000) return refreshMaintenance();
}

export { renderAdvanced };
