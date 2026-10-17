import json
import os
import secrets
import sqlite3
import threading
from contextlib import contextmanager
from pathlib import Path

_lock = threading.RLock()
_transaction_depth = 0
_conn = None

AI_DOMAINS = "\n".join(
    [
        "chatgpt.com",
        "openai.com",
        "oaistatic.com",
        "oaiusercontent.com",
        "claude.ai",
        "claude.com",
        "anthropic.com",
        "gemini.google.com",
        "generativelanguage.googleapis.com",
        "ai.google.dev",
        "grok.com",
        "x.ai",
        "grok.x.ai",
    ]
)
TAIWAN_HOME_FILTER = (
    r"(?i)((台湾|台灣|🇹🇼).*(家宽|家寬|hinet|seednet)|(hinet|seednet).*(家宽|家寬|台湾|台灣|🇹🇼))"
)
INFO_NODE_EXCLUDE = r"(?i)(剩余流量|重置剩余|套餐到期|官网地址|防失联|更新订阅)"
REGULAR_EXCLUDE = r"(?i)(家宽|家寬|hinet|seednet|剩余流量|重置剩余|套餐到期|官网地址|防失联|更新订阅)"


def data_dir() -> Path:
    path = Path(os.environ.get("DATA_DIR", "/opt/mihomo-console/data"))
    path.mkdir(parents=True, exist_ok=True)
    return path


def mihomo_home() -> Path:
    return Path(os.environ.get("MIHOMO_HOME", "/opt/mihomo"))


def connect() -> sqlite3.Connection:
    global _conn
    with _lock:
        if _conn is None:
            path = data_dir() / "console.db"
            _conn = sqlite3.connect(path, check_same_thread=False)
            _conn.row_factory = sqlite3.Row
        return _conn


def _execute(sql, params=()):
    conn = connect()
    with _lock:
        cur = conn.execute(sql, params)
        if not _transaction_depth:
            conn.commit()
        return cur


@contextmanager
def transaction():
    global _transaction_depth
    conn = connect()
    with _lock:
        _transaction_depth += 1
        try:
            yield
            conn.commit()
        except Exception:
            conn.rollback()
            raise
        finally:
            _transaction_depth -= 1


def _query(sql, params=()):
    conn = connect()
    with _lock:
        return list(conn.execute(sql, params))


def init_db():
    conn = connect()
    with _lock:
        conn.executescript(
            """
            CREATE TABLE IF NOT EXISTS settings (
                key TEXT PRIMARY KEY,
                value TEXT NOT NULL
            );
            CREATE TABLE IF NOT EXISTS subscriptions (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                name TEXT NOT NULL,
                url TEXT NOT NULL,
                enabled INTEGER NOT NULL DEFAULT 1,
                interval_hours INTEGER NOT NULL DEFAULT 6,
                last_update TEXT,
                last_info TEXT,
                last_error TEXT
            );
            CREATE TABLE IF NOT EXISTS groups (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                name TEXT NOT NULL UNIQUE,
                strategy TEXT NOT NULL DEFAULT 'fallback',
                node_filter TEXT NOT NULL DEFAULT '',
                exclude_filter TEXT NOT NULL DEFAULT '',
                domains TEXT NOT NULL DEFAULT '',
                rule_sets TEXT NOT NULL DEFAULT '',
                priority INTEGER NOT NULL DEFAULT 100,
                enabled INTEGER NOT NULL DEFAULT 1,
                is_default INTEGER NOT NULL DEFAULT 0
            );
            CREATE TABLE IF NOT EXISTS routing_rules (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                name TEXT NOT NULL,
                domains TEXT NOT NULL DEFAULT '',
                rule_sets TEXT NOT NULL DEFAULT '',
                group_id INTEGER NOT NULL,
                priority INTEGER NOT NULL DEFAULT 100,
                enabled INTEGER NOT NULL DEFAULT 1
            );
            CREATE TABLE IF NOT EXISTS cooldowns (
                node_name TEXT PRIMARY KEY,
                until TEXT NOT NULL,
                reason TEXT NOT NULL DEFAULT '',
                fails INTEGER NOT NULL DEFAULT 0
            );
            CREATE TABLE IF NOT EXISTS node_health (
                node_name TEXT PRIMARY KEY,
                provider TEXT NOT NULL DEFAULT '',
                delay INTEGER NOT NULL DEFAULT 0,
                alive INTEGER NOT NULL DEFAULT 0,
                fails INTEGER NOT NULL DEFAULT 0,
                checked_at TEXT
            );
            """
        )
        if "history" not in {row[1] for row in conn.execute("PRAGMA table_info(node_health)")}:
            conn.execute("ALTER TABLE node_health ADD COLUMN history TEXT NOT NULL DEFAULT '[]'")
        if "selected_nodes" not in {row[1] for row in conn.execute("PRAGMA table_info(groups)")}:
            conn.execute("ALTER TABLE groups ADD COLUMN selected_nodes TEXT")
        conn.commit()
    if get_setting("web_token") is None:
        token = secrets.token_urlsafe(18)
        set_setting("web_token", token)
        (data_dir() / "token").write_text(token + "\n", encoding="utf-8")
        os.chmod(data_dir() / "token", 0o600)
    defaults = {
        "mode": "rule",
        "tun": "1",
        "mihomo_secret": secrets.token_urlsafe(32),
        "auto_update": "1",
        "health_interval": "30",
        "cooldown_seconds": "600",
        "fail_threshold": "2",
    }
    for key, value in defaults.items():
        if get_setting(key) is None:
            set_setting(key, value)
    if not _query("SELECT id FROM groups LIMIT 1"):
        add_group(
            {
                "name": "台湾家宽",
                "strategy": "fallback",
                "selected_nodes": [],
                "node_filter": TAIWAN_HOME_FILTER,
                "exclude_filter": INFO_NODE_EXCLUDE,
                "domains": AI_DOMAINS,
                "rule_sets": "openai,claude,gemini",
                "priority": 10,
                "enabled": 1,
                "is_default": 0,
            }
        )
        add_group(
            {
                "name": "境外常规",
                "strategy": "fallback",
                "selected_nodes": [],
                "node_filter": "",
                "exclude_filter": REGULAR_EXCLUDE,
                "domains": "",
                "rule_sets": "",
                "priority": 100,
                "enabled": 1,
                "is_default": 1,
            }
        )

    # Move legacy service matches once; group ids keep assignments stable on rename.
    if get_setting("routing_rules_migrated") != "1":
        with transaction():
            for group in list_groups():
                if group["domains"] or group["rule_sets"]:
                    save_rule({"name": "AI 服务" if group["name"] in ("AI-台湾家宽", "台湾家宽") else group["name"],
                               "domains": group["domains"], "rule_sets": group["rule_sets"],
                               "group_id": group["id"], "priority": group["priority"],
                               "enabled": group["enabled"]})
            set_setting("routing_rules_migrated", "1")

    if get_setting("taiwan_group_renamed") != "1":
        with transaction():
            old = next((g for g in list_groups() if g["name"] == "AI-台湾家宽"), None)
            if old and not any(g["name"] == "台湾家宽" for g in list_groups()):
                update_group(old["id"], {"name": "台湾家宽"})
            set_setting("taiwan_group_renamed", "1")


