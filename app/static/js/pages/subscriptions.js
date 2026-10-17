import { $, e, empty, formActions } from '../core/dom.js';
import { state } from '../core/state.js';
import { api } from '../core/api.js';
import { act } from '../core/actions.js';
import { subCard } from '../ui/subscription-card.js';
import { openModal, closeModal } from '../ui/modal.js';

function renderSubs() {
  $('#page-subs').innerHTML =
    `<div class="split page-intro"><p class="muted">统一管理订阅、套餐流量与更新时间。单订阅保留原节点名，多订阅自动使用简短编号区分。</p><button class="btn primary" id="add-sub">＋ 添加订阅</button></div><div class="subscription-grid">${state.subs.map((s) => subCard(s)).join('') || empty('还没有订阅，添加后即可开始配置出口。')}</div>`;
  $('#add-sub').onclick = () => subForm();
  document
    .querySelectorAll('[data-sedit]')
    .forEach((b) => (b.onclick = () => subForm(state.subs.find((s) => s.id == b.dataset.sedit))));
  document
    .querySelectorAll('[data-supdate]')
    .forEach(
      (b) =>
        (b.onclick = act(() =>
          api(`/api/subscriptions/${b.dataset.supdate}/update`, { method: 'POST' }),
        )),
    );
  document.querySelectorAll('[data-sdel]').forEach(
    (b) =>
      (b.onclick = act(async () => {
        if (confirm('删除这条订阅？'))
          await api(`/api/subscriptions/${b.dataset.sdel}`, { method: 'DELETE' });
      })),
  );
}

function subForm(sub) {
  openModal(
    /* HTML */ `<h2 id="dialog-title">${sub ? '编辑' : '添加'}订阅</h2>
      <label for="f-name">名称</label><input id="f-name" value="${e(sub?.name)}" /><label
        for="f-url"
        >订阅链接</label
      ><input type="url" id="f-url" value="${e(sub?.url)}" /><label for="f-hours"
        >自动更新间隔（小时）</label
      ><input id="f-hours" type="number" min="1" value="${sub?.interval_hours || 6}" /><label
        class="check"
        ><input
          id="f-enabled"
          type="checkbox"
          ${!sub || sub.enabled ? 'checked' : ''}
        />启用订阅</label
      >${formActions('save-sub')}`,
  );
  $('#cancel').onclick = closeModal;
  $('#save-sub').onclick = act(async () => {
    const body = {
      name: $('#f-name').value.trim(),
      url: $('#f-url').value.trim(),
      interval_hours: Number($('#f-hours').value),
      enabled: $('#f-enabled').checked,
    };
    if (!body.name || !/^https?:\/\//.test(body.url) || body.interval_hours < 1)
      throw new Error('请填写名称、有效订阅链接和至少 1 小时的间隔');
    await api(sub ? `/api/subscriptions/${sub.id}` : '/api/subscriptions', {
      method: sub ? 'PUT' : 'POST',
      body: JSON.stringify(body),
    });
    closeModal();
  });
}

export { renderSubs };
