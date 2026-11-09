// The question × engine table (DESIGN §16 Workbench): probability bars, top option, latency, cost, snapshot.

import { t } from './i18n.js';
import { el } from './questions.js';

export const fmtP = (p) => (p == null ? '—' : p.toFixed(3));
export function fmtUsd(v) {
  if (v == null) return '—';
  if (v === 0) return '$0';
  return `$${v >= 0.01 ? v.toFixed(4) : v.toFixed(7)}`;
}
const fmtMs = (ms) => (ms == null ? '—' : ms >= 1000 ? `${(ms / 1000).toFixed(2)} s` : `${ms} ms`);

function bar(label, p, { strong = false, title = '', threshold = null } = {}) {
  const fill = el('span', { class: 'fill' });
  fill.style.width = `${Math.max(0, Math.min(1, p)) * 100}%`;
  const track = el('span', { class: 'track' }, fill);
  if (threshold != null) {
    const mark = el('span', { class: 'mark' });
    mark.style.left = `${threshold * 100}%`;
    track.append(mark);
  }
  return el('div', { class: `bar${strong ? ' strong' : ''}`, title },
    el('span', { class: 'lbl mono' }, label), track, el('span', { class: 'pct mono' }, fmtP(p)));
}

function describe(question, option) {
  const c = question?.criteria;
  if (question?.type === 'score' && Array.isArray(c)) return c[Number(option)] ?? '';
  if (c && typeof c === 'object') return c[option] ?? '';
  return '';
}

function answerCell(question, a) {
  const box = el('div', { class: 'ans' });
  if (a.type === 'noul') {
    box.append(el('div', { class: 'top' }, el('b', {}, a.top), ' ', el('span', { class: 'mono dim' }, fmtP(a.top_p))));
    box.append(bar('P(true)', a.probabilities.true, { strong: true, threshold: 0.5 }));
  } else if (a.type === 'choice' || a.type === 'score') {
    const head = el('div', { class: 'top' }, el('b', {}, a.top), ' ', el('span', { class: 'mono dim' }, fmtP(a.top_p)));
    if (a.type === 'score' && a.expected != null) head.append(el('span', { class: 'mono dim' }, ` · ${t('expected')}=${a.expected.toFixed(2)}`));
    box.append(head);
    if (a.type === 'score' && describe(question, a.top)) box.append(el('div', { class: 'dim small clip' }, describe(question, a.top)));
    let entries = Object.entries(a.probabilities);
    if (a.type === 'choice') entries = entries.sort((x, y) => y[1] - x[1]);
    const shown = a.type === 'choice' ? entries.slice(0, 4) : entries;
    for (const [k, p] of shown) box.append(bar(k, p, { strong: k === a.top, title: describe(question, k) }));
    if (entries.length > shown.length) box.append(el('div', { class: 'dim small' }, `+${entries.length - shown.length}`));
  } else if (a.type === 'multi') {
    for (const [k, p] of Object.entries(a.probabilities)) box.append(bar(k, p, { strong: p >= 0.5, threshold: 0.5, title: describe(question, k) }));
  } else {
    const x = a.extract || {};
    const value = x.value ?? x.text ?? '∅';
    box.append(el('div', { class: 'top' }, el('b', {}, String(value)), x.unit ? ` ${x.unit}` : ''));
    if (x.text && x.value != null && String(x.value) !== x.text) box.append(el('div', { class: 'dim small' }, `«${x.text}»`));
    if (x.found != null) box.append(bar(t('found'), x.found, { threshold: 0.5 }));
    if (x.interval) box.append(el('div', { class: 'dim small mono' }, `p10–p90: ${x.interval[0]} … ${x.interval[1]}`));
  }
  if (a.vendor_confidence != null) box.title = `vendor confidence ${fmtP(a.vendor_confidence)}`;
  return box;
}

function cell(question, key, result) {
  if (result === undefined) return el('td', { class: 'pending' }, t('pending'));
  const failure = result.question_failures?.[key];
  if (failure) return el('td', {}, el('span', { class: `badge ${failure}` }, t(failure)));
  const a = result.answers?.[key];
  if (!a) return el('td', {}, el('span', { class: 'badge invalid_answer' }, result.request_failure || '—'));
  return el('td', {}, answerCell(question, a));
}

function agreement(key, results) {
  const tops = Object.values(results).map((r) => r?.answers?.[key]?.top).filter((x) => x != null);
  if (tops.length < 2) return null;
  return new Set(tops).size === 1;
}

/**
 * view = {request, engines: [{id, model}], results: {engine_id: EngineResult | undefined}}
 */
export function renderResults(root, view) {
  if (!view?.engines?.length) {
    root.replaceChildren(el('p', { class: 'dim empty' }, t('no_run')));
    return;
  }
  const questions = view.request?.questions || {};
  const head = el('tr', {}, el('th', {}, t('questions')),
    view.engines.map((e) => el('th', {}, el('div', {}, e.id), el('div', { class: 'dim small mono' }, e.model || ''))));
  const rows = Object.entries(questions).map(([key, q]) => {
    const agree = agreement(key, view.results);
    return el('tr', {},
      el('th', { class: 'qcell' }, el('div', { class: 'mono' }, key), el('div', { class: 'dim small' }, q.type),
        agree == null ? '' : el('span', { class: `badge ${agree ? 'agree' : 'disagree'}` }, t(agree ? 'agree' : 'disagree'))),
      view.engines.map((e) => cell(q, key, view.results[e.id])));
  });
  const foot = [
    ['status', (r) => el('span', { class: `badge st-${r.status}`, title: r.detail || '' }, r.request_failure || r.status)],
    ['latency', (r) => fmtMs(r.latency_ms)],
    ['cost', (r) => el('span', { title: t(`source_${r.usage?.cost_source || 'none'}`) }, fmtUsd(r.usage?.cost_usd))],
    ['tokens', (r) => (r.usage?.input_tokens == null ? '—' : `${r.usage.input_tokens} / ${r.usage.output_tokens ?? 0}`)],
    ['snapshot', (r) => el('span', { class: 'mono small' }, r.snapshot || '—')],
  ].map(([label, fn]) => el('tr', { class: 'meta' }, el('th', {}, t(label)),
    view.engines.map((e) => {
      const r = view.results[e.id];
      return el('td', {}, r ? fn(r) : '');
    })));
  const details = view.engines.map((e) => view.results[e.id]).filter((r) => r?.detail)
    .map((r) => el('div', { class: 'detail small' }, el('b', {}, r.engine_id), ': ', r.detail));
  root.replaceChildren(
    el('div', { class: 'table-wrap' }, el('table', { class: 'results' }, el('thead', {}, head), el('tbody', {}, rows), el('tfoot', {}, foot))),
    ...details,
  );
}
