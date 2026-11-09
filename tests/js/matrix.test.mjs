// node --test tests/js: the matrix cells and the History guards of P3 (review P3: J1–J3 left the suite green).
import assert from 'node:assert/strict';
import { test } from 'node:test';

import { isWorkbenchRun, notWorkbenchMessage, rowActions } from '../../web/js/history.js';
import { cellView, fullContextView, limitView } from '../../web/js/matrix.js';

test('a capability cell shows its own mark, and anything unknown as unchecked', () => {
  assert.deepEqual([['yes', '✓'], ['no', '✗'], ['unchecked', '?'], ['never', '—']].map(([v]) => cellView(v).text),
    ['✓', '✗', '?', '—']);
  assert.equal(cellView('yes').cls, 'cap cap-yes');
  assert.equal(cellView('no').cls, 'cap cap-no');
  assert.equal(cellView('bogus').text, '?');
  assert.equal(cellView(undefined).cls, 'cap cap-unchecked');
});

test('a limit is "—" without its type, "?" unknown, "≥" at the largest size tried', () => {
  assert.equal(limitView(240, 'yes', true), '≥240');
  assert.equal(limitView(24, 'yes', false), '24');
  assert.equal(limitView(null, 'unchecked', false), '?');
  assert.equal(limitView(null, 'never', false), '—');
  assert.equal(limitView(240, 'no', true), '—');
});

test('"reads the full context" says how much was read', () => {
  assert.equal(fullContextView(null, null).text, '?');
  assert.equal(fullContextView(true, 59000).text, '✓ 59K');
  assert.equal(fullContextView(false, 900).text, '✗ 900');
  assert.equal(fullContextView(true, null).text, '✓');
});

test('only a comparative run opens, reruns and compares in the Workbench', () => {
  assert.equal(isWorkbenchRun({ kind: 'compare' }), true);
  assert.equal(isWorkbenchRun({}), true);  // records before P3 have no kind
  for (const kind of ['probe', 'dataset', 'experiment']) {
    assert.equal(isWorkbenchRun({ kind }), false);
    assert.deepEqual(rowActions({ kind, tags: [] }), { open: false, rerun: false, pick: false, tags: [] });
  }
  assert.deepEqual(rowActions({ kind: 'compare', tags: ['a'] }), { open: true, rerun: true, pick: true, tags: ['a'] });
});

test('a probe row shows its kind once, and the message names the kind', () => {
  assert.deepEqual(rowActions({ kind: 'probe', tags: ['probe', 'x'] }).tags, ['x']);  // records written before the fix
  assert.match(notWorkbenchMessage('probe'), /Матрица|Matrix/);
  assert.match(notWorkbenchMessage('dataset'), /dataset/);
  assert.doesNotMatch(notWorkbenchMessage('dataset'), /проба|probe/i);
});
