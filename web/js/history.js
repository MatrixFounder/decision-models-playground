// History (DESIGN §10.1): list, filter by tag and engine, open, rerun, compare two runs.

import * as api from './api.js';
import { t } from './i18n.js';
import { el } from './questions.js';
import { fmtP, fmtUsd } from './results.js';

const when = (iso) => new Date(iso).toLocaleString();
const own = (obj, key) => (obj && Object.hasOwn(obj, key) ? obj[key] : undefined);

/** One cell of the comparison: the probability of run A's top option in both runs, or, for an extraction, the two
 * values. Pure (node --test): `null` when either run has no answer. */
export function compareCell(x, y) {
  if (!x || !y) return null;
  if (['text', 'number', 'date'].includes(x.type)) {
    const va = x.extract?.value ?? x.extract?.text ?? '∅';
    const vb = y.extract?.value ?? y.extract?.text ?? '∅';
    return { text: `${va} → ${vb}`, sub: '', changed: String(va) !== String(vb) };
  }
  const pa = own(x.probabilities, x.top) ?? 0;
  const pb = own(y.probabilities, x.top) ?? 0;
  const d = pb - pa;
  const delta = Math.abs(d) < 0.0005 ? '±0.000' : `${d > 0 ? '+' : ''}${d.toFixed(3)}`;
  return {
    text: `${x.top}: ${fmtP(pa)} → ${fmtP(pb)}`,
    sub: `${t('delta')} ${delta}${x.top !== y.top ? ` · B: ${y.top}` : ''}`,
    changed: Math.abs(d) >= 0.05 || x.top !== y.top,
  };
}

/** Two runs side by side, the older (by ULID, millisecond order) as A. Keys are matched with Object.hasOwn: a
 * question key such as `constructor` passes KEY_RE and must not match the prototype. Pure (node --test). */
export function compareRuns(r1, r2) {
  const [a, b] = [r1, r2].sort((x, y) => x.id.localeCompare(y.id));
  const res = (run) => Object.fromEntries(run.results.map((r) => [r.engine_id, r]));
  const ra = res(a);
  const rb = res(b);
  const engines = Object.keys(ra).filter((id) => Object.hasOwn(rb, id));
  const keys = Object.keys(a.request.questions).filter((k) => Object.hasOwn(b.request.questions, k));
  const rows = keys.map((key) => ({
    key,
    cells: engines.map((id) => compareCell(own(ra[id].answers, key), own(rb[id].answers, key))),
  }));
  return { a, b, engines, rows };
}

export class History {
  constructor(root, { onOpen, onRerun }) {
    this.root = root;
    this.onOpen = onOpen;
    this.onRerun = onRerun;
    this.rows = [];
    this.picked = new Set();
    this.engineIds = [];
    this.tag = '';
    this.engine = '';
  }

  setEngines(ids) {
    this.engineIds = ids;
  }

  async load() {
    try {
      this.rows = (await api.listRuns({ tag: this.tag, engine: this.engine, limit: 200 })).runs;
      this.error = null;
    } catch (e) {
      this.error = e.message;
    }
    this.render();
  }

  render() {
    const filters = el('div', { class: 'filters' },
      el('input', { value: this.tag, placeholder: t('filter_tag'), onchange: (e) => { this.tag = e.target.value.trim(); this.load(); } }),
      el('select', { onchange: (e) => { this.engine = e.target.value; this.load(); } },
        el('option', { value: '' }, t('filter_engine')),
        this.engineIds.map((id) => el('option', { value: id, selected: id === this.engine }, id))),
      el('button', { class: 'ghost', onclick: () => this.load() }, '↻'),
      el('div', { class: 'spacer' }),
      el('button', { onclick: () => this.compare() }, t('compare')));
    const out = [filters];
    if (this.error) out.push(el('p', { class: 'error' }, this.error));
    if (!this.rows.length) {
      out.push(el('p', { class: 'dim empty' }, t('no_runs')));
    } else {
      const head = el('tr', {}, el('th', {}, ''), el('th', {}, t('when')), el('th', {}, t('title')),
        el('th', {}, t('engines')), el('th', {}, t('questions')), el('th', {}, t('cost_col')), el('th', {}, ''));
      const body = this.rows.map((r) => el('tr', {},
        el('td', {}, el('input', { type: 'checkbox', checked: this.picked.has(r.id),
          onchange: (e) => { if (e.target.checked) this.picked.add(r.id); else this.picked.delete(r.id); } })),
        el('td', { class: 'nowrap' }, when(r.created_at)),
        el('td', {}, r.title || el('span', { class: 'dim mono small' }, r.id),
          ...(r.tags || []).map((g) => el('span', { class: 'chip' }, g))),
        el('td', {}, Object.entries(r.status || {}).map(([id, s]) => el('span', { class: `chip st-${s}` }, id))),
        el('td', { class: 'mono small' }, (r.questions || []).join(', ')),
        el('td', { class: 'mono nowrap' }, fmtUsd(r.cost_usd)),
        el('td', { class: 'nowrap' },
          el('button', { class: 'ghost', onclick: () => this.open(r.id) }, t('open')),
          el('button', { class: 'ghost', onclick: () => this.onRerun(r.id) }, t('rerun')))));
      out.push(el('div', { class: 'table-wrap' }, el('table', { class: 'history' }, el('thead', {}, head), el('tbody', {}, body))));
    }
    out.push(el('div', { id: 'compare' }));
    this.root.replaceChildren(...out);
  }

  async open(id) {
    try {
      this.onOpen(await api.getRun(id));
    } catch (e) {
      this.error = e.message;
      this.render();
    }
  }

  async compare() {
    const box = this.root.querySelector('#compare');
    const ids = [...this.picked];
    if (ids.length !== 2) {
      box.replaceChildren(el('p', { class: 'dim' }, t('select_two')));
      return;
    }
    let runs;
    try {
      runs = await Promise.all(ids.map((id) => api.getRun(id)));
    } catch (e) {
      box.replaceChildren(el('p', { class: 'error' }, e.message));
      return;
    }
    const { a, b, engines, rows } = compareRuns(runs[0], runs[1]);
    const head = el('tr', {}, el('th', {}, t('questions')), engines.map((id) => el('th', {}, id)));
    const body = rows.map((row) => el('tr', {}, el('th', { class: 'mono' }, row.key), row.cells.map((c) => (c
      ? el('td', { class: c.changed ? 'diff' : '' }, el('div', { class: 'mono' }, c.text),
        c.sub ? el('div', { class: 'mono small' }, c.sub) : '')
      : el('td', { class: 'dim' }, '—')))));
    box.replaceChildren(
      el('h3', {}, t('compare_title')),
      el('p', { class: 'dim small' }, `A ${when(a.created_at)} · ${a.title || a.id}  —  B ${when(b.created_at)} · ${b.title || b.id}`),
      el('div', { class: 'table-wrap' }, el('table', { class: 'compare' }, el('thead', {}, head), el('tbody', {}, body))));
  }

}
