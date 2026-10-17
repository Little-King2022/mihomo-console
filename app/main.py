import asyncio
import hmac
import logging
import re
from contextlib import asynccontextmanager
from datetime import datetime, timedelta
from pathlib import Path
from urllib.parse import unquote_plus

import httpx
import yaml
from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import FileResponse, JSONResponse, Response
from fastapi.staticfiles import StaticFiles

from . import campus, db, diagnostics, service

log = logging.getLogger("mihomo-console")
STATIC = Path(__file__).resolve().parent / "static"
COOKIE = "mc_token"


class TokenAccessFilter(logging.Filter):
    """Keep Uvicorn access logs without recording link credentials."""

    def filter(self, record):
        if isinstance(record.args, tuple) and len(record.args) == 5:
            args = list(record.args)
            args[2] = re.sub(
                r'([?&])([^=&#\s]+)=([^&#\s]*)',
                lambda match: f'{match[1]}{match[2]}=[redacted]'
                if unquote_plus(match[2]) == 'token' else match[0],
                str(args[2]),
            )
            record.args = tuple(args)
        return True


logging.getLogger("uvicorn.access").addFilter(TokenAccessFilter())


def authorized(request: Request) -> bool:
    token = db.get_setting("web_token", "")
    header = request.headers.get("authorization", "")
    presented = ""
    if header.lower().startswith("bearer "):
        presented = header[7:].strip()
    presented = presented or request.cookies.get(COOKIE, "")
    if not token or not presented:
        return False
    return hmac.compare_digest(presented, token)


def require(request: Request):
    if not authorized(request):
        raise HTTPException(status_code=401, detail="未认证")


async def background():
    await asyncio.sleep(8)
    while True:
        interval = max(int(db.get_setting("health_interval", "30")), 15)
        try:
            await asyncio.to_thread(service.collect_health)
            if db.get_setting("auto_update", "1") == "1":
                await asyncio.to_thread(refresh_due_subscriptions)
        except Exception:
            log.exception("后台健康检查失败")
        await asyncio.sleep(interval)


def refresh_due_subscriptions():
    now = datetime.now().astimezone()
    for sub in db.list_subscriptions():
        if not int(sub["enabled"]):
            continue
        due = True
        if sub.get("last_update"):
            try:
                updated = datetime.fromisoformat(sub["last_update"])
                if updated.tzinfo is None:
                    updated = updated.astimezone()
                due = now - updated >= timedelta(hours=max(int(sub["interval_hours"]), 1))
            except ValueError:
                due = True
        if due:
            try:
                service.update_provider(f"sub{sub['id']}")
                db.update_subscription(sub["id"], {"last_update": service.now_iso(), "last_error": ""})
            except Exception as exc:
                db.update_subscription(sub["id"], {"last_error": str(exc)})


@asynccontextmanager
async def lifespan(_app):
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    db.init_db()
    try:
        await asyncio.to_thread(service.migrate_group_nodes)
    except (httpx.HTTPError, RuntimeError):
        log.warning("节点选择迁移将在内核就绪后重试")
    task = asyncio.create_task(background())
    yield
    task.cancel()


class ConsoleStaticFiles(StaticFiles):
    async def get_response(self, path, scope):
        response = await super().get_response(path, scope)
        # ES module imports have stable URLs. Revalidate the full module graph
        # after a deployment, rather than mixing cached and updated modules.
        response.headers["Cache-Control"] = "no-cache"
        return response


app = FastAPI(lifespan=lifespan)
app.mount("/static", ConsoleStaticFiles(directory=STATIC), name="static")


@app.get("/")
def index():
    return FileResponse(STATIC / "index.html", headers={"Cache-Control": "no-cache"})


@app.get("/api/session")
def session(request: Request):
    if not authorized(request):
        return Response(status_code=401)
    return Response(status_code=204)


@app.post("/api/login")
def login(payload: dict):
    token = str(payload.get("token") or "")
    expected = db.get_setting("web_token", "")
    if not expected or not hmac.compare_digest(token, expected):
        raise HTTPException(status_code=401, detail="令牌不正确")
    response = JSONResponse({"ok": True})
    response.set_cookie(COOKIE, token, httponly=True, samesite="lax", max_age=60 * 60 * 24 * 30, path="/")
    return response


