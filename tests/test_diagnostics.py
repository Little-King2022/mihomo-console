import asyncio
import tempfile
import unittest
from pathlib import Path
from unittest.mock import AsyncMock, patch

import httpx
import yaml

from app import diagnostics
from app.render import render_config


class DiagnosticsConfigTest(unittest.TestCase):
    def test_listener_is_bound_to_each_enabled_group_on_loopback(self):
        groups = [dict(id=7, name='家宽', enabled=1, is_default=1),
                  dict(id=11, name='常规', enabled=1, is_default=0),
                  dict(id=12, name='停用', enabled=0, is_default=0)]
        config = yaml.safe_load(render_config([], groups, [], {}, []))
        self.assertEqual([(x['port'], x['proxy'], x['listen']) for x in config['listeners']],
                         [(20007, '家宽', '127.0.0.1'), (20011, '常规', '127.0.0.1')])
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / 'config.yaml'
            path.write_text(yaml.safe_dump(config))
            with patch.object(diagnostics.mihomo, 'config_path', return_value=path):
                self.assertEqual(diagnostics.listener_proxy(groups[0]), 'http://127.0.0.1:20007')
                # Renames and misconfigured listeners must never fall back to
                # the general proxy port or another group's outbound.
                with self.assertRaises(ValueError):
                    diagnostics.listener_proxy(dict(groups[0], name='新家宽'))
                config['listeners'][0]['listen'] = '0.0.0.0'
                path.write_text(yaml.safe_dump(config))
                with self.assertRaises(ValueError):
                    diagnostics.listener_proxy(groups[0])

    def test_ip_providers_and_invalid_responses(self):
        for source, payload in [
            ('ipwhois', dict(ip='1.1.1.1', country='AU', city='Sydney', connection=dict(asn=13335, org='Cloudflare'))),
            ('ipsb', dict(ip='1.1.1.1', country='AU', city='Sydney', asn=13335, asn_organization='Cloudflare')),
            ('ipapi', dict(ip='1.1.1.1', location=dict(country='AU', city='Sydney'), asn=dict(asn=13335, org='Cloudflare'))),
            ('ipapi', dict(ip='1.1.1.1', country='AU', city='Sydney', asn=13335, company='Cloudflare')),
        ]:
            self.assertEqual(diagnostics.normalize_ip(source, payload), dict(ip='1.1.1.1', country='AU', city='Sydney', organization='Cloudflare', asn='AS13335'))
        for payload in ([], {}, {'ip':'invalid'}, {'ip':'1.1.1.1','success':False}, {'error':'quota'}):
            with self.assertRaises(ValueError):
                diagnostics.normalize_ip('ipwhois', payload)


