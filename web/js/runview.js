// One stream's event handler, bound to the view it fills (review P1/round 2: pure, so node --test can check it).
// Cells and the meta line belong to the view; the running state, budget and errors are global (`finish`).
// The handler decides itself whether its view is on screen, from `getCurrent` (round 3: a wiring that passed a
// constant `isCurrent` could not be caught by any test of app.js).

export const TERMINAL = ['run_done', 'budget_stop', 'run_error', 'fatal'];

/**
 * @param view {{results: object, runId?: string}}
 * @param deps {{getCurrent: () => object, render: (view) => void, setMeta: (kind, payload) => void,
 *               finish: (name, payload, view) => void}}
 */
export function makeHandler(view, { getCurrent, render, setMeta, finish }) {
  const mine = () => getCurrent() === view;
  return (name, p) => {
    if (name === 'run_started') {
      view.runId = p.run_id;
      // a pointer run: the request the engines read (the transcript as turns), so the cited turn shows live (P6)
      if (p.request) view.request = p.request;
      if (mine()) setMeta('started', p);
      if (p.request && mine()) render(view);
    } else if (name === 'engine_done' || name === 'engine_failed') {
      view.results[p.engine_id] = p.result;
      if (mine()) render(view);
    } else if (TERMINAL.includes(name)) {
      if (name === 'run_done' && mine()) setMeta('done', p);
      finish(name, p, view);
    }
  };
}
