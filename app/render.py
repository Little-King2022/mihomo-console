import json
import ipaddress
import re
import yaml
from urllib.parse import urlparse

from .campus_config import effective_excludes, effective_filters, effective_policy, load_config
from . import rulesets

# Compatibility exports for consumers of the original public constants.
RULE_PROVIDERS = rulesets.DEFAULT_URLS
BASE_RULE_SETS = rulesets.BASE


def q(value):
    return json.dumps("" if value is None else str(value), ensure_ascii=False)


def provider_name(sub):
    return f"sub{sub['id']}"


def prefix_for(sub, enabled_count):
    # One subscription keeps the airport's original node names.
    # Extra subscriptions get a short id prefix so identical names do not collide.
    if enabled_count <= 1:
        return ""
    return f"s{sub['id']}-"


def selected_node_filter(nodes, subscriptions):
    """Compile stored identities to literal names; users never enter patterns."""
    enabled = {provider_name(sub): sub for sub in subscriptions}
    names = []
    for node in nodes:
        sub = enabled.get(node["provider"])
        if sub:
            names.append(prefix_for(sub, len(subscriptions)) + node["name"])
    return "^(?:%s)$" % "|".join(re.escape(name) for name in names) if names else "^$"


def domain_rules(group_name, domains_text):
    rules = []
    for raw in (domains_text or "").splitlines():
        item = raw.strip()
        if not item or item.startswith("#"):
            continue
        if item.upper().startswith(("DOMAIN,", "DOMAIN-SUFFIX,", "DOMAIN-KEYWORD,")):
            rules.append(f"- {item},{group_name}")
            continue
        if item.startswith("+."):
            item = item[2:]
        if item.startswith("*."):
            item = item[2:]
        rules.append(f"- DOMAIN-SUFFIX,{item},{group_name}")
    return rules


def cooldown_regex(names):
    cleaned = []
    for name in names:
        name = (name or "").strip()
        if name:
            cleaned.append(re.escape(name))
    if not cleaned:
        return ""
    return "^(?:%s)$" % "|".join(sorted(set(cleaned)))


def merge_exclude(base, extra):
    parts = [part for part in (base or "", extra or "") if part]
    if not parts:
        return ""
    if len(parts) == 1:
        return parts[0]
    return "(?:%s)" % "|".join(f"(?:{part})" for part in parts)


def subscription_hosts(subscriptions):
    hosts = []
    for sub in subscriptions:
        host = urlparse(sub["url"]).hostname
        if host and host not in hosts:
            hosts.append(host)
    return hosts


