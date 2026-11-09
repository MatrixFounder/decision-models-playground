// Experiments (DESIGN §11, §16): the Workbench's state and questions asked in other wordings, other option orders and
// N times over; the report shows engine × axis → metric and a heatmap of probabilities. Money and consent follow the
// Datasets screen: a fresh estimate is confirmed before a paid run, and consent to skip ZDR lasts one run.

import * as api from './api.js';
import { getLang, t } from './i18n.js';
import { el } from './questions.js';
import { fmtUsd } from './results.js';

const num = (x) => (x == null ? '—' : String(x));
const pct = (x) => (x == null ? '—' : `${(x * 100).toFixed(1)} %`);
const when = (iso) => (iso ? new Date(iso).toLocaleString() : '—');

/** The wordings typed as JSON, by name; an error names the wording it is about. Pure. */
export function parseFormulations(list) {
  const out = {};
  for (const f of list) {
    const name = (f.name || '').trim();
    if (!name) throw new Error(t('ex_form_name'));
    if (Object.hasOwn(out, name)) throw new Error(t('ex_form_twice', { name }));
    try {
      out[name] = JSON.parse(f.text || '{}');
    } catch (e) {
      throw new Error(t('ex_form_json', { name, msg: e.message }));
    }
  }
  return out;
}

/** A heatmap cell's background: the accent at the probability's strength (0 → none, 1 → full). Pure. */
export function heatColor(p) {
  if (p == null || Number.isNaN(p)) return 'transparent';
  const a = Math.max(0, Math.min(1, p));
  return `rgba(59, 108, 240, ${(0.08 + 0.72 * a).toFixed(3)})`;
}

/** The options of a cell as they were shown: order o of the question's orders, the original when it has none. Pure. */
export function shownOrder(orders, question, o, options) {
  const list = orders?.[question];
  return list?.length ? list[o % list.length] : options;
}

/** Whether an order result reads as a pull to the first place: the top was shown first clearly more often than on
 * these orders a model with no pull would show it (not 1/n — beyond 4 options the orders are random), or an option
 * gains probability when it stands first. Pure. */
export function pullsFirst(order) {
  if (!order) return false;
  const share = order.first_position_share;
  const expected = order.first_position_expected;
  return (share != null && expected != null && share - expected >= 0.15) || (order.first_position_lift ?? 0) >= 0.05;
}

/** The screen that opens a report of this kind. Pure (app.js is not loaded by node, review P5). */
export function reportScreen(kind) {
  return kind === 'experiment' ? 'experiments' : 'datasets';
}

/** Calls of the grid, as the server counts them. Pure. */
export function gridCalls(formulations, orders, repeats, engines) {
  return formulations * orders * repeats * engines;
}

export class ExperimentsView {
  constructor(root, { getEngines, getSelected, getRequest, onBudget, client = api, confirm = (text) => window.confirm(text) }) {
    this.root = root;
    this.getEngines = getEngines;
    this.getSelected = getSelected;
    this.getRequest = getRequest;
    this.onBudget = onBudget;
    this.api = client;
    this.confirm = confirm;
    this.request = null;  // the state and questions of the next run: from the Workbench or from a report
    this.requestError = null;
    this.title = '';
    this.forms = [];  // {name, text}: wordings beyond the base
    this.orders = { enabled: true, k: 6, seed: 7 };
    this.repeats = 5;
    this.picked = new Set();
    this.allowNoZdr = false;
    this.est = null;
    this.estError = null;
    this.running = false;
    this.progress = null;
    this.report = null;
    this.runs = [];
    this.message = null;
    this.estSeq = 0;
    this.reportSeq = 0;
    this.loadSeq = 0;
  }

  available() {
    return this.getEngines().filter((e) => e.available);
  }

