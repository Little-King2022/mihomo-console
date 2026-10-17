"""Passive Linux route inspection and explicitly requested campus probes."""
import ipaddress
import json
import re
import socket
import subprocess
import threading
import time
from datetime import datetime, timezone
from uuid import uuid4

import yaml

from . import db
from .campus_config import (clean_entry, domain_name, effective_excludes,
                            effective_filters, effective_policy, load_config)
from .mihomo import config_path

_probe_lock = threading.Lock()


def config():
    return load_config(db.get_setting("campus_config"))


def change(kind, payload=None, entry_id=None, delete=False):
    # Called while holding service.config_change's process lock and transaction.
    if kind not in ("networks", "dns"):
        raise ValueError("未知校园网配置类型")
    data = config()
    current = next((e for e in data[kind] if e["id"] == entry_id), None)
    if entry_id is not None and current is None:
        raise LookupError("校园网条目不存在")
    if delete:
        data[kind].remove(current)
        result = {"ok": True}
    else:
        result = clean_entry(kind, {**(current or {}), **payload}, data[kind], entry_id)
        result["id"] = entry_id or uuid4().hex
        if current:
            data[kind][data[kind].index(current)] = result
        else:
            data[kind].append(result)
    db.set_setting("campus_config", db.dump_json(data))
    return result


def command(args, timeout=3):
    try:
        result = subprocess.run(args, capture_output=True, text=True, timeout=timeout)
    except FileNotFoundError as exc:
        raise RuntimeError(f"系统未安装 {args[0]}，无法完成检查") from exc
    except subprocess.TimeoutExpired as exc:
        raise RuntimeError("检查超时，请确认校园网连接") from exc
    if result.returncode:
        # Do not expose arbitrary process output or command lines.
        raise RuntimeError(f"{args[0]} 检查失败，目标可能不可达或当前用户权限不足")
    return result.stdout


def route_to(address, port=None, protocol="tcp"):
    args = ["ip", "-j", "-4", "route", "get", address]
    if port:
        args += ["ipproto", protocol, "dport", str(port)]
    rows = json.loads(command(args))
    if not rows or not rows[0].get("dev"):
        raise RuntimeError("没有可用的目标路由")
    row = rows[0]
    return {"interface": row["dev"], "gateway": row.get("gateway"),
            "source": row.get("prefsrc") or row.get("src"), "table": row.get("table", "main"),
            "type": row.get("type", "unicast")}


def system_status():
    result = {"routes": [], "interfaces": [], "dns_servers": [], "error": None}
    try:
        rows = json.loads(command(["ip", "-j", "-4", "route", "show", "table", "main"]))
        result["routes"] = [{k: row[k] for k in ("dst", "gateway", "dev", "prefsrc", "metric", "protocol", "flags") if k in row}
                            for row in rows]
        # ip -4 address omits link_type on some iproute2 releases. Read the
        # full link metadata and filter address families ourselves.
        links = json.loads(command(["ip", "-j", "address", "show"]))
        result["interfaces"] = [{"name": row["ifname"], "state": row.get("operstate"),
                                  "type": row.get("link_type"),
                                  "addresses": [a["local"] for a in row.get("addr_info", []) if a.get("family") == "inet"]}
                                 for row in links]
        defaults = [r for r in rows if r.get("dst") == "default"]
        if defaults:
            default = min(defaults, key=lambda r: r.get("metric", 0))
            result["default"] = default.get("dev")
            try:
                raw = command(["nmcli", "-g", "IP4.DNS", "device", "show", default["dev"]])
                result["dns_servers"] = re.findall(r"\b(?:\d{1,3}\.){3}\d{1,3}\b", raw)
            except RuntimeError:
                pass
    except (RuntimeError, ValueError, KeyError) as exc:
        result["error"] = str(exc) if isinstance(exc, RuntimeError) else "系统路由数据无法读取"
    return result


def status():
    data = config()
    matches = None
    try:
        rendered = yaml.safe_load(config_path().read_text())
        matches = ((rendered.get("tun", {}).get("route-exclude-address") or []) == effective_excludes(data)
                   and (rendered.get("dns", {}).get("nameserver-policy") or {}) == effective_policy(data)
                   and (rendered.get("dns", {}).get("fake-ip-filter") or []) == effective_filters(data))
    except (OSError, AttributeError, yaml.YAMLError):
        pass
    return {**data, "effective_excludes": effective_excludes(data), "config_matches": matches,
            "tun": db.get_setting("tun", "1") == "1", "system": system_status()}


def allowed_address(value, data):
    try:
        address = ipaddress.IPv4Address(str(value or ""))
    except ValueError as exc:
        raise ValueError("检测目标须为 IPv4 地址") from exc
    networks = effective_excludes(data)
    if not any(address in ipaddress.ip_network(n) for n in networks):
        raise ValueError("目标不在启用的校园网段或校园 DNS 中，请先添加网段")
    if address.is_loopback or address.is_multicast or address.is_unspecified or address.is_reserved or address.is_link_local:
        raise ValueError("目标不是可检测的校园网地址")
    return str(address)