def render_config(subscriptions, groups, cooldown_names, settings, routing_rules=None):
    rule_providers = {x['name']: x for x in rulesets.load(settings.get('rulesets'))}
    campus = load_config(settings.get("campus_config"))
    campus_dns = [d for d in campus["dns"] if d["enabled"]]
    enabled_subs = [s for s in subscriptions if int(s["enabled"])]
    enabled_groups = [g for g in groups if int(g["enabled"])]
    if routing_rules is None:
        matches = [g for g in enabled_groups if not int(g.get("is_default") or 0)]
    else:
        names = {g["id"]: g["name"] for g in enabled_groups if "id" in g}
        matches = [dict(r, name=names[r["group_id"]]) for r in routing_rules
                   if int(r["enabled"]) and r["group_id"] in names]
    default_groups = [g for g in enabled_groups if int(g["is_default"])]
    default_name = default_groups[0]["name"] if default_groups else "DIRECT"
    mode = settings.get("mode") or "rule"
    if mode not in ("rule", "global", "direct"):
        mode = "rule"
    tun = str(settings.get("tun", "1")) not in ("0", "false", "False")
    secret = settings.get("mihomo_secret") or ""
    cooled = cooldown_regex(cooldown_names)

    lines = [
        f"mixed-port: 7890",
        "allow-lan: false",
        "bind-address: '*'",
        f"mode: {mode}",
        "log-level: info",
        "ipv6: false",
        "find-process-mode: 'off'",
        "unified-delay: true",
        "tcp-concurrent: true",
        "keep-alive-interval: 30",
        f"external-controller: '127.0.0.1:19090'",
        f"secret: {q(secret)}",
        "external-ui: uis/metacubexd",
        "external-ui-url: https://github.com/MetaCubeX/metacubexd/archive/refs/heads/gh-pages.zip",
        "profile:",
        "  store-selected: true",
        "  store-fake-ip: true",
        "geo-auto-update: true",
        "geo-update-interval: 7",
        "geox-url:",
        "  geoip: https://testingcf.jsdelivr.net/gh/MetaCubeX/meta-rules-dat@release/geoip.metadb",
        "  geosite: https://testingcf.jsdelivr.net/gh/MetaCubeX/meta-rules-dat@release/geosite.dat",
        "  mmdb: https://testingcf.jsdelivr.net/gh/MetaCubeX/meta-rules-dat@release/country.mmdb",
        "  asn: https://testingcf.jsdelivr.net/gh/MetaCubeX/meta-rules-dat@release/GeoLite2-ASN.mmdb",
        "sniffer:",
        "  enable: true",
        "  sniff:",
        "    HTTP:",
        "      ports: [80, 8080-8880]",
        "      override-destination: true",
        "    TLS:",
        "      ports: [443, 8443]",
        "    QUIC:",
        "      ports: [443, 8443]",
        "  skip-domain:",
        "    - Mijia Cloud",
        "    - '+.push.apple.com'",
        "dns:",
        "  enable: true",
        "  listen: 127.0.0.1:53",
        "  ipv6: false",
        "  enhanced-mode: fake-ip",
        "  fake-ip-range: 198.18.0.1/16",
        "  fake-ip-filter:" if effective_filters(campus) else "  fake-ip-filter: []",
        *(f"    - {q(domain)}" for domain in effective_filters(campus)),
        "  default-nameserver:",
        "    - tls://223.5.5.5",
        "    - tls://223.6.6.6",
        "  nameserver:",
        "    - https://doh.pub/dns-query",
        "    - https://dns.alidns.com/dns-query",
        "  nameserver-policy:" if effective_policy(campus) else "  nameserver-policy: {}",
        *(f"    {q(domain)}: {json.dumps(server) if isinstance(server, list) else server}" for domain, server in effective_policy(campus).items()),
        "tun:",
        f"  enable: {'true' if tun else 'false'}",
        "  stack: mixed",
        "  dns-hijack:",
        '    - "any:53"',
        '    - "tcp://any:53"',
        "  auto-route: true",
        "  auto-redirect: false",
        "  auto-detect-interface: true",
        "  strict-route: false",
        "  route-exclude-address:" if effective_excludes(campus) else "  route-exclude-address: []",
        *(f"    - {cidr}" for cidr in effective_excludes(campus)),
        "proxy-providers:",
    ]
    if not enabled_subs:
        lines += ["  console-empty:", "    type: inline", "    payload:", "      - name: console-no-nodes", "        type: reject"]
    for sub in enabled_subs:
        name = provider_name(sub)
        interval = max(int(sub.get("interval_hours") or 6), 1) * 3600
        lines += [
            f"  {name}:",
            "    type: http",
            f"    url: {q(sub['url'])}",
            f"    path: ./providers/{name}.yaml",
            f"    interval: {interval}",
            "    header:",
            "      User-Agent: [clash.meta]",
            f"    exclude-filter: {q('(?i)(剩余流量|重置剩余|套餐到期|官网地址|防失联|更新订阅|到期时间|剩余天数)')}",
            "    health-check:",
            "      enable: true",
            "      url: https://www.gstatic.com/generate_204",
            "      interval: 300",
            "      timeout: 5000",
            "      lazy: false",
            "      expected-status: 204",
            "    override:",
            f"      additional-prefix: {q(prefix_for(sub, len(enabled_subs)))}",
        ]
        if not prefix_for(sub, len(enabled_subs)):
            lines = lines[:-2]
        fetch_group = next((g for g in enabled_groups if g.get('id') == sub.get('fetch_group_id')), None)
        if sub.get('fetch_group_id') and not fetch_group:
            raise ValueError('订阅下载出口不可用，请先选择启用的代理组')
        if fetch_group:
            lines.append(f"    proxy: {q(fetch_group['name'])}")

    # Dedicated loopback inbounds let diagnostics use a group's real outbound
    # without temporarily changing its selection, mode, or the user's rules.
    listeners = [g for g in enabled_groups if 0 < int(g.get("id") or 0) <= 45535]
    if listeners:
        lines.append("listeners:")
        for group in listeners:
            lines += [
                f"- name: console-diagnostics-{group['id']}",
                "  type: http",
                "  listen: 127.0.0.1",
                f"  port: {20000 + int(group['id'])}",
                f"  proxy: {q(group['name'])}",
            ]

    lines.append("proxy-groups:")
    if not enabled_groups:
        default_name = "境外常规"
        enabled_groups = [{"name": default_name, "is_default": 1, "enabled": 1}]
    for group in enabled_groups:
        strategy = group.get("strategy") or "fallback"
        if strategy not in ("fallback", "url-test", "select"):
            strategy = "fallback"
        selected = group.get("selected_nodes")
        explicit = selected is not None
        # The core handles fallback/URL-test health without rebuilding groups.
        # Reloading on every cooldown change otherwise interrupts live streams.
        group_cooled = cooled if strategy == "select" else ""
        exclude = group_cooled if explicit else merge_exclude(group.get("exclude_filter") or "", group_cooled)
        lines += [f"- name: {q(group['name'])}", f"  type: {strategy}"]
        if enabled_subs:
            lines.append("  use:")
            providers = {node["provider"] for node in selected} if explicit else None
            for sub in enabled_subs:
                # Include the providers in their original order. A deny-all filter
                # protects an empty selection or a disabled source subscription.
                if not explicit or provider_name(sub) in providers:
                    lines.append(f"    - {provider_name(sub)}")
            if explicit and not any(provider_name(sub) in providers for sub in enabled_subs):
                # The kernel requires a non-empty use list even when a literal
                # deny-all filter makes the group empty.
                lines.append(f"    - {provider_name(enabled_subs[0])}")
        else:
            lines += ["  use:", "    - console-empty"]
        node_filter = selected_node_filter(selected, enabled_subs) if explicit else group.get("node_filter")
        if node_filter:
            lines.append(f"  filter: {q(node_filter)}")
        if exclude:
            lines.append(f"  exclude-filter: {q(exclude)}")
        if explicit:
            lines.append("  empty-fallback: REJECT")
        if strategy in ("fallback", "url-test"):
            lines += [
                "  url: https://www.gstatic.com/generate_204",
                "  interval: 300",
                "  timeout: 5000",
                "  lazy: false",
                "  expected-status: 204",
                "  interrupt-exist-connections: false",
            ]
        # Provider nodes are appended after this list. Keep manual proxies empty
        # so fallback starts with real nodes; a trailing reject group is added later.
        lines.append("  proxies: [DIRECT]" if strategy == "select" and not enabled_subs and not explicit else "  proxies: []")

    for match in matches:
        unknown = set(filter(None, (match.get('rule_sets') or '').split(','))) - rule_providers.keys()
        if unknown:
            raise ValueError('分流规则引用了不存在的规则集')
    if not set(BASE_RULE_SETS).issubset(rule_providers):
        raise ValueError('缺少基础分流规则集')
    lines.append('rule-providers:')
    rendered_providers = {name: rulesets.provider(item, settings.get('rule_provider_paths', {}).get(name))
                          for name, item in rule_providers.items()}
    for name, item in rule_providers.items():
        if item.get('excluded'):
            rendered_providers['__console_source_' + name] = rulesets.source_provider(item)
    provider_yaml = yaml.safe_dump(rendered_providers,
                                   allow_unicode=True, sort_keys=False)
    lines.extend('  ' + line for line in provider_yaml.rstrip().splitlines())

    lines.append("rules:")
    # Explicit proxy clients still enter the core. Keep their campus traffic
    # direct too; TUN clients bypass the core using the exclusions above.
    for network in campus["networks"]:
        if network["enabled"] and network.get("direct", True):
            lines.append(f"- IP-CIDR,{network['cidr']},DIRECT,no-resolve")
    for item in campus_dns:
        if not item.get("direct", True):
            continue
        lines.extend(domain_rules("DIRECT", item["domain"]) if item["domain"].startswith("+.")
                     else [f"- DOMAIN,{item['domain']},DIRECT"])
    for host in subscription_hosts(enabled_subs):
        lines.append(f"- DOMAIN,{host},DIRECT")
    lines += [
        "- RULE-SET,private,DIRECT",
        "- RULE-SET,reject,REJECT",
    ]
    for group in sorted(
        matches,
        key=lambda g: (int(g.get("priority") if g.get("priority") is not None else 100), int(g.get("id") or 0)),
    ):
        for rule_set in (group.get("rule_sets") or "").split(","):
            rule_set = rule_set.strip()
            if rule_set in rule_providers:
                lines.append(f"- RULE-SET,{rule_set},{group['name']}")
        lines.extend(domain_rules(group["name"], group.get("domains") or ""))
    lines += [
        f"- RULE-SET,proxy,{default_name}",
        "- RULE-SET,direct,DIRECT",
        "- RULE-SET,lancidr,DIRECT,no-resolve",
        "- RULE-SET,cncidr,DIRECT,no-resolve",
        "- GEOIP,LAN,DIRECT,no-resolve",
        "- GEOIP,CN,DIRECT,no-resolve",
        f"- MATCH,{default_name}",
    ]
    text = "\n".join(lines) + "\n"
    if settings.get("router_config"):
        return router_network(text, settings["router_config"], campus, matches, enabled_subs)
    return text


