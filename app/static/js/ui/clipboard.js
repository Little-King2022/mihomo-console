import { toast } from './toast.js';

async function copyCommand(button) {
  const element = document.getElementById(button.dataset.copy);
  if (!element) return;
  const text = element.textContent;
  let copied = false;
  if (navigator.clipboard?.writeText)
    try {
      await navigator.clipboard.writeText(text);
      copied = true;
    } catch {}
  if (!copied) {
    const focused = document.activeElement,
      area = document.createElement('textarea');
    area.value = text;
    area.style.cssText = 'position:fixed;left:-9999px;top:0';
    area.setAttribute('aria-hidden', 'true');
    document.body.append(area);
    area.select();
    try {
      copied = document.execCommand('copy');
    } catch {
    } finally {
      area.remove();
      focused?.focus({ preventScroll: true });
    }
  }
  if (copied) {
    toast('已复制到剪贴板');
    const label = button.querySelector('span');
    if (label) {
      label.textContent = '已复制';
      setTimeout(() => {
        if (label.isConnected) label.textContent = '复制';
      }, 1800);
    }
  } else {
    const range = document.createRange();
    range.selectNodeContents(element);
    const selection = window.getSelection();
    selection.removeAllRanges();
    selection.addRange(range);
    toast('请按 Ctrl+C 或 ⌘C 复制已选中的命令');
  }
}
export function initClipboard() {
  document.addEventListener('click', (event) => {
    const button = event.target.closest('[data-copy]');
    if (button) copyCommand(button);
  });
}

export { copyCommand };
