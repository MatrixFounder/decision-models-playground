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
    local_models: 'Локальные модели', local: 'локально', no_local: 'В config.yaml нет движков mlx_local.',
    lm_id: 'Движок', lm_repo: 'Модель', lm_state: 'Состояние', lm_disk: 'На диске', lm_snapshot: 'Снимок',
    lm_memory: 'Память MLX', lm_last_call: 'Последний вызов', lm_calls: 'Вызовов', lm_unavailable: 'недоступна',
    state_unloaded: 'выгружена', state_loading: 'загружается', state_ready: 'в памяти', state_busy: 'занята',
    unload: 'Выгрузить', compat_server: 'Compat-сервер /v1/systemone', compat_url: 'Адрес',
    compat_access: 'Доступ', compat_lan: 'LAN (по токену)', compat_loopback: 'только этот Mac',
    compat_token: 'Токен SYSTEMONE_TOKEN', compat_default: 'Модель по умолчанию', compat_ext: 'Расширенные типы',
    yes: 'задан', no: 'не задан',
    duplicate_key: 'Ключ вопроса «{key}» повторяется.', duplicate_option: 'В вопросе «{key}» вариант «{id}» повторяется.',
    stream_cut: 'Поток оборвался без итога: обновите историю, прогон мог сохраниться.',
    restore_draft: '↶ вернуть черновик', request_failed: 'запрос: {cls}',
    rerun_confirm: 'Повторить прогон? Оценка {est}, остаток бюджета {rem}.',
    matrix: 'Матрица', model: 'Модель', probe: 'Проба', probing: 'проба…', probe_done: 'проба готова',
    no_profile: 'ни один запрос не получил ответа 2xx ({failure}) — профиль не записан', long_context: 'длинный',
    long_context_hint: 'Шаг 6: state ≈ 90 % контекста с фактом в конце; P(true) ≥ 0.9 — модель читает весь контекст. Перед ним шаг CAL меряет токены. Медленно на локальной модели, стоит денег на облачной.',
    long_tested: 'проверено на {n} токенах состояния', probe_local_hint: 'Проба занимает модель: запросы compat-сервера ждут в очереди',
    probe_no_zdr: 'У {id} ZDR выключен. Проба шлёт только синтетический текст. Отправить её без ZDR?',
    probe_confirm: 'Проба с длинным контекстом: оценка {est}, остаток бюджета {rem}. Запустить?',
    stale: 'устарел', catalog_error: 'Каталог не обновился: {e}',
    max_options: '≤ вариантов', max_levels: '≤ уровней', full_context: 'весь контекст', context: 'Контекст',
    price: '$/M вх.', zdr: 'ZDR', expires: 'Срок', cost_reported: 'usage.cost', probe_col: 'Проба',
    cap_yes: 'есть', cap_no: 'нет', cap_unchecked: 'не проверено', cap_never: 'невозможно для этого вида движка',
    reading_served: 'обслужен', reading_not_served: 'не обслужен', reading_unobserved: 'не наблюдалось',
    catalog_rows: 'Каталог OpenRouter, не в config.yaml ({n})', not_a_compare_run: 'Это проба, не прогон Workbench: её результат — на экране «Матрица».',
    not_workbench_run: 'Это запись вида «{kind}», не прогон Workbench: в Workbench её не открыть.',
    rerun_no_zdr: 'Движки без ZDR: {engines}. Отправить им звонок без ZDR в этом повторе? «Отмена» — повторить без них.',
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
    local_models: 'Local models', local: 'local', no_local: 'config.yaml has no mlx_local engines.',
    lm_id: 'Engine', lm_repo: 'Model', lm_state: 'State', lm_disk: 'On disk', lm_snapshot: 'Snapshot',
    lm_memory: 'MLX memory', lm_last_call: 'Last call', lm_calls: 'Calls', lm_unavailable: 'unavailable',
    state_unloaded: 'unloaded', state_loading: 'loading', state_ready: 'in memory', state_busy: 'busy',
    unload: 'Unload', compat_server: 'Compat server /v1/systemone', compat_url: 'Address',
    compat_access: 'Access', compat_lan: 'LAN (token)', compat_loopback: 'this Mac only',
    compat_token: 'SYSTEMONE_TOKEN', compat_default: 'Default model', compat_ext: 'Extended types',
    yes: 'set', no: 'not set',
    duplicate_key: 'Question key "{key}" is repeated.', duplicate_option: 'Question "{key}" repeats option "{id}".',
    stream_cut: 'The stream ended without a result: refresh History, the run may have been saved.',
    restore_draft: '↶ restore draft', request_failed: 'request: {cls}',
    rerun_confirm: 'Repeat the run? Estimate {est}, budget left {rem}.',
    matrix: 'Matrix', model: 'Model', probe: 'Probe', probing: 'probing…', probe_done: 'probe done',
    no_profile: 'no request got a 2xx answer ({failure}) — no profile written', long_context: 'long',
    long_context_hint: 'Step 6: a state of ~90 % of the context with a fact at the end; P(true) ≥ 0.9 means the model reads all of it. A CAL step measures tokens first. Slow on a local model, costs money on a cloud one.',
    long_tested: 'tested on {n} state tokens', probe_local_hint: 'A probe holds the model: compat-server requests wait in the queue',
    probe_no_zdr: 'ZDR is off for {id}. The probe sends synthetic text only. Send it without ZDR?',
    probe_confirm: 'Long-context probe: estimate {est}, budget left {rem}. Start it?',
    stale: 'stale', catalog_error: 'The catalog did not refresh: {e}',
    max_options: '≤ options', max_levels: '≤ levels', full_context: 'full context', context: 'Context',
    price: '$/M in', zdr: 'ZDR', expires: 'Expires', cost_reported: 'usage.cost', probe_col: 'Probe',
    cap_yes: 'yes', cap_no: 'no', cap_unchecked: 'unchecked', cap_never: 'impossible for this engine kind',
    reading_served: 'served', reading_not_served: 'not served', reading_unobserved: 'unobserved',
    catalog_rows: 'OpenRouter catalog, not in config.yaml ({n})', not_a_compare_run: 'This is a probe, not a Workbench run: its result is on the Matrix screen.',
    not_workbench_run: 'This is a "{kind}" record, not a Workbench run: it does not open in the Workbench.',
    rerun_no_zdr: 'Engines without ZDR: {engines}. Send them the call without ZDR in this rerun? Cancel refuses them.',
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
