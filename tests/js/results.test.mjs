// node --test tests/js: the P6 readings of the results table (DESIGN §14) — spans, multi thresholds, cited turns,
// the playground's own normalization.
import assert from 'node:assert/strict';
import { test } from 'node:test';

import { citedTurn, extractValue, selectedBy, spanSnippet } from '../../web/js/results.js';

const STATE = 'Клиент назвал номер заявки А-48213 и сумму триста пятьдесят тысяч рублей.';

test('a span is cut from the state by code points, as Python counts them', () => {
  const a = STATE.indexOf('А-48213');
  const snip = spanSnippet(STATE, [a, a + 7], 'А-48213', 10);
  assert.equal(snip.match, 'А-48213');
  assert.equal(snip.before, '…ер заявки ');
  assert.equal(snip.after, ' и сумму т…');
  // an emoji is two UTF-16 units and one Python character: the offsets after it still land
  const emoji = '😀 сумма 350 000';
  assert.equal(spanSnippet(emoji, [8, 15], '350 000').match, '350 000');
});

test('a span that is not the answer text is not highlighted', () => {
  assert.equal(spanSnippet(STATE, [0, 6], 'Другое'), null);  // a JSON state's offsets count mlx-vlm's rendering
  assert.equal(spanSnippet({ fragments: [] }, [0, 2], 'x'), null);
  assert.equal(spanSnippet(STATE, [5, 3], null), null);
  assert.equal(spanSnippet(STATE, [0, 999], null), null);
  assert.equal(spanSnippet(STATE, null, 'x'), null);
  assert.equal(spanSnippet(STATE, [0, 6], null).match, 'Клиент');  // no text to check against: the span alone
});

test('the threshold decides the selected options, in the question order', () => {
  const p = { deposit: 0.89, credit_card: 0.01, mortgage: 0.5, insurance: 0.88 };
  assert.deepEqual(selectedBy(p, 0.5), ['deposit', 'mortgage', 'insurance']);  // at the threshold counts
  assert.deepEqual(selectedBy(p, 0.885), ['deposit']);
  assert.deepEqual(selectedBy(p, 0.95), []);
});

test('the cited turn comes from the fragments state', () => {
  const state = { fragments: [{ turn: 't1', participant: 'Оператор', text: 'Здравствуйте' }, { turn: 't2', participant: null, text: 'Да' }] };
  assert.equal(citedTurn(state, 't2').text, 'Да');
  assert.equal(citedTurn(state, 't9'), null);
  assert.equal(citedTurn('plain text', 't1'), null);
});

test("the model's value wins; the playground's reading is marked as its own", () => {
  assert.deepEqual(extractValue({ value: '2026-11-13', normalized: '2026-11-12', text: '12 ноября' }), { value: '2026-11-13', normalized: false });
  assert.deepEqual(extractValue({ value: null, normalized: 350000, text: 'триста пятьдесят тысяч' }), { value: 350000, normalized: true });
  assert.deepEqual(extractValue({ value: 0, normalized: 5 }), { value: 0, normalized: false });  // zero is a value
  assert.deepEqual(extractValue({ value: null, text: '12 ноября' }), { value: '12 ноября', normalized: false });
  assert.deepEqual(extractValue({}), { value: null, normalized: false });
});

// --- review P6 ---------------------------------------------------------------------------------------------------
import { bodyState } from '../../web/js/draft.js';
import { makeHandler } from '../../web/js/runview.js';
import { pointsForRoundedYes, readThreshold } from '../../web/js/results.js';

test('a live pointer run shows the turns the engines read, not the typed text', () => {
  const view = { request: { state: 'Клиент: Перезвоните в четверг.' }, results: {} };
  const rendered = [];
  const on = makeHandler(view, { getCurrent: () => view, render: (v) => rendered.push(v), setMeta: () => {}, finish: () => {} });
  const fragments = { fragments: [{ turn: 't0', participant: 'Клиент', text: 'Перезвоните в четверг.' }] };
  on('run_started', { run_id: 'R', request: { state: fragments, questions: {} } });
  on('engine_done', { engine_id: 'e', result: { answers: {}, pointers: { q: { turn: 't0', probability: 0.9 } } } });
  assert.equal(citedTurn(view.request.state, 't0').text, 'Перезвоните в четверг.');
  assert.equal(rendered.length, 2);  // redrawn with the turns at the start, then with the result
  // the extraction span is not checked against the typed text the engines never read
  assert.equal(spanSnippet(view.request.state, [0, 6], 'Клиент'), null);
  const plain = { request: { state: 'x' }, results: {} };
  makeHandler(plain, { getCurrent: () => plain, render: () => {}, setMeta: () => {}, finish: () => {} })('run_started', { run_id: 'S' });
  assert.equal(plain.request.state, 'x');  // a run without the pointer keeps its own request
});

test('an emptied or invalid threshold field keeps the old threshold', () => {
  assert.equal(readThreshold('', 0.5), 0.5);  // Number('') is 0: every option would be selected
  assert.equal(readThreshold('  ', 0.7), 0.7);
  assert.equal(readThreshold('abc', 0.5), 0.5);
  assert.equal(readThreshold('1.5', 0.5), 0.5);
  assert.equal(readThreshold('-0.1', 0.5), 0.5);
  assert.equal(readThreshold('0.85', 0.5), 0.85);
  assert.equal(readThreshold('0', 0.5), 0);  // typed zero is a choice
  assert.equal(readThreshold('1', 0.5), 1);
});

test('a pointer under a «false» cell is marked as asked for a rounded «да»', () => {
  const ptr = { turn: 't2', probability: 0.9 };
  assert.equal(pointsForRoundedYes({ type: 'noul', top: 'false', probabilities: { true: 0.4996 } }, ptr), true);
  assert.equal(pointsForRoundedYes({ type: 'noul', top: 'true' }, ptr), false);
  assert.equal(pointsForRoundedYes({ type: 'choice', top: 'false' }, ptr), false);
  assert.equal(pointsForRoundedYes({ type: 'noul', top: 'false' }, undefined), false);
});

test('with media, a blank text is no state', () => {
  assert.equal(bodyState('', 1), null);
  assert.equal(bodyState('  \n ', 1), null);
  assert.equal(bodyState(' текст ', 1), ' текст ');
  assert.equal(bodyState('', 0), '');  // without media the server says what is missing
  const obj = { a: 1 };
  assert.equal(bodyState(obj, 1), obj);
});
