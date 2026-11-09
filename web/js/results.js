// The question × engine table (TASK UC-1): probability bars, top option, latency, cost, snapshot.

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

/** The state around an extraction's span: {before, match, after} by code points (Python's offsets), or null when the
 * state is not text, the span does not fit it, or the text there is not the answer's text (a JSON state's offsets
 * count mlx-vlm's rendering of it, not this one). */
export function spanSnippet(state, span, text, context = 48) {
  if (typeof state !== 'string' || !Array.isArray(span) || span.length !== 2) return null;
  const chars = Array.from(state);
  const [a, b] = span;
  if (!Number.isInteger(a) || !Number.isInteger(b) || a < 0 || b <= a || b > chars.length) return null;
  const match = chars.slice(a, b).join('');
  if (text != null && match !== text) return null;
  const from = Math.max(0, a - context);
  const to = Math.min(chars.length, b + context);
  return {
    before: (from > 0 ? '…' : '') + chars.slice(from, a).join(''),
    match,
    after: chars.slice(b, to).join('') + (to < chars.length ? '…' : ''),
  };
}

/** The threshold typed into a multi row; an empty or invalid field keeps the old one (Number('') is 0, review P6). */
export function readThreshold(text, old) {
  if (typeof text !== 'string' || text.trim() === '') return old;
  const v = Number(text);
  return Number.isFinite(v) && v >= 0 && v <= 1 ? v : old;
}

/** A pointer for a «да» that the cell shows as «false»: P(true) in [0.4995, 0.5) rounds to 0.500 by the route's
 * rule (§14.1), so the route reads it as «да» and points; the screen says so instead of citing for a «нет». */
export function pointsForRoundedYes(answer, pointer) {
  return !!pointer && answer?.type === 'noul' && answer.top === 'false';
}

/** A multi answer's options at or above the threshold, in the question's order. */
export function selectedBy(probabilities, threshold) {
  return Object.entries(probabilities || {}).filter(([, p]) => p >= threshold).map(([k]) => k);
}

/** The fragment a pointer cites, from a pointer run's {"fragments": [...]} state. */
export function citedTurn(state, turn) {
  const frags = state && typeof state === 'object' && Array.isArray(state.fragments) ? state.fragments : [];
  return frags.find((f) => f && f.turn === turn) || null;
}

/** An extraction's value: the model's own, else the playground's reading of its text, marked as such (§14). */
export function extractValue(x = {}) {
  if (x.value != null) return { value: x.value, normalized: false };
  if (x.normalized != null) return { value: x.normalized, normalized: true };
  return { value: x.text ?? null, normalized: false };
}

const clip = (s, n) => (s.length > n ? `${s.slice(0, n - 1)}…` : s);

function describe(question, option) {
  const c = question?.criteria;
  if (question?.type === 'score' && Array.isArray(c)) return c[Number(option)] ?? '';
  if (c && typeof c === 'object') return c[option] ?? '';
  return '';
}

function answerCell(question, a, ctx) {
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
    const th = ctx.threshold;
    const picked = selectedBy(a.probabilities, th);
    box.append(el('div', { class: 'top' }, el('span', { class: 'dim small' }, `${t('selected')} ≥ ${th}: `),
      el('b', {}, picked.length ? picked.join(', ') : t('nothing'))));
    for (const [k, p] of Object.entries(a.probabilities)) box.append(bar(k, p, { strong: p >= th, threshold: th, title: describe(question, k) }));
  } else {
    const x = a.extract || {};
    const { value, normalized } = extractValue(x);
    const head = el('div', { class: 'top' }, el('b', {}, value == null ? '∅' : String(value)), x.unit ? ` ${x.unit}` : '');
    if (normalized) head.append(' ', el('span', { class: 'chip warn', title: t('normalized_hint') }, t('normalized')));
    box.append(head);
    const snip = spanSnippet(ctx.state, x.span, x.text);
    if (snip) {
      box.append(el('div', { class: 'small snippet' }, snip.before, el('mark', {}, snip.match), snip.after));
    } else if (x.text && String(value) !== x.text) {
      box.append(el('div', { class: 'dim small' }, `«${x.text}»`));
    }
    if (x.found != null) box.append(bar(t('found'), x.found, { threshold: 0.5 }));
    if (x.interval) box.append(el('div', { class: 'dim small mono' }, `p10–p90: ${x.interval[0]} … ${x.interval[1]}`));
  }
  if (a.vendor_confidence != null) box.title = `vendor confidence ${fmtP(a.vendor_confidence)}`;
  return box;
}

