import { $ } from '../core/dom.js';
import { hideHistoryTooltip, historyTooltipActive } from './history.js';

let focusBeforeModal;
let cleanup;
function openModal(html, { onClose } = {}) {
  cleanup?.();
  cleanup = onClose;
  focusBeforeModal = document.activeElement;
  $('#modal').innerHTML =
    `<div class="dialog" role="dialog" aria-modal="true" aria-labelledby="dialog-title">${html}</div>`;
  $('#modal').classList.remove('hidden');
  $('#modal').onclick = (event) => {
    if (event.target.id === 'modal') closeModal();
  };
  $('#modal input, #modal select, #modal button')?.focus();
}
export function closeModal() {
  hideHistoryTooltip();
  const callback = cleanup;
  cleanup = undefined;
  callback?.();
  $('#modal').classList.add('hidden');
  focusBeforeModal?.focus();
}
export function initModal() {
  document.addEventListener('keydown', (event) => {
    if (event.key === 'Escape' && historyTooltipActive()) {
      hideHistoryTooltip();
      event.preventDefault();
      return;
    }
    if ($('#modal').classList.contains('hidden')) return;
    if (event.key === 'Escape') closeModal();
    if (event.key === 'Tab') {
      const inputs = [
        ...$('#modal').querySelectorAll('button,input,select,textarea,a[href]'),
      ].filter((x) => !x.disabled);
      const first = inputs[0],
        last = inputs.at(-1);
      if (event.shiftKey && document.activeElement === first) {
        event.preventDefault();
        last.focus();
      }
      if (!event.shiftKey && document.activeElement === last) {
        event.preventDefault();
        first.focus();
      }
    }
  });
}

export { openModal };
