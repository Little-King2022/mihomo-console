"""Explicit, serialized component maintenance with staged files and recovery."""
import fcntl
import gzip
import hashlib
import json
import os
import platform
import re
import shutil
import subprocess
import tarfile
import tempfile
import threading
import time
import uuid
from datetime import datetime, timezone
from pathlib import Path, PurePosixPath

import httpx
import yaml

from . import db, mihomo, router

REPOS = {'kernel': 'mihomo', 'panel': 'metacubexd'}
ACTIVE = ('queued', 'running')


class MaintenanceError(RuntimeError):
    pass


def home():
    path = db.data_dir() / 'maintenance'
    path.mkdir(mode=0o700, exist_ok=True)
    return path


def read(name):
    try:
        return json.loads((home() / name).read_text())
    except (OSError, ValueError):
        return None


def write(name, data):
    path = home() / name
    temp = path.with_name(path.name + '.' + uuid.uuid4().hex + '.tmp')
    temp.write_text(json.dumps(data, ensure_ascii=False))
    temp.chmod(0o600)
    os.replace(temp, path)


def timestamp():
    return datetime.now(timezone.utc).isoformat(timespec='seconds')


def version_number(value):
    match = re.search(r'(?<![\d.])v?(\d+)\.(\d+)\.(\d+)(?![\d.])', str(value))
    return tuple(map(int, match.groups())) if match else None


def binary_version(path):
    result = subprocess.run([str(path), '-v'], capture_output=True, text=True, timeout=10)
    if result.returncode or not version_number(result.stdout):
        raise MaintenanceError('无法识别内核版本')
    return 'v' + '.'.join(map(str, version_number(result.stdout)))


def component_path(kind):
    if kind == 'kernel':
        return mihomo.binary_path()
    config = yaml.safe_load(mihomo.config_path().read_text())
    value = config.get('external-ui')
    if not isinstance(value, str) or not value:
        raise MaintenanceError('当前配置未设置 external-ui')
    path = mihomo.config_dir() / value
    if path.is_symlink() or not path.resolve().is_relative_to(mihomo.config_dir().resolve()) or path.resolve() == mihomo.config_dir().resolve():
        raise MaintenanceError('面板目录必须位于 Mihomo 配置目录内，且不能是符号链接')
    return path


def installed(kind):
    try:
        path = component_path(kind)
        if kind == 'kernel':
            return dict(installed=path.is_file(), version=binary_version(path) if path.is_file() else None)
        marker = path / '.console-release.json'
        record = json.loads(marker.read_text()) if marker.exists() else {}
        version = record.get('version') if isinstance(record, dict) else None
        return dict(installed=(path / 'index.html').is_file(), version=version)
    except (OSError, ValueError, subprocess.SubprocessError, MaintenanceError):
        return dict(installed=False, version=None)


def status():
    result = {}
    for kind in REPOS:
        current = installed(kind)
        release = read(kind + '-release.json')
        backup = read(kind + '-backup.json')
        if backup and not (home() / 'backups' / backup.get('snapshot', '') / 'content').exists():
            backup = None
        latest = release.get('version') if release else None
        old, new = version_number(current['version']), version_number(latest)
        result[kind] = dict(current, latest=latest, checked_at=release.get('checked_at') if release else None,
                            release_url=release.get('release_url') if release else None,
                            update_available=bool(new and (old is None or new > old)),
                            rollback_available=bool(backup), rollback_version=backup.get('version') if backup else None)
    return dict(components=result, job=read('job.json'))


def asset_name(kind, tag, assets):
    if kind == 'panel':
        names = ['compressed-dist.tgz']
    else:
        machine = platform.machine().lower()
        if platform.system() != 'Linux' or machine not in ('x86_64', 'amd64', 'aarch64', 'arm64'):
            raise MaintenanceError('当前支持 Linux amd64 / arm64 内核维护')
        names = ([f'mihomo-linux-amd64-v1-{tag}.gz', f'mihomo-linux-amd64-compatible-{tag}.gz', f'mihomo-linux-amd64-{tag}.gz']
                 if machine in ('x86_64', 'amd64') else [f'mihomo-linux-arm64-{tag}.gz'])
    return next((asset for name in names for asset in assets if asset.get('name') == name), None)


