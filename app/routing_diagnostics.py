"""Manual, read-only routing explanations from live rules and kernel state."""
import ipaddress
import json
import re
import socket
import ssl
import threading
import time
from datetime import datetime, timezone
from functools import lru_cache

import httpx
import yaml

from . import campus, router
from .campus_config import domain_name
from .mihomo import client, config_dir, config_path

_lock = threading.Lock()


def clean_request(payload):
    target = str(payload.get('target') or '').strip().lower().rstrip('.')
    try:
        target = str(ipaddress.IPv4Address(target))
    except ValueError:
        target = domain_name(target)
        if target.startswith('+.'):
            raise ValueError('请填写具体域名或 IPv4 地址')
    port = payload.get('port', 443)
    if isinstance(port, bool) or not isinstance(port, int) or not 1 <= port <= 65535:
        raise ValueError('端口须为 1–65535 的整数')
    probe = payload.get('probe', False)
    if not isinstance(probe, bool):
        raise ValueError('连接实测选项须为布尔值')
    return target, port, probe


def suffix_match(host, pattern):
    pattern = pattern.lower()
    if pattern.startswith('+.'):
        pattern = pattern[2:]
        return host == pattern or host.endswith('.' + pattern)
    if pattern.startswith('*.'):
        return host.endswith('.' + pattern[2:])
    return host == pattern


def resolve(target, config):
    try:
        ipaddress.IPv4Address(target)
        return dict(server=None, answers=[target], status='IP', policy=None)
    except ValueError:
        pass
    profile = router.configured()
    server = str(ipaddress.ip_interface(profile['lan']['address']).ip) if profile else '127.0.0.1'
    # Query the LAN DNS path, including dnsmasq's domain overrides and fake IP.
    output = campus.command(['dig', '-4', '@' + server, target, 'A', '+time=2', '+tries=1',
                             '+noall', '+comments', '+answer'], timeout=4)
    status = re.search(r'status: ([A-Z]+)', output)
    answers = list(dict.fromkeys(re.findall(r'^\S+\s+\d+\s+IN\s+A\s+([\d.]+)\s*$', output, re.M)))
    policies = config.get('dns', {}).get('nameserver-policy', {})
    matches = [key for key in policies if suffix_match(target, key)]
    selected = max(matches, key=len) if matches else None
    return dict(server=server, answers=answers[:8], total_answers=len(answers),
                status=status[1] if status else 'UNKNOWN', policy=selected,
                upstream=policies[selected] if selected else config.get('dns', {}).get('nameserver', []),
                upstream_note='配置的解析服务器；缓存命中与最终应答上游未单独抓包')


def interval(element):
    if isinstance(element, str):
        network = ipaddress.IPv4Network(element, strict=False)
        return int(network.network_address), int(network.broadcast_address)
    if 'prefix' in element:
        return interval(f"{element['prefix']['addr']}/{element['prefix']['len']}")
    if 'range' in element:
        return tuple(int(ipaddress.IPv4Address(x)) for x in element['range'])
    raise ValueError('Unsupported nft element')


def contains(elements, address):
    number = int(ipaddress.IPv4Address(address))
    for element in elements:
        first, last = interval(element)
        if first <= number <= last:
            return element
    return None


def kernel_snapshot():
    return json.loads(campus.command(['nft', '-j', 'list', 'table', 'inet', 'mihomo']))


def bypass_match(snapshot, address):
    """Require an actual daddr-return rule in both OUTPUT and PREROUTING."""
    sets = {row['set']['name']: row['set'].get('elem', []) for row in snapshot.get('nftables', []) if 'set' in row}
    evidence = {}
    intercept = False
    for row in snapshot.get('nftables', []):
        rule = row.get('rule', {})
        chain = rule.get('chain')
        expr = rule.get('expr', [])
        matches = [e['match'] for e in expr if 'match' in e]
        tcp = {'op': '==', 'left': {'meta': {'key': 'l4proto'}}, 'right': 'tcp'}
        ipv4 = {'op': '==', 'left': {'meta': {'key': 'nfproto'}}, 'right': 'ipv4'}
        if (chain == 'output' and any('redirect' in e for e in expr) and tcp in matches
                and all(m in (tcp, ipv4) for m in matches)):
            intercept = True
        if chain not in ('output', 'prerouting') or not any('return' in e for e in expr):
            continue
        # A conditional/source-specific rule cannot establish global bypass.
        if len(matches) != 1:
            continue
        match = matches[0]
        if match.get('op') != '==' or match.get('left') != {'payload': {'protocol': 'ip', 'field': 'daddr'}}:
            continue
        right = match.get('right')
        if isinstance(right, str) and right.startswith('@'):
            elements = sets.get(right[1:], [])
        else:
            elements = right['set'] if isinstance(right, dict) and 'set' in right else [right]
        found = contains(elements, address)
        if found is not None:
            evidence[chain] = dict(handle=rule.get('handle'), match=found,
                                   set=right if isinstance(right, str) and right.startswith('@') else None)
    return dict(bypass=bool(evidence.get('output')), lan_bypass=bool(evidence.get('prerouting')),
                tcp_redirect=intercept, evidence=evidence)


