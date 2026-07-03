# kanban-pro — Specification

## Identity

- **Name:** kanban-pro
- **Type:** Backend-agnostic kanban proxy (HTTP service + importable library)
- **Language:** Python 3.12+ (FastAPI, Pydantic, httpx)
- **Created:** 2026-07-03

## Purpose

kanban-pro exposes **one canonical kanban API** and routes every operation to a
pluggable **backend adapter**. Point it at Hermes today, at Trello or a local
SQLite store tomorrow — the clients calling kanban-pro never change.

## Motivation

Kanban backends are interchangeable in concept but incompatible in practice: each
has its own API shape, auth, and vocabulary. Coupling tools directly to one backend
means a migration rewrites every caller. kanban-pro is an **anti-corruption layer**
(ports & adapters / hexagonal architecture): a stable canonical model in the middle,
swappable adapters at the edge. Switching backend = swapping one adapter + config,
not touching callers.

## Core Behavior

kanban-pro speaks a canonical REST API over a canonical domain model. Every request
is translated by the active adapter into backend calls, and the backend's response is
translated back into the canonical model.

### Canonical domain model (the "own kanban")

The minimum a kanban needs, kept deliberately small and backend-neutral:

- **Board** — a container. `id`, `name`, `description`, `columns[]`.
- **Column** (a.k.a. list/lane/status) — `id`, `name`, `order`, optional `wip_limit`.
- **Card** — `id`, `title`, `description`, `labels[]`, `assignees[]`, `created_at`,
  `updated_at`, `ext` (see passthrough), and **`placements[]`** — a set of
  `{board_id, column_id, position}` entries locating the card. A card lives on ≥1
  board, each with its own column + ordering; single-board backends and the native
  store use exactly one placement (see decision below).
- **Label** — `id`, `name`, `color`.
- **Comment** — `id`, `card_id`, `author`, `body`, `created_at`.

### Canonical operations (the port)

CRUD + movement, expressed backend-neutrally:

- Boards: list, get, create, update, delete
- Columns: list, create, update, delete, reorder
- Cards: list, get, create, update, delete, **move** (column + position)
- Labels / assignees / comments: attach, detach, list

The authoritative interface lives in code as a `Protocol` in
`kanban_pro/ports/` — that Protocol IS the contract every adapter implements.

## Key Design Decisions

### 1. Canonical core + typed passthrough (the central tension)

Backends disagree hard: a plain kanban is columns+cards; Jira adds epics, sprints,
custom fields. A strict lowest-common-denominator model would throw that away; a
union-of-everything model would be unusable.

**Decision:** a small **strict canonical core** (above) that every adapter must
support, PLUS an `ext: dict[str, Any]` escape hatch on each entity for
backend-specific fields. Canonical callers ignore `ext`; backend-aware callers can
read/write it. Adapters populate `ext` on the way out and honor it on the way in.
This keeps the common path clean without discarding backend richness.

### 2. The exposed API surface is gated to the locked-in provider

kanban-pro exposes **only the operations the currently-active provider supports** —
not a fixed superset. Each adapter **declares its capabilities** (a `Capability`
set); the active profile's capability set determines which endpoints are advertised.
`GET /capabilities` reports the active surface; calling an op the provider can't do
returns a canonical `not_supported`.

**Tradeoff (flagged for the record):** this is a *normalizing* proxy, not a
*lowest-common-denominator* one — you get full fidelity to whatever backend is
locked in, but the surface **changes per profile**. That is in mild tension with
"callers never change when you switch backend": a caller written against Hermes's
richer set may hit gaps under a thinner provider (e.g. Jira profile without
reordering). The mitigation is the capability check + `GET /capabilities` so callers
can degrade gracefully rather than break blindly. Accepted deliberately: fidelity per
provider is worth more here than a frozen universal surface.

**Initial agreed method set = Hermes's full kanban method set.** v1 supports every
kanban operation Hermes exposes; other providers implement the subset they can and
declare the rest unsupported.

### 3. Provider selection via `--profile`

A **profile** bundles an adapter with its settings. The active profile is chosen at
startup via `--profile <name>` (CLI) or `KANBAN_PRO_PROFILE` (env): e.g.
`--profile hermes`, `--profile jira`, `--profile default`. Adding a backend = new
module in `kanban_pro/adapters/` implementing the port + a profile entry in
`config.py`. No core change; callers pick a profile, not a code path.

### 4. Card placement is a set, not a single column

A card carries `placements[]` (`{board_id, column_id, position}`), not one `column_id`.
Research showed one-card-one-column is violated by Asana, ClickUp, monday, GitLab, and
Jira — a card can sit on several boards at once. The set models this faithfully;
single-board backends and the native store use the **degenerate one-entry** case so the
common path stays trivial. `move_card` targets a `(board_id, column_id, position)` and
updates the matching placement. `MULTI_BOARD_MEMBERSHIP` capability advertises whether a
backend supports >1 placement.

