import { $, e, empty } from '../core/dom.js';
import { state } from '../core/state.js';
import { icon } from '../ui/icons.js';
import { proxyEndpoint as resolveEndpoint, proxyCommands } from './proxy-commands.js';

function proxyEndpoint() {
  return resolveEndpoint(state.overview.proxy_access, location.hostname);
}
function codeBlock(id, label, command, disabled = false) {
  return `<div class="command-block"><div class="command-head"><span>${label}</span><button class="copy-btn" data-copy="${id}" ${disabled ? 'disabled' : ''} aria-label="复制${label}">${icon('copy')}<span>复制</span></button></div><pre><code id="${id}">${e(command)}</code></pre></div>`;
}

function renderProxyGuide() {
  const target = $('#proxy-guide');
  if (!target) return;
  const p = proxyEndpoint();
  if (!p) {
    target.innerHTML = `<div class="panel proxy-panel"><div class="panel-head"><h2>手动连接代理</h2>${icon('terminal')}</div>${empty(state.overview.proxy_access?.available ? 'HTTP 代理端口未开启。' : '暂时无法读取内核端口，连接恢复后自动显示。')}</div>`;
    return;
  }
  const remote = state.proxyLocation === 'remote' && p.local;
  target.innerHTML = `<div class="panel proxy-panel"><div class="panel-head"><div><h2>手动连接代理 <span class="tag">${p.socks ? 'HTTP / SOCKS5' : 'HTTP'}</span></h2><p>终端与其他应用，都能轻松接入</p></div><span class="soft-icon">${icon('terminal')}</span></div>
    <div class="proxy-address"><span class="proxy-port"><span class="small muted">${p.local ? '本机代理端口' : 'HTTP 代理端口'}</span><strong>${p.port}</strong></span><div><code>${e(p.url)}</code><span class="small muted">${p.local ? '仅代理服务器本机可直连' : '允许局域网连接'}</span></div><button class="icon-btn" data-copy="proxy-address-value" title="复制代理地址" aria-label="复制代理地址">${icon('copy')}</button><span id="proxy-address-value" class="hidden">${e(p.url)}</span></div>
    ${p.local ? `<div class="location-options" aria-label="使用位置"><button data-location="server" class="${remote ? '' : 'active'}">在代理服务器上</button><button data-location="remote" class="${remote ? 'active' : ''}">在其他电脑上</button></div>` : ''}
    ${remote ? `<div class="ssh-guide"><p class="small muted">先在你的电脑上建立 SSH 隧道，并保持此终端开启，再在另一个终端执行下方代理命令。</p><label for="ssh-user">服务器 SSH 用户名</label><input id="ssh-user" autocomplete="username" placeholder="输入你的 SSH 用户名" value="${e(state.sshUser)}"><div id="ssh-command"></div></div>` : `<p class="proxy-explanation small">${p.local ? '以下命令请在运行 Mihomo 的服务器上执行。这里的 127.0.0.1 指这台代理服务器。' : '在能访问此服务器的设备上，复制命令即可使用 HTTP 代理。'}</p>`}
    <div class="command-tabs" role="tablist" aria-label="终端类型">${[
      ['shell', 'Shell', 'Bash / Zsh'],
      ['powershell', 'PowerShell', 'Windows'],
      ['cmd', 'CMD', 'Windows'],
    ]
      .map(
        ([key, name, note]) =>
          `<button role="tab" data-shell="${key}" aria-selected="${key === state.commandShell}" class="${key === state.commandShell ? 'active' : ''}">${name}<span>${note}</span></button>`,
      )
      .join('')}</div>
    <div id="proxy-commands"></div><p class="command-note small muted">${icon('shield')} 只影响当前终端会话；支持代理环境变量的程序会自动使用。</p>
    <details class="app-proxy-settings"><summary>在其他应用中填写什么？<span class="chevron">⌄</span></summary><div class="app-settings-grid"><span>HTTP 地址</span><code>${e(p.host)}</code><span>HTTP 端口</span><code>${p.port}</code>${p.socks ? `<span>SOCKS5 端口</span><code>${p.socks}</code>` : ''}<span>HTTPS 代理地址</span><code>${e(p.url)}</code></div><p class="small muted">HTTP 和 HTTPS 代理都填写 http:// 地址。${p.local ? '其他电脑请先建立上方的 SSH 隧道，再填写 127.0.0.1。' : ''}</p></details></div>`;
  renderProxyCommands();
  renderSshCommand();
  if ($('#ssh-user'))
    $('#ssh-user').oninput = (event) => {
      state.sshUser = event.target.value;
      renderSshCommand();
    };
}

function renderProxyCommands() {
  const target = $('#proxy-commands'),
    p = proxyEndpoint();
  if (!target || !p) return;
  const commands = proxyCommands(p.url, state.commandShell);
  target.innerHTML =
    codeBlock('proxy-enable', '启用代理', commands.enable) +
    `<details class="disable-proxy"><summary>关闭当前终端的代理<span class="chevron">⌄</span></summary>${codeBlock('proxy-disable', '关闭代理', commands.disable)}</details>`;
}

function renderSshCommand() {
  const target = $('#ssh-command'),
    p = proxyEndpoint();
  if (!target || !p) return;
  const user = state.sshUser.trim(),
    valid = /^[a-zA-Z0-9_][a-zA-Z0-9_.-]*$/.test(user);
  const server = /^[a-zA-Z0-9.:[\]-]+$/.test(location.hostname) ? location.hostname : 'SERVER';
  target.innerHTML = codeBlock(
    'ssh-tunnel',
    '建立 SSH 隧道',
    `ssh -N -o ExitOnForwardFailure=yes -L 127.0.0.1:${p.port}:127.0.0.1:${p.port} ${valid ? user : '<SSH用户名>'}@${server}`,
    !valid,
  );
}
export function initProxyGuide() {
  document.addEventListener('click', (event) => {
    const button = event.target.closest('button');
    if (button?.dataset.shell) {
      state.commandShell = button.dataset.shell;
      document.querySelectorAll('[data-shell]').forEach((b) => {
        b.classList.toggle('active', b === button);
        b.setAttribute('aria-selected', String(b === button));
      });
      renderProxyCommands();
    }
    if (button?.dataset.location) {
      state.proxyLocation = button.dataset.location;
      renderProxyGuide();
    }
  });
  document.addEventListener('keydown', (event) => {
    if (
      !event.target.matches('[data-shell]') ||
      !['ArrowLeft', 'ArrowRight', 'Home', 'End'].includes(event.key)
    )
      return;
    event.preventDefault();
    const tabs = [...document.querySelectorAll('[data-shell]')],
      index = tabs.indexOf(event.target);
    const next =
      event.key === 'Home'
        ? 0
        : event.key === 'End'
          ? tabs.length - 1
          : (index + (event.key === 'ArrowRight' ? 1 : -1) + tabs.length) % tabs.length;
    tabs[next].click();
    tabs[next].focus();
  });
}

export { renderProxyGuide };
