import { $ } from '../core/dom.js';

function toast(message, bad = false) {
  const box = $('#notice');
  box.textContent = message;
  box.className = `notice ${bad ? 'bad' : ''}`;
  clearTimeout(toast.timer);
  toast.timer = setTimeout(() => box.classList.add('hidden'), 5000);
}

export { toast };
