---
id: WI-2
type: work-item
status: done
opened_at: 2026-11-09
slug: wi-2-retarget-design-references-in-code
effort: S
value: 'code cites the live documents instead of a redirect map'
source: split of docs/DESIGN.md into TASK 001 and ARCHITECTURE.md (2026-11-09)
resolved_at: 2026-11-10
resolved_by: direct edit on the owner's request, 2026-11-10 (no TASK)
---

# WI-2 — Retarget `DESIGN §N` references in code to ARCHITECTURE / TASK

> **Resolved 2026-11-10.**
>
> - The inventory command prints nothing. The count below was 90 references in **57** files, not 56:
>   the table rows sum to 57. Two of them, in `requirements.txt` and `requirements-mlx.txt`, went with the
>   OQ-A2 rewrite of those files. The remaining 88 lines in 55 files were retargeted.
> - 116 section and UC references in the changed code were checked against the headings of `TASK.md` and
>   `ARCHITECTURE.md`; none is unresolved.
> - Runtime strings: the three `ValueError` messages of `app/settings.py` cite `ARCHITECTURE §7.1` /
>   `§7.2`. The `.env` warning of `app/__main__.py` is English now:
>   `WARNING: other users can read .env; run chmod 600 .env (ARCHITECTURE §7.3)`.
> - Ruler comments (`# --- … ---`) keep their width in characters.
> - `make test`: 880 passed, 1 skipped; Node 63/63. Ruff was not run: it is not installed in the venv.
> - Gap found and closed: `app/stability.py` cited DESIGN §11.1 for the caveat that several `choice`
>   questions change order together. Neither TASK nor ARCHITECTURE carried it after the split; it is now
>   TASK §5.2 rule 2.

## Signal

On 2026-11-09 `docs/DESIGN.md` became a redirect map. Its content now lives in [TASK.md](../TASK.md),
[ARCHITECTURE.md](../ARCHITECTURE.md) and [WI-1](wi-1-p7-systemone-http-and-llm-baseline.md). Code still
cites the old sections: 90 references in 56 files.

| Location | References | Files |
|---|---|---|
| `app/` (with `app/engines/`) | 54 | 28 |
| `web/` (`js/`, `styles.css`) | 17 | 12 |
| `scripts/` (with `scripts/spike/`) | 7 | 5 |
| `tests/` (with `js/`, one fixture `_comment`) | 7 | 7 |
| `presets/` (`call_kind.yaml`, `synth/neutral_ru.yaml`) | 2 | 2 |
| `Makefile`, `requirements.txt`, `requirements-mlx.txt` | 3 | 3 |

Most cited old sections: §14 (16), §7.3, §16, §18 (5 each), §8, §9.2, §11, §12 (4 each).

Inventory command:

```bash
grep -rn "DESIGN" app web scripts tests presets Makefile requirements*.txt | grep -v __pycache__
```

## Scope

In scope: every reference found by the inventory command — docstrings, comments, and four runtime
strings:

- `app/settings.py` — three `ValueError` messages: `(DESIGN §18)`, `(DESIGN §9.2)` twice;
- `app/__main__.py:56` — the `.env` permission warning `(DESIGN §18)`. The warning text is Russian; the
  project's main language is English, so translate it in the same change.

Out of scope:

- `docs/reviews/*` — review records are historical and stay as written;
- `docs/SPIKE.md` — a P0 measurement record; its `DESIGN §N` references resolve through the map;
- `docs/DESIGN.md` — stays as the redirect map for the two items above.

## Procedure

1. Wait until `docs/ARCHITECTURE.md` is final. If it goes over 1 500 lines and is split into
   `docs/architectures/` (index mode), cite the index section numbers, not the chunk files.
2. For each reference, look up the old section in the map of [DESIGN.md](../DESIGN.md).
3. One old section with several new places: choose by the comment's topic. Implementation →
   `ARCHITECTURE §N`; a behaviour rule or a metric definition → `TASK UC-n` or `TASK §N`; P7 → `WI-1`.
4. Forms: a module docstring `(docs/DESIGN.md §7.3)` → `(docs/ARCHITECTURE.md §3.5)`; a short comment
   `DESIGN §18` → `ARCHITECTURE §7` or `TASK §6.1`.
5. Keep the other citations (`SPIKE §N`, `SPEC §N`, `audit §N`, `review PN`) as they are.

## Acceptance

- The inventory command prints nothing.
- Every new `ARCHITECTURE §N` / `TASK §N` / `TASK UC-n` names a heading that exists.
- `make test` passes. No test asserts on the message texts (checked 2026-11-09).
- The `.env` permission warning and the three `ValueError` messages keep their guidance, in English.
