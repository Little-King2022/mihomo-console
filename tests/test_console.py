import json
import logging
import os
import tempfile
import unittest
from unittest.mock import Mock, patch

os.environ['DATA_DIR'] = tempfile.mkdtemp(prefix='mihomo-console-tests-')
from app import db, service
from app.main import app, TokenAccessFilter
from app.render import render_config
from fastapi.testclient import TestClient


class ConsoleTest(unittest.TestCase):
    def setUp(self):
        self.previous = db._conn
        db._conn = None
        self.directory = tempfile.TemporaryDirectory()
        self.env = patch.dict(os.environ, DATA_DIR=self.directory.name)
        self.env.start()
        db.init_db()
        self.client = TestClient(app)
        self.client.cookies.set('mc_token', db.get_setting('web_token'))

    def tearDown(self):
        self.client.close()
        db._conn.close()
        db._conn = self.previous
        self.env.stop()
        self.directory.cleanup()

    def test_fresh_install_has_no_subscription_and_preserves_user_data(self):
        self.assertEqual(db.list_subscriptions(), [])
        self.assertTrue(all(g['selected_nodes'] == [] for g in db.list_groups()))
        rendered = render_config([], db.list_groups(), [], {}, db.list_rules())
        self.assertEqual(rendered.count('  empty-fallback: REJECT'), 2)
        db.add_subscription('User subscription', 'https://subscription.example.com/sub', 6, True)
        before = db.list_subscriptions()
        db.init_db()
        self.assertEqual(db.list_subscriptions(), before)
        db.delete_subscription(before[0]['id'])
        db.init_db()
        self.assertEqual(db.list_subscriptions(), [])

    def test_migration_idempotent_and_rule_rename_priority(self):
        rules = db.list_rules()
        self.assertEqual(len(rules), 1)
        db.init_db()
        self.assertEqual(db.list_rules(), rules)
        group = db.get_group(rules[0]['group_id'])
        db.update_group(group['id'], {'name': '新家宽出口'})
        regular = next(g for g in db.list_groups() if g['is_default'])
        db.save_rule(dict(name='Google', domains='google.com', rule_sets='', group_id=regular['id'], priority=30, enabled=1))
        text = render_config(db.list_subscriptions(), db.list_groups(), [], {}, db.list_rules())
        self.assertIn('DOMAIN-SUFFIX,chatgpt.com,新家宽出口', text)
        self.assertLess(text.index('DOMAIN-SUFFIX,gemini.google.com,新家宽出口'), text.index('DOMAIN-SUFFIX,google.com,境外常规'))
        self.assertIn('MATCH,境外常规', text)
        self.assertIn("'+.littleking.site'", text)
        self.assertIn("'+.njau.edu.cn'", text)
        self.assertIn('"auth-6441.wifi.com": 10.64.0.134', text)
        self.assertIn('    - 10.0.0.0/8', text)
        self.assertEqual(text.count('  proxies: []'), 2)

    def test_zero_priority_and_disabled_rule(self):
        groups = db.list_groups()
        default = next(g for g in groups if g['is_default'])
        db.save_rule(dict(name='First', domains='first.example', rule_sets='', group_id=default['id'], priority=0, enabled=1))
        db.save_rule(dict(name='Off', domains='off.example', rule_sets='', group_id=default['id'], priority=1, enabled=0))
        text = render_config(db.list_subscriptions(), groups, [], {}, db.list_rules())
        self.assertLess(text.index('DOMAIN-SUFFIX,first.example'), text.index('DOMAIN-SUFFIX,chatgpt.com'))
        self.assertNotIn('DOMAIN-SUFFIX,off.example', text)

    def test_rule_crud_validation_and_rollback(self):
        target = next(g for g in db.list_groups() if g['is_default'])['id']
        payload = dict(name='Test', domains='example.com', group_id=target, priority=30)
        with patch.object(service, 'apply_config'):
            r = self.client.post('/api/rules', json=payload)
            self.assertEqual(r.status_code, 200, r.text)
            rule_id = r.json()['id']
            self.assertEqual(self.client.put(f'/api/rules/{rule_id}', json={'domains':'https://invalid.test/path'}).status_code, 400)
            self.assertEqual(self.client.put(f'/api/rules/{rule_id}', json={'domains':'example.com,REJECT'}).status_code, 400)
            self.assertEqual(self.client.put(f'/api/rules/{rule_id}', json={'group_id':9999}).status_code, 400)
            self.assertEqual(self.client.delete(f'/api/groups/{target}').status_code, 400)
        with patch.object(service, 'apply_config', side_effect=RuntimeError('invalid config')):
            r = self.client.put(f'/api/rules/{rule_id}', json={'domains':'changed.test'})
            self.assertEqual(r.status_code, 400)
            self.assertEqual(db.get_rule(rule_id)['domains'], 'example.com')
        with patch.object(service, 'apply_config'):
            self.assertEqual(self.client.delete(f'/api/rules/{rule_id}').status_code, 200)
        self.assertIsNone(db.get_rule(rule_id))

    def test_new_rules_do_not_reappear_from_legacy_groups(self):
        rule = db.list_rules()[0]
        db.delete_rule(rule['id'])
        db.init_db()
        text = render_config(db.list_subscriptions(), db.list_groups(), [], {}, db.list_rules())
        self.assertNotIn('DOMAIN-SUFFIX,chatgpt.com', text)
        self.assertEqual(db.list_rules(), [])

    def test_health_dedup_bounded_and_empty_replacement(self):
        row = dict(node_name='A', delay=100, alive=1, history=[{'time':f'2026-10-17T00:{i:02}:00Z','delay':i} for i in range(30)])
        db.upsert_node_health([row])
        db.upsert_node_health([row])
        history = db.list_node_health()[0]['history']
        self.assertEqual(len(history), 24)
        self.assertEqual(history[0]['delay'], 6)
        db.upsert_node_health([])
        self.assertEqual(db.list_node_health(), [])

    def test_config_lock_serializes_processes_and_is_reentrant(self):
        import subprocess
        import sys
        script = "from app import service; print('ready', flush=True);\nwith service.config_lock(): print('acquired', flush=True)"
        with service.config_lock(), service.config_lock():
            process = subprocess.Popen([sys.executable, '-c', script], stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
            self.assertEqual(process.stdout.readline().strip(), 'ready')
            with self.assertRaises(subprocess.TimeoutExpired):
                process.communicate(timeout=.15)
        stdout, stderr = process.communicate(timeout=5)
        self.assertEqual(process.returncode, 0, stderr)
        self.assertIn('acquired', stdout)

    def test_endpoints_require_auth(self):
        self.client.cookies.clear()
        self.assertEqual(self.client.post('/api/groups/1/diagnostics/ip').status_code, 401)
        self.assertEqual(self.client.post('/api/groups/1/diagnostics/purity').status_code, 401)
        for path in ('/api/telemetry', '/api/rules', '/api/groups', '/api/nodes'):
            self.assertEqual(self.client.get(path).status_code, 401)

    def test_link_login_uses_existing_cookie_authentication(self):
        self.client.cookies.clear()
        self.assertEqual(self.client.get('/api/session').status_code, 401)
        token = db.get_setting('web_token')
        response = self.client.post('/api/login', json={'token': token})
        self.assertEqual(response.status_code, 200)
        cookie = response.headers['set-cookie']
        self.assertIn('HttpOnly', cookie)
        self.assertIn('SameSite=lax', cookie)
        self.assertEqual(self.client.get('/api/session').status_code, 204)
        self.client.post('/api/logout')
        for token in ('invalid', ''):
            response = self.client.post('/api/login', json={'token': token})
            self.assertEqual(response.status_code, 401)
            self.assertNotIn('set-cookie', response.headers)
            self.assertEqual(self.client.get('/api/session').status_code, 401)
        # Query credentials are consumed by the page, never accepted by API endpoints.
        self.assertEqual(self.client.get('/api/session?token=invalid').status_code, 401)

    def test_access_logs_redact_link_tokens_and_keep_other_fields(self):
        from uvicorn.logging import AccessFormatter
        access_filter = TokenAccessFilter()
        for path in ('/?token=example-secret&keep=1',
                     '/?keep=1&to%6ben=example-secret&token=another-secret'):
            record = logging.LogRecord('uvicorn.access', logging.INFO, '', 0,
                                       '%s - "%s %s HTTP/%s" %d',
                                       ('127.0.0.1:1234', 'GET', path, '1.1', 200), None)
            self.assertTrue(access_filter.filter(record))
            formatted = AccessFormatter('%(request_line)s %(status_code)s', use_colors=False).format(record)
            self.assertNotIn('example-secret', formatted)
            self.assertNotIn('another-secret', formatted)
            self.assertIn('[redacted]', formatted)
            self.assertIn('keep=1', formatted)
            self.assertIn('200', formatted)

    def test_group_node_migration_preserves_members_and_is_idempotent(self):
        # Legacy installations had filter-based groups with no explicit selection.
        db._execute('UPDATE groups SET selected_nodes=NULL')
        catalog = [dict(provider='sub1', name=name, node_name=name) for name in
                   ('台湾 hinet 家宽 A', '台湾 hinet 家宽 B', '美国 A', '剩余流量')]
        with patch.object(service, 'apply_config'):
            service.migrate_group_nodes(catalog)
            groups = db.list_groups()
            home = next(g for g in groups if g['name'] == '台湾家宽')
            regular = next(g for g in groups if g['is_default'])
            self.assertEqual([n['name'] for n in home['selected_nodes']], ['台湾 hinet 家宽 A', '台湾 hinet 家宽 B'])
            self.assertEqual([n['name'] for n in regular['selected_nodes']], ['美国 A'])
            self.assertEqual(home['node_filter'], '')
            self.assertEqual(home['exclude_filter'], '')
            service.migrate_group_nodes(catalog + [dict(provider='sub1', name='美国 B', node_name='美国 B')])
        self.assertEqual(db.list_groups(), groups)

    def test_group_selection_api_and_rollback(self):
        catalog = [dict(provider='sub1', name='node.one'), dict(provider='sub2', name='node.one')]
        selected = [dict(provider='sub1', name='node.one')]
        with patch.object(service, 'node_catalog', return_value=catalog), patch.object(service, 'apply_config'):
            r = self.client.post('/api/groups', json=dict(name='Selected', selected_nodes=selected))
            self.assertEqual(r.status_code, 200, r.text)
            group_id = r.json()['id']
            self.assertEqual(r.json()['selected_nodes'], selected)
            for body in ({'selected_nodes': []}, {'selected_nodes': [{'provider':'sub1','name':'unknown'}]}, {'node_filter':'.*'}):
                self.assertEqual(self.client.put(f'/api/groups/{group_id}', json=body).status_code, 400)
            second = [dict(provider='sub2', name='node.one')]
            self.assertEqual(self.client.put(f'/api/groups/{group_id}', json={'selected_nodes':second}).status_code, 200)
        with patch.object(service, 'node_catalog', return_value=catalog), patch.object(service, 'apply_config', side_effect=RuntimeError('invalid')):
            self.assertEqual(self.client.put(f'/api/groups/{group_id}', json={'selected_nodes':selected}).status_code, 400)
        self.assertEqual(db.get_group(group_id)['selected_nodes'], second)

    def test_rename_migration_keeps_rule_ids_and_runs_once(self):
        home = next(g for g in db.list_groups() if g['name']=='台湾家宽')
        original = db.list_rules()
        db.update_group(home['id'], {'name':'AI-台湾家宽'})
        db._execute("DELETE FROM settings WHERE key='taiwan_group_renamed'")
        db.init_db()
        self.assertEqual(db.get_group(home['id'])['name'], '台湾家宽')
        self.assertEqual(db.list_rules(), original)
        db.update_group(home['id'], {'name':'我自己的家宽'})
        db.init_db()
        self.assertEqual(db.get_group(home['id'])['name'], '我自己的家宽')

    def test_catalog_uses_provider_and_original_name_across_prefix_changes(self):
        one = [{'id':1,'name':'One','enabled':1}]
        two = one + [{'id':2,'name':'Two','enabled':1}]
        def payload(name):
            return {'providers':{'sub1':{'proxies':[{'name':name,'type':'Shadowsocks','history':[{'time':'2026-10-17T00:00:00Z','delay':88}]}]}}}
        with patch.object(db, 'list_subscriptions', return_value=one):
            original = service.node_catalog(payload('A|B (test)'))[0]
        with patch.object(db, 'list_subscriptions', return_value=two):
            prefixed = service.node_catalog(payload('s1-A|B (test)'))[0]
        self.assertEqual((original['provider'], original['name']), (prefixed['provider'], prefixed['name']))
        self.assertEqual(prefixed['delay'], 88)
        self.assertTrue(prefixed['alive'])

    def test_group_domains_include_cached_rules_and_disabled_rules(self):
        from pathlib import Path
        folder = Path(self.directory.name) / 'ruleset'
        folder.mkdir()
        (folder / 'openai.yaml').write_text('payload:\n  - DOMAIN,api.example.com\n  - DOMAIN-SUFFIX,example.net\n  - IP-CIDR,192.0.2.0/24\n')
        rule = db.list_rules()[0]
        db.save_rule(dict(name='Off', domains='disabled.example', rule_sets='', group_id=rule['group_id'], priority=30, enabled=0))
        with patch.object(service, 'config_dir', return_value=Path(self.directory.name)):
            details = service.group_routing_details()[rule['group_id']]
        values = {d['value'] for d in details[0]['domains']}
        self.assertIn('api.example.com', values)
        self.assertIn('example.net', values)
        self.assertIn('chatgpt.com', values)
        self.assertNotIn('192.0.2.0/24', values)
        self.assertIn('claude', details[0]['pending_sets'])
        self.assertFalse(details[1]['enabled'])
        self.assertEqual(details[1]['domains'][0]['value'], 'disabled.example')

    def test_frontend_modules_are_served_as_javascript_and_revalidated(self):
        for path in ('/', '/static/app.js', '/static/js/core/api.js', '/static/js/pages/nodes.js'):
            response = self.client.get(path)
            self.assertEqual(response.status_code, 200)
            self.assertEqual(response.headers.get('cache-control'), 'no-cache')
            if path.endswith('.js'):
                self.assertIn('javascript', response.headers['content-type'])
            etag = response.headers.get('etag')
            conditional = self.client.get(path, headers={'If-None-Match': etag})
            # Static modules support conditional requests while still requiring revalidation.
            self.assertIn(conditional.status_code, (200, 304))
            self.assertEqual(conditional.headers.get('cache-control'), 'no-cache')
        self.assertIn('type="module"', self.client.get('/').text)

    def test_proxy_listener_details_exclude_controller_credentials(self):
        with patch.object(service, 'client') as factory:
            response = factory.return_value.__enter__.return_value.get.return_value
            response.json.return_value = {'mixed-port': 7890, 'port': 0, 'socks-port': 0,
                                          'allow-lan': False, 'bind-address': '*',
                                          'secret': 'never-expose-this', 'external-controller': 'private-controller'}
            details = service.proxy_access()
        self.assertEqual(details, {'available': True, 'mixed_port': 7890, 'http_port': 0,
                                   'socks_port': 0, 'allow_lan': False, 'bind_address': '*'})
        self.assertNotIn('never-expose-this', json.dumps(details))
        import httpx
        with patch.object(service, 'client', side_effect=httpx.ConnectError('offline')):
            self.assertEqual(service.proxy_access(), {'available': False})

    def test_telemetry_rates_reset_and_disconnect(self):
        service._stats_last = None
        service._stats_cache = None
        service._stats_samples.clear()
        response=Mock()
        response.json.side_effect=[{'uploadTotal':100,'downloadTotal':500,'memory':512,'connections':[]},
                                   {'uploadTotal':200,'downloadTotal':900,'memory':512,'connections':[]},
                                   {'uploadTotal':1,'downloadTotal':2,'connections':[]}]
        http=Mock()
        http.get.return_value=response
        context=Mock()
        context.__enter__=Mock(return_value=http)
        context.__exit__=Mock(return_value=False)
        with patch.object(service,'client',return_value=context), patch.object(service.time,'monotonic',side_effect=[10,12,14]):
            self.assertIsNone(service.telemetry()['download'])
            t=service.telemetry()
            self.assertEqual(t['upload'], 50)
            self.assertEqual(t['download'], 200)
            self.assertIsNone(service.telemetry()['download'])
        import httpx
        with patch.object(service,'client',side_effect=httpx.ConnectError('offline')):
            service._stats_last=None
            self.assertFalse(service.telemetry()['online'])


if __name__ == '__main__':
    unittest.main()
