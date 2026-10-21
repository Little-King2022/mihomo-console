import json
import re
from urllib.parse import urlparse

from .campus_config import effective_excludes, effective_filters, effective_policy, load_config

RULE_PROVIDERS = {
    "reject": "https://testingcf.jsdelivr.net/gh/Loyalsoldier/clash-rules@release/reject.txt",
    "private": "https://testingcf.jsdelivr.net/gh/Loyalsoldier/clash-rules@release/private.txt",
    "direct": "https://testingcf.jsdelivr.net/gh/Loyalsoldier/clash-rules@release/direct.txt",
    "proxy": "https://testingcf.jsdelivr.net/gh/Loyalsoldier/clash-rules@release/proxy.txt",
    "lancidr": "https://testingcf.jsdelivr.net/gh/Loyalsoldier/clash-rules@release/lancidr.txt",
    "cncidr": "https://testingcf.jsdelivr.net/gh/Loyalsoldier/clash-rules@release/cncidr.txt",
    "openai": "https://testingcf.jsdelivr.net/gh/blackmatrix7/ios_rule_script@master/rule/Clash/OpenAI/OpenAI.yaml",
    "claude": "https://testingcf.jsdelivr.net/gh/blackmatrix7/ios_rule_script@master/rule/Clash/Claude/Claude.yaml",
    "gemini": "https://testingcf.jsdelivr.net/gh/blackmatrix7/ios_rule_script@master/rule/Clash/Gemini/Gemini.yaml",
}

BASE_RULE_SETS = ("reject", "private", "direct", "proxy", "lancidr", "cncidr")


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
        if item.upper().startswith("DOMAIN,"):
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
        *(f"    {q(domain)}: {server}" for domain, server in effective_policy(campus).items()),
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
            "    health-check:",
            "      enable: true",
            "      url: http://www.gstatic.com/generate_204",
            "      interval: 60",
            "      timeout: 5000",
            "      lazy: false",
            "      expected-status: 204",
            "    override:",
            f"      additional-prefix: {q(prefix_for(sub, len(enabled_subs)))}",
        ]
        if not prefix_for(sub, len(enabled_subs)):
            lines = lines[:-2]

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
                "  url: http://www.gstatic.com/generate_204",
                "  interval: 60",
                "  timeout: 5000",
                "  lazy: false",
                "  expected-status: 204",
                "  interrupt-exist-connections: false",
            ]
        # Provider nodes are appended after this list. Keep manual proxies empty
        # so fallback starts with real nodes; a trailing reject group is added later.
        lines.append("  proxies: [DIRECT]" if strategy == "select" and not enabled_subs and not explicit else "  proxies: []")

    used_sets = set(BASE_RULE_SETS)
    for group in matches:
        for item in (group.get("rule_sets") or "").split(","):
            item = item.strip()
            if item in RULE_PROVIDERS:
                used_sets.add(item)
    lines.append("rule-providers:")
    for name in sorted(used_sets):
        if name in ("lancidr", "cncidr"):
            behavior = "ipcidr"
        elif name in ("openai", "claude", "gemini"):
            behavior = "classical"
        else:
            behavior = "domain"
        lines += [
            f"  {name}:",
            "    type: http",
            "    proxy: DIRECT",
            f"    behavior: {behavior}",
            f"    url: {q(RULE_PROVIDERS[name])}",
            f"    path: ./ruleset/{name}.yaml",
            "    interval: 86400",
        ]

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
            if rule_set in RULE_PROVIDERS:
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
    return "\n".join(lines) + "\n"
