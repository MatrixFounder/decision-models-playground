// The capability matrix (TASK UC-4): configured engines with their latest probe, then OpenRouter's catalog.
// A cell is yes / no / unchecked (a probe has not observed it) / never (the engine kind cannot serve it).

import * as api from './api.js';
import { t } from './i18n.js';
import { el } from './questions.js';
import { fmtUsd } from './results.js';

export const COLUMNS = ['noul', 'choice', 'score', 'multi', 'text', 'number', 'date', 'audio'];
const MARK = { yes: '✓', no: '✗', unchecked: '?', never: '—' };

/** One capability cell's text and class. Pure (node --test). */
export function cellView(value) {
  const v = MARK[value] ? value : 'unchecked';
  return { text: MARK[v], cls: `cap cap-${v}`, title: t(`cap_${v}`) };
}

/** A limit cell: '—' when its type is not served at all, '?' unknown, '≥240' when the largest size the probe tried
 * was served (the engine keeps its own ceiling, TASK UC-4 rule 2). Pure. */
export function limitView(value, cell, atTop) {
  if (cell === 'never' || cell === 'no') return '—';
  if (value == null) return '?';
  return atTop ? `≥${value}` : String(value);
}

const kTokens = (n) => (n >= 1000 ? `${Math.round(n / 1000)}K` : String(n));

/** "Reads the full context" with how much the long step read: ✓ means "all of these tokens". Pure. */
export function fullContextView(reads, tokens) {
  if (reads == null) return { text: '?', title: t('long_context_hint') };
  const mark = reads ? '✓' : '✗';
  return tokens ? { text: `${mark} ${kTokens(tokens)}`, title: t('long_tested', { n: tokens.toLocaleString() }) }
    : { text: mark, title: t('long_context_hint') };
}

const yesNo = (v) => (v == null ? '?' : v ? '✓' : '✗');
const day = (iso) => (iso ? new Date(iso).toLocaleDateString() : '—');

export class MatrixView {
  constructor(root, { onBudget }) {
    this.root = root;
    this.onBudget = onBudget;
    this.data = null;
    this.error = null;  // loading the matrix
    this.refreshError = null;  // the last catalog refresh: kept until the next one
    this.progress = {};  // engine id → text while a probe runs
    this.running = null;
    this.long = {};  // engine id → the long-context box, kept across re-renders
    this.catalogOpen = false;
  }

  async load() {
    try {
      this.data = await api.getMatrix();
      this.error = null;
    } catch (e) {
      this.error = e.message;
    }
    this.render();
  }

  async refresh() {
    try {
      await api.refreshCatalog();
      this.refreshError = null;
    } catch (e) {
      this.refreshError = e.message;
    }
    this.load();
  }

  async probe(r) {
    if (this.running) return;
    const longContext = !!this.long[r.id];
    // the probe's text is synthetic, but an engine configured without ZDR still sends nothing without a yes
    if (r.zdr === false && !window.confirm(t('probe_no_zdr', { id: r.id }))) return;
    if (longContext) {  // the long step can cost a share of the day's budget: show the estimate first
      let est;
      try {
        est = await api.probeEstimate({ engine_id: r.id, long_context: true });
      } catch (e) {
        this.progress[r.id] = e.message;
        this.render();
        return;
      }
      if (est.estimate_usd > 0 && !window.confirm(t('probe_confirm', {
        est: fmtUsd(est.estimate_usd), rem: fmtUsd(est.budget.remaining_usd) }))) return;
    }
    if (this.running) return;  // another probe started while a dialog was open
    const id = r.id;
    this.running = id;
    this.progress[id] = t('probing');
    this.render();
    api.probe({ engine_id: id, long_context: longContext, allow_no_zdr: r.zdr === false }, (name, p) => {
      if (name === 'probe_started') {
        this.progress[id] = `${t('probing')} · ${t('estimate')} ${fmtUsd(p.estimate_usd)}`;
      } else if (name === 'step_done') {
        this.progress[id] = `${p.step}: ${t(`reading_${p.reading}`)}`;
      } else if (['probe_done', 'budget_stop', 'probe_error', 'fatal'].includes(name)) {
        this.running = null;
        this.progress[id] = name === 'probe_done'
          ? (p.no_profile ? t('no_profile', { failure: p.failure || '—' }) : `${t('probe_done')} · ${fmtUsd(p.cost_usd)}`)
          : (p.message || t('budget_stop', { est: fmtUsd(p.estimate_usd), rem: fmtUsd(p.remaining_usd) }));
        this.onBudget();
        this.load();
        return;
      }
      this.render();
    });
  }

