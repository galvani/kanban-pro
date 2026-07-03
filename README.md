# kanban-pro

A backend-agnostic **kanban proxy**: one canonical kanban API, swappable backend
adapters. Point it at Hermes today, at another kanban tomorrow — your callers never
change.

## What It Does

kanban-pro exposes a canonical kanban model (boards, columns, cards, labels,
comments) over a REST API and routes every operation to a pluggable **adapter**. Each
adapter translates the canonical model to and from a specific backend (Hermes,
Trello, a local store, …). Switching backend is a config change, not a rewrite —
the proxy is an anti-corruption layer between your tools and whatever kanban is
underneath.

## Quick Start

```bash
uv sync                       # install deps (incl. dev tools)
uv run uvicorn kanban_pro.app:app --reload   # run the proxy (once app.py exists)
```

Pick the backend with a **profile** — `--profile hermes` / `--profile jira` /
`--profile default` (or `KANBAN_PRO_PROFILE`). A profile bundles an adapter with its
settings. kanban-pro then exposes **only the operations that provider supports**;
query `GET /capabilities` to see the active surface. See
[SPEC.md](SPEC.md#key-design-decisions).

## Architecture

Ports & adapters (hexagonal):

```
clients ──▶ canonical API (FastAPI) ──▶ KanbanBackend port ──▶ adapter ──▶ backend
                                             ▲
                          canonical domain model (Pydantic)
```

- `kanban_pro/domain/` — canonical models
- `kanban_pro/ports/` — the `KanbanBackend` Protocol (the contract) + capabilities
- `kanban_pro/adapters/` — one module per backend
- `kanban_pro/api/` — FastAPI routes

## Documentation

- [SPEC.md](SPEC.md) — what and why (canonical model, the core+passthrough decision,
  capability model)
- [JOURNAL.md](JOURNAL.md) — decisions and rationale
- [AGENTS.md](AGENTS.md) — conventions & hard rules for coding agents, incl. how to
  author a new adapter

## Status

Scaffolded 2026-07-03. No runtime yet — the canonical model, port, and first adapter
(Hermes) are the next work. See [SPEC.md](SPEC.md#open-questions).

## License

All rights reserved (personal project).
