#!/usr/bin/env python3
"""Check Git publication candidates without printing matching secret values.

Optional --live-data compares files with this deployment's actual web token,
core secret and subscription URLs in memory. No values are written to disk.
"""
import argparse
import math
import re
import sqlite3
import subprocess
import sys
from collections import Counter
from pathlib import Path
from urllib.parse import parse_qsl, urlsplit

ROOT = Path(__file__).resolve().parents[1]


def candidates(staged=False):
    args = ['git', 'ls-files', '-z', '--cached']
    if not staged:
        args += ['--others', '--exclude-standard']
    output = subprocess.check_output(args, cwd=ROOT)
    return sorted(set(Path(item.decode()) for item in output.split(b'\0') if item))


def live_secrets(directory):
    database = directory / 'console.db'
    with sqlite3.connect(database.resolve().as_uri() + '?mode=ro', uri=True) as connection:
        values = [row[0] for row in connection.execute("SELECT value FROM settings WHERE key IN ('web_token', 'mihomo_secret')")]
        values += [row[0] for row in connection.execute('SELECT url FROM subscriptions')]
    token = directory / 'token'
    if token.exists():
        values.append(token.read_text().strip())
    return [value.encode() for value in values if isinstance(value, str) and len(value) >= 8]


def entropy(value):
    return -sum((count / len(value)) * math.log2(count / len(value)) for count in Counter(value).values())


def problems(path, data, secrets):
    issues = []
    if path.suffix in {'.db', '.sqlite', '.sqlite3', '.key', '.pem', '.p12', '.pfx', '.har', '.pyc'} or path.name == 'token' or path.name.startswith('.env') and path.name != '.env.example':
        issues.append('private runtime/credential file')
    if any(value in data for value in secrets):
        issues.append('matches a deployment credential or subscription URL')
    if b'\0' in data[:8192] or path.suffix.lower() in {'.png', '.jpg', '.jpeg', '.webp'}:
        return issues
    content = data.decode('utf-8', errors='replace')
    patterns = {
        'GitHub credential': r'(?:gh[pousr]_|github_pat_)[A-Za-z0-9_]{20,}',
        'private key block': r'-----BEGIN (?:RSA |EC |OPENSSH |DSA )?PRIVATE KEY-----',
        'AWS access key': r'\b(?:AKIA|ASIA)[A-Z0-9]{16}\b',
    }
    for label, pattern in patterns.items():
        if re.search(pattern, content):
            issues.append(label)
    for raw in re.findall(r'https?://[^\s<>\x22\x27`]+', content):
        try:
            raw = re.sub(r'\{[^}]*\}?', 'TEMPLATE', raw)
            url = urlsplit(raw)
            host = url.hostname or ''
            if url.username or url.password:
                issues.append('URL with embedded credentials')
                continue
            example = host in {'localhost', '127.0.0.1', '::1'} or host.endswith(('.example', '.example.com', '.example.net', '.example.org')) or host in {'example.com', 'example.net', 'example.org'}
            if example:
                continue
            if any(key.lower() in {'token', 'access_token', 'api_key', 'password', 'auth'} and len(value) >= 16 for key, value in parse_qsl(url.query)):
                issues.append('URL with credential-like query')
            if any(len(segment) >= 28 and re.fullmatch(r'[A-Za-z0-9_-]+', segment) and entropy(segment) > 3.5 for segment in url.path.split('/')):
                issues.append('URL with high-entropy private path')
        except ValueError:
            issues.append('malformed URL requiring review')
    return sorted(set(issues))


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--staged', action='store_true', help='check only staged/tracked candidates')
    parser.add_argument('--live-data', type=Path, help='read-only deployment data comparison; never prints values')
    args = parser.parse_args()
    try:
        paths = candidates(args.staged)
        secrets = live_secrets(args.live_data) if args.live_data else []
    except (subprocess.CalledProcessError, sqlite3.Error, OSError) as exc:
        print('Unable to prepare scan:', type(exc).__name__, file=sys.stderr)
        return 2
    count = 0
    for path in paths:
        target = ROOT / path
        if not target.is_file():
            continue
        data = subprocess.check_output(['git', 'show', ':' + path.as_posix()], cwd=ROOT) if args.staged else target.read_bytes()
        issues = problems(path, data, secrets)
        for issue in issues:
            print(f'{path}: {issue}')
            count += 1
    if count:
        print(f'FAIL: {count} finding(s); secret values are redacted.')
        return 1
    print(f'PASS: checked {len(paths)} publication candidates; no detected credentials or private runtime files.')
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