@app.post("/api/logout")
def logout():
    response = JSONResponse({"ok": True})
    response.delete_cookie(COOKIE, path="/")
    return response


@app.get("/api/overview")
def overview(request: Request):
    require(request)
    subs = db.list_subscriptions()
    groups = db.list_groups()
    health = db.list_node_health()
    return {
        "mode": db.get_setting("mode", "rule"),
        "tun": db.get_setting("tun", "1") == "1",
        "auto_update": db.get_setting("auto_update", "1") == "1",
        "version": service.version(),
        "proxy_access": service.proxy_access(),
        "subscriptions": len(subs),
        "groups": len([g for g in groups if int(g["enabled"])]),
        "nodes": len(health),
        "alive": sum(1 for row in health if int(row["alive"])),
        "cooldown": len(db.list_cooldowns()),
        "subscription_info": [
            {"id": s["id"], "name": s["name"], "info": s["last_info"], "error": s["last_error"], "updated": s["last_update"]}
            for s in subs
        ],
    }


@app.get("/api/subscriptions")
def subscriptions(request: Request):
    require(request)
    return db.list_subscriptions()


@app.get("/api/campus")
def campus_status(request: Request):
    require(request)
    return campus.status()


def campus_mutation(kind, payload=None, entry_id=None, delete=False):
    try:
        with service.config_change():
            result = campus.change(kind, payload, entry_id, delete)
        return result
    except LookupError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except Exception as exc:
        # Core validation output can contain subscription details; never return it.
        raise HTTPException(status_code=400, detail="校园网配置应用失败，已保留原设置；请检查内核与配置") from exc


@app.post("/api/campus/diagnostics")
def campus_diagnostics(payload: dict, request: Request):
    require(request)
    try:
        return campus.diagnose(payload)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except (RuntimeError, OSError) as exc:
        raise HTTPException(status_code=502, detail=str(exc)) from exc


@app.post("/api/campus/{kind}")
def create_campus_entry(kind: str, payload: dict, request: Request):
    require(request)
    return campus_mutation(kind, payload)


@app.put("/api/campus/{kind}/{entry_id}")
def edit_campus_entry(kind: str, entry_id: str, payload: dict, request: Request):
    require(request)
    return campus_mutation(kind, payload, entry_id)


@app.delete("/api/campus/{kind}/{entry_id}")
def delete_campus_entry(kind: str, entry_id: str, request: Request):
    require(request)
    return campus_mutation(kind, entry_id=entry_id, delete=True)


@app.post("/api/groups/{group_id}/diagnostics/{kind}")
async def group_diagnostics(group_id: int, kind: str, request: Request,
                            source: str = "ipsb", refresh: bool = False):
    require(request)
    try:
        return await diagnostics.run(group_id, kind, source, refresh)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except (httpx.HTTPError, OSError, yaml.YAMLError) as exc:
        raise HTTPException(status_code=502, detail="代理组检测入口不可用，请稍后重试") from exc


@app.post("/api/subscriptions")
def create_subscription(payload: dict, request: Request):
    require(request)
    name = str(payload.get("name") or "").strip()
    url = str(payload.get("url") or "").strip()
    if not name or not url.startswith(("http://", "https://")):
        raise HTTPException(status_code=400, detail="需要名称和 http(s) 订阅链接")
    sub_id = db.add_subscription(name, url, int(payload.get("interval_hours") or 6), bool(payload.get("enabled", True)))
    service.apply_config()
    return db.get_subscription(sub_id)


@app.put("/api/subscriptions/{sub_id}")
def edit_subscription(sub_id: int, payload: dict, request: Request):
    require(request)
    if payload.get("url") and not str(payload["url"]).startswith(("http://", "https://")):
        raise HTTPException(status_code=400, detail="订阅链接必须以 http:// 或 https:// 开头")
    updated = db.update_subscription(sub_id, payload)
    if not updated:
        raise HTTPException(status_code=404, detail="订阅不存在")
    service.apply_config()
    return updated