  /** The Workbench's request and engine selection, as they are now. Not while a run streams: the panel describes the
   * running grid until it ends (review P5). */
  takeFromWorkbench() {
    if (this.running) return false;
    try {
      this.request = this.getRequest();
      this.requestError = null;
    } catch (e) {
      this.request = null;
      this.requestError = e.message;
    }
    const live = new Set(this.available().map((e) => e.id));
    this.picked = new Set(this.getSelected().filter((id) => live.has(id)));
    this.changed();
    return true;
  }

  /** This report's own settings for the next run: its request, wordings, orders, repeats and engines. */
  useReport(report = this.report) {
    if (this.running || !report?.request) return false;
    const s = report.settings || {};
    this.request = report.request;
    this.requestError = null;
    this.forms = Object.entries(report.formulation_questions || {}).filter(([name]) => name !== report.base)
      .map(([name, qs]) => ({ name, text: JSON.stringify(qs, null, 2) }));
    this.orders = { enabled: s.orders_enabled ?? true, k: s.k ?? 6, seed: s.seed ?? 7 };
    this.repeats = s.repeats ?? this.repeats;
    const live = new Set(this.available().map((e) => e.id));
    this.picked = new Set((s.engines || []).filter((id) => live.has(id)));
    this.changed();
    return true;
  }

  /** The list of earlier experiments. The request stays as it is: opening a report must not pair it with whatever
   * the Workbench holds now (review P5) — the panel is filled by "Experiment…" or "Same settings". */
  async load() {
    const seq = ++this.loadSeq;
    const live = new Set(this.available().map((e) => e.id));
    this.picked = new Set([...this.picked].filter((id) => live.has(id)));
    try {
      const runs = (await this.api.listRuns({ kind: 'experiment', limit: 50 })).runs;
      if (seq !== this.loadSeq) return;
      this.runs = runs;
    } catch (e) {
      if (seq !== this.loadSeq) return;
      this.message = e.message;
    }
    this.render();
  }

  /** The request for the current settings; consent to skip ZDR is passed in, never read from the box here. */
  body(allowNoZdr = false) {
    return { request: this.request, formulations: parseFormulations(this.forms), orders: { ...this.orders },
      repeats: this.repeats, engines: [...this.picked], options: { allow_no_zdr: allowNoZdr },
      title: this.title.trim() || null };
  }

  takeConsent() {
    const allow = this.allowNoZdr;
    this.allowNoZdr = false;
    return allow;
  }

  changed() {
    this.est = null;
    this.estimate();
  }

  async estimate() {
    const seq = ++this.estSeq;
    if (!this.request || !this.picked.size) {
      this.estError = null;  // nothing to estimate: no old error stays on screen
      this.render();
      return;
    }
    this.render();
    let body;
    try {
      body = this.body();
    } catch (e) {
      this.estError = e.message;
      this.render();
      return;
    }
    try {
      const est = await this.api.estimateExperiment(body);
      if (seq !== this.estSeq) return;
      this.est = est;
      this.estError = null;
    } catch (e) {
      if (seq !== this.estSeq) return;
      this.est = null;
      this.estError = e.message;
    }
    this.render();
  }