  row(r) {
    const engine = r.source === 'engine';
    const cells = COLUMNS.map((c) => {
      const v = cellView(r.cells?.[c]);
      return el('td', { class: v.cls, title: v.title }, v.text);
    });
    let action = '';
    if (engine) {
      const box = el('input', { type: 'checkbox', checked: !!this.long[r.id],
        onchange: (e) => { this.long[r.id] = e.target.checked; } });
      const hint = r.kind === 'mlx_local' ? t('probe_local_hint') : (r.reason || '');
      action = el('div', { class: 'nowrap' },
        el('button', { class: 'ghost', disabled: !r.available || !!this.running, title: r.available ? hint : r.reason,
          onclick: () => this.probe(r) }, t('probe')),
        el('label', { class: 'small dim', title: t('long_context_hint') }, box, ` ${t('long_context')}`));
    }
    const full = engine ? fullContextView(r.reads_full_context, r.long_context_tokens) : { text: '?', title: '' };
    return el('tr', { class: engine ? '' : 'catalog-row' },
      el('th', {}, el('div', {}, el('b', {}, r.id)),
        r.model !== r.id ? el('div', { class: 'dim small mono' }, r.model) : '',
        engine && !r.available ? el('div', { class: 'dim small' }, r.reason) : '',
        r.stale ? el('span', { class: 'chip warn', title: r.stale }, t('stale')) : '',
        this.progress[r.id] ? el('div', { class: 'small mono' }, this.progress[r.id]) : ''),
      cells,
      el('td', { class: 'mono' }, limitView(r.max_choice_options, r.cells?.choice, r.max_choice_at_top)),
      el('td', { class: 'mono' }, limitView(r.max_score_levels, r.cells?.score, r.max_score_at_top)),
      el('td', { class: 'nowrap', title: full.title }, full.text),
      el('td', { class: 'mono' }, r.context_length ? r.context_length.toLocaleString() : '—'),
      el('td', { class: 'mono' }, r.price_per_mtok == null ? '—' : `$${r.price_per_mtok}`),
      el('td', {}, r.zdr_endpoint == null ? (r.kind === 'mlx_local' ? t('local') : '?') : yesNo(r.zdr_endpoint)),
      el('td', { class: 'mono small' }, r.expiration_date || '—'),
      el('td', {}, r.kind === 'mlx_local' ? '—' : engine ? yesNo(r.reports_cost) : '?'),  // local is free: nothing to report
      el('td', { class: 'mono small' }, r.snapshot || '—'),
      el('td', { class: 'small' }, engine ? `${day(r.probed_at)}${r.probe_cost_usd ? ` · ${fmtUsd(r.probe_cost_usd)}` : ''}` : '—', action));
  }

  render() {
    const head = el('tr', {}, el('th', {}, t('model')), COLUMNS.map((c) => el('th', { class: 'mono' }, c)),
      ['max_options', 'max_levels', 'full_context', 'context', 'price', 'zdr', 'expires', 'cost_reported', 'snapshot',
        'probe_col'].map((k) => el('th', {}, t(k))));
    const rows = this.data?.rows || [];
    const engines = rows.filter((r) => r.source === 'engine');
    const catalog = rows.filter((r) => r.source === 'catalog');
    const out = [el('div', { class: 'filters' }, el('h2', { class: 'section' }, t('matrix')), el('div', { class: 'spacer' }),
      el('span', { class: 'dim small' }, Object.entries(MARK).map(([k, m]) => `${m} ${t(`cap_${k}`)}`).join(' · ')),
      el('button', { class: 'ghost', onclick: () => this.refresh() }, t('refresh_catalog')))];
    for (const e of [this.error, this.refreshError, this.data?.catalog?.error && t('catalog_error', { e: this.data.catalog.error })]) {
      if (e) out.push(el('p', { class: 'error' }, e));
    }
    out.push(el('div', { class: 'table-wrap' }, el('table', { class: 'matrix' }, el('thead', {}, head),
      el('tbody', {}, engines.map((r) => this.row(r))))));
    if (catalog.length) {
      out.push(el('details', { class: 'off-list', open: this.catalogOpen,
        ontoggle: (e) => { this.catalogOpen = e.target.open; } },
      el('summary', { class: 'dim small' }, t('catalog_rows', { n: catalog.length })),
      el('div', { class: 'table-wrap' }, el('table', { class: 'matrix' }, el('thead', {}, head.cloneNode(true)),
        el('tbody', {}, catalog.map((r) => this.row(r)))))));
    }
    this.root.replaceChildren(...out);
  }
}