@app.delete("/api/subscriptions/{sub_id}")
def remove_subscription(sub_id: int, request: Request):
    require(request)
    if not db.get_subscription(sub_id):
        raise HTTPException(status_code=404, detail="订阅不存在")
    db.delete_subscription(sub_id)
    service.apply_config()
    return {"ok": True}


@app.post("/api/subscriptions/{sub_id}/update")
def update_one(sub_id: int, request: Request):
    require(request)
    sub = db.get_subscription(sub_id)
    if not sub:
        raise HTTPException(status_code=404, detail="订阅不存在")
    try:
        service.update_provider(f"sub{sub_id}")
    except httpx.HTTPError as exc:
        db.update_subscription(sub_id, {"last_error": str(exc)})
        raise HTTPException(status_code=502, detail=f"订阅更新失败: {exc}") from exc
    db.update_subscription(sub_id, {"last_update": service.now_iso(), "last_error": ""})
    return {"ok": True}


@app.post("/api/subscriptions/update-all")
def update_every(request: Request):
    require(request)
    errors = service.update_all_providers()
    if errors:
        raise HTTPException(status_code=502, detail="；".join(errors))
    return {"ok": True}


@app.get("/api/groups")
def groups(request: Request):
    require(request)
    items = db.list_groups()
    try:
        status = service.group_status()
    except httpx.HTTPError:
        status = {}
    routing = service.group_routing_details()
    return [dict(g, **status.get(g["name"], {"now": None, "all": []}), routing_details=routing.get(g["id"], [])) for g in items]


def clean_group(payload, existing=None):
    name = str(payload.get("name", existing["name"] if existing else "")).strip()
    strategy = str(payload.get("strategy", existing["strategy"] if existing else "fallback"))
    if not name or any(c in name for c in ",\r\n") or name.upper() in ("DIRECT", "REJECT", "GLOBAL", "PASS", "COMPATIBLE"):
        raise HTTPException(status_code=400, detail="请填写代理组名称，不能使用保留名称、逗号或换行")
    if strategy not in ("fallback", "url-test", "select"):
        raise HTTPException(status_code=400, detail="策略只能是 fallback、url-test 或 select")
    rule_sets = payload.get("rule_sets", existing["rule_sets"] if existing else "")
    if isinstance(rule_sets, list):
        rule_sets = ",".join(rule_sets)
    if "node_filter" in payload or "exclude_filter" in payload:
        raise HTTPException(status_code=400, detail="请在节点卡片中勾选节点")
    selected = payload.get("selected_nodes", existing.get("selected_nodes") if existing else None)
    if not isinstance(selected, list):
        raise HTTPException(status_code=400, detail="请勾选此代理组使用的节点")
    known = {(n["provider"], n["name"]) for n in service.node_catalog()}
    previous = {(n["provider"], n["name"]) for n in (existing or {}).get("selected_nodes") or []}
    cleaned, seen = [], set()
    for node in selected:
        if not isinstance(node, dict) or not isinstance(node.get("provider"), str) or not isinstance(node.get("name"), str):
            raise HTTPException(status_code=400, detail="节点选择格式不正确")
        key = (node["provider"], node["name"])
        if key not in known and key not in previous:
            raise HTTPException(status_code=400, detail="节点已不在当前订阅中，请刷新节点列表")
        if key not in seen:
            cleaned.append({"provider": key[0], "name": key[1]})
            seen.add(key)
    if not cleaned or (payload.get("enabled", existing["enabled"] if existing else True) and not seen.intersection(known)):
        raise HTTPException(status_code=400, detail="请至少勾选一个当前订阅中的节点")
    if existing and existing["is_default"] and not payload.get("is_default", True):
        raise HTTPException(status_code=400, detail="请在另一个启用的代理组中设置默认出口")
    enabled = payload.get("enabled", bool(existing["enabled"]) if existing else True)
    is_default = payload.get("is_default", bool(existing["is_default"]) if existing else False)
    if not enabled and (is_default or (existing and any(r["enabled"] and r["group_id"] == existing["id"] for r in db.list_rules()))):
        raise HTTPException(status_code=400, detail="请先为规则或默认出口选择其他代理组，再停用此组")
    return {
        "name": name,
        "strategy": strategy,
        "node_filter": "",
        "exclude_filter": "",
        "selected_nodes": cleaned,
        "domains": payload.get("domains", existing["domains"] if existing else ""),
        "rule_sets": rule_sets or "",
        "priority": int(payload.get("priority", existing["priority"] if existing else 100)),
        "enabled": 1 if payload.get("enabled", True if not existing else bool(existing["enabled"])) else 0,
        "is_default": 1 if payload.get("is_default", False if not existing else bool(existing["is_default"])) else 0,
    }


