import copy
import json
import os
from pathlib import Path
import subprocess
import tempfile
import unittest
from unittest.mock import patch

import yaml
from fastapi.testclient import TestClient
from app import db, router, service
from app.main import app, clean_rule
from app.render import render_config
from app.router_config import clean_config, render_nft


def example():
    return dict(version=1, domestic_bypass=True, mtu=1500,
                lan=dict(interface='br0', connection='bridge', ports=['eth1', 'eth2'], address='192.168.8.1/24',
                         dhcp_start='192.168.8.50', dhcp_end='192.168.8.200', lease_hours=12, reservations=[]),
                uplinks=[dict(name='Default', interface='wlan0', connection='00000000-0000-0000-0000-000000000001', default=True),
                         dict(name='Campus', interface='eth3', connection='00000000-0000-0000-0000-000000000002', default=False)],
                routes=[dict(id='route1', destination='10.0.0.0/8', gateway='10.1.0.1', interface='eth3', metric=500, enabled=True, bypass=True)],
                masquerade=[dict(id='nat1', source='192.168.8.0/24', destination='0.0.0.0/0', interface='eth3', enabled=True)],
                forwards=[dict(id='https-demo', interface='eth3', protocol='both', port=24443, target_ip='192.168.8.25', target_port=443, enabled=True)],
                host_dns=['223.5.5.5'])