def check(kind):
    if kind not in REPOS:
        raise MaintenanceError('未知维护组件')
    with httpx.Client(timeout=25, follow_redirects=True, trust_env=False) as http:
        response = http.get(f'https://api.github.com/repos/MetaCubeX/{REPOS[kind]}/releases/latest',
                            headers={'Accept': 'application/vnd.github+json', 'User-Agent': 'mihomo-console'})
        if response.status_code == 429 or (response.status_code == 403 and response.headers.get('x-ratelimit-remaining') == '0'):
            message = 'GitHub API 查询额度暂时用尽，请稍后重试；上次检查结果仍保留'
            if read(kind + '-release.json'):
                return dict(status(), warning=message)
            raise MaintenanceError('GitHub API 查询额度暂时用尽，请稍后重试；尚无成功检查记录')
        response.raise_for_status()
        release = response.json()
    tag = release.get('tag_name', '')
    if not re.fullmatch(r'v?\d+\.\d+\.\d+', tag) or release.get('draft') or release.get('prerelease'):
        raise MaintenanceError('未找到有效的稳定版本')
    asset = asset_name(kind, tag, release.get('assets') or [])
    if not asset:
        raise MaintenanceError('该稳定版本没有适用于当前系统的发布包')
    digest = asset.get('digest') or ''
    if not re.fullmatch(r'sha256:[a-fA-F0-9]{64}', digest):
        raise MaintenanceError('发布包缺少 SHA-256 校验信息，暂不能升级')
    url = asset.get('browser_download_url', '')
    expected = f'https://github.com/MetaCubeX/{REPOS[kind]}/releases/download/{tag}/{asset["name"]}'
    if url != expected:
        raise MaintenanceError('发布包地址不属于预期的官方发布')
    api_url = f'https://api.github.com/repos/MetaCubeX/{REPOS[kind]}/releases/assets/{asset["id"]}' if isinstance(asset.get('id'), int) and asset['id'] > 0 else None
    data = dict(version=tag, url=url, api_url=api_url, sha256=digest[7:].lower(), checked_at=timestamp(),
                release_url=f'https://github.com/MetaCubeX/{REPOS[kind]}/releases/tag/{tag}')
    write(kind + '-release.json', data)
    return status()


def download(release, destination):
    cache = home() / 'assets' / (release['sha256'] + '.archive')
    if cache.is_file():
        if hashlib.sha256(cache.read_bytes()).hexdigest() == release['sha256']:
            shutil.copy2(cache, destination)
            return
        cache.unlink()
    failure = None
    for url in filter(None, (release['url'], release.get('api_url'))):
        for attempt in range(2):
            try:
                download_once(release, destination, url)
                cache.parent.mkdir(mode=0o700, exist_ok=True)
                temp = cache.with_name(cache.name + '.' + uuid.uuid4().hex)
                shutil.copy2(destination, temp)
                temp.chmod(0o600)
                os.replace(temp, cache)
                return
            except httpx.TransportError as exc:
                failure = exc
                time.sleep(.5)
    raise failure


def download_once(release, destination, url):
    digest, size = hashlib.sha256(), 0
    with httpx.Client(timeout=120, follow_redirects=True, trust_env=False) as http:
        with http.stream('GET', url, headers={'User-Agent': 'mihomo-console', 'Accept': 'application/octet-stream', 'X-GitHub-Api-Version': '2022-11-28'}) as response:
            response.raise_for_status()
            with destination.open('wb') as output:
                for chunk in response.iter_bytes():
                    size += len(chunk)
                    if size > 64 * 1024 * 1024:
                        raise MaintenanceError('发布包超过 64 MiB 限制')
                    digest.update(chunk)
                    output.write(chunk)
    if digest.hexdigest() != release['sha256']:
        raise MaintenanceError('发布包 SHA-256 校验失败，原版本未改动')


