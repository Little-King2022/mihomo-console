import hashlib
import hmac
import unittest
from unittest.mock import AsyncMock, patch

import httpx

from app import ippure


def basic(address='1.1.1.1'):
    return {'ok': True, 'my_ip': address, 'data': {
        'ip': address,
        'asn': {'number': 13335, 'organization': 'Example Network', 'type': 'isp',
                'is_residential': True, 'domain': 'example.com',
                'network': {'cidr': '1.1.1.0/24', 'range': {'start': '1.1.1.0', 'end': '1.1.1.255'}}},
        'traits': {'is_broadcast': False},
        'geo': {'MaxMind': {'country_code': 'HK', 'subdivisions': 'Hong Kong', 'city': 'Central',
                            'location': {'latitude': 22.3, 'longitude': 114.2}}}}}


class IPPureNormalizationTest(unittest.TestCase):
    def test_website_fields_are_used_without_inference(self):
        report = ippure.normalize_report('1.1.1.1', basic(), {'ok': True, 'data': {'risk_score': 0}})
        self.assertEqual(report['fraud_score'], 0)
        self.assertEqual(report['asn_type'], 'isp')
        self.assertIs(report['is_broadcast'], False)
        self.assertEqual(report['asn'], 'AS13335')
        self.assertEqual(report['locations'][0]['source'], 'MaxMind')
        payload = basic()
        payload['data']['asn'] = {'is_residential': True}
        payload['data']['traits'] = {}
        payload['data']['geo']['MaxMind']['location'] = {'latitude': float('nan'), 'longitude': True}
        report = ippure.normalize_report('1.1.1.1', payload, None)
        self.assertIsNone(report['asn_type'], 'Do not invent an ASN type from a different flag')
        self.assertIsNone(report['is_broadcast'], 'Missing must not become native')
        self.assertIsNone(report['fraud_score'], 'Missing must not become zero')
        self.assertIsNone(report['locations'][0]['latitude'])
        self.assertIsNone(report['locations'][0]['longitude'])
        for invalid in (None, False, -1, 101, float('nan'), '7'):
            self.assertIsNone(ippure.normalize_report('1.1.1.1', basic(), {'ok': True, 'data': {'risk_score': invalid}})['fraud_score'])

    def test_mismatched_ip_rejected_and_ipv6_not_scored(self):
        with self.assertRaises(ValueError):
            ippure.normalize_report('8.8.8.8', basic(), None)
        with self.assertRaises(ValueError):
            ippure.normalize_report('1.1.1.1', {'ok': False}, None)
        report = ippure.normalize_report('2606:4700:4700::1111', basic('2606:4700:4700::1111'), {'ok': True, 'data': {'risk_score': 7}})
        self.assertIsNone(report['fraud_score'])
        self.assertEqual(report['version'], 6)


class IPPureClientTest(unittest.IsolatedAsyncioTestCase):
    async def test_public_session_handshake_and_request_signature(self):
        calls = []
        consumed = []
        class InitStream(httpx.AsyncByteStream):
            async def __aiter__(self):
                yield b'init'
                consumed.append(True)
        def handler(request):
            calls.append(request)
            if 'x-k' not in request.headers:
                return httpx.Response(200, headers={'x-k': 'anonymous-test-session', 'x-t': '1000000'}, stream=InitStream())
            self.assertTrue(consumed, 'Read the handshake body before sending the signed request')
            timestamp, signature = request.headers['x-t'].split('-', 1)
            expected = hmac.new(b'anonymous-test-session', f'GET-{request.url}--{timestamp}'.encode(), hashlib.sha256).hexdigest()
            self.assertEqual(signature, expected)
            self.assertEqual(timestamp, '1000000')
            return httpx.Response(200, json={'ok': True, 'data': {'risk_score': 7}})
        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
            with patch.object(ippure.time, 'time', return_value=1234.5):
                result = await ippure.WebsiteClient(client).get('/api/info/ip-risk/1.1.1.1')
        self.assertEqual(len(calls), 2)
        self.assertEqual(result, {'ok': True, 'data': {'risk_score': 7}})
        self.assertNotIn('anonymous-test-session', str(result))

    async def test_rejected_or_repeated_sessions_are_not_bypassed(self):
        async with httpx.AsyncClient(transport=httpx.MockTransport(lambda _: httpx.Response(403, text='denied'))) as client:
            with self.assertRaises(httpx.HTTPStatusError):
                await ippure.WebsiteClient(client).get('/api/info/ip-risk/1.1.1.1')
        calls = []
        def handler(request):
            calls.append(request)
            return httpx.Response(200, headers={'x-k': 'anonymous-test-session', 'x-t': '1000000'}, text='init')
        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
            with self.assertRaises(ValueError):
                await ippure.WebsiteClient(client).get('/api/info/ip-risk/1.1.1.1')
        self.assertEqual(len(calls), 3)

    async def test_group_bound_outbound_and_exact_website_lookup_with_partial_failure(self):
        captured = []
        real_client = httpx.AsyncClient
        def handler(request):
            self.assertEqual(request.headers['origin'], 'https://ippure.com')
            self.assertEqual(request.url.host, 'api.123169.xyz')
            self.assertTrue(request.url.path.endswith('/1.1.1.1'))
            if '/ip-basic/' in request.url.path:
                return httpx.Response(200, json=basic())
            return httpx.Response(503)
        def factory(**kwargs):
            captured.append(kwargs.copy())
            kwargs.pop('proxy')
            return real_client(transport=httpx.MockTransport(handler), **kwargs)
        fetch = AsyncMock(return_value={'ip': '1.1.1.1'})
        with patch.object(ippure.httpx, 'AsyncClient', side_effect=factory):
            report = await ippure.query('http://127.0.0.1:20007', fetch)
        self.assertEqual(fetch.await_args.args[0], 'http://127.0.0.1:20007')
        self.assertEqual(captured[0]['proxy'], 'http://127.0.0.1:20007')
        self.assertFalse(captured[0]['trust_env'])
        self.assertFalse(captured[0]['follow_redirects'])
        self.assertEqual(report['asn_type'], 'isp')
        self.assertIsNone(report['fraud_score'])
        self.assertTrue(report['warnings'])

    async def test_exit_ip_change_is_reported_without_overwriting_target(self):
        real_client = httpx.AsyncClient
        def handler(request):
            payload = basic() if '/ip-basic/' in request.url.path else {'ok': True, 'data': {'risk_score': 7}}
            payload['my_ip'] = '8.8.8.8'
            return httpx.Response(200, json=payload)
        def factory(**kwargs):
            kwargs.pop('proxy')
            return real_client(transport=httpx.MockTransport(handler), **kwargs)
        with patch.object(ippure.httpx, 'AsyncClient', side_effect=factory):
            report = await ippure.query('http://127.0.0.1:20007', AsyncMock(return_value={'ip':'1.1.1.1'}))
        self.assertEqual(report['ip'], '1.1.1.1')
        self.assertEqual(report['fraud_score'], 7)
        self.assertIn('出口 IP 发生变化', report['warnings'][0])
