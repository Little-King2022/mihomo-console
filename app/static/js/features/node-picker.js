import { $, e, empty, formActions } from '../core/dom.js';
import { stamp } from '../core/format.js';
import { state } from '../core/state.js';
import { api } from '../core/api.js';
import { act } from '../core/actions.js';
import { openModal, closeModal } from '../ui/modal.js';
import { nodeHistory, hideHistoryTooltip } from '../ui/history.js';
import { toast } from '../ui/toast.js';

let groupDraft = null;
const strategies = {
  fallback: '失败自动回退',
  'url-test': '自动选择低延迟',
  select: '手动选择节点',
};

const nodeKey = (node) => JSON.stringify([node.provider, node.name]);

const subscriptionInfoNode = (node) =>
  [
    '剩余流量',
    '重置剩余',
    '距离下次重置',
    '套餐到期',
    '官网地址',
    '防失联',
    '更新订阅',
    '发布页',
    '用中转',
  ].some((word) => node.name.includes(word));

async function groupForm(group) {
  openModal(
    `<h2 id="dialog-title">${group ? '编辑' : '添加'}代理组</h2><div class="empty">正在读取全部订阅节点与测速记录…</div><button class="btn" id="cancel">取消</button>`,
    {
      onClose: () => {
        groupDraft = null;
      },
    },
  );
  const dialog = $('#modal .dialog');
  dialog.classList.add('node-picker-dialog');
  $('#cancel').onclick = closeModal;
  try {
    const catalog = await api('/api/node-catalog');
    if ($('#modal').classList.contains('hidden') || $('#modal .dialog') !== dialog) return;
    const selected = new Set((group?.selected_nodes || []).map(nodeKey));
    const known = new Set(catalog.map(nodeKey));
    const missing = (group?.selected_nodes || [])
      .filter((n) => !known.has(nodeKey(n)))
      .map((n) => ({
        ...n,
        node_name: n.name,
        subscription: state.subs.find((s) => `sub${s.id}` === n.provider)?.name || n.provider,
        history: [],
        available: false,
      }));
    const nodes = [
      ...catalog.map((n) => ({ ...n, available: true, informational: subscriptionInfoNode(n) })),
      ...missing,
    ];
    nodes.sort(
      (a, b) =>
        Number(selected.has(nodeKey(b))) - Number(selected.has(nodeKey(a))) ||
        Number(!!a.informational) - Number(!!b.informational),
    );
    groupDraft = { selected, nodes, search: '', provider: '', status: 'all' };
    dialog.innerHTML = /* HTML */ `<div class="picker-heading">
        <div>
          <span class="eyebrow">PROXY GROUP</span>
          <h2 id="dialog-title">${group ? '编辑' : '添加'}代理组</h2>
        </div>
        <button class="icon-btn" id="picker-close" aria-label="关闭编辑">✕</button>
      </div>
      <div class="picker-fields">
        <div>
          <label for="g-name">代理组名称</label
          ><input id="g-name" value="${e(group?.name)}" placeholder="为这组节点取一个名字" />
        </div>
        <div>
          <label for="g-strategy">节点使用策略</label
          ><select id="g-strategy">
            ${Object.entries(strategies)
              .map(
                ([key, label]) =>
                  `<option value="${key}" ${key === group?.strategy ? 'selected' : ''}>${label}</option>`,
              )
              .join('')}
          </select>
        </div>
      </div>
      <div class="picker-options">
        <label class="check"
          ><input
            id="g-default"
            type="checkbox"
            ${group?.is_default ? 'checked' : ''}
          />作为其余境外流量的默认出口</label
        ><label class="check"
          ><input
            id="g-enabled"
            type="checkbox"
            ${!group || group.enabled ? 'checked' : ''}
          />启用代理组</label
        >
      </div>
      <div class="picker-section-heading">
        <div>
          <h3>选择节点 <span id="picker-count" class="count-badge"></span></h3>
          <p class="small muted">
            勾选卡片，将节点加入这组。柱形从左到右显示最近测速；绿色为成功，红色为失败。
          </p>
        </div>
      </div>
      <div class="picker-toolbar">
        <input
          id="picker-search"
          type="search"
          aria-label="搜索待选节点"
          placeholder="搜索节点、订阅或协议…"
        /><select id="picker-provider" aria-label="按订阅筛选节点">
          <option value="">全部订阅</option>
          ${[...new Map(groupDraft.nodes.map((n) => [n.provider, n.subscription])).entries()].map(([id, name]) => `<option value="${e(id)}">${e(name)}</option>`).join('')}</select
        ><select id="picker-status" aria-label="按节点状态筛选">
          <option value="all">全部节点</option>
          <option value="selected">只看已选</option>
          <option value="alive">测速可用</option>
          <option value="failed">失败或未检测</option>
        </select>
      </div>
      <div class="picker-bulk">
        <span id="picker-visible-count" class="small muted"></span>
        <div class="row">
          <button class="text-btn" id="picker-select-visible">全选可见节点</button
          ><button class="text-btn" id="picker-clear-visible">取消可见节点</button>
        </div>
      </div>
      <div id="picker-node-list" class="picker-node-list"></div>
      <div id="picker-selection-note" class="small muted"></div>
      <div class="picker-bottom">
        <p class="small muted">短暂停用的节点仍会保留勾选，恢复后自动参与转发。</p>
        ${formActions('save-group')}
      </div>`;
    $('#picker-close').onclick = closeModal;
    $('#cancel').onclick = closeModal;
    $('#picker-search').oninput = (event) => {
      groupDraft.search = event.target.value;
      renderNodePicker();
    };
    $('#picker-provider').onchange = (event) => {
      groupDraft.provider = event.target.value;
      renderNodePicker();
    };
    $('#picker-status').onchange = (event) => {
      groupDraft.status = event.target.value;
      renderNodePicker();
    };
    $('#picker-select-visible').onclick = () => {
      visiblePickerNodes()
        .filter((n) => n.available)
        .forEach((n) => groupDraft.selected.add(nodeKey(n)));
      renderNodePicker();
    };
    $('#picker-clear-visible').onclick = () => {
      visiblePickerNodes().forEach((n) => groupDraft.selected.delete(nodeKey(n)));
      renderNodePicker();
    };
    $('#picker-node-list').onchange = (event) => {
      if (!event.target.matches('[data-pick-node]')) return;
      const node = groupDraft.nodes[Number(event.target.dataset.pickNode)],
        key = nodeKey(node);
      if (event.target.checked) groupDraft.selected.add(key);
      else groupDraft.selected.delete(key);
      if (groupDraft.status === 'selected') renderNodePicker();
      else {
        event.target.closest('.pick-node').classList.toggle('selected', event.target.checked);
        updatePickerCount();
      }
    };
    $('#save-group').onclick = act(async () => {
      if (!groupDraft.selected.size) throw new Error('请至少勾选一个节点');
      const selected_nodes = groupDraft.nodes
        .filter((n) => groupDraft.selected.has(nodeKey(n)))
        .map((n) => ({ provider: n.provider, name: n.name }));
      const body = {
        name: $('#g-name').value,
        strategy: $('#g-strategy').value,
        selected_nodes,
        is_default: $('#g-default').checked,
        enabled: $('#g-enabled').checked,
      };
      await api(group ? `/api/groups/${group.id}` : '/api/groups', {
        method: group ? 'PUT' : 'POST',
        body: JSON.stringify(body),
      });
      closeModal();
    });
    renderNodePicker();
    $('#g-name').focus();
  } catch (error) {
    if (error.message !== 'unauthorized') {
      toast(error.message, true);
      if ($('#modal .dialog') === dialog) {
        dialog.innerHTML = `<h2 id="dialog-title">暂时无法加载节点</h2><p class="muted">${e(error.message)}</p><button class="btn" id="cancel">关闭</button>`;
        $('#cancel').onclick = closeModal;
      }
    }
  }
}