def unpack_panel(archive, destination):
    count = size = 0
    with tarfile.open(archive, 'r:gz') as tar:
        for member in tar:
            parts = PurePosixPath(member.name)
            if parts.is_absolute() or '..' in parts.parts or '\\' in member.name or not (member.isdir() or member.isfile()):
                raise MaintenanceError('面板压缩包包含不安全的路径或链接')
            count += 1
            size += member.size
            if count > 20000 or size > 256 * 1024 * 1024:
                raise MaintenanceError('面板解压内容超过限制')
            target = destination.joinpath(*parts.parts)
            if member.isdir():
                target.mkdir(parents=True, exist_ok=True)
            else:
                target.parent.mkdir(parents=True, exist_ok=True)
                with tar.extractfile(member) as source, target.open('wb') as output:
                    shutil.copyfileobj(source, output)
                target.chmod(0o644)
    index = destination / 'index.html'
    if not index.is_file() or b'<html' not in index.read_bytes().lower() or not any(destination.rglob('*.js')):
        raise MaintenanceError('发布包缺少有效的 index.html 或 JavaScript 资源')


def run_system(*args):
    result = subprocess.run(['systemctl', *args, 'mihomo'], capture_output=True, timeout=30)
    if result.returncode:
        raise MaintenanceError('Mihomo 服务操作失败')


def verify_kernel(expected):
    end = time.monotonic() + 25
    while time.monotonic() < end:
        try:
            with mihomo.client() as http:
                response = http.get('/version', timeout=2)
                response.raise_for_status()
                if version_number(response.json().get('version')) != version_number(expected):
                    raise MaintenanceError('运行内核版本不符')
                response = http.get('/configs', timeout=2)
                response.raise_for_status()
                if bool(response.json().get('tun', {}).get('enable')) != (db.get_setting('tun', '1') == '1'):
                    raise MaintenanceError('TUN 运行状态与设置不一致')
                router.verify_domestic_bypass()
                return
        except (httpx.HTTPError, ValueError, RuntimeError, OSError):
            time.sleep(.5)
    raise MaintenanceError('内核启动、TUN 或国内地址集检查未通过')


def validate_kernel(path, expected):
    if version_number(binary_version(path)) != version_number(expected):
        raise MaintenanceError('发布包版本与检查结果不符')
    result = subprocess.run([str(path), '-t', '-d', str(mihomo.config_dir()), '-f', str(mihomo.config_path())],
                            capture_output=True, timeout=60)
    if result.returncode:
        raise MaintenanceError('新内核无法通过当前配置自检，原版本未改动')


def progress(message):
    job = read('job.json') or {}
    job.update(state='running', message=message)
    write('job.json', job)


def snapshot(kind, target, active):
    name = kind + '-' + uuid.uuid4().hex
    directory = home() / 'backups' / name
    directory.mkdir(parents=True, mode=0o700)
    exists = target.exists()
    metadata = dict(kind=kind, target=str(target), active=active, exists=exists, version=installed(kind)['version'])
    if exists:
        if kind == 'kernel':
            shutil.copy2(target, directory / 'content')
            stat = target.stat()
            metadata.update(uid=stat.st_uid, gid=stat.st_gid, mode=stat.st_mode & 0o777)
        else:
            shutil.copytree(target, directory / 'content')
    (directory / 'metadata.json').write_text(json.dumps(metadata))
    return directory


def atomic_binary(source, target, metadata):
    temp = target.with_name('.console-kernel-' + uuid.uuid4().hex)
    try:
        shutil.copy2(source, temp)
        os.chown(temp, metadata.get('uid', 0), metadata.get('gid', 0))
        temp.chmod(metadata.get('mode', 0o755))
        os.replace(temp, target)
    finally:
        temp.unlink(missing_ok=True)


