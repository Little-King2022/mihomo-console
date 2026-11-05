"""Persisted rule providers; defaults are migrated once, never reinserted."""
import hashlib
import ipaddress
import json
import re
from urllib.parse import urlsplit

DEFAULT_URLS = {
    **{n: f'https://testingcf.jsdelivr.net/gh/Loyalsoldier/clash-rules@release/{n}.txt'
       for n in ('reject', 'private', 'direct', 'proxy', 'lancidr', 'cncidr')},
    **{n.lower(): f'https://testingcf.jsdelivr.net/gh/blackmatrix7/ios_rule_script@master/rule/Clash/{n}/{n}.yaml'
       for n in ('OpenAI', 'Claude', 'Gemini', 'Google')},
}
BASE = ('reject', 'private', 'direct', 'proxy', 'lancidr', 'cncidr')


def defaults():
    return [dict(name=n, type='http', behavior='ipcidr' if n in ('lancidr', 'cncidr') else
                 'domain' if n in BASE else 'classical', format='yaml', url=url, interval=86400, payload=[])
            for n, url in DEFAULT_URLS.items()]


def load(raw=None):
    items = defaults() if raw is None else json.loads(raw) if isinstance(raw, str) else raw
    if not isinstance(items, list):
        raise ValueError('规则集列表格式不正确')
    result = [clean(x) for x in items]
    names = [x['name'] for x in result]
    if len(names) != len(set(names)):
        raise ValueError('规则集名称不能重复')
    return result


def clean(raw):
    if not isinstance(raw, dict):
        raise ValueError('规则集格式不正确')
    name = raw.get('name', '')
    if not isinstance(name, str) or not re.fullmatch(r'[a-zA-Z0-9][a-zA-Z0-9_-]{0,63}', name):
        raise ValueError('名称须为 1–64 位字母、数字、下划线或连字符')
    kind, behavior, fmt = raw.get('type', 'http'), raw.get('behavior', 'domain'), raw.get('format', 'yaml')
    if kind not in ('http', 'inline') or behavior not in ('domain', 'ipcidr', 'classical') or fmt not in ('yaml', 'text', 'mrs'):
        raise ValueError('请选择有效的来源、行为与格式')
    if fmt == 'mrs' and behavior == 'classical' or kind == 'inline' and fmt != 'yaml':
        raise ValueError('MRS 不支持 classical；内联规则集使用 YAML 格式')
    if name in BASE:
        original = next(x for x in defaults() if x['name'] == name)
        if (kind, behavior, fmt) != (original['type'], original['behavior'], original['format']):
            raise ValueError('基础规则集须保留原来源类型、行为和格式，以兼容 DNS 与国内绕过')
    url = raw.get('url', '')
    if not isinstance(url, str):
        raise ValueError('规则集 URL 格式不正确')
    if kind == 'http':
        try:
            parsed = urlsplit(url)
            valid = parsed.scheme in ('http', 'https') and parsed.hostname and not parsed.username and not parsed.password and not parsed.fragment
            parsed.port
        except ValueError:
            valid = False
        if not valid or any(c.isspace() for c in url):
            raise ValueError('请填写不含用户名、密码或片段的 HTTP(S) URL')
    interval = raw.get('interval', 86400)
    if isinstance(interval, bool) or not isinstance(interval, int) or not 60 <= interval <= 31536000:
        raise ValueError('更新周期须为 60–31536000 秒的整数')
    payload = raw.get('payload', [])
    if isinstance(payload, str):
        payload = [x.strip() for x in payload.splitlines() if x.strip() and not x.strip().startswith('#')]
    if not isinstance(payload, list) or len(payload) > 10000 or any(not isinstance(x, str) or not x.strip() or '\n' in x or '\r' in x or len(x) > 2048 for x in payload):
        raise ValueError('内联规则须为每行一条的文本，最多 10000 条')
    if kind == 'inline' and not payload:
        raise ValueError('请至少填写一条内联规则')
    if kind == 'inline':
        for entry in payload:
            if behavior == 'ipcidr':
                try:
                    ipaddress.ip_network(entry, strict=False)
                except ValueError as exc:
                    raise ValueError('IP 规则集每行须为有效 IP 或 CIDR 网段') from exc
            elif behavior == 'domain' and (any(c.isspace() for c in entry) or ',' in entry or not re.fullmatch(r'[a-zA-Z0-9_*+.?\-]+', entry)):
                raise ValueError('域名规则集每行须为域名或域名通配符')
            elif behavior == 'classical' and (',' not in entry or not re.fullmatch(r'[A-Z][A-Z0-9-]*', entry.split(',')[0]) or any(not part.strip() for part in entry.split(','))):
                raise ValueError('classical 规则须包含规则类型和匹配内容，如 DOMAIN-SUFFIX,example.com')
    excluded = raw.get('excluded', [])
    if not isinstance(excluded, list) or len(excluded) > 10000 or any(not isinstance(x, str) or not x.strip() or len(x) > 2048 or '\n' in x or '\r' in x for x in excluded):
        raise ValueError('排除条目须为规则文本列表，最多 10000 条')
    if excluded and (kind != 'http' or fmt == 'mrs'):
        raise ValueError('本地排除适用于 HTTP YAML / text 规则集')
    return dict(name=name, type=kind, behavior=behavior, format=fmt, url=url if kind == 'http' else '',
                excluded=list(dict.fromkeys(excluded)),
                interval=interval, payload=payload if kind == 'inline' else [])