### 5. Errors are canonical too

Adapters translate backend errors into a canonical error taxonomy (not_found,
conflict, unauthorized, not_supported, backend_unavailable) so callers get stable
error semantics regardless of backend.

## Tech Stack

- **Python 3.12+**
- **FastAPI** — HTTP API layer (`kanban_pro/api/`)
- **Pydantic v2** — canonical model + validation (`kanban_pro/domain/`)
- **httpx** — async HTTP client for adapters that call remote backends
- **uv** — dependency & environment management
- **ruff** (lint + format), **mypy** (strict), **pytest** (tests)

## Project Structure

```
kanban_pro/
  domain/      # canonical Pydantic models: Board, Column, Card, Label, Comment
  ports/       # KanbanBackend Protocol (the contract) + Capabilities + errors
  adapters/    # one module per backend; hermes.py is the first
  api/         # FastAPI routes mapping canonical ops -> active adapter
  config.py    # adapter selection + per-adapter settings
  app.py       # FastAPI app factory / entrypoint
tests/
```

## Constraints

- **Canonical model stays small.** New fields join the core only when ≥2 backends
  need them; otherwise they live in `ext`. Resist scope creep into a Jira clone.
- **Adapters never leak backend types.** Everything crossing the port boundary is a
  canonical domain model — no raw backend JSON escapes an adapter.
- **Self-hosted / personal tool.** No multi-tenant auth story required yet; keep it
  runnable locally against Hermes.

## Grounding: backend research

The canonical model, capability set, and the retry/heartbeat concerns below are grounded
in a 15-product API survey — see [docs/research/kanban-backends.md](docs/research/kanban-backends.md).
Load-bearing findings:

- **Only Jira enforces a workflow state machine** server-side; everyone else is free-form
  status assignment. → kanban-pro's own transition/WIP enforcement is a *differentiator*.
- **"Column" is modeled ~9 ways** → canonical `Column` carries a **category enum**
  (triage/backlog/unstarted/started/done/canceled, from Linear) so "which column is done?"
  survives translation.
- **One-card-one-column is violated** by Asana/ClickUp/monday/GitLab/Jira → placement may
  be a `(board → column)` **membership set**, not a single pointer. **Open fork** (below).
- **Typed dependencies** exist in most tools but not all → `RELATIONS` capability +
  a `RelationKind` enum modeled on Vikunja.
- **No backend offers idempotency keys**, and retry/rate-limit signaling differs per
  product (Linear even returns HTTP 400, not 429) → the proxy owns a normalized retry
  layer + create-dedupe.
- **Webhook delivery is weak and true heartbeats are rare** → inbound events are hints;
  reconciliation polling + a per-adapter `keepalive/refresh` hook are core concerns, and
  kanban-pro exposes ONE unified event/heartbeat surface to its clients.

These are cross-cutting layers the **proxy core** owns, not any single adapter: normalized
retry/backoff, idempotency/dedupe, reconciliation polling + unified events, and per-adapter
keepalive/refresh (Jira webhooks expire at 30 days; Asana monitors an 8h heartbeat).

## Roadmap

- **v1 — Hermes parity.** Canonical model + port + `hermes` adapter covering Hermes's
  full kanban method set. `--profile` selection. `memory` reference adapter for tests.
- **Later — workflow control (allowed transitions).** Beyond free-form card moves,
  model **permitted column→column transitions** as a state machine per board/profile.
  Providers that expose a workflow (e.g. Jira statuses/transitions) declare it via a
  `WORKFLOW`/`TRANSITIONS` capability; `move_card` is then validated against the
  allowed transitions and callers can query the transition graph. Backends without a
  workflow keep today's free-move behavior.
- **Later — additional profiles** (Jira, Trello, …), each a capability subset.

## What This Project Is NOT

- Not a kanban **UI** — it's the API/proxy layer; a frontend is a separate consumer.
- Not a **sync engine** — it proxies to one active backend at a time, it does not
  two-way-replicate between backends (that's a possible future, explicitly out of
  scope for v1).
- Not a **superset** of every backend's features — the canonical core is minimal;
  richness lives in `ext`.

## Open Questions

- **Ordering:** use rebalanced integer / lexo-rank ordering, not naive floats (Planka &
  Trello float positions need periodic rebalancing — a known pain).
- First adapter: confirm Hermes kanban's actual API surface (endpoints, auth, data
  shape) before finalizing the `hermes` adapter and the canonical↔Hermes mapping.
- Native store is DECIDED as the next build (see TODO.md) — reference Planka's schema +
  Vikunja's relations/WIP; it doubles as the port's proving ground and test fixture.
