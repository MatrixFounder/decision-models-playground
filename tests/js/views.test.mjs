// node --test tests/js: the view-bound stream handler and the run comparison — review round 2.
import assert from 'node:assert/strict';
import { test } from 'node:test';

import { compareCell, compareRuns } from '../../web/js/history.js';
import { makeHandler } from '../../web/js/runview.js';

function harness() {
  const log = { render: [], meta: [], finish: [] };
  let current = null;
  const deps = {
    getCurrent: () => current,
    render: (v) => log.render.push(v.name),
    setMeta: (kind) => log.meta.push(kind),
    finish: (name, p, v) => log.finish.push([name, v.name]),
  };
  return { log, deps, show: (v) => { current = v; } };
}

test('events of a running stream never land in a run opened meanwhile', () => {
  const h = harness();
  const running = { name: 'A', results: {} };
  const opened = { name: 'B', results: { e1: { status: 'ok' } } };
  const on = makeHandler(running, h.deps);
  h.show(running);
  on('run_started', { run_id: 'X' });
  h.show(opened);  // the user opens another run while A streams
  on('engine_done', { engine_id: 'e1', result: { status: 'failed' } });
  on('run_done', { run_id: 'X', cost_usd: 0 });
  assert.equal(opened.results.e1.status, 'ok');           // B untouched
  assert.equal(running.results.e1.status, 'failed');      // A still gets its result
  assert.deepEqual(h.log.render, []);                     // B's table not redrawn with A's data
  assert.deepEqual(h.log.meta, ['started']);              // B's meta line not relabelled
  assert.deepEqual(h.log.finish, [['run_done', 'A']]);    // running state still ends
});

test('a terminal event of any kind finishes the run', () => {
  for (const name of ['run_done', 'budget_stop', 'run_error', 'fatal']) {
    const h = harness();
    const v = { name: 'A', results: {} };
    h.show(v);
    makeHandler(v, h.deps)(name, { message: 'x' });
    assert.deepEqual(h.log.finish, [[name, 'A']]);
  }
});

const run = (id, answers) => ({
  id, created_at: '2026-11-09T12:00:00Z', request: { questions: Object.fromEntries(Object.keys(answers).map((k) => [k, {}])) },
  results: [{ engine_id: 'e1', answers }],
});
const choice = (top, p) => ({ type: 'choice', top, probabilities: { a: p, b: 1 - p } });

test('A is the older run by ULID even within the same second', () => {
  const older = run('01M6ZS5GMSC37266MRXJZ5XN84', { q: choice('a', 0.9) });
  const newer = run('01M6ZS5GMTAAAAAAAAAAAAAAAA', { q: choice('a', 0.7) });
  const { a, rows } = compareRuns(newer, older);
  assert.equal(a.id, older.id);
  assert.equal(rows[0].cells[0].text, 'a: 0.900 → 0.700');
  assert.equal(rows[0].cells[0].changed, true);
});

test('a question key named like an Object member is compared as an ordinary key', () => {
  const x = run('01A', { constructor: choice('a', 0.6) });
  const y = run('01B', { valueof: choice('a', 0.6) });
  assert.deepEqual(compareRuns(x, y).rows, []);                      // no common key, no crash
  const z = run('01C', { constructor: choice('a', 0.6) });
  assert.equal(compareRuns(x, z).rows[0].cells[0].changed, false);
});

test('extraction answers compare their values, never NaN', () => {
  const ex = (v) => ({ type: 'date', top: null, probabilities: { found: 0.9 }, extract: { value: v, text: v } });
  const same = compareCell(ex('2026-11-12'), ex('2026-11-12'));
  assert.equal(same.text, '2026-11-12 → 2026-11-12');
  assert.equal(same.changed, false);
  assert.equal(compareCell(ex('2026-11-12'), ex(null)).changed, true);
  assert.equal(compareCell(choice('a', 0.5), null), null);
});

test('a drop in found marks an extraction as changed even with the same value', () => {
  const ex = (found) => ({ type: 'text', top: null, probabilities: { found }, extract: { value: 'А-48213', found } });
  const cell = compareCell(ex(0.9), ex(0.2));
  assert.equal(cell.changed, true);
  assert.match(cell.sub, /0\.900 → 0\.200/);
  assert.equal(compareCell(ex(0.9), ex(0.88)).changed, false);
});
