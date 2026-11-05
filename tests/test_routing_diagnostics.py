import json
import os
import ssl
import tempfile
import unittest
from pathlib import Path
from unittest.mock import MagicMock, patch

import httpx
import yaml
from fastapi.testclient import TestClient

from app import db, routing_diagnostics as diag, service
from app.main import app


def snapshot():
    return {'nftables': [
        {'set': {'name': 'cn', 'elem': [{'prefix': {'addr': '223.5.5.0', 'len': 24}}, {'range': ['36.0.0.0', '36.0.0.255']}] }},
        *[{'rule': {'chain': chain, 'handle': i, 'expr': [
            {'match': {'op': '==', 'left': {'payload': {'protocol': 'ip', 'field': 'daddr'}}, 'right': '@cn'}},
            {'counter': {'packets': 2}}, {'return': None}]}} for i, chain in enumerate(['output', 'prerouting'])],
        {'rule': {'chain': 'output', 'expr': [
            {'match': {'op': '==', 'left': {'meta': {'key': 'l4proto'}}, 'right': 'tcp'}}, {'redirect': {'port': 1234}}]}}
    ]}


class RoutingDiagnosticsTest(unittest.TestCase):
    def setUp(self):
        self.previous = db._conn
        db._conn = None
        self.tmp = tempfile.TemporaryDirectory()
        self.env = patch.dict(os.environ, DATA_DIR=self.tmp.name, MIHOMO_HOME=self.tmp.name + '/core')
        self.env.start()
        db.init_db()
        self.client = TestClient(app)
        self.client.cookies.set('mc_token', db.get_setting('web_token'))
        self.config = dict(dns={'fake-ip-range': '198.18.0.1/16'}, **{'rule-providers': {}})
        path = diag.config_path()
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(yaml.safe_dump(self.config))
        self.http = MagicMock()
        self.rules = [dict(type='DomainSuffix', payload='example.com', proxy='AI', index=0),
                      dict(type='Match', payload='', proxy='Fallback', index=1)]
        def get(url, **kwargs):
            values = {'/configs': dict(mode='rule', tun={'enable': True}), '/rules': dict(rules=self.rules),
                      '/proxies': dict(proxies={'AI': {'now': 'node A'}}), '/connections': dict(connections=[])}
            return httpx.Response(200, json=values[url], request=httpx.Request('GET', 'http://localhost' + url))
        self.http.get.side_effect = get
        self.patches = [patch.object(diag, 'client'), patch.object(diag, 'kernel_snapshot', return_value=snapshot()),
                        patch.object(diag.router, 'configured', return_value=None),
                        patch.object(diag, 'route_to', return_value={'interface': 'eth0', 'source': '192.0.2.5', 'gateway': '192.0.2.1'})]
        self.mocks = [p.start() for p in self.patches]
        self.mocks[0].return_value.__enter__.return_value = self.http

    def tearDown(self):
        for p in reversed(self.patches): p.stop()
        diag.load_provider.cache_clear()
        self.client.close()
        db._conn.close()
        db._conn = self.previous
        self.env.stop()
        self.tmp.cleanup()

    def test_auth_validation_and_no_implicit_probe(self):
        self.client.cookies.clear()
        self.assertEqual(self.client.post('/api/routing/diagnostics', json={'target': 'example.com'}).status_code, 401)
        self.mocks[0].assert_not_called()
        self.client.cookies.set('mc_token', db.get_setting('web_token'))
        for body in [dict(target='$(id).com'), dict(target='https://example.com/secret'), dict(target='--help'),
                     dict(target='example.com', port=True), dict(target='example.com', port=0),
                     dict(target='example.com', probe='true'), dict(target='+.example.com'), dict(target='::1')]:
            self.assertEqual(self.client.post('/api/routing/diagnostics', json=body).status_code, 400, body)
        with patch.object(diag, 'probe_connection') as probe, patch.object(service, 'apply_config') as apply:
            response = self.client.post('/api/routing/diagnostics', json=dict(target='223.5.5.5'))
            self.assertEqual(response.status_code, 200)
            self.assertEqual(response.json()['results'][0]['path'], 'kernel')
            probe.assert_not_called(); apply.assert_not_called()

    def test_runtime_bypass_uses_actual_sets_not_core_direct(self):
        self.assertTrue(diag.bypass_match(snapshot(), '223.5.5.5')['bypass'])
        self.assertTrue(diag.bypass_match(snapshot(), '36.0.0.9')['lan_bypass'])
        self.assertFalse(diag.bypass_match(snapshot(), '36.0.1.9')['bypass'])
        self.assertFalse(diag.bypass_match(snapshot(), '198.18.0.9')['bypass'])
        self.assertTrue(diag.bypass_match(snapshot(), '198.18.0.9')['tcp_redirect'])
        self.assertFalse(diag.bypass_match({'nftables': []}, '198.18.0.9')['tcp_redirect'])
        changed = snapshot()
        rule = next(row['rule'] for row in changed['nftables'] if row.get('rule', {}).get('chain') == 'prerouting')
        rule['expr'].insert(0, {'match': {'op': '==', 'left': {'meta': {'key': 'iifname'}}, 'right': 'docker0'}})
        actual = diag.bypass_match(changed, '223.5.5.5')
        self.assertTrue(actual['bypass']); self.assertFalse(actual['lan_bypass'])
        self.rules.insert(0, dict(type='IPCIDR', payload='8.8.8.8/32', proxy='DIRECT'))
        result = diag.diagnose(dict(target='8.8.8.8'))['results'][0]
        self.assertEqual(result['rule']['proxy'], 'DIRECT')
        self.assertEqual(result['path'], 'core')

    def test_fake_dns_multiple_addresses_one_probe_and_group_chain(self):
        dns = dict(server='192.0.2.1', answers=['198.18.0.5', '198.18.0.6'], status='NOERROR')
        with patch.object(diag, 'resolve', return_value=dns), patch.object(diag, 'probe_connection', return_value={'ok': True}) as probe:
            report = diag.diagnose(dict(target='www.example.com', probe=True))
        self.assertEqual(len(report['results']), 2)
        self.assertTrue(report['results'][0]['fake_ip'])
        self.assertEqual(report['results'][0]['chain'], ['AI', 'node A'])
        self.assertEqual(report['results'][0]['rule']['type'], 'DomainSuffix')
        self.assertEqual(probe.call_count, 1)

    def test_provider_order_disabled_rules_and_uncertain_geoip(self):
        path = diag.config_dir() / 'ruleset' / 'direct.yaml'
        path.parent.mkdir()
        path.write_text('payload: ["+.example.com"]')
        config = {'rule-providers': {'direct': {'behavior': 'domain', 'path': './ruleset/direct.yaml'}}}
        rules = [dict(type='DomainKeyword', payload='example', proxy='Disabled', extra={'disabled': True}),
                 dict(type='RuleSet', payload='direct', proxy='DIRECT')]
        result = diag.explain_rule(rules, config, 'www.example.com', '203.0.113.8', 'rule')
        self.assertEqual(result['proxy'], 'DIRECT'); self.assertTrue(result['certain'])
        path.unlink()
        rules += [dict(type='GEOIP', payload='CN', proxy='DIRECT'), dict(type='Match', payload='', proxy='Fallback')]
        result = diag.explain_rule(rules, config, 'www.example.com', '203.0.113.8', 'rule')
        self.assertFalse(result['certain'])
        self.assertEqual(len(result['unknown_before']), 2)
        self.assertFalse(diag.rule_match('DomainSuffix', 'example.com', 'evilexample.com', '1.1.1.1', {}))
        self.assertTrue(diag.rule_match('DomainKeyword', 'EXAMPLE', 'www.example.com', '1.1.1.1', {}))
        self.assertEqual(diag.explain_rule([], {}, '', '1.1.1.1', 'direct')['proxy'], 'DIRECT')
        self.assertEqual(diag.explain_rule([], {}, '', '1.1.1.1', 'global')['proxy'], 'GLOBAL')

    def test_inline_text_and_mrs_sets_report_correct_certainty(self):
        providers = {'custom': {'type': 'inline', 'behavior': 'domain', 'payload': ['+.example.com']}}
        self.assertTrue(diag.rule_match('RULE-SET', 'custom', 'www.example.com', '203.0.113.1', providers))
        self.assertFalse(diag.rule_match('RULE-SET', 'custom', 'other.example', '203.0.113.1', providers))
        providers['custom']['format'] = 'mrs'
        self.assertIsNone(diag.rule_match('RULE-SET', 'custom', 'www.example.com', '203.0.113.1', providers))

    def test_classical_ruleset_uses_keywords_and_keeps_unknown_members(self):
        path = diag.config_dir() / 'classical.yaml'
        path.write_text('payload: ["DOMAIN-KEYWORD,example", "DOMAIN,other.test"]')
        providers = {'custom': dict(behavior='classical', path='./classical.yaml')}
        self.assertTrue(diag.rule_match('RuleSet', 'custom', 'www.example.com', '1.1.1.1', providers))
        self.assertFalse(diag.rule_match('RuleSet', 'custom', 'unrelated.test', '1.1.1.1', providers))
        path.write_text('payload: ["GEOIP,CN"]')
        diag.load_provider.cache_clear()
        self.assertIsNone(diag.rule_match('RuleSet', 'custom', 'unrelated.test', '1.1.1.1', providers))

    def test_dns_queries_lan_and_longest_policy_without_shell(self):
        profile = {'lan': {'address': '192.0.2.1/24'}}
        cfg = {'dns': {'nameserver-policy': {'+.example.edu': ['10.1.0.53'], '+.sub.example.edu': ['10.2.0.53']}}}
        output = ';; ->>HEADER<<- opcode: QUERY, status: NOERROR, id: 123\nwww.sub.example.edu. 60 IN A 10.2.0.5\n'
        with patch.object(diag.router, 'configured', return_value=profile), patch.object(diag.campus, 'command', return_value=output) as run:
            dns = diag.resolve('www.sub.example.edu', cfg)
        self.assertEqual(dns['answers'], ['10.2.0.5'])
        self.assertEqual(dns['upstream'], ['10.2.0.53'])
        self.assertEqual(run.call_args.args[0][:4], ['dig', '-4', '@192.0.2.1', 'www.sub.example.edu'])

    def test_missing_kernel_and_failed_dns_are_not_successful_bypass(self):
        self.mocks[1].side_effect = RuntimeError('no nft permissions')
        result = diag.diagnose(dict(target='223.5.5.5'))
        self.assertIsNone(result['results'][0]['kernel']['bypass'])
        self.assertEqual(result['results'][0]['path'], 'unknown')
        with patch.object(diag, 'resolve', return_value=dict(answers=[], status='NXDOMAIN', server='127.0.0.1')):
            result = diag.diagnose(dict(target='missing.example', probe=True))
        self.assertEqual(result['results'], [])
        self.assertTrue(result['warnings'])

    def test_probe_keeps_connection_evidence_when_tls_fails_and_always_closes(self):
        sock = MagicMock()
        sock.getsockname.return_value = ('192.0.2.5', 12345)
        observed = dict(rule='DomainSuffix', payload='example.com', chains=['node A', 'AI'])
        with patch.object(diag.socket, 'socket', return_value=sock), patch.object(diag, 'connection_for', return_value=observed), \
             patch.object(diag.ssl, 'create_default_context') as context:
            context.return_value.wrap_socket.side_effect = ssl.SSLError('private upstream error')
            result = diag.probe_connection(self.http, '198.18.0.8', 'example.com', 443, True)
        self.assertFalse(result['ok']); self.assertEqual(result['observed'], observed)
        self.assertNotIn('private upstream error', json.dumps(result))
        sock.close.assert_called_once()
        with patch.object(diag.socket, 'socket') as create:
            result = diag.probe_connection(self.http, '127.0.0.1', 'localhost', 22, False)
            create.assert_not_called()
        self.assertFalse(result['ok'])

    def test_connection_is_correlated_by_source_address_and_both_ports(self):
        def conn(ip, source, dest):
            return dict(metadata=dict(sourceIP=ip, sourcePort=source, destinationPort=dest), rule='Domain', chains=['AI'])
        self.http.get.side_effect = None
        self.http.get.return_value = httpx.Response(200, request=httpx.Request('GET', 'http://localhost/connections'),
            json={'connections': [conn('192.0.2.6', 12345, 443), conn('192.0.2.5', 12345, 80)]})
        self.assertIsNone(diag.connection_for(self.http, ('192.0.2.5', 12345), 443))

    def test_no_raw_api_error_and_concurrency_guard(self):
        self.http.get.side_effect = httpx.ConnectError('https://secret.example/private')
        response = self.client.post('/api/routing/diagnostics', json=dict(target='example.com'))
        self.assertEqual(response.status_code, 502)
        self.assertNotIn('secret.example', response.text)
        diag._lock.acquire()
        try:
            response = self.client.post('/api/routing/diagnostics', json=dict(target='example.com'))
            self.assertEqual(response.status_code, 400)
        finally:
            diag._lock.release()

    def test_empty_connection_snapshot_may_be_null(self):
        self.http.get.side_effect = None
        self.http.get.return_value = httpx.Response(200, request=httpx.Request('GET', 'http://localhost/connections'),
                                                   json={'connections': None})
        self.assertIsNone(diag.connection_for(self.http, ('192.0.2.5', 12345), 443))


if __name__ == '__main__':
    unittest.main()
