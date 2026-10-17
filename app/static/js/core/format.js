const stamp = (v) => (v ? new Date(v).toLocaleString('zh-CN', { hour12: false }) : '尚未更新');

function bytes(v) {
  if (v == null || !Number.isFinite(Number(v))) return '—';
  const n = Math.max(0, Number(v)),
    i = Math.min(4, Math.floor(Math.log2(n || 1) / 10));
  return `${(n / 1024 ** i).toFixed(i ? 2 : 0)} ${['B', 'KB', 'MB', 'GB', 'TB'][i]}`;
}

const rate = (v) => (v == null ? '采样中' : `${bytes(v)}/s`);

export { stamp, bytes, rate };