def verified_route(address, data, port=None, protocol="tcp"):
    route = route_to(address, port, protocol)
    system = system_status()
    interface = next((i for i in system["interfaces"] if i["name"] == route["interface"]), {})
    # Ethernet/Wi-Fi only: never let a probe silently enter Mihomo, loopback,
    # a container bridge, WireGuard or another virtual tunnel.
    if (route["table"] not in ("main", 254) or route["type"] != "unicast"
            or interface.get("type") != "ether" or not route["interface"].startswith(("en", "eth", "wl"))
            or interface.get("state") != "UP" or route["source"] not in interface.get("addresses", [])):
        raise RuntimeError("目标未通过可用的校园物理网卡路由，已停止检测；请先检查路由")
    return route


def diagnose(payload):
    kind = payload.get("kind", "route")
    if kind not in ("route", "ping", "tcp", "dns"):
        raise ValueError("请选择路由、Ping、TCP 或 DNS 检查")
    data = config()
    policies = [d for d in data["dns"] if d["enabled"] and d.get("server")]
    default_dns = next(iter(policies), {})
    address = allowed_address(payload.get("target", default_dns.get("server")), data)
    port = None
    domain = None
    if kind == "tcp":
        port = payload.get("port", 443)
        if isinstance(port, bool) or not isinstance(port, int) or not 1 <= port <= 65535:
            raise ValueError("端口须为 1–65535 的整数")
    if kind == "dns":
        domain = domain_name(payload.get("domain", default_dns.get("domain", "").removeprefix("+.")))
        if domain.startswith("+."):
            raise ValueError("检测时请填写具体主机名")
        if not any(d["server"] == address and (domain == d["domain"].removeprefix("+.") or
                   (d["domain"].startswith("+.") and domain.endswith("." + d["domain"][2:]))) for d in policies):
            raise ValueError("请检测已配置的域名和对应校园 DNS 服务器")
        port = 53
    if not _probe_lock.acquire(blocking=False):
        raise ValueError("校园网检查正在进行，请稍后再试")
    try:
        route = verified_route(address, data, port, "udp" if kind == "dns" else "tcp")
        result = {"kind": kind, "target": address, "port": port, "domain": domain, "route": route,
                  "checked_at": datetime.now(timezone.utc).isoformat(timespec="seconds"), "ok": True}
        start = time.monotonic()
        try:
            if kind == "ping":
                output = command(["ping", "-n", "-I", route["source"], "-c", "1", "-W", "2", address], timeout=4)
                match = re.search(r"time[=<]([\d.]+)\s*ms", output)
                result["latency_ms"] = float(match[1]) if match else None
                result["message"] = "收到 ICMP 响应"
            elif kind == "tcp":
                with socket.create_connection((address, port), timeout=3, source_address=(route["source"], 0)):
                    pass
                result["message"] = "TCP 端口连接成功"
            elif kind == "dns":
                output = command(["dig", "-4", "-b", route["source"], "@" + address, domain, "A",
                                  "+time=2", "+tries=1", "+ignore", "+noall", "+comments", "+answer", "+stats"], timeout=4)
                status_match = re.search(r"status: ([A-Z]+)", output)
                answers = re.findall(r"^\S+\s+\d+\s+IN\s+A\s+([\d.]+)\s*$", output, re.M)
                server_match = re.search(r"SERVER: ([\d.]+)#53", output)
                if not status_match or not server_match or server_match[1] != address:
                    raise RuntimeError("未收到指定校园 DNS 的有效响应，请核对服务器与网络")
                result.update(dns_reachable=True, dns_status=status_match[1])
                if status_match[1] != "NOERROR":
                    detail = {"NXDOMAIN": "域名不存在", "REFUSED": "拒绝查询", "SERVFAIL": "解析服务失败"}.get(status_match[1], "查询未成功")
                    raise RuntimeError(f"校园 DNS 已响应，但返回 {status_match[1]}（{detail}）")
                if not answers:
                    raise RuntimeError("校园 DNS 已响应，但未返回 IPv4 A 记录")
                result["answers"] = answers
                result["message"] = "校园 DNS 解析成功"
            else:
                result["message"] = "目标通过校园物理网卡，源 IP 与网关见下方"
        except (RuntimeError, OSError) as exc:
            result.update(ok=False, message=str(exc) if isinstance(exc, RuntimeError) else "TCP 连接失败或超时，请核对目标端口")
        result.setdefault("latency_ms", round((time.monotonic() - start) * 1000, 1) if kind in ("tcp", "dns") else None)
        return result
    finally:
        _probe_lock.release()
