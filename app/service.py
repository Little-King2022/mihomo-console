import gzip
import fcntl
import logging
import os
import re
import yaml
import shutil
import subprocess
import threading
import time
from collections import deque
from contextlib import contextmanager
from urllib.parse import quote
from datetime import datetime, timedelta, timezone
from pathlib import Path

import httpx

from . import db
from .mihomo import binary_path, client, config_dir, config_path
from .render import render_config, prefix_for, provider_name, RULE_PROVIDERS

log = logging.getLogger("mihomo-console")
_apply_lock_import = threading.RLock()
_config_lock_depth = 0
_health_lock = threading.Lock()
_stats_lock = threading.Lock()
_stats_samples = deque(maxlen=90)
_stats_last = None
_stats_cache = None


def now_iso():
    return datetime.now(timezone.utc).astimezone().isoformat(timespec="seconds")


@contextmanager
def config_lock():
    global _config_lock_depth
    with _apply_lock_import:
        if _config_lock_depth:
            _config_lock_depth += 1
            try:
                yield
            finally:
                _config_lock_depth -= 1
        else:
            with (db.data_dir() / "config.lock").open("a") as handle:
                fcntl.flock(handle, fcntl.LOCK_EX)
                _config_lock_depth = 1
                try:
                    yield
                finally:
                    _config_lock_depth = 0
                    fcntl.flock(handle, fcntl.LOCK_UN)


def apply_config():
    with config_lock():
        subscriptions = db.list_subscriptions()
        groups = db.list_groups()
        cooled = [row["node_name"] for row in db.list_cooldowns() if row["until"] > now_iso()]
        settings = {
            "mode": db.get_setting("mode", "rule"),
            "tun": db.get_setting("tun", "1"),
            "mihomo_secret": db.get_setting("mihomo_secret", ""),
            "campus_config": db.get_setting("campus_config"),
        }
        text = render_config(subscriptions, groups, cooled, settings, db.list_rules())
        destination = config_path()
        destination.parent.mkdir(parents=True, exist_ok=True)
        (destination.parent / "providers").mkdir(parents=True, exist_ok=True)
        (destination.parent / "ruleset").mkdir(parents=True, exist_ok=True)
        temporary = destination.with_suffix(".yaml.tmp")
        temporary.write_text(text, encoding="utf-8")
        binary = binary_path()
        if binary.exists():
            check = subprocess.run(
                [str(binary), "-t", "-d", str(config_dir()), "-f", str(temporary)],
                capture_output=True,
                text=True,
            )
            if check.returncode != 0:
                detail = (check.stdout + "\n" + check.stderr).strip()
                temporary.unlink(missing_ok=True)
                raise RuntimeError(detail or "mihomo 配置校验失败")
        os.replace(temporary, destination)
        shutil.chown(destination, user="mihomo", group="mihomo")
        os.chmod(destination, 0o664)
        for folder in (destination.parent, destination.parent / "providers", destination.parent / "ruleset"):
            if folder.exists():
                shutil.chown(folder, user="mihomo", group="mihomo")
        reload_core()
        return {"path": str(destination), "bytes": destination.stat().st_size}


def core_active():
    active = subprocess.run(["systemctl", "is-active", "mihomo"], capture_output=True, text=True)
    return active.stdout.strip() == "active"


def reload_core():
    if core_active():
        subprocess.run(["systemctl", "reload", "mihomo"], check=False)


def set_mode(mode):
    if mode not in ("rule", "global", "direct"):
        raise ValueError("mode 必须是 rule、global 或 direct")
    db.set_setting("mode", mode)
    apply_config()
    if not core_active():
        return
    try:
        with client() as http:
            http.patch("/configs", json={"mode": mode})
            if mode == "global":
                default = next((g for g in db.list_groups() if int(g["is_default"]) and int(g["enabled"])), None)
                if default:
                    http.put("/proxies/GLOBAL", json={"name": default["name"]})
    except httpx.HTTPError as exc:
        log.warning("切换模式 API 失败: %s", exc)


