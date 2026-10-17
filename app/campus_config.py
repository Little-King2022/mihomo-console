"""Editable campus exceptions and migration from the original fixed values."""
import ipaddress
import json
import re

# Used only to migrate pre-v2 installations. Never merge these into saved v2 lists.
LEGACY_EXCLUDES = (
    "10.0.0.0/8", "127.0.0.0/8", "169.254.0.0/16", "172.16.0.0/12",
    "192.168.0.0/16", "224.0.0.0/4", "240.0.0.0/4",
)
LEGACY_FILTERS = ("+.lan", "+.local", "+.market.xiaomi.com", "auth-6441.wifi.com",
                  "+.littleking.site", "+.njau.edu.cn")


def load_config(raw=None):
    config = json.loads(raw) if isinstance(raw, str) else raw
    if config is None:
        config = {"networks": [], "dns": []}
    if not isinstance(config, dict) or any(not isinstance(config.get(k), list) for k in ("networks", "dns")):
        raise ValueError("校园网配置格式不正确")
    if config.get("schema_version") == 2:
        return config
    if "schema_version" in config:
        raise ValueError("不支持的校园网配置版本")
    networks = [dict(id=f"legacy-network-{i}", name=cidr, cidr=cidr, enabled=True, direct=False)
                for i, cidr in enumerate(LEGACY_EXCLUDES)]
    dns = [dict(id=f"legacy-dns-{i}", name=domain, domain=domain, enabled=True, direct=False,
                server="10.64.0.134" if domain == "auth-6441.wifi.com" else "")
           for i, domain in enumerate(LEGACY_FILTERS)]
    for kind, entries in (("networks", networks), ("dns", dns)):
        key = "cidr" if kind == "networks" else "domain"
        for entry in config[kind]:
            current = next((i for i, item in enumerate(entries) if item[key] == entry[key]), None)
            migrated = dict(entry, direct=entry.get("direct", True))
            if current is None:
                entries.append(migrated)
            else:
                entries[current] = migrated
    return {"schema_version": 2, "networks": networks, "dns": dns}


def domain_name(value):
    name = str(value or "").strip().lower().rstrip(".")
    suffix = name.startswith("+.")
    name = name[2:] if suffix else name
    try:
        name = name.encode("idna").decode("ascii")
    except UnicodeError as exc:
        raise ValueError("域名格式不正确") from exc
    if (len(name) > 253 or (not suffix and "." not in name) or any(
        not re.fullmatch(r"[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?", label) for label in name.split(".")
    )):
        raise ValueError("请输入域名，可用 +.example.edu.cn 包含子域名")
    return ("+." if suffix else "") + name


def clean_entry(kind, payload, entries, entry_id=None):
    if kind not in ("networks", "dns"):
        raise ValueError("未知校园网配置类型")
    name = str(payload.get("name") or "").strip()
    if not name or len(name) > 80 or any(ord(c) < 32 for c in name):
        raise ValueError("请填写 1–80 字的名称")
    enabled = payload.get("enabled", True)
    direct = payload.get("direct", True)
    if not isinstance(enabled, bool) or not isinstance(direct, bool):
        raise ValueError("启用状态与直连规则选项必须为布尔值")
    result = {"name": name, "enabled": enabled, "direct": direct}
    if kind == "networks":
        try:
            network = ipaddress.IPv4Network(str(payload.get("cidr") or ""), strict=False)
        except ValueError as exc:
            raise ValueError("请输入 IPv4 网段，例如 192.0.2.0/24 或单个 IP") from exc
        if network.prefixlen == 0 or network.overlaps(ipaddress.ip_network("198.18.0.0/15")):
            raise ValueError("不能添加默认路由或 Fake IP 网段")
        result["cidr"] = str(network)
        key = "cidr"
    else:
        domain = domain_name(payload.get("domain"))
        raw_server = str(payload.get("server") or "").strip()
        server = ""
        if raw_server:
            try:
                address = ipaddress.IPv4Address(raw_server)
            except ValueError as exc:
                raise ValueError("DNS 服务器须填写 IPv4 地址，留空使用默认解析器") from exc
            if address.is_loopback or address.is_unspecified or address.is_multicast or address.is_reserved or address.is_link_local or address in ipaddress.ip_network("198.18.0.0/15"):
                raise ValueError("DNS 服务器不能是回环、Fake IP 或保留地址")
            server = str(address)
        result.update(domain=domain, server=server)
        key = "domain"
    if any(e["id"] != entry_id and e[key] == result[key] for e in entries):
        raise ValueError("此网段或域名已存在，请编辑已有条目")
    if entry_id is None and len(entries) >= 128:
        raise ValueError("每类最多保存 128 个校园网条目")
    return result


def effective_excludes(config):
    networks = list(dict.fromkeys(n["cidr"] for n in config["networks"] if n["enabled"]))
    # DNS hosts need an exclusion only when no enabled network already covers them.
    for item in config["dns"]:
        if item["enabled"] and item.get("server"):
            address = ipaddress.ip_address(item["server"])
            if not any(address in ipaddress.ip_network(n) for n in networks):
                networks.append(str(address) + "/32")
    return networks


def effective_filters(config):
    return list(dict.fromkeys(d["domain"] for d in config["dns"] if d["enabled"]))


def effective_policy(config):
    return {d["domain"]: d["server"] for d in config["dns"] if d["enabled"] and d.get("server")}