/** The turn a pointer cites, its probability and what it cost; or why it found none (§14, UC-8). */
function pointerLine(state, p, roundedYes = false) {
  const how = [p.passes ? t('pointer_passes', { n: p.passes }) : t('pointer_no_call'), fmtMs(p.latency_ms), fmtUsd(p.cost_usd)];
  const title = [p.instruction, how.join(' · '), p.detail].filter(Boolean).join('\n');
  if (!p.turn) return el('div', { class: 'pointer', title }, el('span', { class: 'badge st-failed' }, t('pointer_failed', { cls: p.failure || '—' })));
  const f = citedTurn(state, p.turn);
  return el('div', { class: 'pointer small', title },
    roundedYes ? el('span', { class: 'chip warn', title: t('pointer_rounded_hint') }, t('pointer_rounded')) : '',
    '↳ ', el('b', { class: 'mono' }, p.turn), ' ',
    f ? el('mark', {}, `${f.participant ? `${f.participant}: ` : ''}${clip(f.text, 140)}`) : '',
    ' ', el('span', { class: 'mono dim' }, p.probability == null ? t('pointer_one') : fmtP(p.probability)));
}

function cell(question, key, result, ctx) {
  if (result === undefined) return el('td', { class: 'pending' }, t('pending'));
  const failure = result.question_failures?.[key];
  if (failure) return el('td', {}, el('span', { class: `badge ${failure}` }, t(failure)));
  const a = result.answers?.[key];
  if (a) {
    const box = answerCell(question, a, ctx);
    const p = result.pointers?.[key];
    if (p) box.append(pointerLine(ctx.state, p, pointsForRoundedYes(a, p)));
    return el('td', {}, box);
  }
  // no answer and no own failure: the whole request failed, the question was never answered
  return el('td', {}, el('span', { class: 'badge st-failed', title: result.detail || '' },
    result.request_failure ? t('request_failed', { cls: result.request_failure }) : '—'));
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
  const state = view.request?.state;
  view.thresholds = view.thresholds || {};  // multi: the threshold per question, a reading of the answer (not sent)
  const head = el('tr', {}, el('th', {}, t('questions')),
    view.engines.map((e) => el('th', {}, el('div', {}, e.id), el('div', { class: 'dim small mono' }, e.model || ''))));
  const rows = Object.entries(questions).map(([key, q]) => {
    const agree = agreement(key, view.results);
    const multi = q.type === 'multi' || (q.type === 'choice' && q.multiple === true);
    const threshold = view.thresholds[key] ?? 0.5;
    const thInput = multi ? el('label', { class: 'small dim threshold' }, `${t('threshold')} `,
      el('input', { type: 'number', min: 0, max: 1, step: 0.05, value: threshold, class: 'mono',
        onchange: (ev) => {
          view.thresholds[key] = readThreshold(ev.target.value, threshold);
          renderResults(root, view);
        } })) : '';
    return el('tr', {},
      el('th', { class: 'qcell' }, el('div', { class: 'mono' }, key), el('div', { class: 'dim small' }, q.type), thInput,
        agree == null ? '' : el('span', { class: `badge ${agree ? 'agree' : 'disagree'}` }, t(agree ? 'agree' : 'disagree'))),
      view.engines.map((e) => cell(q, key, view.results[e.id], { state, threshold })));
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
