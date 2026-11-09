// node --test tests/js: the Experiments screen — its pure helpers and its money and consent paths with a fake API.
import assert from 'node:assert/strict';
import { test } from 'node:test';

import { ExperimentsView, gridCalls, heatColor, parseFormulations, shownOrder } from '../../web/js/experiments.js';

const tick = () => new Promise((r) => setTimeout(r, 0));
const ENGINES = [
  { id: 'or-d1.1', available: true, paid: true, zdr: true },
  { id: 'or-no-zdr', available: true, paid: true, zdr: false },
  { id: 'mlx-d1-2b', available: true, paid: false },
  { id: 'gone', available: false },
];
const REQUEST = { state: 'x', questions: { q: { type: 'noul', instructions: '?' } } };

function fakeApi(over = {}) {
  const calls = { runs: [], estimates: [] };
  return {
    calls,
    listRuns: async () => ({ runs: [] }),
    estimateExperiment: async (body) => {
      calls.estimates.push(body);
      const paid = body.engines.some((id) => id.startsWith('or-'));
      return { calls: 10 * body.engines.length, total_usd: paid ? 0.01 : 0, budget: { remaining_usd: 1 },
        grid: { formulations: ['base'], orders: 1, repeats: 10 } };
    },
    runExperiment: (body) => { calls.runs.push(body); },
    getReport: async (id) => ({ run_id: id, kind: 'experiment' }),
    reportUrl: () => '#',
    ...over,
  };
}

function view(client, { selected = ['or-d1.1'], request = () => REQUEST, confirms = [] } = {}) {
  const v = new ExperimentsView(null, { getEngines: () => ENGINES, getSelected: () => selected, getRequest: request,
    onBudget: () => {}, client, confirm: (text) => { confirms.push(text); return confirms.answer ?? true; } });
  return v;
}

test('formulations parse by name, and an error names the wording', () => {
  assert.deepEqual(parseFormulations([{ name: ' en ', text: '{"q": {"type": "noul", "instructions": "?"}}' }]),
    { en: { q: { type: 'noul', instructions: '?' } } });
  assert.throws(() => parseFormulations([{ name: 'en', text: '{oops' }]), /en/);
  assert.throws(() => parseFormulations([{ name: '', text: '{}' }]));
  assert.throws(() => parseFormulations([{ name: 'en', text: '{}' }, { name: 'en', text: '{}' }]), /en/);
});

test('heat colours, shown orders and the grid size', () => {
  assert.equal(heatColor(null), 'transparent');
  assert.equal(heatColor(0), 'rgba(59, 108, 240, 0.080)');
  assert.equal(heatColor(1), 'rgba(59, 108, 240, 0.800)');
  assert.equal(heatColor(7), heatColor(1));
  const orders = { topic: [['a', 'b'], ['b', 'a']] };
  assert.deepEqual(shownOrder(orders, 'topic', 3, ['a', 'b']), ['b', 'a']);  // cycles
  assert.deepEqual(shownOrder(orders, 'yes', 2, ['true', 'false']), ['true', 'false']);  // not reordered
  assert.equal(gridCalls(2, 6, 5, 3), 180);
});

test('the Workbench request is taken as it is, and an invalid one says why', async () => {
  const v = view(fakeApi(), { selected: ['or-d1.1', 'gone'] });
  v.takeFromWorkbench();
  assert.deepEqual(v.request, REQUEST);
  assert.deepEqual([...v.picked], ['or-d1.1']);  // an unavailable engine is not carried over
  const w = view(fakeApi(), { request: () => { throw new Error('JSON is not valid'); } });
  w.takeFromWorkbench();
  assert.equal(w.request, null);
  assert.equal(w.requestError, 'JSON is not valid');
  assert.equal(await w.run(), false);
});

test('the no-ZDR consent belongs to one experiment', async () => {
  const client = fakeApi();
  const v = view(client, { selected: ['or-no-zdr'] });
  v.takeFromWorkbench();
  v.allowNoZdr = true;
  assert.equal(await v.run(), true);
  assert.equal(client.calls.runs[0].options.allow_no_zdr, true);
  assert.equal(v.allowNoZdr, false);
  v.running = false;
  await v.run();
  assert.equal(client.calls.runs[1].options.allow_no_zdr, false);
});

test('a paid experiment is confirmed on a fresh estimate; a free one is not asked', async () => {
  const confirms = [];
  const client = fakeApi();
  const v = view(client, { selected: ['mlx-d1-2b'], confirms });
  v.takeFromWorkbench();
  v.est = { calls: 10, total_usd: 0, budget: { remaining_usd: 1 } };
  v.picked.add('or-d1.1');  // Run before the new estimate came back
  confirms.answer = false;
  assert.equal(await v.run(), false);
  assert.match(confirms[0], /20/);  // the fresh estimate: 2 engines, not the stale 10 calls
  assert.equal(client.calls.runs.length, 0);
  const free = view(fakeApi(), { selected: ['mlx-d1-2b'], confirms: [] });
  free.takeFromWorkbench();
  assert.equal(await free.run(), true);
});

