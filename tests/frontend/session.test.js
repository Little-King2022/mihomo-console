import test from 'node:test';
import assert from 'node:assert/strict';
import { authenticate, consumeLoginToken } from '../../app/static/js/core/session.js';

function browserAt(href) {
  const browser = {
    location: { href },
    history: {
      state: { page: 'groups' },
      replaceState(state, title, path) {
        assert.equal(state, this.state);
        browser.location.href = new URL(path, browser.location.href).href;
      },
    },
  };
  return browser;
}

test('link login removes every token parameter before sending the encoded credential in a POST', async (t) => {
  const browser = browserAt('http://console.example/?keep=1&token=a%2Bb%26c%3Dd%23e&token=duplicate#groups');
  const fetch = t.mock.method(globalThis, 'fetch', async (path, options) => {
    assert.equal(browser.location.href, 'http://console.example/?keep=1#groups');
    assert.equal(path, '/api/login');
    assert.equal(options.method, 'POST');
    assert.equal(options.credentials, 'same-origin');
    assert.deepEqual(JSON.parse(options.body), { token: 'a+b&c=d#e' });
    return Response.json({ ok: true });
  });
  await authenticate(consumeLoginToken(browser));
  assert.equal(fetch.mock.callCount(), 1);
  assert.equal(consumeLoginToken(browser), null);
});

test('ordinary visits keep their URL and restore the cookie session', async (t) => {
  const href = 'http://console.example/?keep=1#nodes';
  const browser = browserAt(href);
  t.mock.method(browser.history, 'replaceState', () => assert.fail('URL should remain unchanged'));
  const fetch = t.mock.method(globalThis, 'fetch', async (path) => {
    assert.equal(path, '/api/session');
    return new Response(null, { status: 204 });
  });
  await authenticate(consumeLoginToken(browser));
  assert.equal(browser.location.href, href);
  assert.equal(fetch.mock.callCount(), 1);
});

test('invalid or empty link tokens fail authentication after being removed from the URL', async (t) => {
  const fetch = t.mock.method(globalThis, 'fetch', async (path) => {
    assert.equal(path, '/api/login');
    return new Response('', { status: 401 });
  });
  for (const query of ['token=invalid', 'token=', 'token']) {
    const browser = browserAt(`http://console.example/?${query}#overview`);
    await assert.rejects(authenticate(consumeLoginToken(browser)), /unauthorized/);
    assert.equal(browser.location.href, 'http://console.example/#overview');
  }
  assert.equal(fetch.mock.callCount(), 3);
});

test('network failures leave a clean URL and allow manual login retry', async (t) => {
  const browser = browserAt('http://console.example/?token=example');
  const fetch = t.mock.method(globalThis, 'fetch', async () => { throw new Error('offline'); });
  await assert.rejects(authenticate(consumeLoginToken(browser)), /offline/);
  assert.equal(browser.location.href, 'http://console.example/');
  fetch.mock.mockImplementation(async () => Response.json({ ok: true }));
  await authenticate('manual-retry');
});
