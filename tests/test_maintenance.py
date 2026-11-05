import fcntl
import gzip
import hashlib
import io
import json
import os
from pathlib import Path
import tarfile
import tempfile
import unittest
from unittest.mock import Mock, patch

import httpx
from fastapi.testclient import TestClient
from app import db, maintenance as m, service
from app.main import app


class MaintenanceTest(unittest.TestCase):
    def setUp(self):
        self.previous = db._conn
        db._conn = None
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        self.env = patch.dict(os.environ, DATA_DIR=str(self.root/'data'), MIHOMO_HOME=str(self.root/'core'))
        self.env.start()
        db.init_db()
        self.binary = m.component_path('kernel')
        self.binary.parent.mkdir(parents=True)
        self.binary.write_text('v1.0.0')
        self.binary.chmod(0o755)
        self.config = self.root/'core/config'
        self.config.mkdir()
        (self.config/'config.yaml').write_text('external-ui: uis/metacubexd\ntun:\n  enable: true\n')
        self.panel = m.component_path('panel')
        self.panel.mkdir(parents=True)
        (self.panel/'index.html').write_text('<html>old</html>')
        (self.panel/'config.js').write_text('local backend settings')
        self.versions = patch.object(m, 'binary_version', side_effect=lambda p: Path(p).read_text())
        self.versions.start()
        self.ownership = patch.object(m.shutil,'chown')
        self.ownership.start()
        self.release = dict(version='v1.1.0', url='https://example.com/release', sha256='0'*64)
        self.client = TestClient(app)
        self.client.cookies.set('mc_token',db.get_setting('web_token'))

    def tearDown(self):
        self.client.close()
        self.ownership.stop()
        self.versions.stop()
        db._conn.close()
        db._conn = self.previous
        self.env.stop()
        self.tmp.cleanup()

    def fake_download(self, release, path):
        path.write_bytes(gzip.compress(b'v1.1.0'))

    def panel_archive(self, path, entries=None):
        entries = entries or {'./index.html': b'<html>new</html>', './assets/app.js': b'console.log(1)'}
        with tarfile.open(path,'w:gz') as tar:
            for name,data in entries.items():
                member=tarfile.TarInfo(name);member.size=len(data)
                tar.addfile(member,io.BytesIO(data))

    def test_kernel_validates_before_stop_and_success_can_rollback(self):
        with patch.object(m,'download',side_effect=self.fake_download), patch.object(m,'validate_kernel') as validate, \
             patch.object(m,'run_system') as system, patch.object(m,'verify_kernel') as verify, patch.object(service,'core_active',return_value=True):
            m.execute('kernel','upgrade',self.release)
            self.assertEqual(self.binary.read_text(),'v1.1.0')
            self.assertTrue(validate.called)
            self.assertEqual([x.args for x in system.call_args_list],[('stop',),('start',)])
            verify.assert_called_once_with('v1.1.0')
            self.assertEqual(m.status()['components']['kernel']['rollback_version'],'v1.0.0')
            m.execute('kernel','rollback',None)
            self.assertEqual(self.binary.read_text(),'v1.0.0')
            self.assertEqual(m.status()['components']['kernel']['rollback_version'],'v1.1.0')
            self.assertIsNone(m.read('recovery.json'))

    def test_kernel_config_failure_never_stops_existing_service(self):
        with patch.object(m,'download',side_effect=self.fake_download), patch.object(m,'validate_kernel',side_effect=m.MaintenanceError('invalid config')), \
             patch.object(m,'run_system') as system, patch.object(service,'core_active',return_value=True):
            with self.assertRaises(m.MaintenanceError):m.execute('kernel','upgrade',self.release)
            system.assert_not_called()
        self.assertEqual(self.binary.read_text(),'v1.0.0')

    def test_kernel_startup_failure_restores_original_and_restarts(self):
        with patch.object(m,'download',side_effect=self.fake_download), patch.object(m,'validate_kernel'), \
             patch.object(m,'run_system') as system, patch.object(m,'verify_kernel',side_effect=[m.MaintenanceError('TUN mismatch'),None]), \
             patch.object(service,'core_active',return_value=True):
            with self.assertRaisesRegex(m.MaintenanceError,'已自动恢复'):m.execute('kernel','upgrade',self.release)
            self.assertEqual([x.args for x in system.call_args_list],[('stop',),('start',),('stop',),('start',)])
        self.assertEqual(self.binary.read_text(),'v1.0.0')
        self.assertIsNone(m.read('recovery.json'))

    def test_panel_preserves_config_and_rolls_back_without_kernel_restart(self):
        self.assertIsNone(m.status()['components']['panel']['version'])
        with patch.object(m,'download',side_effect=lambda r,p:self.panel_archive(p)),patch.object(service,'core_active',return_value=False),patch.object(m,'run_system') as system:
            m.execute('panel','upgrade',self.release)
            self.assertEqual((self.panel/'index.html').read_text(),'<html>new</html>')
            self.assertEqual((self.panel/'config.js').read_text(),'local backend settings')
            self.assertEqual(m.status()['components']['panel']['version'],'v1.1.0')
            self.assertTrue(m.status()['components']['panel']['rollback_available'])
            m.execute('panel','rollback',None)
            self.assertEqual((self.panel/'index.html').read_text(),'<html>old</html>')
            self.assertIsNone(m.status()['components']['panel']['version'])
            system.assert_not_called()

    def test_panel_failed_http_verification_restores_files(self):
        http=Mock();http.get.return_value=httpx.Response(502,request=httpx.Request('GET','http://core/ui/'))
        factory=Mock();factory.__enter__=Mock(return_value=http);factory.__exit__=Mock(return_value=False)
        with patch.object(m,'download',side_effect=lambda r,p:self.panel_archive(p)),patch.object(service,'core_active',return_value=True), \
             patch.object(m.mihomo,'client',return_value=factory),patch.object(m,'run_system') as system:
            with self.assertRaisesRegex(m.MaintenanceError,'已自动恢复'):m.execute('panel','upgrade',self.release)
            system.assert_not_called()
        self.assertEqual((self.panel/'index.html').read_text(),'<html>old</html>')

    def test_archive_rejects_traversal_absolute_paths_and_links(self):
        archive=self.root/'test.tgz';folder=self.root/'extracted';folder.mkdir()
        for name in ('../outside','/tmp/outside','test\\outside'):
            self.panel_archive(archive,{name:b'bad'})
            with self.assertRaises(m.MaintenanceError):m.unpack_panel(archive,folder)
        with tarfile.open(archive,'w:gz') as tar:
            member=tarfile.TarInfo('link');member.type=tarfile.SYMTYPE;member.linkname='/etc/passwd';tar.addfile(member)
        with self.assertRaises(m.MaintenanceError):m.unpack_panel(archive,folder)
        self.assertFalse((self.root/'outside').exists())

    def test_download_checksum_mismatch_leaves_installed_components_untouched(self):
        http=httpx.Client(transport=httpx.MockTransport(lambda r:httpx.Response(200,content=b'changed')))
        with patch.object(m.httpx,'Client',return_value=http):
            with self.assertRaisesRegex(m.MaintenanceError,'SHA-256'):m.download(self.release,self.root/'package')
        self.assertEqual(self.binary.read_text(),'v1.0.0')
        self.assertEqual((self.panel/'index.html').read_text(),'<html>old</html>')

    def test_official_release_selection_and_exact_version_comparison(self):
        tag='v1.1.0';name=f'mihomo-linux-amd64-v1-{tag}.gz'
        data=dict(tag_name=tag,assets=[dict(name=name,digest='sha256:'+'a'*64,browser_download_url=f'https://github.com/MetaCubeX/mihomo/releases/download/{tag}/{name}')])
        http=httpx.Client(transport=httpx.MockTransport(lambda r:httpx.Response(200,json=data)))
        with patch.object(m.httpx,'Client',return_value=http),patch.object(m.platform,'machine',return_value='x86_64'),patch.object(m.platform,'system',return_value='Linux'):
            result=m.check('kernel')
        self.assertEqual(result['components']['kernel']['latest'],tag)
        self.assertTrue(result['components']['kernel']['update_available'])
        self.assertNotEqual(m.version_number('v1.19.3'),m.version_number('v1.19.33'))
        self.assertEqual(m.asset_name('panel','v1.0.0',[{'name':'compressed-dist.tgz'}])['name'],'compressed-dist.tgz')

    def test_auth_checks_and_invalid_actions_do_not_trigger_network_or_mutation(self):
        self.client.cookies.clear()
        with patch.object(m,'check') as check,patch.object(m,'start') as start:
            self.assertEqual(self.client.get('/api/maintenance').status_code,401)
            self.assertEqual(self.client.post('/api/maintenance/kernel/check').status_code,401)
            self.assertEqual(self.client.post('/api/kernel/upgrade').status_code,401)
            check.assert_not_called();start.assert_not_called()
        self.client.cookies.set('mc_token',db.get_setting('web_token'))
        self.assertEqual(self.client.post('/api/maintenance/other/upgrade').status_code,400)
        self.assertEqual(self.client.post('/api/maintenance/panel/other').status_code,400)
        self.assertEqual(self.client.post('/api/maintenance/kernel/upgrade',json={'version':'v1.1.0'}).status_code,400)

    def test_cross_process_lock_prevents_duplicate_jobs_and_requires_reviewed_version(self):
        m.write('kernel-release.json',self.release)
        with self.assertRaises(m.MaintenanceError):m.start('kernel','upgrade','v1.2.0')
        with patch.object(m.threading,'Thread') as thread:
            m.start('kernel','upgrade','v1.1.0')
            handle=thread.call_args.kwargs['args'][-1]
            try:
                with self.assertRaisesRegex(m.MaintenanceError,'正在运行'):m.start('kernel','upgrade','v1.1.0')
            finally:
                fcntl.flock(handle,fcntl.LOCK_UN);handle.close()

    def test_restart_recovers_journaled_kernel_switch(self):
        saved=m.snapshot('kernel',self.binary,True)
        self.binary.write_text('v1.1.0')
        m.write('recovery.json',dict(snapshot=saved.name,kind='kernel'))
        m.write('job.json',dict(kind='kernel',state='running'))
        with patch.object(m,'run_system'),patch.object(m,'verify_kernel'):
            m.recover()
        self.assertEqual(self.binary.read_text(),'v1.0.0')
        self.assertIsNone(m.read('recovery.json'))
        self.assertEqual(m.read('job.json')['state'],'failed')

    def test_runtime_tun_mismatch_fails_health_verification(self):
        http=httpx.Client(transport=httpx.MockTransport(lambda r:httpx.Response(200,json={'version':'v1.1.0'} if r.url.path=='/version' else {'tun':{'enable':False}})))
        with patch.object(m.mihomo,'client',return_value=http),patch.object(m.time,'monotonic',side_effect=[0,0,26]), \
             patch.object(m.time,'sleep'),patch.object(m.router,'verify_domestic_bypass') as bypass:
            with self.assertRaises(m.MaintenanceError):m.verify_kernel('v1.1.0')
            bypass.assert_not_called()

    def test_missing_digest_and_nonofficial_asset_urls_cannot_be_checked(self):
        tag='v1.1.0';name='compressed-dist.tgz'
        for digest,url in [(None,f'https://github.com/MetaCubeX/metacubexd/releases/download/{tag}/{name}'),('sha256:'+'a'*64,'https://example.com/file')]:
            data=dict(tag_name=tag,assets=[dict(name=name,digest=digest,browser_download_url=url)])
            http=httpx.Client(transport=httpx.MockTransport(lambda r:httpx.Response(200,json=data)))
            with patch.object(m.httpx,'Client',return_value=http):
                with self.assertRaises(m.MaintenanceError):m.check('panel')
        self.assertIsNone(m.read('panel-release.json'))

    def test_interrupted_panel_switch_recovers_without_restarting_kernel(self):
        saved=m.snapshot('panel',self.panel,True)
        import shutil
        shutil.rmtree(self.panel)
        m.write('recovery.json',dict(snapshot=saved.name,kind='panel'))
        m.write('job.json',dict(kind='panel',state='running'))
        with patch.object(m,'run_system') as system:
            m.recover()
            system.assert_not_called()
        self.assertEqual((self.panel/'index.html').read_text(),'<html>old</html>')
        self.assertIsNone(m.read('recovery.json'))

    def test_download_retries_official_asset_api_after_transport_failure(self):
        payload=b'official verified archive';calls=[]
        def transport(request):
            calls.append(request.url.host)
            if request.url.host=='github.com':
                raise httpx.ConnectError('unavailable',request=request)
            return httpx.Response(200,content=payload)
        original_client=httpx.Client
        release=dict(url='https://github.com/MetaCubeX/metacubexd/releases/download/v1.1.0/compressed-dist.tgz',
                     api_url='https://api.github.com/repos/MetaCubeX/metacubexd/releases/assets/1',sha256=hashlib.sha256(payload).hexdigest())
        with patch.object(m.httpx,'Client',side_effect=lambda **kw:original_client(transport=httpx.MockTransport(transport))),patch.object(m.time,'sleep'):
            m.download(release,self.root/'archive')
        self.assertEqual((self.root/'archive').read_bytes(),payload)
        self.assertEqual(calls,['github.com','github.com','api.github.com'])

    def test_rate_limit_preserves_previous_result_and_never_claims_fresh_check(self):
        m.write('panel-release.json',dict(self.release,checked_at='2026-11-05T00:00:00Z'))
        original_client=httpx.Client
        def client(**kw):return original_client(transport=httpx.MockTransport(lambda r:httpx.Response(403,headers={'x-ratelimit-remaining':'0'},json={'message':'API rate limit exceeded'})))
        with patch.object(m.httpx,'Client',side_effect=client):
            result=m.check('panel')
            self.assertIn('额度',result['warning'])
            self.assertEqual(result['components']['panel']['checked_at'],'2026-11-05T00:00:00Z')
            self.assertEqual(result['components']['panel']['latest'],'v1.1.0')
            (m.home()/'panel-release.json').unlink()
            with self.assertRaisesRegex(m.MaintenanceError,'尚无'):m.check('panel')

    def test_failed_restart_recovery_reports_failure_and_keeps_backup_journal(self):
        saved=m.snapshot('kernel',self.binary,True)
        m.write('recovery.json',dict(snapshot=saved.name,kind='kernel'))
        m.write('job.json',dict(kind='kernel',state='running'))
        with patch.object(m,'restore',side_effect=RuntimeError('unavailable service')):
            with self.assertRaises(m.MaintenanceError):m.recover()
        self.assertEqual(m.read('job.json')['state'],'failed')
        self.assertIsNotNone(m.read('recovery.json'))
        self.assertTrue((saved/'content').exists())

    def test_panel_permissions_are_correct_under_private_systemd_umask(self):
        folder=self.root/'private-panel';folder.mkdir(mode=0o700)
        assets=folder/'assets';assets.mkdir(mode=0o700)
        script=assets/'app.js';script.write_text('app');script.chmod(0o600)
        m.panel_permissions(folder)
        self.assertEqual(folder.stat().st_mode & 0o777,0o750)
        self.assertEqual(assets.stat().st_mode & 0o777,0o750)
        self.assertEqual(script.stat().st_mode & 0o777,0o640)
        m.shutil.chown.assert_any_call(folder,user='mihomo',group='mihomo')
        m.shutil.chown.assert_any_call(script,user='mihomo',group='mihomo')

    def test_verified_download_cache_is_reused_and_corruption_is_rejected(self):
        payload=b'verified archive';release=dict(self.release,sha256=hashlib.sha256(payload).hexdigest())
        cache=m.home()/'assets'/(release['sha256']+'.archive');cache.parent.mkdir();cache.write_bytes(payload)
        with patch.object(m,'download_once') as fetch:
            m.download(release,self.root/'cached-package')
            fetch.assert_not_called()
        self.assertEqual((self.root/'cached-package').read_bytes(),payload)
        cache.write_bytes(b'corrupted')
        with patch.object(m,'download_once',side_effect=m.MaintenanceError('checksum failure')):
            with self.assertRaises(m.MaintenanceError):m.download(release,self.root/'bad-package')
        self.assertFalse(cache.exists())
