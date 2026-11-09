// node --test tests/js: the draft backup rule (web/js/draft.js) — review rounds 2-3.
import assert from 'node:assert/strict';
import { test } from 'node:test';

import { backupOnOpen } from '../../web/js/draft.js';

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