class RouterTest(unittest.TestCase):
    def setUp(self):
        self.previous = db._conn
        db._conn = None
        self.tmp = tempfile.TemporaryDirectory()
        self.env = patch.dict(os.environ, DATA_DIR=self.tmp.name, MIHOMO_HOME=self.tmp.name + '/core', ROUTER_MANAGEMENT='1')
        self.env.start()
        db.init_db()
        db.set_setting('router_config', json.dumps(example()))
        self.client = TestClient(app)
        self.client.cookies.set('mc_token', db.get_setting('web_token'))

    def tearDown(self):
        self.client.close()
        db._conn.close()
        db._conn = self.previous
        self.env.stop()
        self.tmp.cleanup()

    def test_renderer_real_domestic_dns_and_service_overrides(self):
        group = dict(id=1, name='AI', enabled=1, is_default=1, strategy='select', selected_nodes=[])
        rules = [dict(id=1, enabled=1, group_id=1, priority=10, domains='DOMAIN-KEYWORD,claude\nDOMAIN,cdn.example.com', rule_sets='')]
        campus = dict(schema_version=2, networks=[], dns=[dict(id='dns', name='Campus', domain='+.example.edu.cn',
                      enabled=True, direct=True, server='10.1.0.53 10.1.0.54')])
        c = yaml.safe_load(render_config([], [group], [], dict(router_config=json.dumps(example()), campus_config=json.dumps(campus)), rules))
        self.assertTrue(c['tun']['auto-route'])
        self.assertTrue(c['tun']['auto-redirect'])
        self.assertIn('cncidr', c['tun']['route-exclude-address-set'])
        self.assertIn('10.0.0.0/8', c['tun']['route-exclude-address'])
        self.assertEqual(c['dns']['nameserver-policy']['+.example.edu.cn'], ['10.1.0.53', '10.1.0.54'])
        filters = c['dns']['fake-ip-filter']
        self.assertLess(filters.index('DOMAIN-KEYWORD,claude,fake-ip'), filters.index('RULE-SET,direct,real-ip'))
        self.assertEqual(filters[-1], 'MATCH,real-ip')
        self.assertIn('DOMAIN-KEYWORD,claude,AI', c['rules'])
        self.assertIn('DOMAIN,cdn.example.com,AI', c['rules'])
        self.assertNotIn('tproxy-port', c)

    def test_campus_status_uses_custom_rulesets_and_existing_filtered_paths(self):
        from app import campus, rulesets
        items = rulesets.load(db.get_setting('rulesets'))
        items.append(rulesets.clean(dict(name='custom-example', type='inline', behavior='domain', payload=['+.example.com'])))
        next(x for x in items if x['name'] == 'reject')['excluded'] = ['+.ads.example.com']
        db.set_setting('rulesets', db.dump_json(items))
        group = db.list_groups()[0]
        db.save_rule(dict(name='Example', domains='', rule_sets='custom-example', group_id=group['id'], priority=5, enabled=1))
        settings = {key: db.get_setting(key) for key in ('router_config', 'campus_config', 'mode', 'tun', 'mihomo_secret', 'rulesets')}
        settings['rule_provider_paths'] = {'reject': './ruleset/console-reject-example.yaml'}
        path = db.mihomo_home() / 'config/config.yaml'
        path.parent.mkdir(parents=True)
        path.write_text(render_config([], db.list_groups(), [], settings, db.list_rules()))
        with patch.object(campus, 'system_status', return_value={}):
            result = self.client.get('/api/campus')
        self.assertEqual(result.status_code, 200)
        self.assertTrue(result.json()['config_matches'])
        self.assertFalse((path.parent / 'ruleset').exists(), 'Status reads must not create provider caches')

    def test_identical_core_yaml_still_applies_nat_without_kernel_reload(self):
        from contextlib import nullcontext
        profile = example()
        profile['forwards'][0]['port'] = 24444
        db.set_setting('router_config', db.dump_json(profile))
        path = db.mihomo_home() / 'config/config.yaml'
        path.parent.mkdir(parents=True)
        path.write_text('unchanged\n')
        with patch.object(service, 'render_config', return_value='unchanged\n'), \
             patch.object(service, 'core_active', return_value=True), \
             patch.object(router, 'system_transaction', return_value=nullcontext()) as system, \
             patch.object(service, 'reload_core') as reload:
            result = service.apply_config()
        self.assertFalse(result['changed'])
        reload.assert_not_called()
        self.assertEqual(system.call_args.args[0]['forwards'][0]['port'], 24444)

    def test_untrusted_network_input_rejected(self):
        for key, value in [('gateway', '10.1.0.1;id'), ('interface', 'eth3;id'), ('destination', '198.18.0.0/16')]:
            c = example(); c['routes'][0][key] = value
            with self.assertRaises(ValueError): clean_config(c)
        c = example(); c['forwards'][0]['target_ip'] = '1.1.1.1'
        with self.assertRaises(ValueError): clean_config(c)
        c = example(); c['lan']['dhcp_start'] = '192.168.49.1'
        with self.assertRaises(ValueError): clean_config(c)
        c = example(); c['masquerade'][0]['source'] = '0.0.0.0/0'
        with self.assertRaises(ValueError): clean_config(c)

    def test_owned_firewall_preserves_both_forward_protocols(self):
        text = render_nft(example())
        self.assertIn('tcp dport 24443', text)
        self.assertIn('udp dport 24443', text)
        self.assertNotIn('flush ruleset', text)
        self.assertNotIn('DOCKER', text)
        self.assertIn('table ip mihomo_console', text)

    def test_api_auth_topology_guard_and_database_rollback(self):
        self.client.cookies.clear()
        with patch.object(router, 'status') as status:
            self.assertEqual(self.client.get('/api/router').status_code, 401)
            self.assertEqual(self.client.put('/api/router', json=example()).status_code, 401)
            status.assert_not_called()
        self.client.cookies.set('mc_token', db.get_setting('web_token'))
        c = example(); c['lan']['address'] = '192.168.51.1/24'
        with patch.object(service, 'apply_config'):
            self.assertEqual(self.client.put('/api/router', json=c).status_code, 400)
        c = example(); c['mtu'] = 1400
        with patch.object(service, 'apply_config', side_effect=RuntimeError('validation failed')):
            self.assertEqual(self.client.put('/api/router', json=c).status_code, 400)
        self.assertEqual(router.configured()['mtu'], 1500)

    def test_system_failure_restores_files_and_static_routes(self):
        targets = {k: str(Path(self.tmp.name) / (k + '.conf')) for k in router.TARGETS}
        for path in targets.values(): Path(path).write_text('previous\n')
        calls = []
        def command(args, check=True, **kwargs):
            calls.append(args)
            if args[:3] == ['nft', 'list', 'table']:
                return subprocess.CompletedProcess(args, 1, stdout='', stderr='')
            if args[:3] == ['nmcli', '-g', 'ipv4.routes']:
                return subprocess.CompletedProcess(args, 0, stdout='10.0.0.0/8 10.1.0.1', stderr='')
            return subprocess.CompletedProcess(args, 0, stdout='', stderr='')
        with patch.object(router, 'TARGETS', targets), patch.object(router, 'command', side_effect=command):
            with self.assertRaises(RuntimeError):
                with router.system_transaction(example(), dict(networks=[], dns=[])):
                    raise RuntimeError('core reload failed')
        for path in targets.values(): self.assertEqual(Path(path).read_text(), 'previous\n')
        route_writes = [x[-1] for x in calls if x[:3] == ['nmcli', 'connection', 'modify']]
        self.assertEqual(route_writes[-1], '10.0.0.0/8 10.1.0.1')
        self.assertFalse((Path(self.tmp.name) / 'router-applied.json').exists())

    def test_keyword_rules_survive_editor_validation(self):
        group = db.list_groups()[0]['id']
        result = clean_rule(dict(name='Claude', domains='DOMAIN-KEYWORD,claude\nDOMAIN-KEYWORD,anthropic', group_id=group))
        self.assertEqual(result['domains'], 'DOMAIN-KEYWORD,claude\nDOMAIN-KEYWORD,anthropic')

    def test_reserved_range_does_not_trigger_native_interval_overflow(self):
        campus = dict(schema_version=2, dns=[], networks=[dict(id=str(i), name=n, cidr=n, enabled=True)
                      for i, n in enumerate(['0.0.0.0/8', '240.0.0.0/4'])])
        import ipaddress
        c = yaml.safe_load(render_config([], db.list_groups(), [],
            dict(router_config=json.dumps(example()), campus_config=json.dumps(campus)), []))
        ranges = [ipaddress.ip_network(n) for n in c['tun']['route-exclude-address']]
        self.assertTrue(any(ipaddress.ip_address('240.1.1.1') in n for n in ranges))
        self.assertFalse(any(ipaddress.ip_address('255.255.255.255') in n for n in ranges))
        self.assertFalse(any(ipaddress.ip_address('198.18.1.1') in n for n in ranges))
        self.assertNotIn('lancidr', c['tun']['route-exclude-address-set'])

    def test_truncated_native_domestic_set_is_repaired_in_batches(self):
        source = Path(self.tmp.name) / 'core/config/ruleset/cncidr.yaml'
        source.parent.mkdir(parents=True)
        source.write_text(yaml.safe_dump(dict(payload=['1.0.1.0/24', '223.5.5.0/24'])))
        batches = []
        def command(args, check=True, **kwargs):
            if args[:3] == ['nft', '-j', 'list']:
                elements = [{'prefix': dict(addr='1.0.1.0', len=24)}]
                if batches: elements.append({'prefix': dict(addr='223.5.5.0', len=24)})
                return subprocess.CompletedProcess(args, 0, stdout=json.dumps({'nftables': [{'set': {'elem': elements}}]}))
            if args == ['nft', '-f', '-']: batches.append(kwargs['input'])
            return subprocess.CompletedProcess(args, 0, stdout='')
        with patch.object(router, 'command', side_effect=command):
            result = router.verify_domestic_bypass()
        self.assertEqual(result['missing'], 0)
        self.assertEqual(result['source_prefixes'], 2)
        self.assertIn('223.5.5.0-223.5.5.255', batches[0])
        self.assertNotIn('flush ruleset', batches[0])