  async run() {
    if (this.running || !this.request || !this.picked.size) return false;
    this.estSeq += 1;  // an estimate still in flight must not land over what the run reports
    let body;
    let est;
    try {
      body = this.body();
      est = await this.api.estimateExperiment(body);
    } catch (e) {
      this.estError = e.message;
      this.render();
      return false;
    }
    let now;
    try {
      now = this.body();
    } catch (e) {
      now = null;
    }
    if (this.running) return false;
    if (JSON.stringify(now) !== JSON.stringify(body)) {  // the settings changed while the estimate was on its way
      this.message = t('ex_changed');  // a new estimate follows; confirm that one
      this.changed();
      return false;
    }
    this.est = est;
    const noZdr = this.available().filter((e) => this.picked.has(e.id) && e.zdr === false).map((e) => e.id);
    const skipsZdr = this.allowNoZdr && noZdr.length > 0;
    if (est.total_usd > 0 || skipsZdr) {
      let text = t('ex_confirm', { calls: est.calls, est: fmtUsd(est.total_usd), rem: fmtUsd(est.budget.remaining_usd) });
      if (skipsZdr) text += `\n${t('ds_confirm_no_zdr', { engines: noZdr.join(', ') })}`;
      if (!this.confirm(text)) return false;
    }
    if (this.running) return false;
    const consent = this.takeConsent();
    this.running = true;
    this.progress = { done: 0, total: est.calls };
    this.report = null;
    this.message = null;
    this.render();
    this.api.runExperiment(this.body(consent), (ev, p) => {
      if (ev === 'run_started') this.progress = { done: 0, total: p.calls };
      else if (ev === 'progress') this.progress = { done: p.done, total: p.total };
      else if (['run_done', 'budget_stop', 'run_error', 'fatal'].includes(ev)) {
        this.running = false;
        this.progress = null;
        this.onBudget();
        if (p.run_id) {
          this.message = ev === 'budget_stop' ? t('ex_budget_partial') : (p.report_error || null);
          this.openReport(p.run_id);
          this.load();
          return;
        }
        this.message = p.message || t('budget_stop', { est: fmtUsd(p.estimate_usd), rem: fmtUsd(p.remaining_usd) });
      }
      this.render();
    });
    return true;
  }

  async openReport(runId) {
    const seq = ++this.reportSeq;
    try {
      const report = await this.api.getReport(runId);
      if (seq !== this.reportSeq) return;
      this.report = report;
      this.message = null;
    } catch (e) {
      if (seq !== this.reportSeq) return;
      this.message = e.message;
    }
    this.render();
  }

  // --- rendering ----------------------------------------------------------------------------------------------------
  render() {
    if (!this.root) return;
    this.root.replaceChildren(el('div', { class: 'filters' }, el('h2', { class: 'section' }, t('experiments'))),
      el('div', { class: 'ex-grid' }, this.setupPanel(), this.reportPanel()));
  }

