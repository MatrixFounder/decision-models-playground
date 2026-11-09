// The question editor: an editable list ⇄ the request's {key: question} map (DESIGN §6.1).

import { t } from './i18n.js';

export const TYPES = ['noul', 'choice', 'score', 'multi', 'text', 'number', 'date'];

const el = (tag, attrs = {}, ...children) => {
  const node = document.createElement(tag);
  for (const [k, v] of Object.entries(attrs)) {
    if (k === 'class') node.className = v;
    else if (k.startsWith('on')) node.addEventListener(k.slice(2), v);
    else if (v !== undefined && v !== null && v !== false) node.setAttribute(k, v === true ? '' : v);
  }
  for (const c of children.flat()) node.append(c instanceof Node ? c : document.createTextNode(String(c)));
  return node;
};

/** {key: question} → editable list. */
export function fromRequest(questions = {}) {
  return Object.entries(questions).map(([key, q]) => {
    const item = { key, type: q.type, instructions: typeof q.instructions === 'string' ? q.instructions : JSON.stringify(q.instructions) };
    if (q.type === 'noul') item.criteria = { true: q.criteria?.true ?? '', false: q.criteria?.false ?? '' };
    else if (q.type === 'choice' || q.type === 'multi') item.options = Object.entries(q.criteria || {}).map(([id, d]) => [id, d ?? '']);
    else if (q.type === 'score') item.levels = [...(q.criteria || [])];
    if (q.type === 'number') {
      item.low = q.range?.[0] ?? '';
      item.high = q.range?.[1] ?? '';
      item.step = q.step ?? '';
      item.unit = q.unit ?? '';
    }
    return item;
  });
}

/** Editable list → {key: question}; empty optional fields are dropped. */
export function toRequest(list) {
  const out = {};
  for (const item of list) {
    const q = { type: item.type, instructions: item.instructions };
    if (item.type === 'noul') {
      const c = {};
      if (item.criteria?.true?.trim()) c.true = item.criteria.true.trim();
      if (item.criteria?.false?.trim()) c.false = item.criteria.false.trim();
      if (Object.keys(c).length) q.criteria = c;
    } else if (item.type === 'choice' || item.type === 'multi') {
      q.criteria = Object.fromEntries((item.options || []).map(([id, d]) => [id.trim(), d.trim() ? d.trim() : null]));
    } else if (item.type === 'score') {
      q.criteria = (item.levels || []).map((s) => s.trim());
    }
    if (item.type === 'number') {
      if (item.low !== '' && item.high !== '') q.range = [Number(item.low), Number(item.high)];
      if (item.step !== '' && item.step != null && q.range) q.step = Number(item.step);
      if (item.unit?.trim()) q.unit = item.unit.trim();
    }
    out[item.key.trim()] = q;
  }
  return out;
}

function blank(type, index) {
  const item = { key: `q${index + 1}`, type, instructions: '' };
  if (type === 'noul') item.criteria = { true: '', false: '' };
  if (type === 'choice' || type === 'multi') item.options = [['a', ''], ['b', '']];
  if (type === 'score') item.levels = ['', ''];
  if (type === 'number') Object.assign(item, { low: '', high: '', step: '', unit: '' });
  return item;
}

function retype(item, type) {
  const next = blank(type, 0);
  next.key = item.key;
  next.instructions = item.instructions;
  if ((type === 'choice' || type === 'multi') && item.options) next.options = item.options;
  if (type === 'choice' && item.levels) next.options = item.levels.map((s, i) => [`l${i}`, s]);
  if (type === 'score' && item.options) next.levels = item.options.map(([id, d]) => d || id);
  return next;
}

export class QuestionEditor {
  constructor(root, onChange) {
    this.root = root;
    this.onChange = onChange;
    this.list = [];
  }

  set(list) {
    this.list = list;
    this.render();
  }

  add() {
    this.list.push(blank('noul', this.list.length));
    this.render();
    this.onChange();
  }

  changed(rerender = false) {
    if (rerender) this.render();
    this.onChange();
  }

  input(value, set, attrs = {}) {
    return el('input', { value, ...attrs, oninput: (e) => { set(e.target.value); this.changed(); } });
  }

  render() {
    this.root.replaceChildren(...this.list.map((item, i) => this.card(item, i)));
  }

  card(item, i) {
    const head = el('div', { class: 'q-head' },
      this.input(item.key, (v) => { item.key = v; }, { class: 'mono q-key', placeholder: t('key'), spellcheck: 'false' }),
      el('select', { class: 'q-type', onchange: (e) => { this.list[i] = retype(item, e.target.value); this.changed(true); } },
        TYPES.map((ty) => el('option', { value: ty, selected: ty === item.type }, ty))),
      el('button', { class: 'icon ghost', title: t('remove'), onclick: () => { this.list.splice(i, 1); this.changed(true); } }, '×'),
    );
    const instr = el('textarea', { rows: 2, placeholder: t('instructions'),
      oninput: (e) => { item.instructions = e.target.value; this.changed(); } });
    instr.value = item.instructions;
    return el('div', { class: 'q-card' }, head, instr, this.criteria(item));
  }

  criteria(item) {
    if (item.type === 'noul') {
      return el('div', { class: 'q-crit' },
        el('label', { class: 'q-row' }, el('span', { class: 'tag yes' }, 'true'),
          this.input(item.criteria.true, (v) => { item.criteria.true = v; }, { placeholder: t('criteria_true') })),
        el('label', { class: 'q-row' }, el('span', { class: 'tag no' }, 'false'),
          this.input(item.criteria.false, (v) => { item.criteria.false = v; }, { placeholder: t('criteria_false') })));
    }
    if (item.type === 'choice' || item.type === 'multi') {
      const rows = item.options.map((opt, j) => el('div', { class: 'q-row' },
        this.input(opt[0], (v) => { opt[0] = v; }, { class: 'mono q-opt', placeholder: t('option_id'), spellcheck: 'false' }),
        this.input(opt[1], (v) => { opt[1] = v; }, { placeholder: t('option_desc') }),
        el('button', { class: 'icon ghost', onclick: () => { item.options.splice(j, 1); this.changed(true); } }, '×')));
      return el('div', { class: 'q-crit' }, rows,
        el('button', { class: 'link', onclick: () => { item.options.push([`o${item.options.length + 1}`, '']); this.changed(true); } }, t('add_option')));
    }
    if (item.type === 'score') {
      const rows = item.levels.map((lvl, j) => el('div', { class: 'q-row' },
        el('span', { class: 'lvl mono' }, String(j)),
        this.input(lvl, (v) => { item.levels[j] = v; }, { placeholder: `${t('level')} ${j}` }),
        el('button', { class: 'icon ghost', onclick: () => { item.levels.splice(j, 1); this.changed(true); } }, '×')));
      return el('div', { class: 'q-crit' }, rows,
        el('button', { class: 'link', onclick: () => { item.levels.push(''); this.changed(true); } }, t('add_level')));
    }
    if (item.type === 'number') {
      return el('div', { class: 'q-crit q-row q-number' },
        this.input(item.low, (v) => { item.low = v; }, { type: 'number', placeholder: t('range_low') }),
        this.input(item.high, (v) => { item.high = v; }, { type: 'number', placeholder: t('range_high') }),
        this.input(item.step, (v) => { item.step = v; }, { type: 'number', placeholder: t('step') }),
        this.input(item.unit, (v) => { item.unit = v; }, { placeholder: t('unit') }));
    }
    return el('div');
  }
}

export { el };
