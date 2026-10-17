import { $, e } from '../core/dom.js';
import { state } from '../core/state.js';
import { api } from '../core/api.js';
import { act } from '../core/actions.js';

function renderAdvanced() {
  $('#page-advanced').innerHTML = /* HTML */ `<div class="panel advanced">
      <span class="eyebrow">METACUBEXD</span>
      <h2>需要更深入的控制？</h2>
      <p class="muted">打开完整面板查看连接、日志和内核规则。沿用当前登录，无需输入内核密钥。</p>
      <a
        class="btn primary"
        href="/ui/#/?hostname=${encodeURIComponent(location.hostname)}&amp;port=${location.port || (location.protocol === 'https:' ? 443 : 80)}&amp;${location.protocol === 'https:' ? 'https' : 'http'}=true"
        target="_blank"
        rel="noreferrer"
        >打开 MetaCube XD ↗</a
      >
    </div>
    <div class="panel">
      <h2>内核维护</h2>
      <p class="muted">当前版本 ${e(state.overview.version)}。升级会短暂重启代理内核。</p>
      <button class="btn" id="upgrade">检查并升级内核</button>
    </div>`;
  $('#upgrade').onclick = act(async () => {
    if (confirm('升级内核会短暂中断代理连接，继续？'))
      await api('/api/kernel/upgrade', { method: 'POST' });
  });
}

export { renderAdvanced };