class DiagnosticsProbeTest(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        diagnostics._cache.clear()
        diagnostics._inflight.clear()
        diagnostics._slots = asyncio.Semaphore(4)
        self.group = patch.object(diagnostics.db, 'get_group', side_effect=lambda ident: dict(id=ident, name=f'组{ident}', enabled=ident != 99))
        self.listener = patch.object(diagnostics, 'listener_proxy', side_effect=lambda g: f"http://127.0.0.1:{20000+g['id']}")
        self.node = patch.object(diagnostics, 'current_node', new=AsyncMock(return_value='nodeA'))
        for item in (self.group, self.listener, self.node):
            item.start()
            self.addCleanup(item.stop)

    async def test_groups_sources_and_current_nodes_have_independent_cached_results(self):
        async def fetch(proxy, url, **kwargs):
            return dict(ip='1.1.1.1' if proxy.endswith('20001') else '8.8.8.8')
        with patch.object(diagnostics, 'fetch', new=AsyncMock(side_effect=fetch)) as request:
            one = await diagnostics.run(1, 'ip')
            two = await diagnostics.run(2, 'ip')
            self.assertEqual(one['ip']['ip'], '1.1.1.1')
            self.assertEqual(two['ip']['ip'], '8.8.8.8')
            self.assertEqual(one['group_id'], 1)
            self.assertEqual(two['group_id'], 2)
            self.assertFalse(one['node_changed'])
            await diagnostics.run(1, 'ip', refresh=True)
            self.assertEqual(request.await_count, 3, 'Manual refresh must bypass a just-completed cached result')
            await diagnostics.run(1, 'ip', source='ipwhois')
            self.assertEqual(request.await_args.args[1], diagnostics.IP_SOURCES['ipwhois'])
            with patch.object(diagnostics, 'current_node', new=AsyncMock(return_value='nodeB')):
                changed = await diagnostics.run(1, 'ip')
            self.assertEqual(changed['node'], 'nodeB')
            self.assertEqual(request.await_count, 5)

    async def test_purity_uses_the_requested_group_and_preserves_probe_provenance(self):
        query = AsyncMock(side_effect=[{'ip': '1.1.1.1', 'fraud_score': 7}, {'ip': '8.8.8.8', 'fraud_score': 85}])
        with patch.object(diagnostics.ippure, 'query', new=query):
            home = await diagnostics.run(1, 'purity', refresh=True)
            regular = await diagnostics.run(2, 'purity', refresh=True)
        self.assertEqual(home['report']['fraud_score'], 7)
        self.assertEqual(regular['report']['fraud_score'], 85)
        self.assertEqual(home['group_id'], 1)
        self.assertEqual(regular['group_id'], 2)
        self.assertEqual(query.await_args_list[0].args[0], 'http://127.0.0.1:20001')
        self.assertEqual(query.await_args_list[1].args[0], 'http://127.0.0.1:20002')
        self.assertEqual(home['node'], 'nodeA')
        self.assertIn('checked_at', home)

    async def test_immediate_manual_refresh_returns_a_new_measurement(self):
        with patch.object(diagnostics, 'fetch', new=AsyncMock(side_effect=[{'ip': '1.1.1.1'}, {'ip': '8.8.8.8'}])) as request:
            first = await diagnostics.run(1, 'ip', refresh=True)
            second = await diagnostics.run(1, 'ip', refresh=True)
        self.assertEqual(first['ip']['ip'], '1.1.1.1')
        self.assertEqual(second['ip']['ip'], '8.8.8.8')
        self.assertNotEqual(first['checked_at'], second['checked_at'])
        self.assertEqual(request.await_count, 2)

    async def test_concurrent_requests_share_probe_and_bad_source_never_probes(self):
        async def fetch(*args, **kwargs):
            await asyncio.sleep(.02)
            return {'ip':'1.1.1.1'}
        with patch.object(diagnostics, 'fetch', new=AsyncMock(side_effect=fetch)) as request:
            results = await asyncio.gather(diagnostics.run(1, 'ip'), diagnostics.run(1, 'ip'))
            self.assertEqual(results[0], results[1])
            self.assertEqual(request.await_count, 1)
            for kind, source, ident in [('ip', 'https://evil.test', 1), ('other', 'ipwhois', 1), ('ip', 'ipwhois', 99)]:
                with self.assertRaises(ValueError):
                    await diagnostics.run(ident, kind, source)
            self.assertEqual(request.await_count, 1)

    async def test_latency_partial_failure_and_ip_errors_are_visible_without_fallback(self):
        async def fetch(proxy, url, **kwargs):
            if 'cloudflare' in url:
                raise httpx.ConnectError('failed')
        with patch.object(diagnostics, 'fetch', new=AsyncMock(side_effect=fetch)) as request:
            result = await diagnostics.run(1, 'latency')
            values = [x['delay'] for x in result['targets'] if x['delay'] is not None]
            self.assertEqual(result['average'], round(sum(values)/len(values)))
            self.assertEqual(result['targets'][1]['error'], '超时或连接失败')
            self.assertTrue(all(call.args[0] == 'http://127.0.0.1:20001' for call in request.await_args_list))
        with patch.object(diagnostics, 'fetch', new=AsyncMock(side_effect=httpx.ConnectError('private internals'))):
            result = await diagnostics.run(1, 'ip')
            self.assertIn('error', result)
            self.assertNotIn('ip', result)
            self.assertNotIn('private internals', result['error'])

    async def test_transport_always_uses_explicit_proxy_and_ignores_environment(self):
        client = AsyncMock()
        response = httpx.Response(200, json={'ip':'1.1.1.1'}, request=httpx.Request('GET', 'https://ipwho.is/'))
        client.stream = unittest.mock.MagicMock()
        client.stream.return_value.__aenter__.return_value = response
        client.__aenter__.return_value = client
        with patch.object(diagnostics.httpx, 'AsyncClient', return_value=client) as factory:
            self.assertEqual(await diagnostics.fetch('http://127.0.0.1:20001', diagnostics.IP_SOURCES['ipwhois'], json_body=True), {'ip':'1.1.1.1'})
            self.assertEqual(factory.call_args.kwargs['proxy'], 'http://127.0.0.1:20001')
            self.assertFalse(factory.call_args.kwargs['trust_env'])
            self.assertFalse(factory.call_args.kwargs['follow_redirects'])
