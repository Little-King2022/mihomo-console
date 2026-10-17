function proxyCommands(url, shell) {
  const bypass = 'localhost,127.0.0.1,::1';
  if (shell === 'powershell')
    return {
      enable: `$env:HTTP_PROXY = "${url}"\n$env:HTTPS_PROXY = $env:HTTP_PROXY\n$env:NO_PROXY = "${bypass}"`,
      disable:
        'Remove-Item Env:HTTP_PROXY, Env:HTTPS_PROXY, Env:NO_PROXY -ErrorAction SilentlyContinue',
    };
  if (shell === 'cmd')
    return {
      enable: `set "HTTP_PROXY=${url}"\nset "HTTPS_PROXY=${url}"\nset "NO_PROXY=${bypass}"`,
      disable: 'set "HTTP_PROXY="\nset "HTTPS_PROXY="\nset "NO_PROXY="',
    };
  return {
    enable: `export http_proxy="${url}"\nexport https_proxy="$http_proxy"\nexport HTTP_PROXY="$http_proxy"\nexport HTTPS_PROXY="$http_proxy"\nexport no_proxy="${bypass}"\nexport NO_PROXY="$no_proxy"`,
    disable: 'unset http_proxy https_proxy HTTP_PROXY HTTPS_PROXY no_proxy NO_PROXY',
  };
}
export function proxyEndpoint(config, hostname) {
  const p = config;
  if (!p?.available) return null;
  const port = Number(p.mixed_port || p.http_port);
  if (!Number.isInteger(port) || port < 1 || port > 65535) return null;
  const address = p.bind_address;
  const local = !p.allow_lan || ['127.0.0.1', 'localhost', '::1'].includes(address);
  const host = local
    ? '127.0.0.1'
    : address && address !== '*' && address !== '0.0.0.0' && address !== '::'
      ? address
      : hostname;
  const formatted = host.includes(':') && !host.startsWith('[') ? '[' + host + ']' : host;
  return {
    port,
    host,
    local,
    url: 'http://' + formatted + ':' + port,
    socks: Number(p.mixed_port || p.socks_port) || null,
  };
}

export { proxyCommands };