function visiblePickerNodes() {
  return groupDraft.nodes.filter((n) => {
    const selected = groupDraft.selected.has(nodeKey(n));
    return (
      (n.node_name + ' ' + n.subscription + ' ' + (n.type || ''))
        .toLowerCase()
        .includes(groupDraft.search.toLowerCase()) &&
      (!groupDraft.provider || n.provider === groupDraft.provider) &&
      (groupDraft.status === 'all' ||
        (groupDraft.status === 'selected' && selected) ||
        (groupDraft.status === 'alive' && n.alive) ||
        (groupDraft.status === 'failed' && !n.alive))
    );
  });
}

function updatePickerCount() {
  $('#picker-count').textContent =
    '已选 ' + groupDraft.selected.size + ' / ' + groupDraft.nodes.length;
  const missing = groupDraft.nodes.filter(
    (n) => !n.available && groupDraft.selected.has(nodeKey(n)),
  ).length;
  $('#picker-selection-note').textContent = missing
    ? `${missing} 个已选节点暂未出现在启用的订阅中，可取消勾选或等待订阅恢复。`
    : '';
}

function renderNodePicker() {
  hideHistoryTooltip();
  const nodes = visiblePickerNodes();
  updatePickerCount();
  $('#picker-visible-count').textContent = `显示 ${nodes.length} 个节点`;
  $('#picker-node-list').innerHTML =
    nodes
      .map((n) => {
        const index = groupDraft.nodes.indexOf(n),
          selected = groupDraft.selected.has(nodeKey(n));
        return /* HTML */ `<label
          class="pick-node ${selected ? 'selected' : ''} ${n.available ? '' : 'missing'}"
          ><div class="pick-node-top">
            <span class="small muted">${e(n.subscription)}${n.type ? ' · ' + e(n.type) : ''}</span
            ><input
              type="checkbox"
              data-pick-node="${index}"
              ${selected ? 'checked' : ''}
              aria-label="选择 ${e(n.node_name)}"
            />
          </div>
          <strong class="pick-node-name">${e(n.node_name)}</strong>
          <div class="split">
            <span class="pick-delay ${n.alive ? 'ok' : 'bad'}"
              >${n.alive ? n.delay + ' <small>ms</small>' : '—'}</span
            ><span
              class="tag ${!n.available ? '' : n.cooldown_until ? 'warn' : n.alive ? 'ok' : n.checked_at ? 'bad' : ''}"
              >${!n.available ? '暂不可用' : n.informational ? '订阅说明' : n.cooldown_until ? '短暂停用' : n.alive ? '测速可用' : n.checked_at ? '测速失败' : '尚未测速'}</span
            >
          </div>
          ${nodeHistory(n)}<span class="pick-checked small muted"
            >最近检测 ${stamp(n.checked_at)}</span
          ></label
        >`;
      })
      .join('') || empty('没有符合条件的节点，试试其他搜索或筛选条件。');
}

export { strategies, groupForm };