def update_provider(name):
    if not core_active():
        raise RuntimeError("新的 mihomo 服务还没有切换到正式环境")
    with client() as http:
        response = http.put(f"/providers/proxies/{name}")
        response.raise_for_status()


def update_all_providers():
    errors = []
    for sub in db.list_subscriptions():
        if not int(sub["enabled"]):
            continue
        try:
            update_provider(f"sub{sub['id']}")
            db.update_subscription(sub["id"], {"last_update": now_iso(), "last_error": ""})
        except Exception as exc:
            errors.append(f"{sub['name']}: {exc}")
            db.update_subscription(sub["id"], {"last_error": str(exc)})
    return errors


def healthcheck_providers():
    if not core_active():
        raise RuntimeError("新的 mihomo 服务还没有切换到正式环境")
    with client() as http:
        response = http.get("/providers/proxies")
        response.raise_for_status()
        providers = response.json().get("providers") or {}
        for name, provider in providers.items():
            if name == "default" or provider.get("vehicleType") == "Compatible":
                continue
            http.get(f"/providers/proxies/{name}/healthcheck")
    return collect_health()


def collect_health():
    with _health_lock:
        return _collect_health()


def _collect_health():
    if not core_active():
        return {"total": 0, "alive": 0, "cooldown": 0, "reloaded": False, "skipped": True}
    with client() as http:
        response = http.get("/providers/proxies")
        response.raise_for_status()
        payload = response.json()
    providers = payload.get("providers") or {}
    threshold = int(db.get_setting("fail_threshold", "2"))
    cooldown_seconds = int(db.get_setting("cooldown_seconds", "600"))
    previous = {row["node_name"]: row for row in db.list_node_health()}
    active_until = {row["node_name"]: row["until"] for row in db.list_cooldowns()}
    health_rows = []
    cooled = []
    changed = False
    moment = datetime.now().astimezone()
    moment_text = moment.isoformat(timespec="seconds")
    enabled_providers = {f"sub{s['id']}" for s in db.list_subscriptions() if s["enabled"]}
    for provider_name, provider in providers.items():
        if provider_name not in enabled_providers:
            continue
        info = provider.get("subscriptionInfo") or {}
        if info:
            for sub in db.list_subscriptions():
                if f"sub{sub['id']}" == provider_name:
                    db.update_subscription(
                        sub["id"],
                        {"last_info": db.dump_json(info), "last_update": provider.get("updatedAt") or now_iso(), "last_error": ""},
                    )
        for proxy in provider.get("proxies") or []:
            name = proxy.get("name") or ""
            history = proxy.get("history") or []
            latest = history[-1] if history else {}
            delay = int(latest.get("delay") or 0)
            stamp = str(latest.get("time") or "")
            alive = delay > 0
            old = previous.get(name) or {}
            fails = int(old.get("fails") or 0)
            if alive:
                fails = 0
            elif stamp and stamp != (old.get("checked_at") or ""):
                fails += 1
            health_rows.append(
                {
                    "node_name": name,
                    "provider": provider_name,
                    "delay": delay,
                    "alive": 1 if alive else 0,
                    "fails": fails,
                    "checked_at": stamp or None,
                    "history": history,
                }
            )
            until = active_until.get(name)
            if alive:
                if until:
                    changed = True
                continue
            if not stamp:
                if until and until > moment_text:
                    cooled.append({"node_name": name, "until": until, "reason": "健康检查失败", "fails": fails})
                continue
            if fails >= threshold:
                fresh = not until or until < moment_text
                new_until = (moment + timedelta(seconds=cooldown_seconds)).isoformat(timespec="seconds") if fresh else until
                if fresh:
                    changed = True
                cooled.append({"node_name": name, "until": new_until, "reason": "健康检查失败", "fails": fails})
            elif until and until > moment_text:
                cooled.append({"node_name": name, "until": until, "reason": "健康检查失败", "fails": fails})
    for row in db.list_cooldowns():
        if row["until"] > moment.isoformat(timespec="seconds") and row["node_name"] not in {i["node_name"] for i in cooled}:
            still = next((h for h in health_rows if h["node_name"] == row["node_name"]), None)
            if still is None or not still["alive"]:
                cooled.append(row)
    current_names = {row["node_name"] for row in health_rows}
    cooled = [row for row in cooled if row["node_name"] in current_names]
    previous_names = {row["node_name"] for row in db.list_cooldowns()}
    new_names = {row["node_name"] for row in cooled}
    if previous_names != new_names:
        changed = True
    db.upsert_node_health(health_rows)
    db.replace_cooldowns(cooled)
    migrate_group_nodes(node_catalog(payload))
    if changed and health_rows:
        apply_config()
    alive = sum(1 for row in health_rows if row["alive"])
    return {"total": len(health_rows), "alive": alive, "cooldown": len(cooled), "reloaded": changed and bool(health_rows)}


