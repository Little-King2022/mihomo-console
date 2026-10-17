"""Small, on-demand HTTPS probes through each group's own loopback inbound."""

import asyncio
import ipaddress
import json
import time
from collections import OrderedDict
from datetime import datetime, timezone

import httpx
import yaml

from . import db, ippure, mihomo

IP_SOURCES = {
    "ipwhois": "https://ipwho.is/",
    "ipsb": "https://api.ip.sb/geoip",
    "ipapi": "https://api.ipapi.is/",
}
TARGETS = {
    "Google": "https://www.gstatic.com/generate_204",
    "Cloudflare": "https://www.cloudflare.com/cdn-cgi/trace",
    "GitHub": "https://github.com/robots.txt",
}
_cache = OrderedDict()
_inflight = {}
_slots = asyncio.Semaphore(4)


def listener_proxy(group):
    """Only accept the matching generated, loopback-only listener; fail closed."""
    config = yaml.safe_load(mihomo.config_path().read_text()) or {}
    for entry in config.get("listeners", []):
        if (entry.get("name") == f"console-diagnostics-{group['id']}"
                and entry.get("proxy") == group["name"]
                and entry.get("type") == "http"
                and entry.get("listen") == "127.0.0.1"
                and entry.get("port") == 20000 + group["id"]):
            return f"http://127.0.0.1:{entry['port']}"
    raise ValueError("该代理组的检测入口尚未就绪，请应用配置后重试")


def normalize_ip(source, data):
    if not isinstance(data, dict) or data.get("success") is False or data.get("error"):
        raise ValueError("IP 查询服务暂时不可用，请更换查询来源")
    try:
        address = str(ipaddress.ip_address(data.get("ip", "")))
    except ValueError as exc:
        raise ValueError("IP 查询服务未返回有效地址，请更换查询来源") from exc
    connection = data.get("connection") if isinstance(data.get("connection"), dict) else {}
    location = data.get("location") if isinstance(data.get("location"), dict) else {}
    company = data.get("company") or {}
    asn = data.get("asn")
    if isinstance(asn, dict):
        organization = asn.get("org") or asn.get("organization")
        asn = asn.get("asn")
    else:
        organization = data.get("asn_organization") or data.get("organization")
    if source == "ipwhois":
        asn = connection.get("asn")
        organization = connection.get("org") or connection.get("isp")
    if isinstance(company, dict):
        organization = organization or company.get("name")
    else:
        organization = organization or company
    return {
        "ip": address,
        "country": location.get("country") or data.get("country"),
        "city": location.get("city") or data.get("city"),
        "organization": organization,
        "asn": ("AS" + str(asn).removeprefix("AS")) if asn else None,
    }


async def current_node(name):
    async with await mihomo.async_client() as core:
        response = await core.get("/proxies", timeout=4)
        response.raise_for_status()
    group = response.json().get("proxies", {}).get(name)
    if not group:
        raise ValueError("内核中未找到该代理组，请应用配置后重试")
    node = group.get("now")
    if not node or node in ("DIRECT", "REJECT", "COMPATIBLE"):
        raise ValueError("该代理组当前没有可用的代理节点")
    return node


async def fetch(proxy, url, *, json_body=False):
    # No environment proxy, redirects or direct fallback: every request must
    # enter the explicitly bound group. Each probe uses a fresh connection.
    async with httpx.AsyncClient(proxy=proxy, trust_env=False, follow_redirects=False,
                                 timeout=httpx.Timeout(8, connect=5),
                                 headers={"User-Agent": "MihomoConsole/1.0", "Cache-Control": "no-cache"}) as client:
        async with client.stream("GET", url) as response:
            response.raise_for_status()
            if response.is_redirect:
                raise ValueError("检测地址返回重定向")
            if json_body:
                body = bytearray()
                async for part in response.aiter_bytes():
                    body.extend(part)
                    if len(body) > 65536:
                        raise ValueError("IP 查询服务返回内容异常")
                return json.loads(body)