def get_setting(key, default=None):
    rows = _query("SELECT value FROM settings WHERE key = ?", (key,))
    if not rows:
        return default
    return rows[0]["value"]


def set_setting(key, value):
    _execute(
        "INSERT INTO settings(key, value) VALUES(?, ?) ON CONFLICT(key) DO UPDATE SET value = excluded.value",
        (key, str(value)),
    )


def rows_as_dicts(rows):
    return [dict(row) for row in rows]


def list_subscriptions():
    return rows_as_dicts(_query("SELECT * FROM subscriptions ORDER BY id"))


def get_subscription(sub_id):
    rows = _query("SELECT * FROM subscriptions WHERE id = ?", (sub_id,))
    return dict(rows[0]) if rows else None


def add_subscription(name, url, interval_hours=6, enabled=True):
    cur = _execute(
        "INSERT INTO subscriptions(name, url, enabled, interval_hours) VALUES(?, ?, ?, ?)",
        (name.strip(), url.strip(), 1 if enabled else 0, int(interval_hours)),
    )
    return cur.lastrowid


def update_subscription(sub_id, fields):
    current = get_subscription(sub_id)
    if not current:
        return None
    merged = dict(current)
    for key in ("name", "url", "enabled", "interval_hours", "last_update", "last_info", "last_error"):
        if key in fields and fields[key] is not None:
            merged[key] = fields[key]
    _execute(
        """UPDATE subscriptions
           SET name=?, url=?, enabled=?, interval_hours=?, last_update=?, last_info=?, last_error=?
           WHERE id=?""",
        (
            str(merged["name"]).strip(),
            str(merged["url"]).strip(),
            int(merged["enabled"]),
            int(merged["interval_hours"]),
            merged["last_update"],
            merged["last_info"],
            merged["last_error"],
            sub_id,
        ),
    )
    return get_subscription(sub_id)


def delete_subscription(sub_id):
    _execute("DELETE FROM subscriptions WHERE id = ?", (sub_id,))


def decode_group(row):
    item = dict(row)
    item["selected_nodes"] = json.loads(item["selected_nodes"]) if item.get("selected_nodes") is not None else None
    return item


def list_groups():
    return [decode_group(row) for row in _query("SELECT * FROM groups ORDER BY priority, id")]


def get_group(group_id):
    rows = _query("SELECT * FROM groups WHERE id = ?", (group_id,))
    return decode_group(rows[0]) if rows else None