def version():
    try:
        with client() as http:
            response = http.get("/version")
            response.raise_for_status()
            data = response.json()
            return data.get("version") or str(data)
    except Exception:
        if not binary_path().exists():
            return "未安装"
        result = subprocess.run([str(binary_path()), "-v"], capture_output=True, text=True)
        line = (result.stdout or result.stderr).splitlines()
        return line[0] if line else "未知"


def latest_release():
    with httpx.Client(timeout=30, follow_redirects=True) as http:
        response = http.get(
            "https://api.github.com/repos/MetaCubeX/mihomo/releases/latest",
            headers={"Accept": "application/vnd.github+json", "User-Agent": "mihomo-console"},
        )
        response.raise_for_status()
        return response.json()


def upgrade_kernel():
    release = latest_release()
    tag = release.get("tag_name") or ""
    asset = None
    for item in release.get("assets") or []:
        name = item.get("name") or ""
        if name == f"mihomo-linux-amd64-{tag}.gz":
            asset = item
            break
    if asset is None:
        raise RuntimeError(f"发布 {tag} 中没有 linux amd64 内核包")
    current = version()
    if tag.lstrip("v") and tag.lstrip("v") in current:
        return {"changed": False, "version": current, "latest": tag}
    destination = binary_path()
    destination.parent.mkdir(parents=True, exist_ok=True)
    archive = destination.with_suffix(".gz.download")
    with httpx.Client(timeout=120, follow_redirects=True) as http:
        downloaded = http.get(asset["browser_download_url"], headers={"User-Agent": "mihomo-console"})
        downloaded.raise_for_status()
        archive.write_bytes(downloaded.content)
    raw = gzip.decompress(archive.read_bytes())
    staged = destination.with_suffix(".new")
    staged.write_bytes(raw)
    os.chmod(staged, 0o755)
    check = subprocess.run([str(staged), "-v"], capture_output=True, text=True)
    if check.returncode != 0:
        raise RuntimeError((check.stdout + check.stderr).strip() or "新内核无法运行")
    subprocess.run(["systemctl", "stop", "mihomo"], check=False)
    if destination.exists():
        shutil.copy2(destination, destination.with_name("mihomo.bak"))
    os.replace(staged, destination)
    os.chmod(destination, 0o755)
    shutil.chown(destination, user="root", group="root")
    archive.unlink(missing_ok=True)
    start = subprocess.run(["systemctl", "start", "mihomo"], capture_output=True, text=True)
    if start.returncode != 0:
        raise RuntimeError(start.stderr.strip() or "内核已替换，但服务启动失败")
    return {"changed": True, "version": tag, "previous": current}


@contextmanager
def config_change():
    # Serialize edits with health-triggered renders; invalid edits roll back SQLite.
    with config_lock(), db.transaction():
        yield
        apply_config()