  setupPanel() {
    const r = this.request;
    const state = r ? (typeof r.state === 'string' ? r.state : JSON.stringify(r.state)) : '';
    const qs = r ? Object.entries(r.questions || {}).map(([k, q]) => el('div', { class: 'small' },
      el('span', { class: 'mono' }, k), ` (${q.type}) `, typeof q.instructions === 'string' ? q.instructions : '')) : [];
    const forms = this.forms.map((f, i) => el('div', { class: 'ex-form' },
      el('div', { class: 'run-row' },
        el('input', { value: f.name, class: 'mono', placeholder: 'en', onchange: (e) => { f.name = e.target.value; this.changed(); } }),
        el('div', { class: 'spacer' }),
        el('button', { class: 'ghost', onclick: () => { this.forms.splice(i, 1); this.changed(); } }, '✕')),
      el('textarea', { class: 'mono', rows: 8, spellcheck: 'false', onchange: (e) => { f.text = e.target.value; this.changed(); } }, f.text)));
    const engines = this.available().map((e) => el('label', { class: 'chip-check' },
      el('input', { type: 'checkbox', checked: this.picked.has(e.id), disabled: this.running, onchange: (ev) => {
        if (ev.target.checked) this.picked.add(e.id); else this.picked.delete(e.id);
        this.changed();
      } }), ` ${e.id}`));
    const noZdr = this.available().some((e) => this.picked.has(e.id) && e.zdr === false);
    const est = this.est;
    const fill = el('span', { class: 'fill' });
    if (this.progress) fill.style.width = `${(100 * this.progress.done) / Math.max(1, this.progress.total)}%`;
    return el('div', { class: 'panel' },
      el('div', { class: 'panel-head' }, el('h2', {}, t('ex_request')), el('div', { class: 'spacer' }),
        el('button', { class: 'ghost', disabled: this.running, onclick: () => this.takeFromWorkbench() }, t('ex_take'))),
      this.requestError ? el('div', { class: 'error small' }, this.requestError) : '',
      r ? el('div', { class: 'dim small clip-3' }, state.slice(0, 400)) : el('p', { class: 'dim' }, t('ex_no_request')),
      ...qs,
      el('h3', {}, t('ex_formulations')),
      el('div', { class: 'dim small' }, t('ex_formulations_hint')),
      ...forms,
      el('button', { class: 'ghost', disabled: !r, onclick: () => {
        this.forms.push({ name: this.forms.length ? `f${this.forms.length + 1}` : 'en', text: JSON.stringify(r.questions, null, 2) });
        this.render();
      } }, t('ex_add_form')),
      el('label', { class: 'small' }, t('ex_title'), ' ', el('input', { value: this.title, placeholder: t('ex_title_ph'),
        onchange: (e) => { this.title = e.target.value; } })),
      el('h3', {}, t('ex_axes')),
      el('div', { class: 'run-row small' },
        el('label', {}, el('input', { type: 'checkbox', checked: this.orders.enabled,
          onchange: (e) => { this.orders.enabled = e.target.checked; this.changed(); } }), ` ${t('ex_orders')}`),
        el('label', { title: t('ex_k_hint') }, 'k ', el('input', { type: 'number', min: 2, max: 24, value: this.orders.k, class: 'ds-limit',
          onchange: (e) => { this.orders.k = parseInt(e.target.value, 10) || 6; this.changed(); } })),
        el('label', {}, t('ex_repeats'), ' ', el('input', { type: 'number', min: 1, max: 20, value: this.repeats, class: 'ds-limit',
          onchange: (e) => { this.repeats = parseInt(e.target.value, 10) || 1; this.changed(); } }))),
      el('div', { class: 'ds-engines' }, engines),
      noZdr ? el('label', { class: 'small' }, el('input', { type: 'checkbox', checked: this.allowNoZdr,
        onchange: (e) => { this.allowNoZdr = e.target.checked; } }), ` ${t('allow_no_zdr')}`) : '',
      el('div', { class: 'run-row' },
        el('span', { class: 'mono small' }, est ? `${est.grid.formulations.length} × ${est.grid.orders} × ${est.grid.repeats} × ${this.picked.size} = ${est.calls} ${t('ds_calls')} · ${t('estimate')} ${fmtUsd(est.total_usd)}` : ''),
        el('div', { class: 'spacer' }),
        el('button', { class: 'primary', disabled: this.running || !est, onclick: () => this.run() }, t(this.running ? 'running' : 'run'))),
      this.estError ? el('div', { class: 'error small' }, this.estError) : '',
      this.progress ? el('div', { class: 'progress' }, el('span', { class: 'track' }, fill),
        el('span', { class: 'mono small' }, `${this.progress.done} / ${this.progress.total}`)) : '',
      this.message ? el('div', { class: 'warn small' }, this.message) : '',
      this.runs.length ? el('details', { class: 'off-list' }, el('summary', { class: 'dim small' }, t('ex_runs', { n: this.runs.length })),
        this.runs.map((x) => el('div', { class: 'small ds-run' },
          el('button', { class: 'link', onclick: () => this.openReport(x.id) }, when(x.created_at)),
          ` · ${x.engines.join(', ')} · ${fmtUsd(x.cost_usd)}`))) : '');
  }