@lru_cache(maxsize=24)
def load_provider(path, modified, fmt='yaml'):
    text = config_dir().joinpath(path).read_text()
    if fmt == 'text':
        return [x.strip() for x in text.splitlines() if x.strip() and not x.strip().startswith('#')]
    data = yaml.safe_load(text)
    return data.get('payload', [])


def rule_match(kind, value, host, address, providers, depth=0):
    kind = kind.upper().replace('-', '')
    if kind == 'DOMAIN':
        return host == value.lower()
    if kind == 'DOMAINSUFFIX':
        return bool(host) and suffix_match(host, '+.' + value)
    if kind == 'DOMAINKEYWORD':
        return bool(host) and value.lower() in host
    if kind == 'IPCIDR':
        return ipaddress.IPv4Address(address) in ipaddress.ip_network(value, strict=False)
    if kind in ('MATCH', 'FINAL'):
        return True
    if kind == 'RULESET':
        if depth > 4:
            return None
        provider = providers.get(value, {})
        path = config_dir().joinpath(provider.get('path', ''))
        if provider.get('behavior') not in ('domain', 'ipcidr', 'classical') or provider.get('format') == 'mrs':
            return None
        if provider.get('type') == 'inline':
            items = provider.get('payload', [])
        elif path.is_file():
            items = load_provider(provider['path'], path.stat().st_mtime_ns, provider.get('format', 'yaml'))
        else:
            return None
        if provider['behavior'] == 'ipcidr':
            return any(ipaddress.IPv4Address(address) in ipaddress.ip_network(n, strict=False) for n in items if ':' not in n)
        if provider['behavior'] == 'classical':
            unknown = False
            for item in items:
                parts = item.split(',')
                if len(parts) < 2:
                    unknown = True
                    continue
                matched = rule_match(parts[0], parts[1], host, address, providers, depth + 1)
                if matched is True:
                    return True
                unknown |= matched is None
            return None if unknown else False
        return any(suffix_match(host, p) for p in items) if host else False
    # GEOIP/GEOSITE and custom rules are deliberately not approximated.
    return None


def explain_rule(rules, config, host, address, mode):
    if mode in ('global', 'direct'):
        return dict(proxy='GLOBAL' if mode == 'global' else 'DIRECT', type='MODE', payload=mode,
                    certain=True, unknown_before=[])
    unknown = []
    for rule in rules:
        if rule.get('extra', {}).get('disabled'):
            continue
        match = rule_match(rule['type'], rule.get('payload', ''), host, address, config.get('rule-providers', {}))
        if match is None:
            unknown.append(dict(type=rule['type'], payload=rule.get('payload')))
        if match is True:
            return {k: rule.get(k) for k in ('index', 'type', 'payload', 'proxy')} | dict(certain=not unknown, unknown_before=unknown)
    return dict(certain=False, proxy=None, unknown_before=unknown)


def proxy_chain(name, proxies):
    chain = []
    while name and name not in chain and len(chain) < 12:
        chain.append(name)
        name = proxies.get(name, {}).get('now')
    return chain


def route_to(address, port):
    return campus.route_to(address, port)


def nat_candidates(profile, address, interface):
    if not profile:
        return []
    return [dict(source=n['source'], interface=n['interface'], action='masquerade') for n in profile['masquerade']
            if n['enabled'] and n['interface'] == interface
            and ipaddress.IPv4Address(address) in ipaddress.ip_network(n['destination'])]


def connection_for(http, source, port):
    response = http.get('/connections', timeout=3)
    response.raise_for_status()
    for conn in response.json().get('connections') or []:
        meta = conn.get('metadata', {})
        if (str(meta.get('sourcePort')) == str(source[1]) and meta.get('sourceIP') == source[0]
                and str(meta.get('destinationPort')) == str(port)):
            return dict(rule=conn.get('rule'), payload=conn.get('rulePayload'),
                        chains=[n for n in conn.get('chains', []) if not n.startswith('__console_lb_')],
                        inbound=meta.get('type'), host=meta.get('host'), destination=meta.get('destinationIP'))
    return None


