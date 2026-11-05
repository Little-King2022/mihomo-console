"""Manage only explicitly imported router resources; never flush global rules."""
from contextlib import contextmanager
from datetime import datetime, timezone
import hashlib
import bisect
import ipaddress
import json
import os
from pathlib import Path
import subprocess
import tempfile
import yaml

from . import db
from .campus_config import load_config
from .router_config import clean_config, render_dhcp, render_dns, render_nft

TARGETS = {
    'dhcp': '/etc/dnsmasq.d/20-router-lan.conf',
    'dns': '/etc/dnsmasq.d/90-mihomo-dns.conf',
    'nft': '/etc/mihomo-console/router.nft',
    'resolver': '/etc/resolvconf/resolv.conf.d/base',
    'sysctl': '/etc/sysctl.d/99-router.conf',
}


def command(args, check=True, **kwargs):
    result = subprocess.run(args, capture_output=True, text=True, timeout=25, **kwargs)
    if check and result.returncode:
        # Never return raw command output: upstream config may contain credentials.
        raise RuntimeError(f'{args[0]} 应用失败，已保留备份')
    return result


def configured():
    raw = db.get_setting('router_config')
    return clean_config(json.loads(raw)) if raw else None


def replace(path, text, mode=0o600):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.NamedTemporaryFile(dir=path.parent, prefix='.' + path.name, delete=False) as f:
        staged = Path(f.name)
        f.write(text.encode())
        os.fchmod(f.fileno(), mode)
    os.replace(staged, path)


def nft_transaction(text):
    exists = command(['nft', 'list', 'table', 'ip', 'mihomo_console'], check=False).returncode == 0
    return ('delete table ip mihomo_console\n' if exists else '') + text


@contextmanager
def system_transaction(profile, campus):
    """Validate first, snapshot, then roll back owned files and routes on failure."""
    if not profile:
        yield
        return
    if os.environ.get('ROUTER_MANAGEMENT') != '1':
        raise RuntimeError('路由器管理尚未在本机启用')
    profile = clean_config(profile)
    generated = dict(dhcp=render_dhcp(profile), dns=render_dns(campus), nft=render_nft(profile),
                     resolver=''.join('nameserver ' + x + '\n' for x in profile['host_dns']),
                     sysctl='# Managed by mihomo-console\nnet.ipv4.ip_forward = 1\n')
    fingerprint = hashlib.sha256(json.dumps([profile, campus], sort_keys=True).encode()).hexdigest()
    marker = db.data_dir() / 'router-applied.json'
    if marker.exists() and json.loads(marker.read_text()).get('sha256') == fingerprint and all(
        Path(TARGETS[k]).exists() and Path(TARGETS[k]).read_text() == text for k, text in generated.items()
    ) and command(['nft', 'list', 'table', 'ip', 'mihomo_console'], check=False).returncode == 0:
        yield
        return
    backup = db.data_dir() / 'network-backups' / datetime.now().strftime('%Y%m%d-%H%M%S-%f')
    backup.mkdir(parents=True, mode=0o700)
    old_files = {str(p): p.read_text() if p.exists() else None for p in map(Path, TARGETS.values())}
    old_nft = command(['nft', 'list', 'table', 'ip', 'mihomo_console'], check=False)
    old_routes = {}
    for uplink in profile['uplinks']:
        old_routes[uplink['connection']] = command(['nmcli', '-g', 'ipv4.routes', 'connection', 'show', 'uuid', uplink['connection']]).stdout.strip()
    (backup / 'state.json').write_text(json.dumps(dict(files=old_files, nft=old_nft.stdout, routes=old_routes), ensure_ascii=False, indent=2))
    (backup / 'state.json').chmod(0o600)
    combined = backup / 'dnsmasq.conf'
    combined.write_text(generated['dhcp'] + generated['dns'])
    command(['dnsmasq', '--test', '--conf-file=' + str(combined)])
    candidate = backup / 'router.nft'
    candidate.write_text(nft_transaction(generated['nft']))
    command(['nft', '-c', '-f', str(candidate)])
    changed_routes = []
    applied_nft = False
    dns_changed = any(old_files[TARGETS[k]] != generated[k] for k in ('dhcp', 'dns'))
    resolver_changed = old_files[TARGETS['resolver']] != generated['resolver']
    try:
        # Only the non-default campus uplink's static routes are managed here.
        # Bridge membership, WAN DHCP and the management/default route stay live.
        for uplink in profile['uplinks']:
            if uplink['default']:
                continue
            desired = ', '.join(f"{r['destination']} {r['gateway']} {r['metric']}" for r in profile['routes']
                                if r['enabled'] and r['interface'] == uplink['interface'])
            if desired != old_routes[uplink['connection']]:
                changed_routes.append(uplink)
                command(['nmcli', 'connection', 'modify', 'uuid', uplink['connection'], 'ipv4.routes', desired])
                command(['nmcli', 'device', 'reapply', uplink['interface']])
        for key, text in generated.items():
            if old_files[TARGETS[key]] != text:
                replace(TARGETS[key], text, 0o644)
        command(['sysctl', '-w', 'net.ipv4.ip_forward=1'])
        command(['nft', '-f', str(candidate)])
        applied_nft = True
        if dns_changed:
            command(['dnsmasq', '--test'])
            command(['systemctl', 'restart', 'dnsmasq'])
        if resolver_changed:
            command(['resolvconf', '-u'])
        yield
        replace(marker, json.dumps(dict(sha256=fingerprint, applied_at=datetime.now(timezone.utc).isoformat(), backup=str(backup))))
    except Exception:
        for name, old in old_files.items():
            if old is None:
                Path(name).unlink(missing_ok=True)
            else:
                replace(name, old, 0o644)
        if applied_nft:
            undo = backup / 'undo.nft'
            undo.write_text(nft_transaction(old_nft.stdout))
            command(['nft', '-f', str(undo)])
        for uplink in reversed(changed_routes):
            command(['nmcli', 'connection', 'modify', 'uuid', uplink['connection'], 'ipv4.routes', old_routes[uplink['connection']]])
            command(['nmcli', 'device', 'reapply', uplink['interface']])
        if dns_changed:
            command(['systemctl', 'restart', 'dnsmasq'])
        if resolver_changed:
            command(['resolvconf', '-u'])
        raise


