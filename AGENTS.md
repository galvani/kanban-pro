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
  domain/     # canonical Pydantic models (the ONLY types that cross the port)
  ports/      # KanbanBackend Protocol + Capability/Fulfilment + canonical error taxonomy
  adapters/   # one module per backend, each implements the port
  core/       # the one service: augmenting dispatch (adapter + overlay), dedupe,
              # change-log, flow engine, work reports
  mcp/        # MCP server (PRIMARY interface) — 41 tools + 9 resources
  api/        # FastAPI: serves the web UI (snapshot + SSE + card detail). Secondary.
  migrate.py  # kanban-pro-migrate — copy any profile into any other
  config.py   # profile selection + per-profile settings
  cli/        # shell CLI (PRIMARY interface) — 🔜 not built yet
```

The interface layers are thin and stateless; all behavior lives in `core/`, which wraps
the active adapter (`ActorPolicyBackend(RecordingBackend(AugmentingBackend(adapter)))`).
Interfaces call `core/`, **never an adapter directly** — that is what makes the guards and
the audit trail unbypassable. The outermost layer (`core/actor_policy.py`) refuses writes
from a connection with no identity, so nothing can land in the log as `actor: unknown`.

**Before changing code, read [docs/internals.md](docs/internals.md)** — the layer stack,
the invariants you must not break, the 23 event kinds, `ext` versioning, and the traps
(e.g. the workflow lives on the board as `board.flow` by column id — set via `set_flow`,
NOT a config file; the adapter registry is in `config.py`, not `adapters/__init__.py`).
Design rationale: [SPEC.md](SPEC.md)
(authoritative); adapter recipe: [docs/adapter-structure.md](docs/adapter-structure.md).

## Conventions

- **The port is the contract.** `kanban_pro/ports/` defines `KanbanBackend`. Change
  the port only when the canonical model genuinely needs it, and update every adapter
  in the same change (single source of truth — no partially-migrated port).
- **Only canonical models cross the boundary.** Adapters translate backend JSON to/
  from `domain/` models internally; raw backend types must never escape an adapter.
- **Backend-specific fields go in `ext`,** not the core model. A field joins the core
  only when ≥2 backends need it. Reserved namespaces are pinned in
  [docs/methods.md](docs/methods.md#card-ext-conventions-reserved-namespaces) — notably
  `ext["work_report"]`, which is written only through `record_work_report` (never as a
  whole-blob `ext` patch) so each write emits its `work_report.updated` event.
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

## Example skills carry a GENERATED tool reference

`examples/skills/*/SKILL.md` contain a block rendered from the live MCP server. After
ANY change to the MCP tool surface, regenerate: `uv run python -m tests.toolref
--write` — `tests/test_toolref.py` fails the suite until you do. Jan's installed
copies live in `~/.claude/skills/kanban-{worker,orchestrator,retro}` — re-copy on change.

The three skills split by role: **kanban-worker** pulls and works cards ·
**kanban-orchestrator** plans them onto the board · **kanban-retro** analyses, after the
fact, how the work actually flowed — and proposes the SOUL/skill/knowledge changes that
would have prevented what went wrong.

## Verify

```bash
uv run ruff format . && uv run ruff check . && uv run mypy kanban_pro && uv run pytest
```

Run this (and fix what it flags) before every commit/push.

## Journaling

Record notable decisions in [JOURNAL.md](JOURNAL.md) (newest-first, what + why).
