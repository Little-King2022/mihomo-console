"""Validated, credential-free desired state for a Linux NAS/router."""
import ipaddress
import json
import re


def ipv4(value):
    try:
        address = ipaddress.IPv4Address(str(value))
    except ValueError as exc:
        raise ValueError('请输入有效的 IPv4 地址') from exc
    if address.is_unspecified or address.is_multicast or address.is_loopback or address in ipaddress.ip_network('198.18.0.0/15'):
        raise ValueError('不能使用回环、组播或 Fake IP 地址')
    return str(address)


def network(value, default=False):
    try:
        n = ipaddress.IPv4Network(str(value), strict=False)
    except ValueError as exc:
        raise ValueError('请输入有效的 IPv4 网段') from exc
    if (not default and not n.prefixlen) or n.overlaps(ipaddress.ip_network('198.18.0.0/15')):
        if not (default and not n.prefixlen):
            raise ValueError('不能使用默认路由或 Fake IP 网段')
    return str(n)


def integer(value, low, high):
    if isinstance(value, bool):
        raise ValueError('请输入有效整数')
    try:
        result = int(value)
    except (ValueError, TypeError) as exc:
        raise ValueError('请输入有效整数') from exc
    if not low <= result <= high:
        raise ValueError(f'数值须在 {low}–{high} 之间')
    return result


def interface(value):
    if not re.fullmatch(r'[a-zA-Z0-9][a-zA-Z0-9_.-]{0,14}', str(value)):
        raise ValueError('网卡名称无效')
    return str(value)


def enabled(row, key='enabled', default=True):
    value = row.get(key, default)
    if not isinstance(value, bool):
        raise ValueError('开关须为布尔值')
    return value


def clean_config(raw):
    if not isinstance(raw, dict) or raw.get('version') != 1:
        raise ValueError('路由器配置版本无效')
    lan = raw.get('lan', {})
    try:
        address = ipaddress.IPv4Interface(str(lan.get('address')))
    except ValueError as exc:
        raise ValueError('LAN 地址无效') from exc
    ipv4(address.ip)
    if address.network.prefixlen < 16 or address.network.prefixlen > 30:
        raise ValueError('LAN 掩码须为 /16 到 /30')
    result = dict(version=1, domestic_bypass=enabled(raw, 'domestic_bypass'),
                  mtu=integer(raw.get('mtu', 1500), 1280, 9000),
                  lan=dict(interface=interface(lan.get('interface')), address=str(address),
                           connection=str(lan.get('connection', '')),
                           ports=[interface(p) for p in lan.get('ports', [])],
                           dhcp_start=ipv4(lan.get('dhcp_start')), dhcp_end=ipv4(lan.get('dhcp_end')),
                           lease_hours=integer(lan.get('lease_hours', 12), 1, 720), reservations=[]),
                  uplinks=[], routes=[], masquerade=[], forwards=[], host_dns=[])
    for key in ('dhcp_start', 'dhcp_end'):
        ip = ipaddress.ip_address(result['lan'][key])
        if ip not in address.network or ip in (address.ip, address.network.network_address, address.network.broadcast_address):
            raise ValueError('DHCP 地址池必须位于 LAN 内，不能包含网关')
    if int(ipaddress.ip_address(result['lan']['dhcp_start'])) > int(ipaddress.ip_address(result['lan']['dhcp_end'])):
        raise ValueError('DHCP 起始地址不能大于结束地址')
    if ipaddress.ip_address(result['lan']['dhcp_start']) <= address.ip <= ipaddress.ip_address(result['lan']['dhcp_end']):
        raise ValueError('DHCP 地址池不能包含 LAN 网关')
    for key in ('reservations',):
        for r in lan.get(key, []):
            if not re.fullmatch(r'(?:[0-9a-fA-F]{2}:){5}[0-9a-fA-F]{2}', str(r.get('mac'))):
                raise ValueError('固定租约 MAC 地址无效')
            ip = ipv4(r.get('ip'))
            if ipaddress.ip_address(ip) not in address.network or ip in (str(address.ip), str(address.network.network_address), str(address.network.broadcast_address)):
                raise ValueError('固定租约地址必须为 LAN 内可用地址')
            name = str(r.get('name', '')).strip()
            if name and not re.fullmatch(r'[a-zA-Z0-9][a-zA-Z0-9.-]{0,62}', name):
                raise ValueError('租约主机名只能包含字母、数字、点和横线')
            result['lan']['reservations'].append(dict(mac=r['mac'].lower(), ip=ip, name=name))
    reservations = result['lan']['reservations']
    if len({r['ip'] for r in reservations}) != len(reservations) or len({r['mac'] for r in reservations}) != len(reservations):
        raise ValueError('固定租约的地址和 MAC 不能重复')
    for r in raw.get('uplinks', []):
        connection = str(r.get('connection', ''))
        if not re.fullmatch(r'[0-9a-fA-F-]{36}', connection):
            raise ValueError('出口连接 UUID 无效')
        result['uplinks'].append(dict(name=str(r.get('name', ''))[:80], interface=interface(r.get('interface')),
                                      connection=connection, default=enabled(r, 'default', False)))
    if not result['uplinks'] or sum(u['default'] for u in result['uplinks']) != 1:
        raise ValueError('必须指定一个主默认出口')
    allowed = {r['interface'] for r in result['uplinks']}
    for key in ('routes', 'masquerade', 'forwards'):
        entries = raw.get(key, [])
        if not isinstance(entries, list) or len(entries) > 128:
            raise ValueError('每类最多 128 个条目')
        for r in entries:
            nic = interface(r.get('interface'))
            if nic not in allowed:
                raise ValueError('请选择已管理的出口网卡')
            row = dict(id=str(r.get('id', ''))[:64], interface=nic, enabled=enabled(r))
            if key == 'routes':
                row.update(destination=network(r.get('destination')), gateway=ipv4(r.get('gateway')),
                           metric=integer(r.get('metric', 500), 1, 65535), bypass=enabled(r, 'bypass'))
            elif key == 'masquerade':
                row.update(source=network(r.get('source')), destination=network(r.get('destination', '0.0.0.0/0'), True))
                if not ipaddress.ip_network(row['source']).subnet_of(address.network):
                    raise ValueError('NAT 源地址须位于 LAN 网段')
            else:
                protocol = r.get('protocol', 'tcp')
                if protocol not in ('tcp', 'udp', 'both'):
                    raise ValueError('端口转发协议无效')
                target = ipv4(r.get('target_ip'))
                if ipaddress.ip_address(target) not in address.network:
                    raise ValueError('端口转发目标须位于 LAN')
                row.update(protocol=protocol, port=integer(r.get('port'), 1, 65535), target_ip=target,
                           target_port=integer(r.get('target_port'), 1, 65535))
            result[key].append(row)
    result['host_dns'] = list(dict.fromkeys(ipv4(x) for x in raw.get('host_dns', [])))
    if not result['host_dns']:
        raise ValueError('至少需要一个主机真实 DNS 服务器')
    if len(result['lan']['reservations']) > 128:
        raise ValueError('固定租约最多 128 项')
    return result


