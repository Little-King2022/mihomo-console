import { $, e, empty } from '../core/dom.js';
import { bytes, rate } from '../core/format.js';
import { state } from '../core/state.js';
import { api } from '../core/api.js';
import { act } from '../core/actions.js';
import { icon } from '../ui/icons.js';
import { subCard } from '../ui/subscription-card.js';
import { chart } from '../ui/chart.js';
import { renderProxyGuide } from '../features/proxy-guide.js';
import { renderGroupDiagnostics } from '../features/group-diagnostics.js';

function metric(label, value, note, klass = '', symbol = 'pulse') {
  return `<div class="card metric ${klass}"><div class="metric-head"><span class="k">${label}</span><span class="metric-symbol">${icon(symbol)}</span></div><div class="v">${value}</div><div class="metric-note">${note}</div></div>`;
}

function renderOverview() {
  const o = state.overview;
  $('#page-overview').innerHTML = /* HTML */ `<div class="overview-status">
      <span id="live-status" class="tag">连接内核中</span
      ><span class="small muted">自动刷新 · 每 2 秒采样</span>
    </div>
    <div id="live-metrics" class="cards live-cards"></div>
    <div class="overview-grid">
      <div class="panel traffic-panel">
        <div class="panel-head">
          <div>
            <h2>流量脉搏 <span class="live-dot"></span></h2>
            <p>此刻的上传与下载</p>
          </div>
          <div class="chart-legend">
            <span class="download">下载</span><span class="upload">上传</span>
          </div>
        </div>
        <div id="traffic-chart" class="traffic-chart"></div>
        <div class="traffic-footer">
          <span>${icon('pulse')} <strong>${o.alive}</strong> / ${o.nodes} 节点可用</span
          ><button class="text-btn" data-go="nodes">查看节点健康 ${icon('arrow')}</button>
        </div>
      </div>
      <div class="panel routing-panel">
        <div class="panel-head">
          <div>
            <h2>出口一览</h2>
            <p>让常用服务各行其道</p>
          </div>
          <span class="soft-icon">${icon('route')}</span>
        </div>
        <div class="routing-list">
          ${
            state.groups
              .filter((g) => g.enabled)
              .map(
                (g) =>
                  /* HTML */ `<div class="route-item">
                    <span class="route-symbol ${g.is_default ? 'regular' : 'home'}"
                      >${icon(g.is_default ? 'globe' : 'shield')}</span
                    >
                    <div>
                      <strong>${e(g.name)}</strong>
                      <p>${e(g.now || '正在等待节点')}</p>
                      <div class="route-services">
                        ${state.rules
                          .filter((r) => r.group_id === g.id && r.enabled)
                          .map((r) => `<span>${e(r.name)}</span>`)
                          .join('')}${g.is_default ? '<span>其余境外服务</span>' : ''}
                      </div>
                    </div>
                    <span class="route-alive ${g.now ? '' : 'pending'}"></span>
                  </div>`,
              )
              .join('') || empty('添加代理组，开始安排出口。')
          }
        </div>
        <button class="text-btn routing-link" data-go="rules">管理分流规则 ${icon('arrow')}</button>
      </div>
    </div>
    <div id="group-diagnostics"></div>
    <div class="overview-grid setup-grid">
      <div id="proxy-guide"></div>
      <div class="subscription-section">
        <div class="panel-head">
          <div>
            <h2>订阅用量</h2>
            <p>${state.subs.length} 条订阅，流量尽在掌握</p>
          </div>
          <button class="icon-btn" id="update-all" title="更新全部订阅" aria-label="更新全部订阅">
            ${icon('refresh')}
          </button>
        </div>
        <div class="subscription-grid">
          ${state.subs.map((s) => subCard(s, true)).join('') || empty('添加订阅后，这里会显示套餐用量。')}
        </div>
        <div class="subscription-footer">
          <button class="text-btn" id="auto">
            ${icon('refresh')} 自动更新${o.auto_update ? '已开启' : '已关闭'}</button
          ><button class="text-btn" data-go="subs">管理订阅 ${icon('arrow')}</button>
        </div>
      </div>
    </div>
    <div class="panel connections-panel">
      <div class="connections-head">
        <h2 class="summary-title">
          ${icon('link')} 当前连接 <span id="connection-count" class="count-badge"></span>
        </h2>
        <span class="small muted">最近建立的 12 条连接</span>
      </div>
      <div id="connections" class="table-wrap"></div>
    </div>`;
  $('#auto').onclick = act(() =>
    api('/api/settings', { method: 'POST', body: JSON.stringify({ auto_update: !o.auto_update }) }),
  );
  $('#update-all').onclick = act(() => api('/api/subscriptions/update-all', { method: 'POST' }));
  renderGroupDiagnostics();
  renderProxyGuide();
  renderLive();
}

function renderLive() {
  if (state.page !== 'overview' || !$('#live-metrics')) return;
  const t = state.telemetry,
    online = t?.online;
  $('#live-status').className = `tag ${online ? 'ok' : 'bad'}`;
  $('#live-status').textContent = online ? '内核运行中' : t?.error || '等待内核数据';
  $('#live-metrics').innerHTML =
    metric(
      '↓ 下载速率',
      online ? rate(t.download) : '—',
      '累计下载 ' + (online ? bytes(t.download_total) : '—'),
      'download',
      'down',
    ) +
    metric(
      '↑ 上传速率',
      online ? rate(t.upload) : '—',
      '累计上传 ' + (online ? bytes(t.upload_total) : '—'),
      'upload',
      'up',
    ) +
    metric('活动连接', online ? t.connections : '—', '此刻正在转发的连接', 'connection', 'link') +
    metric(
      '内核内存',
      online ? bytes(t.memory) : '—',
      t?.memory_source === 'rss' ? 'Mihomo 进程实际驻留内存' : 'Mihomo 内核报告的内存',
      'memory',
      'cpu',
    );
  $('#traffic-chart').innerHTML = chart(
    t?.history || [],
    ['download', 'upload'],
    ['#4b6ef5', '#16a68e'],
  );
  $('#connection-count').textContent = online ? t.connections : '—';
  $('#connections').innerHTML = !online
    ? empty('内核暂时不可达，恢复后自动刷新。')
    : /* HTML */ `<table>
        <thead>
          <tr>
            <th>目标</th>
            <th>转发路径 / 规则</th>
            <th>下载 / 上传</th>
          </tr>
        </thead>
        <tbody>
          ${(t.recent || []).map((c) => `<tr><td><strong>${e(c.host)}</strong><div class="muted small">${e(c.network.toUpperCase())}</div></td><td>${e([...c.chains].reverse().join(' → '))}<div class="muted small">${e(c.rule)}</div></td><td>${bytes(c.download)} / ${bytes(c.upload)}</td></tr>`).join('') || '<tr><td colspan="3" class="empty">当前没有活动连接</td></tr>'}
        </tbody>
      </table>`;
}

export { renderOverview, renderLive };