def provider(item, managed_path=None):
    if item.get('excluded'):
        if managed_path is None:
            raise ValueError('本地排除规则尚未生成')
        return dict(type='file', behavior=item['behavior'], format='yaml', path=managed_path)
    result = {k: item[k] for k in ('type', 'behavior', 'format')}
    if item['type'] == 'inline':
        result['payload'] = item['payload']
    else:
        # Changing source/format must not reuse a stale provider cache.
        original = next((x for x in defaults() if x['name'] == item['name']), None)
        same = original and all(item[k] == original[k] for k in ('url', 'behavior', 'format'))
        suffix = '' if same else '-' + hashlib.sha256(json.dumps([item[k] for k in ('url', 'behavior', 'format')]).encode()).hexdigest()[:16]
        result.update(url=item['url'], interval=item['interval'], proxy='DIRECT',
                      path=f"./ruleset/{item['name']}{suffix}.{item['format']}")
    return result


def references(name):
    from . import db
    refs = []
    if name in BASE:
        refs.append('基础分流 / DNS / TUN')
    for rule in db.list_rules():
        if name in rule['rule_sets'].split(','):
            refs.append('分流规则：' + rule['name'])
    for group in db.list_groups():
        if name in group['rule_sets'].split(','):
            refs.append('代理组兼容规则：' + group['name'])
    return refs


def listing():
    from . import db
    return [dict(item, references=references(item['name']), protected=item['name'] in BASE)
            for item in load(db.get_setting('rulesets'))]


def read_payload(item, directory):
    if item['type'] == 'inline':
        return item['payload']
    if item['format'] == 'mrs':
        return None
    return filtered(item, parse_payload((directory / source_provider(item)['path']).read_text(), item['format']))


def source_provider(item):
    """Original HTTP source remains owned and refreshed by Mihomo."""
    return provider(dict(item, excluded=[]))


def exclusion_key(value, behavior):
    # Domain lists use +. for suffix matches. Accept a bare domain as the same
    # entry in delete requests, without broad substring or parent-domain removal.
    return value.strip().lower().removeprefix('+.').removeprefix('*.').lstrip('.').rstrip('.') if behavior == 'domain' else value.strip()


def filtered(item, entries):
    excluded = {exclusion_key(x, item['behavior']) for x in item.get('excluded', [])}
    return [x for x in entries if exclusion_key(x, item['behavior']) not in excluded]


def prepare_overrides(items, directory):
    """Write immutable filtered files before validation; old config paths stay valid.

    Paths include both source bytes and exclusions. Failed validation/reload leaves
    the previous file intact, including across background source refreshes.
    """
    import os
    import shutil
    import httpx
    import yaml
    result = {}
    for item in items:
        if item['type'] != 'http' or not item.get('excluded'):
            continue
        source = directory / source_provider(item)['path']
        if not source.exists():
            # The core may not have cached a newly added source yet. Fetch once;
            # no secret URLs or upstream errors are returned to the caller.
            with httpx.Client(timeout=25, follow_redirects=True, trust_env=False) as http:
                with http.stream('GET', item['url']) as response:
                    response.raise_for_status()
                    chunks, size = [], 0
                    for chunk in response.iter_bytes():
                        size += len(chunk)
                        if size > 32 * 1024 * 1024:
                            raise ValueError('规则集来源超过 32 MiB')
                        chunks.append(chunk)
            source.parent.mkdir(parents=True, exist_ok=True)
            temporary = source.with_suffix(source.suffix + '.console-tmp')
            temporary.write_bytes(b''.join(chunks))
            shutil.chown(temporary, user='mihomo', group='mihomo')
            os.chmod(temporary, 0o640)
            os.replace(temporary, source)
        data = source.read_bytes()
        revision = hashlib.sha256(data + json.dumps([item['behavior'], item['format'], sorted(item['excluded'])], ensure_ascii=False).encode()).hexdigest()[:24]
        path = f"./ruleset/console-{item['name']}-{revision}.yaml"
        destination = directory / path
        if not destination.exists():
            entries = parse_payload(data.decode('utf-8-sig'), item['format'])
            effective = filtered(item, entries)
            # A domain provider containing no entries is valid. Never restore
            # upstream rules simply because the user removed every entry.
            text = yaml.dump({'payload': effective}, Dumper=getattr(yaml, 'CSafeDumper', yaml.SafeDumper), allow_unicode=True)
            temporary = destination.with_suffix('.yaml.tmp')
            temporary.write_text(text)
            shutil.chown(temporary, user='mihomo', group='mihomo')
            os.chmod(temporary, 0o640)
            os.replace(temporary, destination)
        result[item['name']] = path
    return result


def parse_payload(text, fmt):
    import yaml
    if fmt == 'mrs':
        raise ValueError('MRS 为二进制格式，请将来源改为 YAML 或 text 后编辑条目')
    if fmt == 'yaml':
        data = yaml.load(text, Loader=getattr(yaml, 'CSafeLoader', yaml.SafeLoader))
        entries = data.get('payload') if isinstance(data, dict) else None
    else:
        entries = [line.strip() for line in text.splitlines() if line.strip() and not line.strip().startswith('#')]
    if not isinstance(entries, list) or any(not isinstance(x, str) or not x.strip() for x in entries):
        raise ValueError('来源文件须包含有效的规则列表')
    return entries


def entry_listing(name, directory, query='', page=1):
    from . import db
    item = next((x for x in load(db.get_setting('rulesets')) if x['name'] == name), None)
    if item is None:
        raise LookupError('规则集不存在')
    entries = item['payload'] if item['type'] == 'inline' else parse_payload((directory / source_provider(item)['path']).read_text(), item['format'])
    visible = filtered(item, entries)
    matches = [x for x in visible if query.lower() in x.lower()]
    size = 100
    return dict(name=name, total=len(visible), matched=len(matches), page=page, page_size=size,
                entries=matches[(page-1)*size:page*size], excluded=item.get('excluded', []), type=item['type'])