def panel_permissions(directory):
    # systemd may use UMask=0077. Copied/extracted directories must be readable
    # by the kernel account after leaving the private staging directory.
    for path in [directory, *directory.rglob('*')]:
        if path.is_symlink():
            raise MaintenanceError('面板目录不能包含符号链接')
        shutil.chown(path, user='mihomo', group='mihomo')
        path.chmod(0o750 if path.is_dir() else 0o640)


def replace_panel(source, target):
    target.parent.mkdir(parents=True, exist_ok=True)
    old = target.with_name('.console-panel-old-' + uuid.uuid4().hex)
    try:
        if target.exists():
            os.replace(target, old)
        try:
            os.replace(source, target)
        except Exception:
            if old.exists():
                os.replace(old, target)
            raise
    finally:
        if old.exists():
            shutil.rmtree(old)


def restore(directory):
    metadata = json.loads((directory / 'metadata.json').read_text())
    kind = metadata['kind']
    target = component_path(kind)
    if str(target) != metadata['target']:
        raise MaintenanceError('维护目标已变化，拒绝覆盖其他目录')
    if kind == 'kernel':
        if metadata['active']:
            run_system('stop')
        atomic_binary(directory / 'content', target, metadata)
        if metadata['active']:
            run_system('start')
            verify_kernel(metadata['version'])
    else:
        if metadata['exists']:
            with tempfile.TemporaryDirectory(dir=target.parent, prefix='.console-restore-') as tmp:
                staged = Path(tmp) / 'panel'
                shutil.copytree(directory / 'content', staged)
                panel_permissions(staged)
                replace_panel(staged, target)
        elif target.exists():
            shutil.rmtree(target)


def backup_directory(pointer):
    name = pointer.get('snapshot', '')
    if not re.fullmatch(r'(kernel|panel)-[a-f0-9]{32}', name):
        raise MaintenanceError('备份记录无效')
    path = home() / 'backups' / name
    if not (path / 'metadata.json').is_file():
        raise MaintenanceError('备份文件不存在')
    return path


def execute(kind, action, release):
    from . import service
    with service.config_lock():
        target = component_path(kind)
        target.parent.mkdir(parents=True, exist_ok=True)
        active = service.core_active()
        with tempfile.TemporaryDirectory(dir=target.parent, prefix='.console-maintenance-') as tmp:
            folder = Path(tmp)
            staged = folder / 'content'
            if action == 'upgrade':
                progress('正在下载并校验官方发布包')
                archive = folder / 'archive'
                download(release, archive)
                if kind == 'kernel':
                    with gzip.open(archive, 'rb') as src, staged.open('wb') as dst:
                        total = 0
                        while chunk := src.read(1024 * 1024):
                            total += len(chunk)
                            if total > 256 * 1024 * 1024:
                                raise MaintenanceError('内核解压内容超过限制')
                            dst.write(chunk)
                    staged.chmod(0o755)
                    progress('正在用新内核验证当前配置')
                    validate_kernel(staged, release['version'])
                else:
                    staged.mkdir()
                    unpack_panel(archive, staged)
                    # Keep the installation's local backend configuration.
                    if (target / 'config.js').is_file():
                        shutil.copy2(target / 'config.js', staged / 'config.js')
                    (staged / '.console-release.json').write_text(json.dumps({'version': release['version']}))
            else:
                pointer = read(kind + '-backup.json')
                if not pointer:
                    raise MaintenanceError('没有可用的上一版本备份')
                previous = backup_directory(pointer)
                if kind == 'kernel':
                    shutil.copy2(previous / 'content', staged)
                    validate_kernel(staged, pointer['version'])
                else:
                    shutil.copytree(previous / 'content', staged)
            progress('正在备份当前版本')
            saved = snapshot(kind, target, active)
            metadata = json.loads((saved / 'metadata.json').read_text())
            write('recovery.json', dict(snapshot=saved.name, kind=kind))
            try:
                progress('正在切换版本并验证服务')
                if kind == 'kernel':
                    if active:
                        run_system('stop')
                    atomic_binary(staged, target, metadata)
                    if active:
                        run_system('start')
                        verify_kernel(binary_version(target))
                else:
                    panel_permissions(staged)
                    replace_panel(staged, target)
                    if active:
                        with mihomo.client() as http:
                            response = http.get('/ui/', timeout=5)
                            response.raise_for_status()
                            if response.content != (target / 'index.html').read_bytes():
                                raise MaintenanceError('面板访问验证未通过')
                write(kind + '-backup.json', dict(snapshot=saved.name, version=metadata['version']))
                (home() / 'recovery.json').unlink(missing_ok=True)
            except Exception as exc:
                progress('切换失败，正在恢复原版本')
                try:
                    restore(saved)
                    (home() / 'recovery.json').unlink(missing_ok=True)
                except Exception as recovery_error:
                    raise MaintenanceError('维护失败且自动恢复未完成；已保留备份，请检查本机服务') from recovery_error
                raise MaintenanceError('维护失败，已自动恢复原版本') from exc


