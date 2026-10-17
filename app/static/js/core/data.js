import { api } from './api.js';
import { state } from './state.js';
import { emit } from './events.js';

let refreshing = null;
let lastRefresh = 0;
export function refreshedAt() {
  return lastRefresh;
}
export function loadAll() {
  // Mutations and polling share one in-flight refresh instead of rendering stale data.
  if (refreshing) return refreshing;
  refreshing = (async () => {
    const data = await Promise.all(
      ['/api/overview', '/api/subscriptions', '/api/rules', '/api/groups', '/api/nodes'].map(
        (path) => api(path),
      ),
    );
    [state.overview, state.subs, state.rules, state.groups, state.nodes] = data;
    lastRefresh = Date.now();
    emit('data:updated');
  })().finally(() => {
    refreshing = null;
  });
  return refreshing;
}
