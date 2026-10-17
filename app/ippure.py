"""IPPure's website report endpoints, through a group's dedicated inbound."""

import hashlib
import hmac
import ipaddress
import json
import math
import time

import httpx

API_ORIGIN = 'https://api.123169.xyz'
WEBSITE = 'https://ippure.com'


def mapping(value):
    return value if isinstance(value, dict) else {}


def text(value):
    return value[:512] if isinstance(value, str) else None


def score(value):
    if isinstance(value, (int, float)) and not isinstance(value, bool) and math.isfinite(value) and 0 <= value <= 100:
        return value
    return None


def coordinate(value, limit):
    if isinstance(value, (int, float)) and not isinstance(value, bool) and math.isfinite(value) and abs(value) <= limit:
        return value
    return None


def normalize_report(address, basic, risk):
    if not isinstance(basic, dict) or basic.get('ok') is not True:
        raise ValueError('IPPure 未返回有效的 IP 基本信息，请稍后重试')
    data = mapping(basic.get('data'))
    if data.get('ip') != address:
        raise ValueError('IPPure 返回的 IP 与检测出口不一致，请重新检测')
    asn = mapping(data.get('asn'))
    network = mapping(asn.get('network'))
    traits = mapping(data.get('traits'))
    locations = []
    for provider, location in mapping(data.get('geo')).items():
        if not isinstance(location, dict):
            continue
        point = mapping(location.get('location'))
        locations.append(dict(source=text(provider), country_code=text(location.get('country_code')),
                              region=text(location.get('subdivisions')), city=text(location.get('city')),
                              timezone=text(location.get('time_zone')), postal_code=text(location.get('postal_code')),
                              latitude=coordinate(point.get('latitude'), 90),
                              longitude=coordinate(point.get('longitude'), 180)))
    broadcast = traits.get('is_broadcast')
    number = asn.get('number')
    risk_data = mapping(risk.get('data')) if isinstance(risk, dict) and risk.get('ok') is True else {}
    value = score(risk_data.get('risk_score')) if ipaddress.ip_address(address).version == 4 else None
    return dict(ip=address, version=ipaddress.ip_address(address).version,
                asn=f'AS{number}' if isinstance(number, int) and not isinstance(number, bool) and number > 0 else None,
                organization=text(asn.get('organization')), asn_type=text(asn.get('type')),
                asn_domain=text(asn.get('domain')), network=text(network.get('cidr')),
                network_start=text(mapping(network.get('range')).get('start')),
                network_end=text(mapping(network.get('range')).get('end')),
                is_broadcast=broadcast if isinstance(broadcast, bool) else None,
                is_residential=asn.get('is_residential') if isinstance(asn.get('is_residential'), bool) else None,
                fraud_score=value, locations=locations)


class WebsiteClient:
    """Follow the anonymous session handshake used by the public web client.

    The server issues a short-lived session key; subsequent GET requests carry
    its timestamped HMAC. Keys stay inside this per-probe client and are never
    persisted, logged or returned to the console/browser.
    """

    def __init__(self, client):
        self.client = client
        self.key = None
        self.offset = 0

    async def get(self, path):
        url = API_ORIGIN + path
        for _ in range(3):
            headers = {}
            if self.key:
                stamp = int(time.time() * 1000) + self.offset
                message = f'GET-{url}--{stamp}'
                signature = hmac.new(self.key.encode(), message.encode(), hashlib.sha256).hexdigest()
                headers = {'x-k': self.key, 'x-t': f'{stamp}-{signature}'}
            async with self.client.stream('GET', url, headers=headers) as response:
                response.raise_for_status()
                # Consume the initialization response too, so the session can
                # reuse its proxy/TLS connection for the signed request.
                body = bytearray()
                async for part in response.aiter_bytes():
                    body.extend(part)
                    if len(body) > 131072:
                        raise ValueError('IPPure 返回内容异常，请稍后重试')
                if response.headers.get('x-k'):
                    self.key = response.headers['x-k']
                    try:
                        self.offset = int(response.headers.get('x-t', '').split('-')[0]) - int(time.time() * 1000)
                    except ValueError:
                        raise ValueError('IPPure 网页会话初始化失败，请稍后重试') from None
                    continue
                data = json.loads(body)
                if not isinstance(data, dict):
                    raise ValueError('IPPure 返回内容异常，请稍后重试')
                return data
        raise ValueError('IPPure 网页会话暂时不可用，请稍后重试')


async def query(proxy, fetch_json):
    # Identify this group's outbound first; reports are then requested for that
    # exact IP. Never substitute the browser's or the console's direct address.
    address = None
    for url in ('https://api.ip.sb/geoip', 'https://my.ippure.com/v1/info'):
        try:
            info = await fetch_json(proxy, url, json_body=True)
            address = str(ipaddress.ip_address(mapping(info).get('ip', '')))
            break
        except (httpx.HTTPError, ValueError):
            continue
    if not address:
        raise ValueError('无法获取此代理组的出口 IP，请确认节点可用后重试')
    async with httpx.AsyncClient(proxy=proxy, trust_env=False, follow_redirects=False,
                                 timeout=httpx.Timeout(8, connect=5),
                                 headers={'User-Agent': 'MihomoConsole/1.0', 'Origin': WEBSITE,
                                          'Referer': WEBSITE + '/'}) as client:
        website = WebsiteClient(client)
        basic = await website.get('/api/info/ip-basic/' + address)
        warnings = []
        risk = None
        if ipaddress.ip_address(address).version == 4:
            try:
                risk = await website.get('/api/info/ip-risk/' + address)
                if risk.get('ok') is not True or score(mapping(risk.get('data')).get('risk_score')) is None:
                    warnings.append('IPPure 暂未提供有效的风险系数，可稍后手动重试')
            except (httpx.HTTPError, ValueError):
                warnings.append('IPPure 系数查询暂时失败，已保留基本信息，请手动重试')
        else:
            warnings.append('IPPure 暂不提供 IPv6 风险系数')
        report = normalize_report(address, basic, risk)
        observed = [item.get('my_ip') for item in (basic, risk) if isinstance(item, dict) and item.get('my_ip')]
        if any(value != address for value in observed):
            warnings.append('检测期间出口 IP 发生变化；本报告对应所显示的 IP，请手动重新检测确认')
        report['warnings'] = warnings
        return report
