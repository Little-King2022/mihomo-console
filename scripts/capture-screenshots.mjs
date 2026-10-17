// Render the real frontend using only public, synthetic fixtures. No login or
// upstream network is allowed; this script never reads production data files.
import { readFile, mkdir } from 'node:fs/promises';
import { dirname, resolve } from 'node:path';
import { fileURLToPath } from 'node:url';

const { chromium } = await import(process.env.PLAYWRIGHT_MODULE || 'playwright');
const root = resolve(dirname(fileURLToPath(import.meta.url)), '..');
const demo = JSON.parse(await readFile(resolve(root, 'docs/demo-data.json'), 'utf8'));
const base = new URL(process.env.SCREENSHOT_BASE_URL || 'http://127.0.0.1:8787');
const output = resolve(root, 'docs/images');
await mkdir(output, { recursive: true });
const browser = await chromium.launch({
  ...(process.env.CHROMIUM_PATH ? { executablePath: process.env.CHROMIUM_PATH } : {}),
  headless: true,
});
try {
  const context = await browser.newContext({ viewport: { width: 1440, height: 1080 }, locale: 'zh-CN', timezoneId: 'Asia/Taipei' });
  let externalRequests = 0;
  await context.route('**/*', async (route) => {
    const url = new URL(route.request().url());
    if (url.origin !== base.origin) { externalRequests++; return route.abort(); }
    const pathname = url.pathname;
    if (!pathname.startsWith('/api/')) return route.continue();
    if (pathname === '/api/session') return route.fulfill({ status: 204 });
    const endpoints = {
      '/api/overview': demo.overview, '/api/subscriptions': demo.subscriptions,
      '/api/rules': demo.rules, '/api/groups': demo.groups, '/api/nodes': demo.nodes,
      '/api/node-catalog': demo.catalog, '/api/telemetry': demo.telemetry,
      '/api/campus': demo.campus,
    };
    let data = endpoints[pathname];
    const match = pathname.match(/^\/api\/groups\/(\d+)\/diagnostics\/(ip|latency|purity)$/);
    if (match) {
      const group = demo.groups.find((item) => item.id === Number(match[1]));
      if (!group) return route.fulfill({ status: 404, contentType: 'application/json', body: '{}' });
      const ip = group.id === 1 ? '203.0.113.24' : '198.51.100.18';
      data = { group_id: group.id, group_name: group.name, node: group.now, checked_at: '2026-10-17T10:00:00+08:00', node_changed: false };
      if (match[2] === 'ip') data.ip = { ip, country: group.id === 1 ? 'Taiwan' : 'Hong Kong', city: group.id === 1 ? 'Taipei' : 'Hong Kong', organization: 'Example Residential Network', asn: 'AS64500' };
      if (match[2] === 'latency') Object.assign(data, { average: 133, targets: [{ name: 'Google', delay: 92 }, { name: 'Cloudflare', delay: 118 }, { name: 'GitHub', delay: 189 }] });
      if (match[2] === 'purity') data.report = { ip, version: 4, asn: 'AS64500', organization: 'Example Residential Network', asn_type: 'isp', asn_domain: 'network.example.com', network: '203.0.113.0/24', network_start: '203.0.113.0', network_end: '203.0.113.255', is_broadcast: false, is_residential: true, fraud_score: 7, warnings: [], locations: ['IP2Location', 'DB-IP', 'MaxMind'].map((source) => ({ source, country_code: 'TW', region: 'Taiwan', city: 'Taipei' })) };
    }
    if (data === undefined) throw new Error(`Unexpected API request: ${pathname}`);
    return route.fulfill({ status: 200, contentType: 'application/json', body: JSON.stringify(data) });
  });
  const page = await context.newPage();
  const errors = [];
  page.on('pageerror', (error) => errors.push(error.message));
  await page.addInitScript(() => { localStorage.setItem('mihomo-theme', 'light'); });
  await page.goto(new URL('/#overview', base).href);
  await page.locator('#proxy-enable').waitFor();
  await page.waitForFunction(() => document.querySelector('#diagnostic-ip-value')?.textContent.includes('203.0.113'));
  await page.waitForFunction(() => document.querySelector('#traffic-chart svg'));
  await page.evaluate(() => {
    const badge = document.createElement('div');
    badge.textContent = '演示数据 · DEMO';
    badge.style.cssText = 'position:fixed;right:18px;bottom:14px;z-index:100;background:#131d31e8;color:#fff;padding:7px 12px;border-radius:8px;font:11px sans-serif;letter-spacing:.6px;pointer-events:none';
    document.body.append(badge);
  });
  await page.screenshot({ path: resolve(output, 'overview.png'), fullPage: true });
  await page.getByRole('button', { name: '代理组', exact: true }).click();
  await page.locator('[data-domain-group="1"] summary').click();
  await page.mouse.move(1420, 20);
  await page.waitForTimeout(220);
  if ((await page.locator('body').innerText()).includes('undefined')) throw new Error('Incomplete group fixture');
  await page.screenshot({ path: resolve(output, 'groups.png'), fullPage: true });
  await page.locator('[data-gedit="1"]').click();
  await page.locator('.pick-node').first().waitFor();
  await page.screenshot({ path: resolve(output, 'node-picker.png') });
  await page.locator('#picker-close').click();
  await page.setViewportSize({ width: 1440, height: 1200 });
  await page.getByRole('button', { name: '节点健康', exact: true }).click();
  await page.locator('.health-bar').first().hover();
  await page.locator('#history-tooltip').waitFor({ state: 'visible' });
  await page.screenshot({ path: resolve(output, 'health.png') });
  await page.setViewportSize({ width: 1440, height: 1080 });
  await page.getByRole('button', { name: 'IP 纯净度', exact: true }).click();
  await page.locator('#purity-run').click();
  await page.waitForFunction(() => document.querySelector('#purity-ip')?.textContent.includes('203.0.113'));
  await page.screenshot({ path: resolve(output, 'purity.png'), fullPage: true });
  await page.getByRole('button', { name: '校园网', exact: true }).click();
  await page.locator('#campus-add-network').waitFor();
  await page.screenshot({ path: resolve(output, 'campus.png'), fullPage: true });
  await page.getByRole('button', { name: 'IP 纯净度', exact: true }).click();
  await page.setViewportSize({ width: 390, height: 1000 });
  await page.evaluate(() => document.documentElement.dataset.theme = 'dark');
  await page.screenshot({ path: resolve(output, 'mobile.png'), fullPage: true });
  if (errors.length) throw new Error(errors.join('\n'));
  if (externalRequests) throw new Error(`Blocked ${externalRequests} unexpected external requests`);
  console.log('Captured 7 screenshots from synthetic fixtures; no credentials or external API requests.');
} finally {
  await browser.close();
}
