// Interface language: Russian or English (DESIGN §16). Strings only; data stays as entered.

const STRINGS = {
  ru: {
    workbench: 'Workbench', history: 'История', state: 'Состояние', questions: 'Вопросы', engines: 'Движки',
    results: 'Результаты', preset: 'Пресет…', text: 'Текст', json: 'JSON', load_file: 'Файл…',
    add_question: '+ вопрос', add_option: '+ вариант', add_level: '+ уровень', remove: 'Удалить',
    key: 'ключ', instructions: 'Вопрос модели', criteria_true: 'Да — это значит…', criteria_false: 'Нет — это значит…',
    option_id: 'id', option_desc: 'описание (можно пусто)', level: 'уровень', range_low: 'от', range_high: 'до',
    step: 'шаг', unit: 'единица',
    title_ph: 'Название прогона', tags_ph: 'теги через запятую', allow_no_zdr: 'Разрешить движки без ZDR',
    run: 'Запустить', running: 'Идёт…', estimate: 'Оценка', remaining: 'осталось', budget_today: 'Сегодня',
    no_run: 'Выберите движки и запустите прогон.', pending: 'ждём…',
    unsupported: 'не поддерживается', refused: 'отказ', invalid_answer: 'неверный ответ',
    status: 'Статус', latency: 'Задержка', cost: 'Цена', snapshot: 'Снимок', tokens: 'Токены',
    agree: 'согласие', disagree: 'расходятся', expected: 'E', found: 'найдено',
    zdr_yes: 'ZDR', zdr_no: 'нет ZDR', zdr_unknown: 'ZDR ?', per_mtok: '/M вх.', free: 'бесплатно',
    budget_stop: 'Оценка {est} больше остатка бюджета {rem}: прогон не запущен.',
    refresh_catalog: 'Обновить каталог', filter_tag: 'тег', filter_engine: 'все движки',
    open: 'Открыть', rerun: 'Повторить', compare: 'Сравнить выбранные', select_two: 'Отметьте два прогона.',
    when: 'Когда', title: 'Название', cost_col: 'Цена', no_runs: 'Прогонов пока нет.',
    compare_title: 'Сравнение: P лучшего варианта прогона A', delta: 'Δ',
    json_invalid: 'JSON не разобран: {msg}', state_info: '{chars} симв.',
    copy_json: 'Скопировать JSON', loaded_run: 'Открыт прогон {id}',
    source_provider: 'по ответу', source_price: 'по прайсу', source_none: '—',
    unavailable: 'Недоступны ({n})',
  },
  en: {
    workbench: 'Workbench', history: 'History', state: 'State', questions: 'Questions', engines: 'Engines',
    results: 'Results', preset: 'Preset…', text: 'Text', json: 'JSON', load_file: 'File…',
    add_question: '+ question', add_option: '+ option', add_level: '+ level', remove: 'Remove',
    key: 'key', instructions: 'Question to the model', criteria_true: 'Yes means…', criteria_false: 'No means…',
    option_id: 'id', option_desc: 'description (optional)', level: 'level', range_low: 'from', range_high: 'to',
    step: 'step', unit: 'unit',
    title_ph: 'Run title', tags_ph: 'tags, comma separated', allow_no_zdr: 'Allow engines without ZDR',
    run: 'Run', running: 'Running…', estimate: 'Estimate', remaining: 'left', budget_today: 'Today',
    no_run: 'Pick engines and run.', pending: 'waiting…',
    unsupported: 'unsupported', refused: 'refused', invalid_answer: 'invalid answer',
    status: 'Status', latency: 'Latency', cost: 'Cost', snapshot: 'Snapshot', tokens: 'Tokens',
    agree: 'agree', disagree: 'disagree', expected: 'E', found: 'found',
    zdr_yes: 'ZDR', zdr_no: 'no ZDR', zdr_unknown: 'ZDR ?', per_mtok: '/M in', free: 'free',
    budget_stop: 'Estimate {est} exceeds the budget left {rem}: the run did not start.',
    refresh_catalog: 'Refresh catalog', filter_tag: 'tag', filter_engine: 'all engines',
    open: 'Open', rerun: 'Rerun', compare: 'Compare selected', select_two: 'Tick two runs.',
    when: 'When', title: 'Title', cost_col: 'Cost', no_runs: 'No runs yet.',
    compare_title: 'Comparison: P of run A\'s top option', delta: 'Δ',
    json_invalid: 'JSON not parsed: {msg}', state_info: '{chars} chars',
    copy_json: 'Copy JSON', loaded_run: 'Opened run {id}',
    source_provider: 'reported', source_price: 'by price', source_none: '—',
    unavailable: 'Unavailable ({n})',
  },
};

let lang = 'ru';
try { lang = localStorage.getItem('lang') || 'ru'; } catch { /* storage blocked */ }

export const getLang = () => lang;

export function setLang(next) {
  lang = STRINGS[next] ? next : 'ru';
  try { localStorage.setItem('lang', lang); } catch { /* storage blocked */ }
  document.documentElement.lang = lang;
  applyStatic();
}

export function t(key, vars = {}) {
  const s = STRINGS[lang]?.[key] ?? STRINGS.ru[key] ?? key;
  return s.replace(/\{(\w+)\}/g, (_, k) => (vars[k] ?? `{${k}}`));
}

/** Fill every [data-i18n] text and [data-i18n-ph] placeholder. */
export function applyStatic(root = document) {
  root.querySelectorAll('[data-i18n]').forEach((el) => { el.textContent = t(el.dataset.i18n); });
  root.querySelectorAll('[data-i18n-ph]').forEach((el) => { el.placeholder = t(el.dataset.i18nPh); });
  root.querySelectorAll('[data-i18n-title]').forEach((el) => { el.title = t(el.dataset.i18nTitle); });
}