@app.post("/api/groups")
def create_group(payload: dict, request: Request):
    require(request)
    try:
        with service.config_change():
            group_id = db.add_group(clean_group(payload))
    except HTTPException:
        raise
    except Exception as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return db.get_group(group_id)


@app.put("/api/groups/{group_id}")
def edit_group(group_id: int, payload: dict, request: Request):
    require(request)
    current = db.get_group(group_id)
    if not current:
        raise HTTPException(status_code=404, detail="代理组不存在")
    try:
        with service.config_change():
            updated = db.update_group(group_id, clean_group(payload, current))
    except HTTPException:
        raise
    except Exception as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return updated


@app.delete("/api/groups/{group_id}")
def remove_group(group_id: int, request: Request):
    require(request)
    if not db.get_group(group_id):
        raise HTTPException(status_code=404, detail="代理组不存在")
    if db.get_group(group_id)["is_default"] or any(r["group_id"] == group_id for r in db.list_rules()):
        raise HTTPException(status_code=400, detail="此组仍作为默认出口或被规则使用，请先修改分流目标")
    try:
        with service.config_change():
            db.delete_group(group_id)
    except Exception as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return {"ok": True}


@app.post("/api/mode")
def change_mode(payload: dict, request: Request):
    require(request)
    try:
        service.set_mode(str(payload.get("mode") or ""))
    except Exception as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return {"mode": db.get_setting("mode")}


@app.post("/api/tun")
def change_tun(payload: dict, request: Request):
    require(request)
    db.set_setting("tun", "1" if payload.get("enabled") else "0")
    try:
        service.apply_config()
    except Exception as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return {"tun": db.get_setting("tun") == "1"}


@app.post("/api/settings")
def change_settings(payload: dict, request: Request):
    require(request)
    if "auto_update" in payload:
        db.set_setting("auto_update", "1" if payload["auto_update"] else "0")
    if "cooldown_seconds" in payload:
        db.set_setting("cooldown_seconds", str(max(int(payload["cooldown_seconds"]), 60)))
    if "fail_threshold" in payload:
        db.set_setting("fail_threshold", str(max(int(payload["fail_threshold"]), 1)))
    return {
        "auto_update": db.get_setting("auto_update") == "1",
        "cooldown_seconds": int(db.get_setting("cooldown_seconds", "600")),
        "fail_threshold": int(db.get_setting("fail_threshold", "2")),
    }


@app.get("/api/nodes")
def nodes(request: Request):
    require(request)
    cooled = {row["node_name"]: row for row in db.list_cooldowns()}
    items = []
    for row in db.list_node_health():
        item = dict(row)
        hold = cooled.get(row["node_name"])
        item["cooldown_until"] = hold["until"] if hold else ""
        items.append(item)
    return items


@app.post("/api/nodes/healthcheck")
def run_healthcheck(request: Request):
    require(request)
    try:
        return service.healthcheck_providers()
    except Exception as exc:
        raise HTTPException(status_code=502, detail=str(exc)) from exc


@app.post("/api/nodes/cooldowns/clear")
def clear_holds(request: Request):
    require(request)
    db.clear_cooldowns()
    service.apply_config()
    return {"ok": True}


@app.get("/api/kernel")
def kernel(request: Request):
    require(request)
    return {"version": service.version()}


@app.post("/api/kernel/upgrade")
def upgrade(request: Request):
    require(request)
    try:
        return service.upgrade_kernel()
    except Exception as exc:
        raise HTTPException(status_code=502, detail=str(exc)) from exc


@app.get("/api/telemetry")
def telemetry(request: Request):
    require(request)
    return service.telemetry()


