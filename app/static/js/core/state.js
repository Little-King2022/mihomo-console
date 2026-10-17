const state = {
  page: 'overview',
  authenticated: false,
  overview: null,
  subs: [],
  rules: [],
  groups: [],
  nodes: [],
  telemetry: null,
  filter: '',
  health: 'all',
  nodeProvider: null,
};
Object.assign(state, {
  commandShell: /Win/.test(globalThis.navigator?.platform || '') ? 'powershell' : 'shell',
  proxyLocation: 'server',
  sshUser: '',
});

export { state };