def group_status():
    with client() as http:
        response = http.get("/proxies", timeout=4)
        response.raise_for_status()
        proxies = response.json().get("proxies", {})
    return {g["name"]: {"now": proxies.get(g["name"], {}).get("now"),
                         "all": proxies.get(g["name"], {}).get("all", [])}
            for g in db.list_groups()}


def select_node(group, name):
    if group["strategy"] != "select" or not group["enabled"]:
        raise ValueError("请先将代理组设为启用的手动选择策略")
    available = group_status().get(group["name"], {}).get("all", [])
    if name not in available:
        raise ValueError("节点不在此代理组中")
    with client() as http:
        response = http.put("/proxies/" + quote(group["name"], safe=""), json={"name": name})
        response.raise_for_status()


def process_memory():
    try:
        result = subprocess.run(["systemctl", "show", "mihomo", "--property=MainPID", "--value"],
                                capture_output=True, text=True, timeout=2)
        pid = int(result.stdout.strip())
        if pid > 0:
            for line in Path(f"/proc/{pid}/status").read_text().splitlines():
                if line.startswith("VmRSS:"):
                    return int(line.split()[1]) * 1024
    except (OSError, ValueError, subprocess.TimeoutExpired):
        pass
    return None


def telemetry():
    global _stats_last, _stats_cache
    with _stats_lock:
        moment = time.monotonic()
        if _stats_cache and _stats_last and moment - _stats_last[0] < 1:
            return _stats_cache
        try:
            with client() as http:
                response = http.get("/connections", timeout=4)
                response.raise_for_status()
                data = response.json()
            up, down = int(data.get("uploadTotal") or 0), int(data.get("downloadTotal") or 0)
            upload = download = None
            if _stats_last:
                elapsed = max(moment - _stats_last[0], .001)
                # A core restart resets totals; never show negative speed.
                if up >= _stats_last[1] and down >= _stats_last[2]:
                    upload = round((up - _stats_last[1]) / elapsed)
                    download = round((down - _stats_last[2]) / elapsed)
            _stats_last = (moment, up, down)
            sample = {"time": now_iso(), "upload": upload, "download": download}
            _stats_samples.append(sample)
            connections = data.get("connections") or []
            recent = sorted(connections, key=lambda c: c.get("start", ""), reverse=True)[:12]
            core_memory = data.get("memory")
            memory = core_memory or process_memory()
            _stats_cache = {"online": True, **sample, "upload_total": up, "download_total": down,
                            "memory": memory, "memory_source": "core" if core_memory else "rss", "connections": len(connections),
                            "history": list(_stats_samples), "recent": [
                                {"host": c.get("metadata", {}).get("host") or c.get("metadata", {}).get("destinationIP", ""),
                                 "network": c.get("metadata", {}).get("network", ""),
                                 "chains": c.get("chains", []), "rule": c.get("rule", ""),
                                 "upload": c.get("upload", 0), "download": c.get("download", 0)} for c in recent]}
            return _stats_cache
        except (httpx.HTTPError, ValueError):
            _stats_last = None
            _stats_cache = None
            return {"online": False, "error": "内核暂时不可达，正在重试", "history": list(_stats_samples)}


def proxy_access():
    """Expose only client-facing listener details, never controller credentials."""
    try:
        with client() as http:
            response = http.get("/configs", timeout=4)
            response.raise_for_status()
            config = response.json()
        return {"available": True, "mixed_port": int(config.get("mixed-port") or 0),
                "http_port": int(config.get("port") or 0),
                "socks_port": int(config.get("socks-port") or 0),
                "allow_lan": bool(config.get("allow-lan")),
                "bind_address": str(config.get("bind-address") or "127.0.0.1")}
    except (httpx.HTTPError, ValueError, TypeError):
        return {"available": False}