def probe_connection(http, address, target, port, fake):
    ip = ipaddress.IPv4Address(address)
    if ip.is_loopback or ip.is_multicast or ip.is_unspecified or ip.is_reserved or ip.is_link_local:
        return dict(ok=False, message='该地址不适合连接实测', observed=None)
    start = time.monotonic()
    result = dict(ok=False, observed=None, tls=False)
    sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    sock.settimeout(4)
    try:
        sock.bind(('0.0.0.0', 0))
        sock.connect((address, port))
        source = sock.getsockname()
        result['observed'] = connection_for(http, source, port)
        if port == 443:
            sock = ssl.create_default_context().wrap_socket(sock, server_hostname=target)
            result['tls'] = True
            result['message'] = 'TLS 握手成功，已验证目标证书'
        else:
            result['message'] = 'TCP 建连成功；透明代理可能先接受本地连接，不代表远端服务已响应'
        result['observed'] = connection_for(http, source, port) or result['observed']
        result['ok'] = True
    except ssl.SSLError:
        result['message'] = 'TLS 握手或证书校验失败；不据此判定规则错误或节点失效'
    except OSError:
        result['message'] = '连接失败或超时；目标服务也可能未开放此端口'
    except (httpx.HTTPError, ValueError):
        result['message'] = '无法读取本次连接的内核记录'
    finally:
        sock.close()
    result['latency_ms'] = round((time.monotonic() - start) * 1000, 1)
    if fake and not result.get('observed'):
        result['note'] = 'Fake IP 连接未捕获内核记录，不能确认实际分流'
    return result


def diagnose(payload):
    target, port, active = clean_request(payload)
    if not _lock.acquire(blocking=False):
        raise ValueError('分流诊断正在进行，请稍后再试')
    try:
        config = yaml.safe_load(config_path().read_text())
        profile = router.configured()
        warnings = []
        with client() as http:
            runtime_response = http.get('/configs', timeout=4)
            runtime_response.raise_for_status()
            runtime = runtime_response.json()
            response = http.get('/rules', timeout=4)
            response.raise_for_status()
            rules = response.json()['rules']
            response = http.get('/proxies', timeout=4)
            response.raise_for_status()
            proxies = response.json()['proxies']
            try:
                snapshot = kernel_snapshot()
            except (RuntimeError, ValueError):
                snapshot = None
                warnings.append('无法读取运行中的 nftables 绕过规则，绕过状态保持未知')
            dns = resolve(target, config)
            fake_range = ipaddress.ip_network(config.get('dns', {}).get('fake-ip-range', '198.18.0.1/16'), strict=False)
            host = '' if dns['status'] == 'IP' else target
            results = []
            for address in dns['answers']:
                fake = ipaddress.IPv4Address(address) in fake_range
                kernel = bypass_match(snapshot, address) if snapshot is not None else dict(bypass=None, lan_bypass=None, evidence={})
                rule = explain_rule(rules, config, host, address, runtime.get('mode', 'rule'))
                chain = proxy_chain(rule.get('proxy'), proxies)
                try:
                    route = route_to(address, port)
                except (RuntimeError, ValueError):
                    route = None
                enabled = runtime.get('tun', {}).get('enable', False)
                path = 'kernel' if kernel['bypass'] else 'core' if enabled and kernel.get('tcp_redirect') else 'unknown'
                if not enabled:
                    path = 'system'
                entry = dict(address=address, fake_ip=fake, kernel=kernel, rule=rule, chain=chain,
                             path=path, system_route=route,
                             lan_nat=nat_candidates(profile, address, route['interface']) if path in ('kernel', 'system') and route else [])
                # One user action opens at most one TCP connection, regardless of DNS answer count.
                if active and not results:
                    entry['probe'] = probe_connection(http, address, target, port, fake)
                    if entry['probe'].get('observed') and path == 'kernel':
                        warnings.append('连接实测进入了 mihomo，与地址绕过推导不一致；请检查防火墙优先级或其他重定向规则')
                results.append(entry)
        if not dns['answers']:
            warnings.append('DNS 未返回 IPv4 A 记录；未执行连接实测')
        if dns.get('total_answers', 0) > 8:
            warnings.append('仅展示前 8 个 IPv4 地址；连接实测只检查首个地址')
        if dns['status'] == 'IP' and any(r['fake_ip'] for r in results):
            warnings.append('输入的是 Fake IP，缺少原始域名，规则预测可能不完整；建议输入域名')
        return dict(target=target, port=port, checked_at=datetime.now(timezone.utc).isoformat(timespec='seconds'),
                    mode=runtime.get('mode'), tun=runtime.get('tun', {}).get('enable', False),
                    dns=dns, results=results, warnings=warnings,
                    scope='本机 TCP 视角；LAN 绕过依据 PREROUTING，LAN NAT 为已配置的候选规则。连接实测不代表局域网终端实测。')
    finally:
        _lock.release()
