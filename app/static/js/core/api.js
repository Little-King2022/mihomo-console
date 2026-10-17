import { emit } from './events.js';

async function api(path, options = {}) {
  const response = await fetch(path, {
    credentials: 'same-origin',
    headers: { 'Content-Type': 'application/json' },
    ...options,
  });
  if (response.status === 401) {
    emit('session:expired');
    throw new Error('unauthorized');
  }
  const raw = await response.text();
  let data;
  try {
    data = raw ? JSON.parse(raw) : null;
  } catch {
    throw new Error('服务响应异常，请稍后重试');
  }
  if (!response.ok) throw new Error(data?.detail || response.statusText);
  return data;
}

export { api };
