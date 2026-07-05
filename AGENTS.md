# kanban-pro — Agent Instructions

## What

A backend-agnostic kanban proxy: a canonical kanban model + a `KanbanBackend` port,
with swappable per-backend adapters. Callers use one stable API; the backend is a
config choice. See [SPEC.md](SPEC.md) for the authoritative design.

## Why

Decouple tools from any single kanban backend. Switching backend (Hermes → other)
must be an adapter/config change, never a caller rewrite. kanban-pro is the
anti-corruption layer that makes that true.

## Tech Stack

Python 3.12+ · FastAPI · Pydantic v2 · httpx · uv · ruff · mypy (strict) · pytest.

## Architecture (ports & adapters)

```
kanban_pro/
  domain/    # canonical Pydantic models (the ONLY types that cross the port)
  ports/     # KanbanBackend Protocol + Capability/Fulfilment + canonical error taxonomy
  adapters/  # one module per backend, each implements the port
  core/      # the one service: augmenting dispatch (adapter + overlay), dedupe, events
  mcp/       # MCP server (PRIMARY interface)
  cli/       # shell CLI (PRIMARY interface)
  api/       # FastAPI routes (secondary interface)
  config.py  # profile selection + per-profile settings
  app.py     # app factory / entrypoint
```

The three interface layers are thin and stateless; all behavior lives in `core/`,
which wraps the active adapter (`AugmentingBackend`). Interfaces call `core/`,
**never an adapter directly**. Details in [SPEC.md](SPEC.md) (authoritative) and
[docs/adapter-structure.md](docs/adapter-structure.md).

## Conventions

- **The port is the contract.** `kanban_pro/ports/` defines `KanbanBackend`. Change
  the port only when the canonical model genuinely needs it, and update every adapter
  in the same change (single source of truth — no partially-migrated port).
- **Only canonical models cross the boundary.** Adapters translate backend JSON to/
  from `domain/` models internally; raw backend types must never escape an adapter.
- **Backend-specific fields go in `ext`,** not the core model. A field joins the core
  only when ≥2 backends need it.
- **Async everywhere** for I/O (httpx, FastAPI handlers).
- **Type-complete.** mypy strict must pass; no bare `Any` except inside `ext`.
- **Match existing style** — read a sibling module before adding one.

## Authoring a new adapter

See [docs/adapter-structure.md](docs/adapter-structure.md) for the full plan (store vs
remote adapters, `BaseAdapter`, the augmenting layer, remote-adapter layout, registration,
and the shared contract suite). In brief:

1. Create `kanban_pro/adapters/<name>.py` implementing the `KanbanBackend` Protocol.
2. Declare its `Capabilities` — be honest about what the backend can't do.
3. Map canonical ⇄ backend in that module only; surface backend-specific extras via
   `ext`; translate backend errors into the canonical error taxonomy.
4. Register the adapter name so `config.py` can select it.
5. Add contract tests (the same suite the reference adapter passes).

## What NOT to do

- Don't grow the canonical model into a Jira clone — keep the core minimal, use `ext`.
- Don't let an adapter fail with an opaque backend error for an unsupported op —
  declare the missing capability and return canonical `not_supported`.
- Don't couple an interface layer (`mcp/`, `cli/`, `api/`) to any specific adapter —
  interfaces call `core/`, which dispatches over the port.
- Don't add speculative abstraction: extract only on real, substantial reuse
  (≥2 call sites); a thin wrapper around a one-liner is noise.

## Verify

```bash
uv run ruff format . && uv run ruff check . && uv run mypy kanban_pro && uv run pytest
```

Run this (and fix what it flags) before every commit/push.

## Journaling

Record notable decisions in [JOURNAL.md](JOURNAL.md) (newest-first, what + why).