  reportPanel() {
    const r = this.report;
    if (!r) return el('div', { class: 'panel' }, el('p', { class: 'dim empty' }, t('ex_no_report')));
    const asked = r.request ? (typeof r.request.state === 'string' ? r.request.state : JSON.stringify(r.request.state)) : '';
    const parts = [el('div', { class: 'panel-head' }, el('h2', {}, t('ex_report')),
      el('span', { class: 'dim small mono' }, `${r.run_id} · ${when(r.created_at)} · ${fmtUsd(r.cost_usd)}`),
      el('div', { class: 'spacer' }),
      el('button', { class: 'ghost', disabled: this.running || !r.request, title: t('ex_same_hint'),
        onclick: () => this.useReport() }, t('ex_same')),
      el('a', { class: 'button ghost', href: this.api.reportUrl(r.run_id, 'md', getLang() === 'en' ? 'en' : 'ru') }, 'Markdown')),
    el('div', { class: 'small' }, `${r.formulations.join(', ')} × ${r.n_orders} ${t('ex_orders_n')} × ${r.repeats} ${t('ex_repeats_n')}`),
    asked ? el('div', { class: 'dim small clip-3', title: asked.slice(0, 2000) }, asked.slice(0, 400)) : '',
    el('p', { class: 'dim small' }, t('ex_report_note'))];
    if (r.budget_stopped) parts.push(el('p', { class: 'warn small' }, t('ex_budget_partial')));
    for (const [key, q] of Object.entries(r.questions)) {
      const groups = r.groups.filter((g) => g.question === key);
      parts.push(el('h3', {}, el('span', { class: 'mono' }, key), ` (${q.type}) `, typeof q.instructions === 'string' ? q.instructions : ''));
      parts.push(this.metricsTable(groups));
      for (const g of groups.filter((x) => x.metrics.unmeasured)) {
        parts.push(el('div', { class: 'dim small' }, `${g.engine_id}: ${t('ex_unmeasured', { n: g.metrics.unmeasured })}`));
      }
      for (const g of groups) parts.push(this.heatmap(r, key, g));
    }
    return el('div', { class: 'panel' }, parts);
  }

  metricsTable(groups) {
    const head = el('tr', {}, ['ds_engine', 'ds_answered', 'ex_spread', 'ex_se', 'ex_rflip', 'ex_odp', 'ex_oflip', 'ex_first',
      'ex_lift', 'ex_fdp', 'ex_fflip'].map((k) => el('th', {}, t(k))));
    const rows = groups.map((g) => {
      const m = g.metrics;
      const rep = m.repeats;
      const o = m.order || {};
      const f = m.formulation || {};
      const pull = pullsFirst(m.order);
      const first = o.first_position_share != null
        ? el('span', { class: pull ? 'warn' : '' }, `${pct(o.first_position_share)} (${pct(o.first_position_expected)})`) : '—';
      const lift = m.order ? el('span', { class: pull ? 'warn' : '' }, num(o.first_position_lift)) : '—';
      return el('tr', {}, el('td', {}, el('b', {}, g.engine_id)),
        [`${m.answered}/${m.calls}`, `${num(rep.spread_mean)} / ${num(rep.spread_max)}`, num(rep.diff_se), pct(rep.top_flip_share),
          m.order ? `${num(o.dp_mean)} / ${num(o.dp_max)}` : '—', m.order ? pct(o.flip_share) : '—', first, lift,
          m.formulation ? `${num(f.dp_mean)} / ${num(f.dp_max)}` : '—', m.formulation ? pct(f.flip_share) : '—']
          .map((v) => el('td', { class: 'mono' }, v)));
    });
    return el('div', { class: 'table-wrap' }, el('table', { class: 'small' }, el('thead', {}, head), el('tbody', {}, rows)));
  }

  heatmap(r, key, g) {
    const m = g.metrics;
    const head = el('tr', {}, el('th', {}, g.engine_id), m.options.map((o) => el('th', { class: 'mono' }, o)));
    const rows = m.heatmap.map((row) => el('tr', {},
      el('th', { class: 'mono small', title: shownOrder(r.orders, key, row.order, m.options).join(' → ') },
        `${row.formulation} · o${row.order}`),
      m.options.map((o) => {
        const p = row.p?.[o];
        const td = el('td', { class: 'mono small heat' }, p == null ? '—' : p.toFixed(2));
        td.style.backgroundColor = heatColor(p);
        return td;
      })));
    return el('details', { class: 'off-list' }, el('summary', { class: 'small' }, t('ex_heat', { engine: g.engine_id })),
      el('div', { class: 'table-wrap' }, el('table', { class: 'heatmap' }, el('thead', {}, head), el('tbody', {}, rows))));
  }
}
