import { $, e } from '../core/dom.js';
import { rate } from '../core/format.js';
import { state } from '../core/state.js';

const validRate = value => typeof value === 'number' && Number.isFinite(value) && value >= 0;

export function compactRate(value) {
  if (!validRate(value)) return '—';
  const units = ['B', 'K', 'M', 'G', 'T'];
  const index = Math.min(4, Math.floor(Math.log2(value || 1) / 10));
  return `${(value / 1024 ** index).toFixed(index ? 1 : 0)}${units[index]}/s`;
}

export function trafficSparkline(history = []) {
  const samples = Array.isArray(history) ? history.slice(-60) : [];
  const max = Math.max(1, ...samples.flatMap(sample => ['download', 'upload'].map(key => validRate(sample?.[key]) ? sample?.[key] : 0)));
  const lines = ['download', 'upload'].map((key, index) => {
    const color = index ? '#8bd5e5' : '#b4dcac';
    const segments = []; let current = [];
    samples.forEach((sample, i) => {
      if (!validRate(sample?.[key])) {
        if (current.length) segments.push(current);
        current = [];
        return;
      }
      current.push(`${(samples.length > 1 ? i * 200 / (samples.length - 1) : 200).toFixed(1)},${(52 - sample?.[key] / max * 44).toFixed(1)}`);
    });
    if (current.length) segments.push(current);
    return segments.filter(segment => segment.length > 1).map(segment => {
      const start = segment[0].split(',')[0], end = segment.at(-1).split(',')[0];
      return `<polygon points="${start},52 ${segment.join(' ')} ${end},52" fill="${color}" fill-opacity=".13"/><polyline points="${segment.join(' ')}" fill="none" stroke="${color}" stroke-width="1.5" stroke-linejoin="round" stroke-linecap="round" vector-effect="non-scaling-stroke"/>`;
    }).join('');
  }).join('');
  return `<svg viewBox="0 0 200 56" preserveAspectRatio="none" role="img" aria-label="实时上下行流量趋势"><path d="M0 52H200" stroke="#adc3d8" stroke-opacity=".2"/>${lines}</svg>`;
}

export function renderSidebarTraffic() {
  const telemetry = state.telemetry;
  const online = telemetry?.online;
  $('#sidebar-traffic-status').textContent = online ? (validRate(telemetry.download) ? '实时' : '采样中') : telemetry ? '离线' : '等待采样';
  $('#sidebar-traffic-chart').innerHTML = trafficSparkline(online ? telemetry.history || [] : []);
  $('#sidebar-traffic-rates').innerHTML = ['download', 'upload'].map((key, index) => {
    const value = online ? telemetry[key] : null;
    const text = validRate(value) ? rate(value) : online ? '采样中' : '—';
    const label = `${index ? '上传' : '下载'} ${text}`;
    return `<span class="sidebar-rate ${key}" title="${e(label)}" aria-label="${e(label)}"><span aria-hidden="true">${index ? '↑' : '↓'}</span><span class="traffic-full">${e(text)}</span><span class="traffic-compact" aria-hidden="true">${e(compactRate(value))}</span></span>`;
  }).join('');
}
