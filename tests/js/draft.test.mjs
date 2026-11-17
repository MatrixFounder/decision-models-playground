// node --test tests/js: the draft backup rule (web/js/draft.js) — review rounds 2-3.
import assert from 'node:assert/strict';
import { test } from 'node:test';

import { backupOnOpen, switchStateText } from '../../web/js/draft.js';

const draft = (fromRun, text) => JSON.stringify({ state: text, fromRun });

test('opening a run keeps the user draft aside', () => {
  const d = draft(null, 'my transcript');
  assert.equal(backupOnOpen(d, null), d);
});

test('a second Open does not replace the user draft with the first opened run', () => {
  const d = draft(null, 'my transcript');
  let backup = backupOnOpen(d, null);         // open run A
  const afterA = draft('RUN_A', 'run A');     // the draft is now run A's
  backup = backupOnOpen(afterA, backup);      // open run B
  assert.equal(backup, d);
});

test('an edit to an opened run is the user draft again', () => {
  const d = draft(null, 'my transcript');
  let backup = backupOnOpen(d, null);
  const edited = draft(null, 'run A, edited');  // userEdited() clears fromRun
  backup = backupOnOpen(edited, backup);
  assert.equal(backup, edited);
});

test('nothing stored or an unreadable draft keeps the existing backup', () => {
  assert.equal(backupOnOpen(null, 'B'), 'B');
  assert.equal(backupOnOpen('{not json', 'B'), 'B');
  assert.equal(backupOnOpen(null, null), null);
});

// The State Text / JSON switch converts the field instead of failing on a plain call text (JSON Parse error, 'О').
const CALL = 'Оператор: Добрый день!\nКлиент: Да, слушаю.';

test('Text → JSON writes a plain call text as a JSON string, so the request sends the same text', () => {
  const json = switchStateText(CALL, 'json');
  assert.equal(JSON.parse(json), CALL);
});

test('JSON → Text unwraps the JSON string: a round trip gives the original text', () => {
  assert.equal(switchStateText(switchStateText(CALL, 'json'), 'text'), CALL);
});

test('Text → JSON keeps a text that already is a JSON object, array or string', () => {
  for (const s of ['{"fragments": []}', '[1, 2]', '"готовая строка"']) assert.equal(switchStateText(s, 'json'), s);
});

test('Text → JSON writes a JSON number, boolean or null as a string: the state stays text', () => {
  for (const s of ['42', 'true', 'null']) assert.equal(JSON.parse(switchStateText(s, 'json')), s);
});

test('JSON → Text keeps an object, a scalar or a broken JSON as it is', () => {
  for (const s of ['{"fragments": []}', '42', '{broken']) assert.equal(switchStateText(s, 'text'), s);
});

test('a blank field stays blank in both directions', () => {
  for (const s of ['', '  \n']) {
    assert.equal(switchStateText(s, 'json'), s);
    assert.equal(switchStateText(s, 'text'), s);
  }
});