async def latency_probe(proxy, name, url):
    start = time.monotonic()
    try:
        await asyncio.wait_for(fetch(proxy, url), timeout=10)
        return {"name": name, "delay": max(1, round((time.monotonic() - start) * 1000)), "error": None}
    except (httpx.HTTPError, ValueError, TimeoutError):
        return {"name": name, "delay": None, "error": "超时或连接失败"}


async def measure(group, node, proxy, kind, source):
    async with _slots:
        result = {"group_id": group["id"], "group_name": group["name"], "node": node,
                  "source": source if kind == "ip" else None}
        if kind == "purity":
            try:
                result["report"] = await asyncio.wait_for(ippure.query(proxy, fetch), timeout=35)
            except httpx.HTTPStatusError as exc:
                if exc.response.status_code == 429:
                    result["error"] = "IPPure 查询次数受限，请稍后手动重试"
                elif exc.response.status_code == 403:
                    result["error"] = "IPPure 暂时拒绝此出口的查询，请更换代理组或稍后重试"
                else:
                    result["error"] = "IPPure 网页接口暂时不可用，请稍后重试"
            except (httpx.HTTPError, TimeoutError):
                result["error"] = "IPPure 查询超时或连接失败，请确认节点可用后手动重试"
            except ValueError as exc:
                result["error"] = str(exc) if str(exc).startswith(("IPPure", "无法获取此代理组")) else "IPPure 返回内容异常，请稍后重试"
        elif kind == "ip":
            try:
                data = await asyncio.wait_for(fetch(proxy, IP_SOURCES[source], json_body=True), timeout=10)
                result["ip"] = normalize_ip(source, data)
            except (httpx.HTTPError, TimeoutError):
                result["error"] = "IP 查询超时或服务不可用，请重试或更换来源"
            except ValueError as exc:
                result["error"] = str(exc) if "IP 查询" in str(exc) else "IP 查询服务返回内容异常，请更换来源"
        else:
            result["targets"] = await asyncio.gather(*(latency_probe(proxy, name, url) for name, url in TARGETS.items()))
            values = [item["delay"] for item in result["targets"] if item["delay"] is not None]
            result["average"] = round(sum(values) / len(values)) if values else None
        try:
            result["node_changed"] = await current_node(group["name"]) != node
        except (httpx.HTTPError, ValueError):
            result["node_changed"] = True
        result["checked_at"] = datetime.now(timezone.utc).isoformat()
        return result


async def run(group_id, kind, source="ipsb", refresh=False):
    if kind not in ("ip", "latency", "purity") or source not in IP_SOURCES:
        raise ValueError("不支持的检测类型或 IP 查询来源")
    group = db.get_group(group_id)
    if not group or not int(group["enabled"]):
        raise ValueError("请选择已启用的代理组")
    proxy = await asyncio.to_thread(listener_proxy, group)
    node = await current_node(group["name"])
    key = (group_id, group["name"], node, kind, source if kind == "ip" else None)
    cached = _cache.get(key)
    # Explicit refresh (including a page reload) must always perform a new probe.
    # Only concurrent in-flight requests are merged; there is no click cooldown.
    if not refresh and cached and time.monotonic() - cached[0] < 60:
        return cached[1]
    if key not in _inflight:
        if len(_inflight) >= 8:
            raise ValueError("正在检测其他代理组，请稍后再试")
        _inflight[key] = asyncio.create_task(measure(group, node, proxy, kind, source))
    task = _inflight[key]
    try:
        result = await asyncio.shield(task)
        _cache[key] = (time.monotonic(), result)
        _cache.move_to_end(key)
        while len(_cache) > 128:
            _cache.popitem(last=False)
        return result
    finally:
        if task.done() and _inflight.get(key) is task:
            _inflight.pop(key, None)