def node_catalog(payload=None):
    subscriptions = [s for s in db.list_subscriptions() if s["enabled"]]
    if payload is None:
        with client() as http:
            response = http.get("/providers/proxies", timeout=5)
            response.raise_for_status()
            payload = response.json()
    providers = payload.get("providers") or {}
    health = {n["node_name"]: n for n in db.list_node_health()}
    holds = {n["node_name"]: n["until"] for n in db.list_cooldowns()}
    items = []
    for sub in subscriptions:
        provider = provider_name(sub)
        prefix = prefix_for(sub, len(subscriptions))
        for proxy in providers.get(provider, {}).get("proxies") or []:
            name = proxy.get("name") or ""
            if not name:
                continue
            raw = name[len(prefix):] if prefix and name.startswith(prefix) else name
            old = health.get(name, {})
            samples = {h["time"]: h for h in old.get("history", []) if h.get("time")}
            for h in proxy.get("history") or []:
                if h.get("time"):
                    samples[h["time"]] = {"time": h["time"], "delay": int(h.get("delay") or 0)}
            history = sorted(samples.values(), key=lambda h: h["time"])[-24:]
            latest = history[-1] if history else {}
            delay = int(latest.get("delay") or 0)
            items.append({"provider": provider, "name": raw, "node_name": name,
                          "subscription": sub["name"], "type": proxy.get("type", ""),
                          "history": history, "delay": delay, "alive": delay > 0,
                          "checked_at": latest.get("time"), "cooldown_until": holds.get(name)})
    return items


def legacy_group_selection(group, catalog):
    include = re.compile(group.get("node_filter") or ".*")
    exclude = re.compile(group["exclude_filter"]) if group.get("exclude_filter") else None
    return [{"provider": n["provider"], "name": n["name"]} for n in catalog
            if include.search(n["node_name"]) and (not exclude or not exclude.search(n["node_name"]))]


def migrate_group_nodes(catalog=None):
    if not any(g.get("selected_nodes") is None for g in db.list_groups()):
        return
    catalog = node_catalog() if catalog is None else catalog
    if not catalog:
        return
    # Preserve the old membership, including nodes temporarily held by health checks.
    with config_change():
        for group in db.list_groups():
            if group.get("selected_nodes") is None:
                selected = legacy_group_selection(group, catalog)
                db.update_group(group["id"], {"selected_nodes": selected, "node_filter": "", "exclude_filter": ""})


def group_routing_details():
    sets = {}
    for rule in db.list_rules():
        for name in (rule.get("rule_sets") or "").split(","):
            if name not in RULE_PROVIDERS or name in sets:
                continue
            try:
                payload = yaml.safe_load((config_dir() / "ruleset" / f"{name}.yaml").read_text())
                sets[name] = payload.get("payload", [])
            except (OSError, ValueError, AttributeError, yaml.YAMLError):
                sets[name] = None
    grouped = {}
    for rule in db.list_rules():
        domains = {}
        def add(raw, source):
            raw = str(raw).strip()
            if not raw or raw.startswith("#"):
                return
            if "," in raw:
                fields = raw.split(",")
                if fields[0].upper() not in ("DOMAIN", "DOMAIN-SUFFIX") or len(fields) < 2:
                    return
                kind, value = fields[0].upper(), fields[1]
            else:
                kind, value = "DOMAIN-SUFFIX", raw
            value = value.removeprefix("+.").removeprefix("*.").lstrip(".")
            if value:
                domains.setdefault((kind, value), {"type": kind, "value": value, "source": source})
        for raw in (rule.get("domains") or "").splitlines():
            add(raw, "手动域名")
        pending = []
        for name in filter(None, (rule.get("rule_sets") or "").split(",")):
            if sets.get(name) is None:
                pending.append(name)
            else:
                for raw in sets[name]:
                    add(raw, name)
        grouped.setdefault(rule["group_id"], []).append({"id": rule["id"], "name": rule["name"],
                        "enabled": bool(rule["enabled"]), "priority": rule["priority"],
                        "domains": list(domains.values()), "pending_sets": pending})
    return grouped
