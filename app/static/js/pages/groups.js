import { $, e } from '../core/dom.js';
import { state } from '../core/state.js';
import { api } from '../core/api.js';
import { act } from '../core/actions.js';
import { icon } from '../ui/icons.js';
import { strategies, groupForm } from '../features/node-picker.js';

function groupRouting(g) {
  const rules = g.routing_details || [],
    count = new Set(rules.flatMap((r) => r.domains.map((d) => d.type + ':' + d.value))).size;
  return /* HTML */ `<details class="group-routing" data-domain-group="${g.id}">
    <summary>
      <span>${icon('route')} 分流域名 <span class="count-badge">${count}</span></span
      ><span class="chevron">⌄</span>
    </summary>
    <div class="group-routing-body">
      ${g.is_default ? '<p class="default-route-note">默认出口：也会接收未被其他规则匹配的境外流量。</p>' : ''}
      ${
        rules
          .map(
            (r) =>
              /* HTML */ `<div class="routing-rule ${r.enabled ? '' : 'rule-disabled'}">
                <div class="split">
                  <strong>${e(r.name)}</strong
                  ><span class="tag ${r.enabled ? 'ok' : ''}"
                    >${r.enabled ? '优先级 ' + r.priority : '已停用 · 不参与分流'}</span
                  >
                </div>
                <div class="domain-chips">
                  ${r.domains.map((d) => `<span title="${e(d.source)} · ${d.type === 'DOMAIN' ? '仅此域名' : '包含子域名'}"><span class="domain-kind">${d.type === 'DOMAIN' ? '=' : '*.'}</span>${e(d.value)}</span>`).join('') || '<span class="muted small">没有单独的域名条目</span>'}
                </div>
                ${r.pending_sets.length ? `<p class="small muted">规则集 ${e(r.pending_sets.join('、'))} 尚未缓存，下载后显示域名。</p>` : ''}
              </div>`,
          )
          .join('') || '<p class="small muted">尚无指定此组的服务规则。</p>'
      }
      <div class="domain-legend small muted">
        *. 包含子域名　= 仅匹配完整域名；最终出口由规则优先级决定。
      </div>
    </div>
  </details>`;
}

function renderGroups() {
  const expanded = new Map(
    [...document.querySelectorAll('[data-domain-group][open]')].map((d) => [
      d.dataset.domainGroup,
      d.querySelector('.group-routing-body')?.scrollTop || 0,
    ]),
  );
  $('#page-groups').innerHTML = /* HTML */ `<div class="split page-intro">
      <p class="muted">
        勾选节点组成出口池，再选择自动回退、自动测速或手动切换。展开分流域名，可查看经过每组的服务。
      </p>
      <button class="btn primary" id="add-group">＋ 添加代理组</button>
    </div>
    <div class="group-grid">
      ${state.groups
        .map(
          (g) =>
            /* HTML */ `<article class="card group-card" data-group-card="${g.id}">
              <div class="split">
                <span class="eyebrow">${strategies[g.strategy]}</span
                ><span class="tag ${g.enabled ? 'ok' : ''}"
                  >${g.is_default ? '默认境外出口' : g.enabled ? '已启用' : '已停用'}</span
                >
              </div>
              <h2>${e(g.name)}</h2>
              <div class="current-node">
                <span class="muted small">当前出口</span
                ><strong>${e(g.now || '等待内核节点')}</strong>
              </div>
              <p class="small muted">
                已勾选 ${g.selected_nodes?.length ?? g.all.length} 个节点 · 当前候选 ${g.all.length}
                个 · ${state.rules.filter((r) => r.group_id === g.id).length} 条分流规则
              </p>
              <div class="assigned">
                ${state.rules
                  .filter((r) => r.group_id === g.id)
                  .map(
                    (r) =>
                      `<span class="tag ${r.enabled ? '' : 'muted'}">${e(r.name)}${r.enabled ? '' : ' · 已停用'}</span>`,
                  )
                  .join('')}
              </div>
              ${g.strategy === 'select' ? `<label for="select-${g.id}">当前使用节点</label><select id="select-${g.id}" data-select="${g.id}" ${g.enabled ? '' : 'disabled'}>${g.all.map((n) => `<option value="${e(n)}" ${n === g.now ? 'selected' : ''}>${e(n)}</option>`).join('')}</select>` : '<p class="small muted">在勾选的节点中自动选择可用出口。</p>'}${groupRouting(g)}
              <div class="actions">
                <button class="btn primary" data-gedit="${g.id}">
                  ${icon('layers')} 选择节点与编辑</button
                ><button class="btn subtle warn" data-gdel="${g.id}">删除</button>
              </div>
            </article>`,
        )
        .join('')}
    </div>`;
  document.querySelectorAll('[data-domain-group]').forEach((d) => {
    if (expanded.has(d.dataset.domainGroup)) {
      d.open = true;
      d.querySelector('.group-routing-body').scrollTop = expanded.get(d.dataset.domainGroup);
    }
  });
  $('#add-group').onclick = () => groupForm();
  document
    .querySelectorAll('[data-gedit]')
    .forEach(
      (b) => (b.onclick = () => groupForm(state.groups.find((g) => g.id == b.dataset.gedit))),
    );
  document.querySelectorAll('[data-gdel]').forEach(
    (b) =>
      (b.onclick = act(async () => {
        if (confirm('删除这个代理组？'))
          await api(`/api/groups/${b.dataset.gdel}`, { method: 'DELETE' });
      })),
  );
  document.querySelectorAll('[data-select]').forEach(
    (b) =>
      (b.onchange = act(() =>
        api(`/api/groups/${b.dataset.select}/select`, {
          method: 'POST',
          body: JSON.stringify({ name: b.value }),
        }),
      )),
  );
}

export { renderGroups };
