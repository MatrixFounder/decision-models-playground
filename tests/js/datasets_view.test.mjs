// node --test tests/js: the Datasets screen's money and consent paths with a fake API (review P4: the ZDR consent
// outlived its run, a paid run could start unconfirmed, a late estimate or report could land over a newer one).
import assert from 'node:assert/strict';
import { test } from 'node:test';

import { confusionRows, DatasetsView } from '../../web/js/datasets.js';

const tick = () => new Promise((r) => setTimeout(r, 0));

function deferred() {
  let resolve;
  const promise = new Promise((r) => { resolve = r; });
  return { promise, resolve };
}

const ENGINES = [
  { id: 'or-d1.1', available: true, paid: true, zdr: true },
  { id: 'or-no-zdr', available: true, paid: true, zdr: false },
  { id: 'mlx-d1-2b', available: true, paid: false },
  { id: 'gone', available: false, paid: true },
];

function fakeApi(over = {}) {
  const calls = { runs: [], estimates: [] };
  const client = {
    calls,
    listDatasets: async () => ({ datasets: [{ name: 'am', items: 4 }] }),
    getPresets: async () => ({ presets: [] }),
    getDataset: async (name) => ({ name, items: 4, questions: {}, preview: [], items_by_id: [] }),
    listRuns: async () => ({ runs: [] }),
    estimateDataset: async (name, body) => {
      calls.estimates.push(body);
      const paid = body.engines.some((id) => id.startsWith('or-'));
      return { calls: 4 * body.engines.length, total_usd: paid ? 0.01 : 0, budget: { remaining_usd: 1 } };
    },
    runDataset: (name, body) => { calls.runs.push(body); },
    getReport: async (id) => ({ run_id: id, dataset: { name: 'am' }, groups: [] }),
    reportUrl: () => '#',
    ...over,
  };
  return client;
}

function view(client, confirms = []) {
  const v = new DatasetsView(null, { getEngines: () => ENGINES, onBudget: () => {}, client,
    confirm: (text) => { confirms.push(text); return confirms.answer ?? true; } });
  v.render = () => {};
  return v;
}

test('the no-ZDR consent belongs to one run', async () => {
  const client = fakeApi();
  const v = view(client);
  await v.load();
  v.selected = 'am';
  v.picked = new Set(['or-no-zdr']);
  v.allowNoZdr = true;
  assert.equal(await v.run(), true);
  assert.equal(client.calls.runs[0].options.allow_no_zdr, true);
  assert.equal(v.allowNoZdr, false);  // cleared at the start
  v.running = false;
  assert.equal(await v.run(), true);
  assert.equal(client.calls.runs[1].options.allow_no_zdr, false);  // the next run asks again
});

test('a paid run is confirmed on a fresh estimate of exactly this request', async () => {
  const client = fakeApi();
  const confirms = [];
  const v = view(client, confirms);
  await v.load();
  v.selected = 'am';
  v.picked = new Set(['mlx-d1-2b']);
  v.est = { calls: 4, total_usd: 0, budget: { remaining_usd: 1 } };  // a free selection's estimate on screen
  v.picked.add('or-d1.1');  // then a paid engine, Run clicked before the new estimate came back
  confirms.answer = false;
  assert.equal(await v.run(), false);
  assert.equal(client.calls.runs.length, 0);
  assert.equal(confirms.length, 1);
  assert.match(confirms[0], /8/);  // the fresh estimate: 2 engines × 4 items, not the stale 4
  assert.deepEqual(client.calls.estimates.at(-1).engines.sort(), ['mlx-d1-2b', 'or-d1.1']);
});

test('a free run starts without a dialog; a failed estimate starts nothing', async () => {
  const confirms = [];
  const v = view(fakeApi(), confirms);
  await v.load();
  v.selected = 'am';
  v.picked = new Set(['mlx-d1-2b']);
  assert.equal(await v.run(), true);
  assert.equal(confirms.length, 0);
  const broken = fakeApi({ estimateDataset: async () => { throw new Error('400 no engine'); } });
  const w = view(broken);
  await w.load();
  w.selected = 'am';
  assert.equal(await w.run(), false);
  assert.equal(broken.calls.runs.length, 0);
  assert.equal(w.estError, '400 no engine');
});

test('a late estimate never lands over a newer one, and a change clears the shown one', async () => {
  const slow = deferred();
  let first = true;
  const client = fakeApi({ estimateDataset: (name, body) => {
    if (first) { first = false; return slow.promise; }
    return Promise.resolve({ calls: 99, total_usd: 0, budget: { remaining_usd: 1 }, body });
  } });
  const v = view(client);
  v.selected = 'am';
  v.picked = new Set(['or-d1.1']);
  v.est = { calls: 4, total_usd: 0.01, budget: { remaining_usd: 1 } };  // the figure on screen
  const a = v.estimate();
  v.picked.add('mlx-d1-2b');
  v.changed();
  assert.equal(v.est, null);  // the old figure is gone at once: Run waits
  await tick();
  slow.resolve({ calls: 4, total_usd: 0.01, budget: { remaining_usd: 1 } });
  await a;
  assert.equal(v.est.calls, 99);
});

test('only the last report opened lands, with its own texts', async () => {
  const slow = deferred();
  const client = fakeApi({
    getReport: (id) => (id === 'A' ? slow.promise : Promise.resolve({ run_id: 'B', dataset: { name: 'am' },
      groups: [{ errors: [{ id: 'pos-01' }] }] })),
    getDataset: async (name, params) => (params?.ids ? { items: [{ id: 'pos-01', state: 'text of B' }] } : { name }),
  });
  const v = view(client);
  v.selected = 'am';
  const a = v.openReport('A');
  await v.openReport('B');
  // the late report is of another dataset: it must not switch the screen to it either
  slow.resolve({ run_id: 'A', dataset: { name: 'other' }, groups: [{ errors: [{ id: 'pos-01' }] }] });
  await a;
  assert.equal(v.report.run_id, 'B');
  assert.equal(v.selected, 'am');
  assert.deepEqual(v.texts, { 'pos-01': 'text of B' });
});

test('an engine that went away is unpicked; the paid ones are picked by default', async () => {
  const v = view(fakeApi());
  v.picked = new Set(['gone', 'or-d1.1']);
  await v.load();
  assert.deepEqual([...v.picked], ['or-d1.1']);
  const w = view(fakeApi());
  await w.load();
  assert.deepEqual([...w.picked].sort(), ['or-d1.1', 'or-no-zdr']);
});

test('confusion rows: a row per label seen, the diagonal marked', () => {
  const rows = confusionRows({ options: ['a', 'b', 'c'], confusion: { a: { a: 3, b: 1 }, c: { c: 2 } } });
  assert.deepEqual(rows, [{ label: 'a', counts: [3, 1, 0], diag: 0 }, { label: 'c', counts: [0, 0, 2], diag: 2 }]);
  assert.deepEqual(confusionRows(null), []);
});