test('a broken formulation stops the run before any call', async () => {
  const client = fakeApi();
  const v = view(client);
  v.takeFromWorkbench();
  v.forms = [{ name: 'en', text: '{not json' }];
  assert.equal(await v.run(), false);
  assert.match(v.estError, /en/);
  assert.equal(client.calls.runs.length, 0);
});

test('a late estimate or report never lands over a newer one', async () => {
  let release;
  const slow = new Promise((r) => { release = r; });
  let first = true;
  const client = fakeApi({
    estimateExperiment: (body) => (first ? (first = false, slow) : Promise.resolve({ calls: 99, total_usd: 0, budget: { remaining_usd: 1 }, grid: {} })),
    getReport: (id) => (id === 'A' ? slow.then(() => ({ run_id: 'A' })) : Promise.resolve({ run_id: 'B' })),
  });
  const v = view(client);
  v.takeFromWorkbench();  // the first (slow) estimate
  v.changed();  // a newer one
  await tick();
  const a = v.openReport('A');
  await v.openReport('B');
  release({ calls: 1, total_usd: 0, budget: { remaining_usd: 1 }, grid: {} });
  await a;
  await tick();
  assert.equal(v.est.calls, 99);
  assert.equal(v.report.run_id, 'B');
});

// --- review P5 ---------------------------------------------------------------------------------------------------------
import { pullsFirst, reportScreen } from '../../web/js/experiments.js';

test('a pull to the first place is read against the expected share and the lift, not 1/n', () => {
  // 5 options, random orders: an order-invariant model at 0.5 with 0.5 expected is no pull (it was 0.5 vs 0.2 before)
  assert.equal(pullsFirst({ first_position_share: 0.5, first_position_expected: 0.5, first_position_chance: 0.2, first_position_lift: 0 }), false);
  assert.equal(pullsFirst({ first_position_share: 1, first_position_expected: 0.33, first_position_lift: 0.6 }), true);
  assert.equal(pullsFirst({ first_position_share: 0.34, first_position_expected: 0.33, first_position_lift: 0.06 }), true);
  assert.equal(pullsFirst({ first_position_share: null, first_position_expected: null, first_position_lift: 0.01 }), false);
  assert.equal(pullsFirst(null), false);
});

test('a report opens on its own screen', () => {
  assert.equal(reportScreen('experiment'), 'experiments');
  assert.equal(reportScreen('dataset'), 'datasets');
});

test('a grid changed while its estimate was on the way is not sent', async () => {
  let release;
  const pending = new Promise((r) => { release = r; });
  const client = fakeApi({ estimateExperiment: () => pending });
  const v = view(client);
  v.request = REQUEST;
  v.picked = new Set(['or-d1.1']);
  const started = v.run();
  v.repeats = 10;  // edited while the estimate is computed
  release({ calls: 10, total_usd: 0.01, budget: { remaining_usd: 1 }, grid: {} });
  assert.equal(await started, false);
  assert.equal(client.calls.runs.length, 0);
  assert.ok(v.message);
});

test('a run started while the confirm was open is not doubled', async () => {
  const client = fakeApi();
  const v = new ExperimentsView(null, { getEngines: () => ENGINES, getSelected: () => ['or-d1.1'], getRequest: () => REQUEST,
    onBudget: () => {}, client, confirm: () => { v.running = true; return true; } });  // another run began meanwhile
  v.request = REQUEST;
  v.picked = new Set(['or-d1.1']);
  assert.equal(await v.run(), false);
  assert.equal(client.calls.runs.length, 0);
});

test('nothing is taken from the Workbench while an experiment runs', () => {
  let asked = 0;
  const v = view(fakeApi(), { request: () => { asked += 1; return REQUEST; } });
  v.running = true;
  assert.equal(v.takeFromWorkbench(), false);
  assert.equal(asked, 0);
});

test('opening the screen or a report does not read the Workbench; "same settings" takes the report\'s', async () => {
  let asked = 0;
  const report = { run_id: 'R', kind: 'experiment', base: 'base', request: { state: 's', questions: { q: { type: 'noul', instructions: '?' } } },
    formulation_questions: { base: { q: { type: 'noul', instructions: '?' } }, en: { q: { type: 'noul', instructions: 'In English?' } } },
    settings: { orders_enabled: false, k: 8, seed: 3, repeats: 4, engines: ['or-d1.1', 'gone'] } };
  const v = view(fakeApi({ getReport: async () => report }), { request: () => { asked += 1; return REQUEST; } });
  await v.load();
  await v.openReport('R');
  assert.equal(asked, 0);
  assert.equal(v.request, null);
  assert.equal(v.useReport(), true);
  assert.deepEqual(v.request, report.request);
  assert.deepEqual(v.forms.map((f) => f.name), ['en']);
  assert.deepEqual(v.orders, { enabled: false, k: 8, seed: 3 });
  assert.equal(v.repeats, 4);
  assert.deepEqual([...v.picked], ['or-d1.1']);  // an engine gone since is not carried over
});

test('no engine picked: an old estimate error does not stay', async () => {
  const v = view(fakeApi());
  v.request = REQUEST;
  v.estError = 'old';
  v.picked = new Set();
  await v.estimate();
  assert.equal(v.estError, null);
});
