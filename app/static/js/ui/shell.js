import { $ } from '../core/dom.js';
import { state } from '../core/state.js';
import { icon } from './icons.js';

function renderMode() {
  document.querySelectorAll('#modes button').forEach((b) => {
    const on = b.dataset.mode === state.overview.mode;
    b.classList.toggle('active', on);
    b.setAttribute('aria-pressed', on);
  });
  $('#tun').textContent = state.overview.tun ? 'TUN 已开启' : 'TUN 已关闭';
  $('#tun').classList.toggle('primary', state.overview.tun);
  $('#kernel-version').textContent = state.overview.version;
}

function renderThemeButton() {
  const dark = document.documentElement.dataset.theme === 'dark';
  $('#theme-toggle').innerHTML = icon(dark ? 'sun' : 'moon');
  $('#theme-toggle').setAttribute('aria-label', dark ? '切换浅色主题' : '切换深色主题');
}
export function initShell(navigate) {
  document.querySelectorAll('[data-icon]').forEach((el) => (el.innerHTML = icon(el.dataset.icon)));
  document.querySelectorAll('[data-brand]').forEach((el) => {
    el.innerHTML = `<span class="brand-symbol">m<span></span></span><span class="brand-word">mihomo<span>网络控制台</span></span>`;
    el.onclick = (event) => {
      event.preventDefault();
      if (state.authenticated) navigate('overview');
    };
  });

  renderThemeButton();
  $('#theme-toggle').onclick = () => {
    const theme = document.documentElement.dataset.theme === 'dark' ? 'light' : 'dark';
    document.documentElement.dataset.theme = theme;
    try {
      localStorage.setItem('mihomo-theme', theme);
    } catch {}
    renderThemeButton();
  };
}

export { renderMode };
