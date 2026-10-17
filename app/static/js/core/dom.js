const $ = (sel) => document.querySelector(sel);

const e = (v) =>
  String(v ?? '').replace(
    /[&<>"']/g,
    (c) => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' })[c],
  );

function empty(text) {
  return `<div class="empty">${text}</div>`;
}

function formActions(id) {
  return `<div class="actions"><button class="btn primary" id="${id}">保存并应用</button><button class="btn" id="cancel">取消</button></div>`;
}

export { $, e, empty, formActions };