def add_group(fields):
    if int(fields.get("is_default") or 0):
        _execute("UPDATE groups SET is_default = 0")
    cur = _execute(
        """INSERT INTO groups(name, strategy, node_filter, exclude_filter, domains, rule_sets, priority, enabled, is_default, selected_nodes)
           VALUES(?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
        (
            fields["name"].strip(),
            fields.get("strategy") or "fallback",
            fields.get("node_filter") or "",
            fields.get("exclude_filter") or "",
            fields.get("domains") or "",
            fields.get("rule_sets") or "",
            int(fields.get("priority") or 100),
            int(fields.get("enabled", 1)),
            int(fields.get("is_default") or 0),
            dump_json(fields["selected_nodes"]) if fields.get("selected_nodes") is not None else None,
        ),
    )
    return cur.lastrowid


def update_group(group_id, fields):
    current = get_group(group_id)
    if not current:
        return None
    merged = dict(current)
    for key in (
        "name",
        "strategy",
        "node_filter",
        "exclude_filter",
        "selected_nodes",
        "domains",
        "rule_sets",
        "priority",
        "enabled",
        "is_default",
    ):
        if key in fields and fields[key] is not None:
            merged[key] = fields[key]
    if int(merged.get("is_default") or 0):
        _execute("UPDATE groups SET is_default = 0 WHERE id != ?", (group_id,))
    _execute(
        """UPDATE groups
           SET name=?, strategy=?, node_filter=?, exclude_filter=?, domains=?, rule_sets=?, priority=?, enabled=?, is_default=?, selected_nodes=?
           WHERE id=?""",
        (
            str(merged["name"]).strip(),
            merged["strategy"],
            merged["node_filter"] or "",
            merged["exclude_filter"] or "",
            merged["domains"] or "",
            merged["rule_sets"] or "",
            int(merged["priority"]),
            int(merged["enabled"]),
            int(merged["is_default"]),
            dump_json(merged["selected_nodes"]) if merged.get("selected_nodes") is not None else None,
            group_id,
        ),
    )
    return get_group(group_id)


def delete_group(group_id):
    _execute("DELETE FROM groups WHERE id = ?", (group_id,))


def list_cooldowns():
    return rows_as_dicts(_query("SELECT * FROM cooldowns"))


def replace_cooldowns(items):
    conn = connect()
    with _lock:
        conn.execute("DELETE FROM cooldowns")
        conn.executemany(
            "INSERT INTO cooldowns(node_name, until, reason, fails) VALUES(?, ?, ?, ?)",
            [(i["node_name"], i["until"], i.get("reason", ""), int(i.get("fails") or 0)) for i in items],
        )
        conn.commit()


def clear_cooldowns():
    _execute("DELETE FROM cooldowns")


def upsert_node_health(items):
    conn = connect()
    with _lock:
        old = {r["node_name"]: json.loads(r["history"]) for r in conn.execute("SELECT node_name, history FROM node_health")}
        conn.execute("DELETE FROM node_health")
        for item in items:
            samples = {h["time"]: h for h in old.get(item["node_name"], []) if h.get("time")}
            for h in item.get("history", []):
                if h.get("time"):
                    samples[h["time"]] = {"time": h["time"], "delay": int(h.get("delay") or 0)}
            history = sorted(samples.values(), key=lambda h: h["time"])[-24:]
            conn.execute(
                "INSERT INTO node_health(node_name,provider,delay,alive,fails,checked_at,history) VALUES(?,?,?,?,?,?,?)",
                (item["node_name"], item.get("provider", ""), int(item.get("delay") or 0),
                 int(item.get("alive") or 0), int(item.get("fails") or 0), item.get("checked_at"), dump_json(history)))
        conn.commit()


def list_node_health():
    items = rows_as_dicts(_query("SELECT * FROM node_health ORDER BY alive DESC, delay"))
    for item in items:
        item["history"] = json.loads(item["history"])
    return items


def list_rules():
    return rows_as_dicts(_query("SELECT * FROM routing_rules ORDER BY priority, id"))


def get_rule(rule_id):
    rows = _query("SELECT * FROM routing_rules WHERE id=?", (rule_id,))
    return dict(rows[0]) if rows else None


def save_rule(fields, rule_id=None):
    keys = ("name", "domains", "rule_sets", "group_id", "priority", "enabled")
    values = [fields[k] for k in keys]
    if rule_id is None:
        return _execute("INSERT INTO routing_rules(name,domains,rule_sets,group_id,priority,enabled) VALUES(?,?,?,?,?,?)", values).lastrowid
    _execute("UPDATE routing_rules SET name=?,domains=?,rule_sets=?,group_id=?,priority=?,enabled=? WHERE id=?", values + [rule_id])
    return rule_id


def delete_rule(rule_id):
    _execute("DELETE FROM routing_rules WHERE id=?", (rule_id,))


def get_node_fail(name):
    rows = _query("SELECT fails FROM node_health WHERE node_name = ?", (name,))
    return int(rows[0]["fails"]) if rows else 0


def dump_json(value):
    return json.dumps(value, ensure_ascii=False)
