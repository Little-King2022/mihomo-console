import test from 'node:test';
import assert from 'node:assert/strict';
import { readFile, readdir } from 'node:fs/promises';
import { dirname, join, resolve } from 'node:path';
import { fileURLToPath, pathToFileURL } from 'node:url';
import { state } from '../../app/static/js/core/state.js';
import { healthSubscriptionChoices, resolveHealthSubscription, subscriptionHealthNodes } from '../../app/static/js/pages/nodes.js';
import { diagnosticKey, latencyTone } from '../../app/static/js/features/group-diagnostics.js';
import { riskBand, riskPosition, ipAttribute, locationText } from '../../app/static/js/pages/purity.js';
import { nodeHistory } from '../../app/static/js/ui/history.js';

const root = resolve(dirname(fileURLToPath(import.meta.url)), '../../app/static');
async function moduleFiles(directory) {
  const result = [];
  for (const entry of await readdir(directory, { withFileTypes: true })) {
    if (entry.isDirectory()) result.push(...await moduleFiles(join(directory, entry.name)));
    else if (entry.name.endsWith('.js')) result.push(join(directory, entry.name));
  }
  return result;
}

test('browser modules have explicit local dependencies and no import cycles', async () => {
  const files = [join(root, 'app.js'), ...await moduleFiles(join(root, 'js'))];
  const graph = new Map();
  for (const file of files) {
    const code = await readFile(file, 'utf8');
    const imports = [...code.matchAll(/\bfrom\s+['"]([^'"]+)['"]/g)].map((match) => {
      assert.ok(match[1].startsWith('.'), `Unexpected runtime dependency in ${file}`);
      const target = resolve(dirname(file), match[1]);
      assert.ok(files.includes(target), `Missing module: ${target}`);
      return target;
    });
    graph.set(file, imports);
  }
  const visiting = new Set(), visited = new Set();
  function visit(file) {
    assert.ok(!visiting.has(file), `Circular import: ${file}`);
    if (visited.has(file)) return;
    visiting.add(file);
    graph.get(file).forEach(visit);
    visiting.delete(file);
    visited.add(file);
  }
  visit(join(root, 'app.js'));
  assert.equal(visited.size, files.length, 'Unused module outside the application graph');
  for (const file of files.filter((file) => file !== join(root, 'app.js'))) {
    await import(pathToFileURL(file));
  }
});

test('health subscription helpers keep provider isolation and recover from a removed subscription', () => {
  state.subs = [{ id: 1, name: 'One', enabled: 1 }, { id: 2, name: 'Two', enabled: 1 }];
  state.nodes = [{ provider: 'sub1', node_name: 'A' }, { provider: 'sub2', node_name: 'B' }];
  state.nodeProvider = 'sub2';
  assert.deepEqual(healthSubscriptionChoices().map((choice) => choice.count), [2, 1, 1]);
  assert.deepEqual(subscriptionHealthNodes().map((node) => node.node_name), ['B']);
  state.subs.pop();
  resolveHealthSubscription();
  assert.equal(state.nodeProvider, 'sub1');
  state.nodeProvider = 'all';
  assert.equal(subscriptionHealthNodes().length, 2);
  state.subs = [];
  state.nodes = [];
  state.nodeProvider = 'sub999';
  resolveHealthSubscription();
  assert.equal(state.nodeProvider, 'all');
});

test('both history views share accessible success and failure details', () => {
  const html = nodeHistory({ history: [{ time: '2026-10-17T00:00:00Z', delay: 82 }, { time: '2026-10-17T00:01:00Z', delay: 0 }] });
  assert.match(html, /data-health-result="延迟 82 ms"/);
  assert.match(html, /data-health-result="连接失败"/);
  assert.match(html, /tabindex="0"/);
  assert.match(html, /tabindex="-1"/);
});


test('diagnostic snapshots stay isolated by group and source and survive node changes', () => {
  const group = { id: 1, name: 'Home', now: 'nodeA' };
  const original = diagnosticKey(group, 'ip', 'ipsb');
  assert.notEqual(original, diagnosticKey({ ...group, id: 2 }, 'ip', 'ipsb'));
  for (const changed of [{ ...group, name: 'Renamed' }, { ...group, now: 'nodeB' }]) {
    assert.equal(original, diagnosticKey(changed, 'ip', 'ipsb'));
  }
  assert.notEqual(original, diagnosticKey(group, 'ip', 'ipwhois'));
  assert.equal(diagnosticKey(group, 'latency', 'ipsb'), diagnosticKey(group, 'latency', 'ipwhois'));
  assert.deepEqual([null, 0, 299, 300, 799, 800, 1600].map(latencyTone), ['unavailable', 'fast', 'fast', 'moderate', 'moderate', 'slow', 'slow']);
});


test('IPPure uses website risk intervals and treats missing fields as unknown', () => {
  assert.deepEqual([0, 15, 15.1, 25, 40, 50, 70, 100].map(v => riskBand(v).label), ['极度纯净', '极度纯净', '纯净', '纯净', '中性', '轻度风险', '中度风险', '极度风险']);
  for (const missing of [null, undefined, false, '7', NaN, -1, 101]) assert.equal(riskBand(missing).value, null);
  for (const [value, position] of [[0, 0], [15, 100/6], [25, 100/3], [40, 50], [70, 500/6], [100, 100]]) assert.ok(Math.abs(riskPosition(value)-position)<1e-10);
  assert.equal(ipAttribute('isp'), '住宅 IP');
  assert.equal(ipAttribute('enterprise'), '商业宽带');
  assert.equal(ipAttribute('business'), '机房 IP');
  assert.equal(ipAttribute(undefined), '暂未提供');
  assert.equal(ipAttribute('__proto__'), '暂未提供');
  assert.equal(ipAttribute('constructor'), '暂未提供');
  assert.equal(locationText({ country_code:'HK', region:'Hong Kong', city:'Central' }).includes('Central'), true);
});