def status():
    config = configured()
    if not config:
        return {'configured': False}
    leases = []
    path = Path('/var/lib/misc/dnsmasq.leases')
    if path.exists():
        for line in path.read_text().splitlines():
            parts = line.split()
            if len(parts) >= 4:
                leases.append(dict(expires=parts[0], mac=parts[1], ip=parts[2], name=parts[3]))
    from .campus import system_status
    marker = db.data_dir() / 'router-applied.json'
    return dict(configured=True, config=config, leases=leases, system=system_status(),
                applied=json.loads(marker.read_text()) if marker.exists() else None)


def change(payload):
    old = configured()
    if not old:
        raise ValueError('请先导入本机路由器配置')
    new = clean_config(payload)
    # Imported topology is fixed while online; addresses/bridge ports require
    # a separate maintenance operation rather than an accidental form edit.
    if new['uplinks'] != old['uplinks'] or any(new['lan'][k] != old['lan'][k] for k in ('interface', 'connection', 'address', 'ports')):
        raise ValueError('在线管理保留网卡、网桥和网关拓扑；请仅编辑路由、DHCP 与 NAT')
    if any(r['interface'] == next(u['interface'] for u in new['uplinks'] if u['default']) for r in new['routes']):
        raise ValueError('主出口默认路由由 DHCP 维护，静态路由请选择专用出口')
    db.set_setting('router_config', db.dump_json(new))
    return new


def verify_domestic_bypass(repair=True):
    """Check every source prefix; repair native nft batch truncation if present."""
    profile = configured()
    if not profile or not profile['domestic_bypass'] or db.get_setting('tun', '1') != '1':
        return
    from . import rulesets
    item = next(x for x in rulesets.load(db.get_setting('rulesets')) if x['name'] == 'cncidr')
    source = [ipaddress.ip_network(x) for x in rulesets.read_payload(item, db.mihomo_home() / 'config') if ':' not in x]
    raw = command(['nft', '-j', 'list', 'set', 'inet', 'mihomo', 'inet4_route_exclude_address_set']).stdout
    elements = next(x['set'].get('elem', []) for x in json.loads(raw)['nftables'] if 'set' in x)
    intervals = []
    for row in elements:
        if isinstance(row, str):
            n = ipaddress.ip_network(row); a, b = int(n.network_address), int(n.broadcast_address)
        elif 'prefix' in row:
            n = ipaddress.ip_network(f"{row['prefix']['addr']}/{row['prefix']['len']}")
            a, b = int(n.network_address), int(n.broadcast_address)
        elif 'range' in row:
            a, b = [int(ipaddress.ip_address(x)) for x in row['range']]
        else:
            raise RuntimeError('无法验证国内绕过地址集')
        intervals.append((a, b))
    intervals.sort()
    starts = [a for a, _ in intervals]
    missing = []
    for n in source:
        i = bisect.bisect_right(starts, int(n.network_address)) - 1
        if i < 0 or intervals[i][1] < int(n.broadcast_address):
            missing.append(n)
    if missing:
        if not repair:
            raise RuntimeError('国内绕过地址集不完整，拒绝确认配置已应用')
        merged = []
        for n in sorted(source, key=lambda n: int(n.network_address)):
            a, b = int(n.network_address), int(n.broadcast_address)
            if merged and a <= merged[-1][1] + 1:
                merged[-1][1] = max(merged[-1][1], b)
            else:
                merged.append([a, b])
        entries = [str(ipaddress.ip_address(a)) + ('-' + str(ipaddress.ip_address(b)) if a != b else '') for a, b in merged]
        text = 'flush set inet mihomo inet4_route_exclude_address_set\n'
        for offset in range(0, len(entries), 512):
            text += 'add element inet mihomo inet4_route_exclude_address_set { ' + ', '.join(entries[offset:offset + 512]) + ' }\n'
        command(['nft', '-c', '-f', '-'], input=text)
        command(['nft', '-f', '-'], input=text)
        return verify_domestic_bypass(repair=False)
    report = dict(source_prefixes=len(source), kernel_intervals=len(intervals), missing=0)
    replace(db.data_dir() / 'domestic-bypass.json', json.dumps(report))
    return report