def render_dhcp(config):
    c = clean_config(config)
    l = c['lan']
    a = ipaddress.ip_interface(l['address'])
    lines = ['# Managed by mihomo-console. Edit in the router page.', 'interface=' + l['interface'],
             'bind-interfaces', 'no-resolv', 'domain-needed', 'bogus-priv', 'log-dhcp',
             f"dhcp-range={l['dhcp_start']},{l['dhcp_end']},{a.netmask},{l['lease_hours']}h",
             f'dhcp-option=option:router,{a.ip}', f'dhcp-option=option:dns-server,{a.ip}']
    for r in l['reservations']:
        lines.append(f"dhcp-host={r['mac']},{r['ip']}" + (',' + r['name'] if r['name'] else ''))
    return '\n'.join(lines) + '\n'


def render_dns(campus):
    lines = ['# Managed by mihomo-console. Domain policy is edited in the campus page.']
    for r in campus['dns']:
        if r['enabled']:
            for server in r.get('server', '').split():
                lines.append(f"server=/{r['domain'].removeprefix('+.')}/{ipv4(server)}")
    lines.append('server=/#/127.0.0.1#1053')
    return '\n'.join(lines) + '\n'


def render_nft(config):
    c = clean_config(config)
    q = json.dumps
    lines = ['table ip mihomo_console {', 'chain prerouting {',
             'type nat hook prerouting priority -101; policy accept;']
    for r in c['forwards']:
        if r['enabled']:
            for protocol in (['tcp', 'udp'] if r['protocol'] == 'both' else [r['protocol']]):
                lines.append(f"iifname {q(r['interface'])} {protocol} dport {r['port']} counter dnat to {r['target_ip']}:{r['target_port']}")
    lines += ['}', 'chain postrouting {', 'type nat hook postrouting priority 101; policy accept;']
    for r in c['masquerade']:
        if r['enabled']:
            dest = '' if r['destination'] == '0.0.0.0/0' else 'ip daddr ' + r['destination'] + ' '
            lines.append(f"ip saddr {r['source']} {dest}oifname {q(r['interface'])} counter masquerade")
    return '\n'.join(lines + ['}', '}']) + '\n'