def worker(kind, action, release, handle):
    try:
        execute(kind, action, release)
        job = read('job.json')
        job.update(state='success', message='升级完成' if action == 'upgrade' else '已恢复上一版本', finished_at=timestamp())
        write('job.json', job)
    except Exception as exc:
        job = read('job.json') or {}
        job.update(state='failed', message=str(exc) if isinstance(exc, MaintenanceError) else '下载、校验或备份失败；请检查网络与本机权限，原版本已保留', finished_at=timestamp())
        write('job.json', job)
    finally:
        fcntl.flock(handle, fcntl.LOCK_UN)
        handle.close()


def start(kind, action, expected=None):
    if kind not in REPOS or action not in ('upgrade', 'rollback'):
        raise MaintenanceError('未知维护操作')
    handle = (home() / 'maintenance.lock').open('a')
    try:
        fcntl.flock(handle, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except BlockingIOError:
        handle.close()
        raise MaintenanceError('已有维护任务正在运行')
    try:
        release = read(kind + '-release.json') if action == 'upgrade' else None
        if action == 'upgrade':
            if kind == 'kernel' and not installed(kind)['installed']:
                raise MaintenanceError('请先安装并配置可用的 Mihomo 内核')
            if not release or expected != release['version']:
                raise MaintenanceError('请先检查更新，再确认页面显示的版本')
            old, new = version_number(installed(kind)['version']), version_number(release['version'])
            if old and new <= old:
                raise MaintenanceError('当前已是该版本或更新版本')
        elif not read(kind + '-backup.json'):
            raise MaintenanceError('没有可用的上一版本备份')
        if read('recovery.json'):
            raise MaintenanceError('上次维护尚未恢复，请先重启控制台完成恢复')
        write('job.json', dict(id=uuid.uuid4().hex, kind=kind, action=action, state='queued', message='维护任务已排队', started_at=timestamp()))
        thread = threading.Thread(target=worker, args=(kind, action, release, handle), daemon=True)
        thread.start()
    except Exception:
        fcntl.flock(handle, fcntl.LOCK_UN)
        handle.close()
        raise
    return status()


def recover():
    """A console restart during a switch restores the journaled previous files."""
    from . import service
    with (home() / 'maintenance.lock').open('a') as handle:
        try:
            fcntl.flock(handle, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            return
        journal = read('recovery.json')
        if journal:
            try:
                with service.config_lock():
                    restore(backup_directory(journal))
                    (home() / 'recovery.json').unlink(missing_ok=True)
            except Exception as exc:
                job = read('job.json') or {'kind': journal.get('kind')}
                job.update(state='failed', message='维护中断后的自动恢复未完成；已保留备份，请检查本机服务', finished_at=timestamp())
                write('job.json', job)
                raise MaintenanceError('维护中断后的自动恢复未完成') from exc
        job = read('job.json')
        if job and job.get('state') in ACTIVE:
            job.update(state='failed', message='控制台重启中断了维护；已保留或恢复原版本，请重新检查', finished_at=timestamp())
            write('job.json', job)
