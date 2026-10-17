import { bytes, stamp } from '../core/format.js';

function chart(history, keys, colors, height = 160) {
  const values = history.filter((h) => keys.some((k) => h[k] != null));
  if (values.length < 2)
    return '<div class="chart-wait"><span class="sampling-dots"><i></i><i></i><i></i></span><strong>正在聆听网络的脉搏</strong><span>几秒后，流量曲线将在这里展开</span></div>';
  const max = Math.max(1, ...values.flatMap((h) => keys.map((k) => Number(h[k]) || 0)));
  const paths = keys
    .map((k, j) => {
      const points = values
        .map(
          (h, i) =>
            `${(i / (values.length - 1)) * 900},${height - 12 - ((Number(h[k]) || 0) / max) * (height - 24)}`,
        )
        .join(' ');
      return /* HTML */ `<defs
          ><linearGradient id="traffic-fill-${j}" x1="0" y1="0" x2="0" y2="1"
            ><stop offset="0%" stop-color="${colors[j]}" stop-opacity=".14" /><stop
              offset="100%"
              stop-color="${colors[j]}"
              stop-opacity=".005" /></linearGradient></defs
        ><polygon
          points="0,${height} ${points} 900,${height}"
          fill="url(#traffic-fill-${j})"
        /><polyline
          points="${points}"
          fill="none"
          stroke="${colors[j]}"
          stroke-width="2.5"
          vector-effect="non-scaling-stroke"
        />`;
    })
    .join('');
  return /* HTML */ `<div class="chart-scale muted small">${bytes(max)}/s</div>
    <svg
      viewBox="0 0 900 ${height}"
      preserveAspectRatio="none"
      role="img"
      aria-label="最近流量速率趋势"
    >
      <path
        d="M0 ${height / 4}H900 M0 ${height / 2}H900 M0 ${(height * 3) / 4}H900 M0 ${height - 1}H900"
        stroke="var(--line)"
        stroke-dasharray="4 6"
      />
      ${paths}
    </svg>
    <div class="split small muted"><span>${stamp(values[0].time)}</span><span>现在</span></div>`;
}

export { chart };
