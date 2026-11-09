// node --test tests/js: the calibration charts' geometry and the Datasets screen's pure helpers (P4).
import assert from 'node:assert/strict';
import { test } from 'node:test';

import { pathOf, reliabilityGeometry, scale, thresholdGeometry } from '../../web/js/charts.js';
import { datasetName, fileFormat, labelSummary, pct, progressShare } from '../../web/js/datasets.js';

test('scale maps a domain onto a range, inverted axes included', () => {
  const sy = scale([0, 1], [100, 0]);
  assert.equal(sy(0), 100);
  assert.equal(sy(1), 0);
  assert.equal(sy(0.25), 75);
});

test('a path breaks where a value is missing', () => {
  const id = (v) => v;
  assert.equal(pathOf([[0, 1], [1, 2], [2, 3]], id, id), 'M0.0,1.0L1.0,2.0L2.0,3.0');
  assert.equal(pathOf([[0, 1], [1, null], [2, 3], [3, 4]], id, id), 'M0.0,1.0M2.0,3.0L3.0,4.0');
  assert.equal(pathOf([[0, null]], id, id), '');
});

test('the threshold chart draws agreement only for noul, and marks both thresholds', () => {
  const noul = { curve: [{ t: 0.5, agreement: 0.75, coverage: 1, accuracy: 0.75 }, { t: 0.99, agreement: 1, coverage: 0.5, accuracy: null }],
    whip: { t: 0.61 }, selective: { t: 0.7 } };
  const g = thresholdGeometry(noul);
  assert.deepEqual(g.lines.map((l) => l.key), ['agreement', 'coverage', 'accuracy']);
  assert.deepEqual(g.marks.map((m) => m.t), [0.61, 0.7]);
  assert.ok(g.marks[0].x < g.marks[1].x);
  const choice = thresholdGeometry({ curve: [{ t: 0.5, coverage: 1, accuracy: 0.5 }], selective: { t: null } });
  assert.deepEqual(choice.lines.map((l) => l.key), ['coverage', 'accuracy']);
  assert.deepEqual(choice.marks, []);
  assert.deepEqual(thresholdGeometry(null).lines, []);
});

test('reliability points sit at (mean probability, frequency), the biggest bin largest', () => {
  const g = reliabilityGeometry([{ mean_p: 0.1, freq: 0, n: 1 }, { mean_p: 0.9, freq: 1, n: 9 }]);
  assert.equal(g.points.length, 2);
  assert.ok(g.points[0].cx < g.points[1].cx && g.points[0].cy > g.points[1].cy);
  assert.ok(g.points[1].r > g.points[0].r);
  assert.deepEqual(reliabilityGeometry([]).points, []);
});

test('the file format and a dataset name from a file name', () => {
  assert.equal(fileFormat('labels.CSV'), 'csv');
  assert.equal(fileFormat('items.jsonl'), 'jsonl');
  assert.equal(fileFormat('x.json'), 'jsonl');
  assert.equal(datasetName('Мои звонки 2026.jsonl'), '2026');
  assert.equal(datasetName('Calls Nov.csv'), 'calls_nov');
  assert.equal(datasetName('___'), 'dataset');
  assert.equal(datasetName('a'.repeat(80) + '.csv').length, 64);
});

test('labels, shares and percentages', () => {
  assert.equal(labelSummary({ is_tag: { true: 31, false: 28 } }), 'true 31 · false 28');
  assert.equal(labelSummary({ a: { x: 1 }, b: { y: 2 } }), 'a: x 1; b: y 2');
  assert.equal(progressShare(3, 4), 0.75);
  assert.equal(progressShare(5, 4), 1);
  assert.equal(progressShare(1, 0), 0);
  assert.equal(pct(0.9), '90.0 %');
  assert.equal(pct(null), '—');
});
