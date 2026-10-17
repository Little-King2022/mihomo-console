import { api } from './api.js';

// Consume the link once, preserving other query parameters and the current page.
export function consumeLoginToken(browser = window) {
  const url = new URL(browser.location.href);
  const token = url.searchParams.get('token');
  if (token !== null) {
    url.searchParams.delete('token');
    browser.history.replaceState(browser.history.state, '', url.pathname + url.search + url.hash);
  }
  return token;
}

export function authenticate(token = null) {
  return token === null
    ? api('/api/session')
    : api('/api/login', { method: 'POST', body: JSON.stringify({ token }) });
}
