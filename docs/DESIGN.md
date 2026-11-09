# Decision Models Playground — design document (retired)

> **Retired on 2026-11-09.** This document was split into:
>
> - [TASK.md](TASK.md) — TASK 001: goals, owner decisions, scope, use cases, requirements, acceptance
>   records of P0–P6, risks;
> - [ARCHITECTURE.md](ARCHITECTURE.md) — components, engines, data model, interfaces, security,
>   reliability, configuration, tests;
> - [WI-1](backlog/wi-1-p7-systemone-http-and-llm-baseline.md) in [BACKLOG.md](BACKLOG.md) — the P7 plan.
>
> [SPIKE.md](SPIKE.md) and the review records in [reviews/](reviews/) still cite `DESIGN §N`; code cites the new
> documents since 2026-11-10 (WI-2).
> The table below maps each old section to its new place. The full Russian original (1392 lines) is in git:
> `git show 94072a4:docs/DESIGN.md`.

## Section map

| Old § | Section | New place |
|---|---|---|
| §1 | Purpose, goals G1–G4, why not LM Studio | TASK §2.1 |
| §2 | Owner decisions | TASK §3 |
| §3 | Out of scope | TASK §4 |
| §4 | Use cases UC-1…UC-8 | TASK §5 |
| §5 | Architecture, figure 1, component table, process model | ARCHITECTURE §3.1–§3.4 |
| §6 | Internal contract | ARCHITECTURE §4.1 |
| §6.1 | Request | ARCHITECTURE §4.1 |
| §6.2 | Answer, validation rules | ARCHITECTURE §4.1; TASK §5.1 (UC-1 criteria) |
| §6.3 | Failure classes, how OpenRouter refuses | ARCHITECTURE §4.2, §3.5.3 |
| §7 | Engines | ARCHITECTURE §3.5 |
| §7.1 | Engine protocol, `BaseEngine` | ARCHITECTURE §3.5.1, §3.5.2, §9.1 |
| §7.2 | `openrouter_decisions` | ARCHITECTURE §3.5.3, §5.4 |
| §7.3 | `mlx_local`, preflight | ARCHITECTURE §3.5.4 |
| §7.4 | `systemone_http` | WI-1 (P7.1); ARCHITECTURE §3.5.7 |
| §7.5 | `openrouter_llm` | WI-1 (P7.3); ARCHITECTURE §3.5.7 |
| §7.6 | Registry and availability | ARCHITECTURE §3.5.5 |
| §7.7 | Answer-name table | ARCHITECTURE §3.5.6 |
| §8 | Local models and memory, figure 2 | ARCHITECTURE §3.6, §8.2, §9.5; TASK §6.3 |
| §9 | Compat server `/v1/systemone` | ARCHITECTURE §5.3; TASK §5.5 (UC-5) |
| §9.1 | Contract | ARCHITECTURE §5.3 |
| §9.2 | Access | ARCHITECTURE §7.2 |
| §9.3 | whip-insight dev profile | TASK §5.5; ARCHITECTURE §5.5 |
| §10 | Runs and history, figure 3 | ARCHITECTURE §3.7 |
| §10.1 | Run record | ARCHITECTURE §4.3, §9.2 |
| §11 | Experiments | TASK §5.2 (UC-2); ARCHITECTURE §3.8 |
| §11.1 | P5 implementation, stability metrics, UC-2 acceptance | TASK §5.2 (order caveat: rule 2), §8.2; ARCHITECTURE §3.8 |
| §12 | Datasets and calibration | TASK §5.3 (UC-3) |
| §12.1 | Dataset format | ARCHITECTURE §4.4 |
| §12.2 | Sources: files, tags, synthetic, LLM | TASK §5.3; LLM labelling → WI-1 (P7.5) |
| §12.3 | Metrics | TASK §5.3 |
| §12.4 | P4 implementation, UC-3 acceptance | TASK §5.3, §8.2; ARCHITECTURE §3.8, §4.4 |
| §13 | Capability probe and catalog | TASK §5.4 (UC-4) |
| §13.1 | Probe | TASK §5.4, §8.2; ARCHITECTURE §3.9, §4.5 |
| §13.2 | OpenRouter catalog, matrix | TASK §5.4; ARCHITECTURE §3.9, §5.4 |
| §14 | Research modes | TASK §5.6–§5.8 (UC-6…UC-8) |
| §14.1 | P6 implementation: pointer, extraction, multi, media; P6 measurements | TASK §5.6–§5.8, §8.2; ARCHITECTURE §3.10 |
| §15 | HTTP API, SSE | ARCHITECTURE §5.1, §5.2 |
| §16 | Interface (screens) | TASK §6.5; ARCHITECTURE §3.2 |
| §17 | Configuration | ARCHITECTURE §10 |
| §17.1 | `.env` | ARCHITECTURE §10.2; [.env.example](../.env.example) |
| §17.2 | `config.yaml` | ARCHITECTURE §10.3; [config.example.yaml](../config.example.yaml) |
| §18 | Security and data | TASK §6.1; ARCHITECTURE §7 |
| §19 | Cost and budget | TASK §6.2; ARCHITECTURE §3.8, §9.3 |
| §20 | Repository layout | ARCHITECTURE §10.5 |
| §21 | Testing | ARCHITECTURE §11 |
| §22 | Increments P0–P7 | TASK §8.1 |
| §22.1 | P7 plan | WI-1 |
| §23 | Relation to whip-insight | TASK §2.2; ARCHITECTURE §5.5 |
| §24 | Risks RQ-1…RQ-13 | TASK §9, §10 |

Figures: figure 1 (components) → ARCHITECTURE §3.3; figure 2 (local model state) → ARCHITECTURE §3.6;
figure 3 (comparative run) → ARCHITECTURE §3.7.