def router_network(text, raw, campus, matches, subscriptions):
    """Linux router profile: real domestic DNS plus kernel-level CIDR bypass."""
    from .router_config import clean_config
    profile = clean_config(json.loads(raw) if isinstance(raw, str) else raw)
    config = yaml.safe_load(text)
    config['dns']['listen'] = '127.0.0.1:1053'
    config['tun'].update(device='Mihomo', mtu=profile['mtu'], **{
        'auto-route': True, 'auto-redirect': True, 'strict-route': False,
        'iproute2-table-index': 2022, 'iproute2-rule-index': 9000,
    })
    excludes = config['tun']['route-exclude-address']
    excludes += [r['destination'] for r in profile['routes'] if r['enabled'] and r['bypass']]
    # The native nft encoder rejects intervals ending at the address-family
    # maximum. Limited broadcast is already local traffic.
    broadcast = ipaddress.ip_network('255.255.255.255/32')
    compiled = []
    for n in ipaddress.collapse_addresses(ipaddress.ip_network(x) for x in excludes):
        if broadcast.subnet_of(n):
            compiled.extend(str(x) for x in n.address_exclude(broadcast))
        else:
            compiled.append(str(n))
    config['tun']['route-exclude-address'] = compiled
    if profile['domestic_bypass']:
        # lancidr contains 198.18/15; excluding it would bypass fake-IP proxies.
        config['tun']['route-exclude-address-set'] = ['cncidr']
    filters = []
    for domain in effective_filters(campus):
        kind = 'DOMAIN-SUFFIX' if domain.startswith(('+.', '*.')) else 'DOMAIN'
        filters.append(f'{kind},{domain.removeprefix("+.").removeprefix("*.")},real-ip')
    filters += [f'DOMAIN,{host},real-ip' for host in subscription_hosts(subscriptions)]
    for group in sorted(matches, key=lambda g: int(g.get('priority', 100))):
        filters += [r.removeprefix('- ') for r in domain_rules('fake-ip', group.get('domains', ''))]
        filters += [f'RULE-SET,{s},fake-ip' for s in group.get('rule_sets', '').split(',') if s in config['rule-providers'] and config['rule-providers'][s]['behavior'] != 'ipcidr']
    filters += ['RULE-SET,reject,fake-ip', 'RULE-SET,private,real-ip']
    if profile['domestic_bypass']:
        filters += ['RULE-SET,direct,real-ip']
    filters += ['RULE-SET,proxy,fake-ip', 'MATCH,real-ip']
    config['dns'].update({'fake-ip-filter-mode': 'rule', 'fake-ip-filter': filters,
                          'default-nameserver': profile['host_dns'],
                          'proxy-server-nameserver': ['https://223.5.5.5/dns-query', 'https://119.29.29.29/dns-query']})
    config['sniffer']['force-dns-mapping'] = True
    default_interface = next(u['interface'] for u in profile['uplinks'] if u['default'])
    for provider in config['proxy-providers'].values():
        provider.setdefault('override', {})['interface-name'] = default_interface
    return yaml.safe_dump(config, allow_unicode=True, sort_keys=False)
