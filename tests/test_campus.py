import json
import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import yaml
from fastapi.testclient import TestClient

from app import campus, db, service
from app.campus_config import LEGACY_EXCLUDES, LEGACY_FILTERS, clean_entry, load_config

AUTH_DNS = "10.64.0.134"
from app.main import app
from app.render import render_config


class CampusTest(unittest.TestCase):
    def setUp(self):
        self.previous = db._conn
        db._conn = None
        self.directory = tempfile.TemporaryDirectory()
        self.env = patch.dict(os.environ, DATA_DIR=self.directory.name,
                              MIHOMO_HOME=self.directory.name + '/core')
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

    def test_authenticated_crud_normalization_and_rollback(self):
        with patch.object(service, 'apply_config') as apply:
            r = self.client.post('/api/campus/networks', json=dict(name='Library', cidr='192.0.2.19/24'))
            self.assertEqual(r.status_code, 200, r.text)
            entry = r.json()
            self.assertEqual(entry['cidr'], '192.0.2.0/24')
            r = self.client.put('/api/campus/networks/' + entry['id'], json=dict(enabled=False))
            self.assertFalse(r.json()['enabled'])
            self.assertEqual(apply.call_count, 2)
            self.assertEqual(self.client.post('/api/campus/networks', json=dict(name='Duplicate', cidr='192.0.2.0/24')).status_code, 400)
        before = campus.config()
        with patch.object(service, 'apply_config', side_effect=RuntimeError('secret-bearing validation output')):
            r = self.client.put('/api/campus/networks/' + entry['id'], json=dict(enabled=True))
            self.assertEqual(r.status_code, 400)
            self.assertNotIn('secret-bearing', r.text)
            self.assertEqual(campus.config(), before)
            r = self.client.delete('/api/campus/networks/' + entry['id'])
            self.assertEqual(r.status_code, 400)
            self.assertEqual(campus.config(), before)
        with patch.object(service, 'apply_config'):
            self.assertEqual(self.client.delete('/api/campus/networks/' + entry['id']).status_code, 200)
            self.assertEqual(self.client.delete('/api/campus/networks/' + entry['id']).status_code, 404)
            r = self.client.post('/api/campus/dns', json=dict(name='DNS', domain='+.LIBRARY.EXAMPLE.', server='10.1.2.3'))
            self.assertEqual(r.status_code, 200, r.text)
            self.assertEqual(r.json()['domain'], '+.library.example')
        self.client.cookies.clear()
        with patch.object(campus, 'diagnose') as check, patch.object(campus, 'status') as status:
            for method, url, body in [('GET', '/api/campus', None), ('POST', '/api/campus/networks', {}),
                                      ('PUT', '/api/campus/dns/id', {}), ('DELETE', '/api/campus/dns/id', None),
                                      ('POST', '/api/campus/diagnostics', {})]:
                self.assertEqual(self.client.request(method, url, json=body).status_code, 401)
            check.assert_not_called()
            status.assert_not_called()

    def test_reject_invalid_values_and_allow_editing_original_entries(self):
        for value in ['0.0.0.0/0', '128.0.0.0/1', '198.18.0.0/16', '::1', '10.2.3.4;id']:
            with self.subTest(value=value), self.assertRaises(ValueError):
                clean_entry('networks', dict(name='Bad', cidr=value), [])
        for domain in ['bad.example,DIRECT', 'https://bad.example', '-bad.example']:
            with self.subTest(domain=domain), self.assertRaises(ValueError):
                clean_entry('dns', dict(name='Bad', domain=domain, server=AUTH_DNS), [])
        for cidr in LEGACY_EXCLUDES:
            self.assertEqual(clean_entry('networks', dict(name='Editable', cidr=cidr), [])['cidr'], cidr)
        for domain in LEGACY_FILTERS:
            self.assertEqual(clean_entry('dns', dict(name='Editable', domain=domain, server=''), [])['domain'], domain)
        for server in ['127.0.0.1', '0.0.0.0', '224.0.0.1', '198.18.0.2', '::1', 'https://dns.example']:
            with self.subTest(server=server), self.assertRaises(ValueError):
                clean_entry('dns', dict(name='Bad', domain='library.example', server=server), [])

    def test_render_preserves_campus_constraints_and_disable(self):
        data = dict(networks=[dict(id='a', name='Library', cidr='192.0.2.0/24', enabled=True)],
                    dns=[dict(id='b', name='Library DNS', domain='+.library.example', server='203.0.113.53', enabled=True)])
        def render():
            return render_config([], db.list_groups(), [], {'campus_config': json.dumps(data)}, db.list_rules())
        result = yaml.safe_load(render())
        self.assertEqual(result['tun']['route-exclude-address'], [*LEGACY_EXCLUDES, '192.0.2.0/24', '203.0.113.53/32'])
        self.assertEqual(result['dns']['fake-ip-filter'], [*LEGACY_FILTERS, '+.library.example'])
        self.assertEqual(result['dns']['nameserver-policy']['auth-6441.wifi.com'], AUTH_DNS)
        self.assertEqual(result['dns']['nameserver-policy']['+.library.example'], '203.0.113.53')
        self.assertFalse(result['allow-lan'])
        self.assertEqual(result['external-controller'], '127.0.0.1:19090')
        rules = result['rules']
        self.assertLess(rules.index('IP-CIDR,192.0.2.0/24,DIRECT,no-resolve'), rules.index('RULE-SET,reject,REJECT'))
        self.assertIn('DOMAIN-SUFFIX,library.example,DIRECT', rules)
        for kind in data:
            data[kind][0]['enabled'] = False
        result = yaml.safe_load(render())
        self.assertEqual(result['tun']['route-exclude-address'], list(LEGACY_EXCLUDES))
        self.assertEqual(result['dns']['fake-ip-filter'], list(LEGACY_FILTERS))
        self.assertEqual(result['dns']['nameserver-policy'], {'auth-6441.wifi.com': AUTH_DNS})
        self.assertNotIn('192.0.2.0/24', render())

    def test_migration_is_persistent_and_preserves_existing_custom_entries(self):
        old = dict(networks=[dict(id='custom', name='Custom', cidr='192.0.2.0/24', enabled=True)],
                   dns=[dict(id='custom-dns', name='DNS', domain='library.example', server='10.2.3.4', enabled=False)])
        db.set_setting('campus_config', json.dumps(old))
        db.init_db()
        migrated = campus.config()
        self.assertEqual(migrated['schema_version'], 2)
        self.assertEqual([n['cidr'] for n in migrated['networks'][:7]], list(LEGACY_EXCLUDES))
        self.assertEqual([d['domain'] for d in migrated['dns'][:6]], list(LEGACY_FILTERS))
        self.assertTrue(all(not n['direct'] for n in migrated['networks'][:7]))
        self.assertTrue(migrated['networks'][-1]['direct'])
        self.assertFalse(migrated['dns'][-1]['enabled'])
        before = db.get_setting('campus_config')
        db.init_db()
        self.assertEqual(before, db.get_setting('campus_config'))

    def test_delete_all_original_entries_stays_empty_after_restart_and_render(self):
        original = campus.config()
        with patch.object(service, 'apply_config'):
            for kind in ('networks', 'dns'):
                for entry in original[kind]:
                    r = self.client.delete('/api/campus/' + kind + '/' + entry['id'])
                    self.assertEqual(r.status_code, 200, r.text)
        empty = dict(schema_version=2, networks=[], dns=[])
        self.assertEqual(campus.config(), empty)
        db.init_db()
        self.assertEqual(campus.config(), empty)
        result = yaml.safe_load(render_config([], db.list_groups(), [], {'campus_config': db.get_setting('campus_config')}))
        self.assertEqual(result['tun']['route-exclude-address'], [])
        self.assertEqual(result['dns']['fake-ip-filter'], [])
        self.assertEqual(result['dns']['nameserver-policy'], {})
        with self.assertRaises(ValueError):
            campus.allowed_address(AUTH_DNS, empty)
        with patch.object(campus, 'route_to') as route:
            with self.assertRaises(ValueError):
                campus.diagnose({'kind': 'dns'})
            route.assert_not_called()

    def test_edit_original_auth_dns_and_real_ip_filter(self):
        data = campus.config()
        auth = next(d for d in data['dns'] if d['domain'] == 'auth-6441.wifi.com')
        lan = next(d for d in data['dns'] if d['domain'] == '+.lan')
        network = next(n for n in data['networks'] if n['cidr'] == '127.0.0.0/8')
        with patch.object(service, 'apply_config'):
            r = self.client.put('/api/campus/dns/' + auth['id'], json={'domain': 'login.example', 'server': '10.2.3.53'})
            self.assertEqual(r.status_code, 200, r.text)
            self.assertFalse(r.json()['direct'])
            self.assertEqual(self.client.put('/api/campus/dns/' + lan['id'], json={'enabled': False}).status_code, 200)
            self.assertEqual(self.client.put('/api/campus/networks/' + network['id'], json={'cidr': '127.0.0.1/32'}).status_code, 200)
        result = yaml.safe_load(render_config([], db.list_groups(), [], {'campus_config': db.get_setting('campus_config')}))
        self.assertEqual(result['dns']['nameserver-policy'], {'login.example': '10.2.3.53'})
        self.assertNotIn('auth-6441.wifi.com', result['dns']['fake-ip-filter'])
        self.assertNotIn('+.lan', result['dns']['fake-ip-filter'])
        self.assertIn('login.example', result['dns']['fake-ip-filter'])
        self.assertIn('127.0.0.1/32', result['tun']['route-exclude-address'])
        self.assertNotIn('127.0.0.0/8', result['tun']['route-exclude-address'])
        self.assertNotIn('DOMAIN,login.example,DIRECT', result['rules'])

    def test_disabled_exclusion_does_not_override_enabled_dns_dependency(self):
        data = load_config()
        for entry in data['networks']:
            entry['enabled'] = False
        result = yaml.safe_load(render_config([], db.list_groups(), [], {'campus_config': json.dumps(data)}))
        self.assertEqual(result['tun']['route-exclude-address'], [AUTH_DNS + '/32'])
        for entry in data['dns']:
            entry['enabled'] = False
        result = yaml.safe_load(render_config([], db.list_groups(), [], {'campus_config': json.dumps(data)}))
        self.assertEqual(result['tun']['route-exclude-address'], [])
        self.assertEqual(result['dns']['fake-ip-filter'], [])

    def test_actual_validation_failure_preserves_file_and_database(self):
        # Exercise apply_config, not a mock of it. Kernel self-test never starts TUN.
        path = Path(self.directory.name) / 'core/config/config.yaml'
        path.parent.mkdir(parents=True)
        path.write_text('original configuration')
        binary = Path(self.directory.name) / 'core/mihomo'
        binary.write_text('#!/bin/sh\nexit 1\n')
        binary.chmod(0o700)
        before = campus.config()
        r = self.client.post('/api/campus/networks', json=dict(name='Library', cidr='192.0.2.0/24'))
        self.assertEqual(r.status_code, 400)
        self.assertEqual(campus.config(), before)
        self.assertEqual(path.read_text(), 'original configuration')
        self.assertFalse(path.with_suffix('.yaml.tmp').exists())

    def test_status_is_passive_and_detects_stale_generated_config(self):
        path = Path(self.directory.name) / 'core/config/config.yaml'
        path.parent.mkdir(parents=True)
        path.write_text(render_config([], db.list_groups(), [], {}))
        with patch.object(campus, 'system_status', return_value={}), patch.object(campus, 'diagnose') as probe:
            self.assertTrue(self.client.get('/api/campus').json()['config_matches'])
            db.set_setting('campus_config', json.dumps(dict(networks=[dict(id='a', cidr='192.0.2.0/24', enabled=True)], dns=[])))
            self.assertFalse(self.client.get('/api/campus').json()['config_matches'])
            probe.assert_not_called()

    def test_system_links_retain_type_and_filter_ipv6(self):
        routes = [{'dst': 'default', 'dev': 'eth0', 'gateway': '10.1.1.1', 'metric': 100}]
        links = [{'ifname': 'eth0', 'operstate': 'UP', 'link_type': 'ether', 'addr_info': [
            {'family': 'inet', 'local': '10.1.1.2'}, {'family': 'inet6', 'local': 'fe80::1'}]}]
        with patch.object(campus, 'command', side_effect=[json.dumps(routes), json.dumps(links), AUTH_DNS]) as command:
            result = campus.system_status()
            self.assertIsNone(result['error'])
            self.assertEqual(result['interfaces'][0]['type'], 'ether')
            self.assertEqual(result['interfaces'][0]['addresses'], ['10.1.1.2'])
            self.assertIn(unittest.mock.call(['ip', '-j', 'address', 'show']), command.call_args_list)

    def test_diagnostics_reject_tunnel_route_and_unknown_targets(self):
        physical = dict(name='eth0', state='UP', type='ether', addresses=['10.1.1.2'])
        route = dict(interface='eth0', source='10.1.1.2', gateway='10.1.1.1', table='main', type='unicast')
        with patch.object(campus, 'system_status', return_value={'interfaces': [physical]}), \
             patch.object(campus, 'route_to', return_value=route) as lookup, \
             patch.object(campus, 'command') as command, patch.object(campus.socket, 'create_connection') as tcp:
            result = campus.diagnose(dict(kind='route', target=AUTH_DNS))
            self.assertTrue(result['ok'])
            command.assert_not_called()
            with self.assertRaises(ValueError):
                campus.diagnose(dict(kind='tcp', target='8.8.8.8'))
            for bad in [dict(interface='Meta', table=2022), dict(source='198.18.0.1'), dict(type='local'), dict(table=2022)]:
                lookup.return_value = {**route, **bad}
                with self.assertRaises(RuntimeError):
                    campus.diagnose(dict(kind='tcp', target=AUTH_DNS, port=53))
            tcp.assert_not_called()
            lookup.return_value = route
            self.assertTrue(campus.diagnose(dict(kind='tcp', target=AUTH_DNS, port=53))['ok'])
            tcp.assert_called_once_with((AUTH_DNS, 53), timeout=3, source_address=('10.1.1.2', 0))

    def test_dns_uses_configured_server_and_real_records_only(self):
        route = dict(interface='eth0', source='10.1.1.2', gateway='10.1.1.1', table='main', type='unicast')
        answer = ';; ->>HEADER<<- opcode: QUERY, status: NOERROR, id: 1\nauth-6441.wifi.com. 60 IN A 10.1.1.4\n;; SERVER: 10.64.0.134#53(10.64.0.134) (UDP)\n'
        with patch.object(campus, 'verified_route', return_value=route) as verify, \
             patch.object(campus, 'command', return_value=answer) as command:
            r = campus.diagnose(dict(kind='dns'))
            self.assertTrue(r['ok'])
            self.assertEqual(r['answers'], ['10.1.1.4'])
            args = command.call_args.args[0]
            self.assertEqual(args[:5], ['dig', '-4', '-b', '10.1.1.2', '@' + AUTH_DNS])
            self.assertEqual(verify.call_args.args[2:], (53, 'udp'))
            for output in [';; status: NXDOMAIN', '', answer.replace('10.64.0.134#53', '127.0.0.1#53')]:
                command.return_value = output
                self.assertFalse(campus.diagnose(dict(kind='dns'))['ok'])
            command.return_value = answer.replace('status: NOERROR', 'status: NXDOMAIN')
            failure = campus.diagnose(dict(kind='dns'))
            self.assertFalse(failure['ok'])
            self.assertTrue(failure['dns_reachable'])
            self.assertEqual(failure['dns_status'], 'NXDOMAIN')
            self.assertIn('NXDOMAIN', failure['message'])
            with self.assertRaises(ValueError):
                campus.diagnose(dict(kind='dns', domain='unknown.example'))
        self.assertTrue(campus._probe_lock.acquire(blocking=False))
        try:
            with self.assertRaises(ValueError):
                campus.diagnose(dict(kind='route'))
        finally:
            campus._probe_lock.release()

    def test_ping_failure_is_not_reported_as_latency(self):
        route = dict(interface='eth0', source='10.1.1.2', gateway='10.1.1.1', table='main', type='unicast')
        with patch.object(campus, 'verified_route', return_value=route), \
             patch.object(campus, 'command', side_effect=RuntimeError('Ping 超时')):
            result = campus.diagnose(dict(kind='ping'))
            self.assertFalse(result['ok'])
            self.assertIsNone(result['latency_ms'])


if __name__ == '__main__':
    unittest.main()
