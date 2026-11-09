// node --test tests/js: the SSE reader in web/js/api.js always ends with exactly one terminal event — review P1.
import assert from 'node:assert/strict';
import { test } from 'node:test';

import { streamPost } from '../../web/js/api.js';

function serve(...chunks) {
  globalThis.fetch = async () => new Response(new ReadableStream({
    start(c) {
      for (const x of chunks) c.enqueue(new TextEncoder().encode(x));
      c.close();
    },
  }), { status: 200, headers: { 'content-type': 'text/event-stream' } });
}

function collect() {
  const events = [];
  let done;
  // a lost terminal event must fail the test, not hang the suite (review round 2)
  const finished = Promise.race([
    new Promise((r) => { done = r; }),
    new Promise((_, reject) => setTimeout(() => reject(new Error(`no terminal event; got ${JSON.stringify(events.map((e) => e[0]))}`)), 3000)),
  ]);
  const on = (name, payload) => {
    events.push([name, payload]);
    if (['run_done', 'budget_stop', 'run_error', 'fatal'].includes(name)) setTimeout(done, 20);
  };
  return { events, on, finished };
}

const frame = (name, data) => `event: ${name}\ndata: ${JSON.stringify(data)}\n\n`;

test('frames split across chunks are parsed', async () => {
  const f = frame('run_started', { run_id: 'X' }) + frame('run_done', { run_id: 'X', cost_usd: 0 });
  serve(f.slice(0, 7), f.slice(7, 40), f.slice(40));
  const c = collect();
  streamPost('/api/runs', {}, c.on);
  await c.finished;
  assert.deepEqual(c.events.map((e) => e[0]), ['run_started', 'run_done']);
});

test('a stream that ends without a terminal event reports fatal', async () => {
  serve(frame('run_started', { run_id: 'X' }), frame('engine_done', { engine_id: 'e' }));
  const c = collect();
  streamPost('/api/runs', {}, c.on);
  await c.finished;
  assert.deepEqual(c.events.map((e) => e[0]), ['run_started', 'engine_done', 'fatal']);
});

test('a terminal frame that is not JSON (a bare NaN) still ends the stream with fatal', async () => {
  serve(frame('run_started', { run_id: 'X' }), 'event: run_done\ndata: {"cost_usd": NaN}\n\n');
  const c = collect();
  streamPost('/api/runs', {}, c.on);
  await c.finished;
  assert.deepEqual(c.events.map((e) => e[0]), ['run_started', 'fatal']);
});

test('nothing after the first terminal event', async () => {
  serve(frame('budget_stop', { estimate_usd: 1 }), frame('run_done', {}));
  const c = collect();
  streamPost('/api/runs', {}, c.on);
  await c.finished;
  assert.deepEqual(c.events.map((e) => e[0]), ['budget_stop']);
});

test('an HTTP error before the stream is fatal with the detail', async () => {
  globalThis.fetch = async () => new Response(JSON.stringify({ detail: 'unknown engines: x' }), { status: 400 });
  const c = collect();
  streamPost('/api/runs', {}, c.on);
  await c.finished;
  assert.deepEqual(c.events, [['fatal', { message: 'unknown engines: x' }]]);
});