@app.post("/api/groups/{group_id}/select")
def select_node(group_id: int, payload: dict, request: Request):
    require(request)
    group = db.get_group(group_id)
    if not group:
        raise HTTPException(status_code=404, detail="代理组不存在")
    try:
        service.select_node(group, str(payload.get("name") or ""))
    except (ValueError, httpx.HTTPError) as exc:
        raise HTTPException(status_code=400, detail="节点选择失败，请确认节点属于手动选择组") from exc
    return {"ok": True}


@app.get("/api/rules")
def rules(request: Request):
    require(request)
    return db.list_rules()


def clean_rule(payload, current=None):
    fields = dict(current or {"name": "", "domains": "", "rule_sets": "", "group_id": 0, "priority": 100, "enabled": 1})
    fields.update({k: v for k, v in payload.items() if k in fields})
    fields["name"] = str(fields["name"]).strip()
    if not fields["name"]:
        raise HTTPException(status_code=400, detail="规则名称不能为空")
    try:
        fields["group_id"] = int(fields["group_id"])
        fields["priority"] = int(fields["priority"])
    except (ValueError, TypeError) as exc:
        raise HTTPException(status_code=400, detail="请选择代理组并填写数字优先级") from exc
    group = db.get_group(fields["group_id"])
    if not group or not group["enabled"]:
        raise HTTPException(status_code=400, detail="请选择已启用的代理组")
    sets = fields["rule_sets"]
    if isinstance(sets, str):
        sets = [x.strip() for x in sets.split(",") if x.strip()]
    if not isinstance(sets, list) or any(x not in ("openai", "claude", "gemini") for x in sets):
        raise HTTPException(status_code=400, detail="不支持的规则集")
    fields["rule_sets"] = ",".join(dict.fromkeys(sets))
    domains = []
    for raw in str(fields["domains"] or "").splitlines():
        item = raw.strip()
        if not item or item.startswith("#"):
            continue
        prefix = ""
        if item.upper().startswith("DOMAIN,"):
            prefix, item = "DOMAIN,", item[7:]
        item = item.removeprefix("*.").removeprefix("+.").rstrip(".")
        try:
            item = item.encode("idna").decode("ascii").lower()
        except UnicodeError as exc:
            raise HTTPException(status_code=400, detail="域名格式不正确") from exc
        if len(item) > 253 or not all(re.fullmatch(r"[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?", part) for part in item.split(".")):
            raise HTTPException(status_code=400, detail="每行填写一个域名，如 google.com，不要包含网址路径")
        domains.append(prefix + item)
    fields["domains"] = "\n".join(dict.fromkeys(domains))
    fields["enabled"] = int(bool(fields["enabled"]))
    if not domains and not sets:
        raise HTTPException(status_code=400, detail="请至少添加一个域名或规则集")
    return fields


@app.post("/api/rules")
def create_rule(payload: dict, request: Request):
    require(request)
    try:
        with service.config_change():
            rule_id = db.save_rule(clean_rule(payload))
    except HTTPException:
        raise
    except Exception as exc:
        raise HTTPException(status_code=400, detail="规则应用失败，已保留原配置") from exc
    return db.get_rule(rule_id)


@app.put("/api/rules/{rule_id}")
def edit_rule(rule_id: int, payload: dict, request: Request):
    require(request)
    current = db.get_rule(rule_id)
    if not current:
        raise HTTPException(status_code=404, detail="规则不存在")
    try:
        with service.config_change():
            db.save_rule(clean_rule(payload, current), rule_id)
    except HTTPException:
        raise
    except Exception as exc:
        raise HTTPException(status_code=400, detail="规则应用失败，已保留原配置") from exc
    return db.get_rule(rule_id)


@app.delete("/api/rules/{rule_id}")
def remove_rule(rule_id: int, request: Request):
    require(request)
    if not db.get_rule(rule_id):
        raise HTTPException(status_code=404, detail="规则不存在")
    try:
        with service.config_change():
            db.delete_rule(rule_id)
    except Exception as exc:
        raise HTTPException(status_code=400, detail="规则应用失败，已保留原配置") from exc
    return {"ok": True}


@app.get("/api/node-catalog")
def node_catalog(request: Request):
    require(request)
    try:
        return service.node_catalog()
    except httpx.HTTPError as exc:
        raise HTTPException(status_code=502, detail="无法读取订阅节点，请稍后刷新") from exc
