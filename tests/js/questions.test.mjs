// node --test tests/js: the question editor's round trip (web/js/questions.js) — review P1.
import assert from 'node:assert/strict';
import { test } from 'node:test';

import { fromRequest, toRequest } from '../../web/js/questions.js';

const roundTrip = (q) => toRequest(fromRequest(q));

test('d1 choice + multiple becomes multi and stays multi (the HIGH finding)', () => {
  const out = roundTrip({ p: { type: 'choice', multiple: true, instructions: '?', criteria: { a: 'A', b: null } } });
  assert.equal(out.p.type, 'multi');
  assert.equal('multiple' in out.p, false);
  assert.deepEqual(out.p.criteria, { a: 'A', b: null });
});

test('plain choice, noul, score and number survive the round trip', () => {
  const q = {
    c: { type: 'choice', instructions: 'Which?', criteria: { a: 'A', b: null } },
    n: { type: 'noul', instructions: 'Yes?', criteria: { true: 'yes', false: 'no' } },
    s: { type: 'score', instructions: 'How much?', criteria: ['low', 'mid', 'high'] },
    x: { type: 'number', instructions: 'Amount?', range: [0, 100], step: 10, unit: 'RUB' },
    d: { type: 'date', instructions: 'When?' },
  };
  assert.deepEqual(roundTrip(q), q);
});

test('structured instructions stay structured', () => {
  const instructions = { text: 'Is it a refund?', hint: ['money back'] };
  assert.deepEqual(roundTrip({ q: { type: 'noul', instructions } }).q.instructions, instructions);
});

test('a duplicate question key throws instead of dropping a question', () => {
  const list = fromRequest({ a: { type: 'noul', instructions: '1' }, b: { type: 'noul', instructions: '2' } });
  list[1].key = 'a';
  assert.throws(() => toRequest(list), /a/);
});

test('a duplicate option id throws instead of dropping an option', () => {
  const list = fromRequest({ c: { type: 'choice', instructions: '?', criteria: { a: null, b: null } } });
  list[0].options[1][0] = 'a';
  assert.throws(() => toRequest(list));
});

test('a key that names an Object prototype member is an ordinary key', () => {
  const out = roundTrip({ constructor: { type: 'noul', instructions: '?' }, valueof: { type: 'noul', instructions: '?' } });
  assert.deepEqual(Object.keys(out), ['constructor', 'valueof']);
});
