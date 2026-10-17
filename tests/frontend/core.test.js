import test from 'node:test';
import assert from 'node:assert/strict';
import { api } from '../../app/static/js/core/api.js';
import { events } from '../../app/static/js/core/events.js';
import { loadAll } from '../../app/static/js/core/data.js';
import { state } from '../../app/static/js/core/state.js';
import { e } from '../../app/static/js/core/dom.js';
import { bytes, rate } from '../../app/static/js/core/format.js';
import { proxyCommands, proxyEndpoint } from '../../app/static/js/features/proxy-commands.js';

test('API emits session expiry without depending on login UI', async (t) => {
  let expired = 0;
  const listener = () => expired++;
  events.addEventListener('session:expired', listener);
  t.after(() => events.removeEventListener('session:expired', listener));
  t.mock.method(globalThis, 'fetch', async () => new Response('', { status: 401 }));
  await assert.rejects(api('/api/session'), /unauthorized/);
  assert.equal(expired, 1);
});

test('API handles empty successful responses and invalid server responses', async (t) => {
  const fetch = t.mock.method(globalThis, 'fetch', async () => new Response(null, { status: 204 }));
  assert.equal(await api('/api/session'), null);
  fetch.mock.mockImplementation(async () => new Response('<html>Bad Gateway</html>', { status: 502 }));
  await assert.rejects(api('/api/overview'), /服务响应异常/);
});

test('concurrent refresh callers await one complete snapshot', async (t) => {
  let unlock;
  const gate = new Promise((resolve) => { unlock = resolve; });
  const payloads = {
    '/api/overview': { version: 'test' },
    '/api/subscriptions': [{ id: 1 }],
    '/api/rules': [{ id: 2 }],
    '/api/groups': [{ id: 3 }],
    '/api/nodes': [{ node_name: 'A' }],
  };
  const fetch = t.mock.method(globalThis, 'fetch', async (path) => {
    await gate;
    return Response.json(payloads[path]);
  });
  let observed = 0;
  const listener = () => {
    observed++;
    assert.deepEqual(state.overview, payloads['/api/overview']);
    assert.deepEqual(state.nodes, payloads['/api/nodes']);
  };
  events.addEventListener('data:updated', listener);
  t.after(() => events.removeEventListener('data:updated', listener));
  const first = loadAll();
  const second = loadAll();
  assert.equal(first, second);
  assert.equal(fetch.mock.callCount(), 5);
  assert.equal(observed, 0);
  unlock();
  await Promise.all([first, second]);
  assert.equal(observed, 1);
});

test('failed refresh preserves the last snapshot and can be retried', async (t) => {
  const previous = state.overview;
  const fetch = t.mock.method(globalThis, 'fetch', async () => { throw new Error('offline'); });
  await assert.rejects(loadAll(), /offline/);
  assert.equal(state.overview, previous);
  fetch.mock.mockImplementation(async () => Response.json({ recovered: true }));
  await loadAll();
  assert.deepEqual(state.overview, { recovered: true });
});

test('proxy addresses honor loopback scope and IPv6 URL syntax', () => {
  assert.equal(proxyEndpoint({ available: false }, 'console.example'), null);
  assert.equal(proxyEndpoint({ available: true, mixed_port: 0 }, 'console.example'), null);
  const local = proxyEndpoint({ available: true, mixed_port: 7890, allow_lan: false, bind_address: '*' }, 'console.example');
  assert.equal(local.url, 'http://127.0.0.1:7890');
  assert.equal(local.socks, 7890);
  const ipv6 = proxyEndpoint({ available: true, mixed_port: 7890, allow_lan: true, bind_address: '2001:db8::2' }, 'console.example');
  assert.equal(ipv6.url, 'http://[2001:db8::2]:7890');
});

test('all terminal commands retain HTTP proxy protocol and disable commands', () => {
  for (const shell of ['shell', 'powershell', 'cmd']) {
    const { enable, disable } = proxyCommands('http://127.0.0.1:7890', shell);
    assert.match(enable, /http:\/\/127\.0\.0\.1:7890/);
    assert.doesNotMatch(enable, /https:\/\//);
    assert.match(enable, /HTTPS_PROXY/);
    assert.match(enable, /NO_PROXY/);
    assert.match(disable, /HTTP_PROXY/);
    assert.match(disable, /NO_PROXY/);
  }
});

test('shared display helpers escape user text and format unknown telemetry', () => {
  assert.equal(e('<img src=x onerror="bad()">'), '&lt;img src=x onerror=&quot;bad()&quot;&gt;');
  assert.equal(bytes(null), '—');
  assert.equal(bytes(1024), '1.00 KB');
  assert.equal(rate(null), '采样中');
});
