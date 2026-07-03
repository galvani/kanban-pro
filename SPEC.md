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
- **Card** — `id`, `title`, `description`, `labels[]`, `assignees[]` (User ids),
  `start_date?`, `due_date?` (both nullable), `checklists[]`, `attachments[]`,
  `created_at`, `updated_at`, `ext` (see passthrough), and **`placements[]`** — a set of
  `{board_id, column_id, position}` entries locating the card. A card lives on ≥1
  board, each with its own column + ordering; single-board backends and the native
  store use exactly one placement (see decision below).
- **Checklist** (nested on Card, not a board entity) — `id`, `title`,
  `items[]` where each item is `{id, text, done, order}`. Lightweight "definition of
  done" — items are NOT cards (no column/assignee/placement). Gated by the `CHECKLISTS`
  capability; polyfills via write-through for backends without native checklists.
  *(Subtasks — child **cards** — are modeled separately as `PARENT`/`CHILD` relations,
  not checklists.)*
- **Attachment** (nested on Card) — `id`, `url`, `title`. **Link-only** for v1 (a
  reference: PR, doc, image URL) — no file storage. File uploads are deferred behind a
  future `ATTACHMENTS_FILES` capability; the proxy does not own blob storage in v1.
  Gated by the `ATTACHMENTS` capability; polyfills via write-through.
- **Label** — `id`, `name`, `color`.
- **Comment** — `id`, `card_id`, `author` (User id), `body`, `created_at`.
- **User** — `id`, `display_name`, `ext`. Deliberately minimal; referenced by
  `Card.assignees[]` and `Comment.author`. `ext` holds backend-specific user keys
  (Jira accountId, Trello member id, GitHub login, …) since backends key users
  differently.

### Canonical operations (the port)

CRUD + movement, expressed backend-neutrally:

- Boards: list, get, create, update, delete
- Columns: list, create, update, delete, reorder
- Cards: list, get, create, update, **archive/unarchive**, delete (guarded — see
  decision 7), **move** (column + position)
- Labels / assignees / comments: attach, detach, list
- **Bulk** (API/MCP surface): batch `create` / `move` / `update` / `archive` — e.g.
  move many cards at once (agents reorganizing a board).

The authoritative interface lives in code as a `Protocol` in
`kanban_pro/ports/` — that Protocol IS the contract every adapter implements.

**Bulk is a core convenience, not part of the port.** The port stays single-item; the
`core/` service implements bulk by looping over single-item port methods and returning
**per-item results with partial-success** (some succeed, some fail — each reported).
Adapters MAY later override a bulk op with a native batch endpoint for efficiency, but
none is required to. This keeps the adapter contract simple while giving clients batching.

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

### 2. Capability fulfilment: delegate → polyfill → unavailable

kanban-pro exposes the **full canonical surface** and fulfils each operation the best
way available for the active provider:

1. **Delegate** — the backend supports it natively → the adapter forwards to the
   backend (use everything Hermes can do).
2. **Polyfill** — the backend lacks it, but kanban-pro provides it itself from its
   **overlay store** (its own persistence — the native store reused as a decorator over
   the adapter). Missing comments, typed relations, custom fields, WIP limits, and
   allowed-transition **workflow enforcement** are supplied by kanban-pro on top of
   whatever the backend stores.
3. **Unavailable** — can't be delegated *or* polyfilled → canonical `not_supported`
   (rare — a genuinely un-emulatable primitive).

Each adapter declares which capabilities it fulfils **natively**; kanban-pro knows
which of the rest it can **polyfill**; `GET /capabilities` reports each capability plus
its `Fulfilment` (native / polyfilled / unavailable) so clients know the guarantees.

This makes kanban-pro an **augmenting** proxy, not merely a normalizing one:
`--profile hermes` still picks Hermes, but callers see the rich canonical set
regardless of Hermes's gaps. **v1 = Hermes's full method set (delegated) + kanban-pro
polyfilling the rest.** *(Supersedes the earlier "surface gated to the provider"
stance — we augment rather than gate.)*

**Architecture:** `AugmentingBackend = adapter + overlay`, a decorator over the port.
Reads merge (backend fields + overlay data); writes route to adapter-if-capable else
the overlay. The overlay is the native store (see decision on the native backend) — so
that build serves double duty: standalone backend AND augmentation layer.

**Where polyfilled data lives — prefer write-through, overlay is the fallback.** The
naive worry is that polyfilled data lives only in kanban-pro (a hidden system of record).
We avoid that by writing polyfill data **back into the backend's own containers** wherever
one exists, so the backend stays authoritative and can surface it (eventually via an MCP
that reads these containers). Three persistence strategies, best first:

1. **Native-typed** — backend has the real feature → delegate (no polyfill).
2. **Native-encoded (write-through)** — backend lacks the typed feature but has a
   free-form container (a **comment**, the **description**, a **custom/extra field**, or a
   label convention). kanban-pro **serializes** the data there behind a marker
   (e.g. a `<!-- kanban-pro:relations {…} -->` comment or a namespaced field), so Hermes
   both stores and can look it up. Backend remains the source of truth.
3. **Overlay (fallback)** — backend has NO usable container → kanban-pro's own overlay
   store holds it. Only *this* case is a partial system of record.

The persistence strategy is **per-adapter, per-capability** (Hermes may have comments but
no custom fields, etc.). Costs to keep in mind for write-through: it can clutter the
backend's native UI (mitigate with hidden markers), needs reliable round-trip parsing,
and querying serialized data may require scanning until an MCP/index exists. For
out-of-band deletes, reconciliation still GCs any overlay-fallback rows. Enforcement-only
polyfills (Tier 1: workflow/WIP) persist **nothing** in the backend — the rules are
kanban-pro config, applied at move time.

**Polyfill tiers (safest first):**
- **Tier 1 — pure enforcement, no stored data:** allowed-transition workflow + WIP
  limits. kanban-pro validates `move_card` then delegates; only the *rules* live in
  kanban-pro (config), so there is **no source-of-truth split**. Works over any backend.
- **Tier 2 — overlay data keyed to backend IDs:** typed relations, custom fields, extra
  comments/labels. The system-of-record split above applies here.
- **Tier 3 — hard, needs backend cooperation:** faithful ordering when the backend owns
  position; multi-board membership over a single-board backend. Attempt last.

**v1 polyfill commitments (Q8):** kanban-pro guarantees **`WORKFLOW` + `WIP_LIMITS`**
(Tier 1) + **`ARCHIVE`** (flag) + **`RELATIONS`** (write-through) regardless of backend.
Other Tier-2 caps (`CHECKLISTS`, `ATTACHMENTS`, `COMMENTS`, `MULTI_ASSIGNEE`) are
**delegate-if-native, else best-effort/unavailable — decided per backend** once Hermes's
native surface is confirmed. `CUSTOM_FIELDS` is always available (it's `ext`). Tier 3 is
`unavailable` in v1.

### 3. Provider selection via `--profile`

A **profile** bundles an adapter with its settings. The active profile is chosen at
startup via `--profile <name>` (CLI) or `KANBAN_PRO_PROFILE` (env): e.g.
`--profile hermes`, `--profile jira`, `--profile default`. Adding a backend = new
module in `kanban_pro/adapters/` implementing the port + a profile entry in
`config.py`. No core change; callers pick a profile, not a code path.

**One active profile per run (v1).** Exactly one backend is locked in per instance —
clean single API + one capability set; switching backend = restart with a different
profile. **Multi-mount** (several profiles live at once, namespaced `/hermes/…`,
`/jira/…`, capabilities per mount) is deferred: profiles are already named/registered, so
a mount-prefix layer can be added later without reworking the core.

**Config location.** Profile *definitions* (adapter + non-secret settings) live in a
**config file**; **secrets** (backend tokens) come from **env / secret store**, never a
committed file (matches the credential-holder pattern — keys out of committed configs);
env also selects the active profile.

### 4. Card placement is a set, not a single column

A card carries `placements[]` (`{board_id, column_id, position}`), not one `column_id`.
Research showed one-card-one-column is violated by Asana, ClickUp, monday, GitLab, and
Jira — a card can sit on several boards at once. The set models this faithfully;
single-board backends and the native store use the **degenerate one-entry** case so the
common path stays trivial. `move_card` targets a `(board_id, column_id, position)` and
updates the matching placement. `MULTI_BOARD_MEMBERSHIP` capability advertises whether a
backend supports >1 placement.

### 5. Interfaces: MCP-first and shell-first (harness-native)

kanban-pro's primary consumers are **agent harnesses** — Hermes, Claude Code, Codex,
OpenCode, GPT-based agents, and any future one. So the canonical API is exposed
**MCP-first and shell-first**, not HTTP-first:

- **MCP server (primary).** Every canonical operation is an MCP **tool** ("skill"); the
  active provider's `Capability` + `Fulfilment` set is exposed as an MCP **resource**. A
  harness thus *introspects what this kanban can do* and calls the right tools natively —
  this is how a harness "natively understands the kanban" with zero bespoke integration.
- **CLI / shell (primary).** The same operations as subcommands, so shell-first harnesses
  (Codex, Claude Code) drive it by shelling out, and humans get a real CLI.
- **HTTP/REST (secondary).** The same operations for programmatic/non-agent clients.

**Every known harness works with no new code:** if it speaks MCP or a shell, it's already
a client. All three surfaces are **thin layers over one core service + the port** — no
logic lives in an interface layer, so MCP/CLI/HTTP cannot drift.

### 6. Errors are canonical too

Adapters translate backend errors into a canonical error taxonomy (not_found,
conflict, unauthorized, not_supported, backend_unavailable) so callers get stable
error semantics regardless of backend.

### 7. Deletion is archive-first (safety for agent-driven ops)

Because harnesses call these operations, an unguarded `delete` is dangerous — a
misfiring agent could irrecoverably destroy a card. So removal is **archive-first**:

- `archive(card)` / `unarchive(card)` — soft, recoverable; the default "remove from
  board." Archived cards are hidden from normal listings.
- `delete(card)` — permanent purge, but **guarded: only permitted on an
  already-archived card** (archive → then delete). A live card cannot be one-shot
  destroyed. This keeps the safety of archive-only while still allowing a deliberate
  purge.
- `ARCHIVE` capability advertises native support; where a backend only hard-deletes,
  archive is **polyfilled via write-through** (an archived flag), so the recoverable
  behavior is universal.

*(Open: if strict archive-only — no permanent delete ever — is preferred, drop the
guarded `delete`. Current stance keeps the guarded purge.)*

### 8. Idempotency & dedupe (no backend provides it)

The research found **zero backends offer idempotency keys**, so the proxy owns dedupe.
Clients are harnesses that retry on timeout/error, so duplicate-on-retry is a frequent
failure mode, not an edge case. Design:

- **Naturally-idempotent ops need no key** — `update`, `move`, `archive`, set-field.
  Repeating them converges to the same state (move-to-C twice = still in C).
- **Create/add ops REQUIRE a client-supplied idempotency key** — create card, add
  comment / checklist item / relation / attachment. `core/` keeps a short-TTL
  key→result cache; a retry with the same key returns the original result instead of
  appending a duplicate. A harness generates one key per logical action and reuses it on
  retry — the only thing that actually dedupes.
- **No server-generated random key as a dedupe substitute.** A key generated when absent
  differs on each retry → no dedupe (a false comfort); it's only useful for tracing.
- **No-key create fallback:** derive a **content-hash** key (endpoint + normalized
  payload + target) over a short TTL window — best-effort dedupe of double-fired
  identical creates. **Opt-outable**, because it false-positives on two *genuinely*
  identical entities (e.g. two real "Buy milk" cards); not forced.

### 9. Events: unified push surface + mandatory reconciliation

kanban-pro is the **single live event source** over all backends. Two halves:

- **Internal reconciliation (mandatory).** Backend webhook delivery is weak (drops, no
  ordering — see research), so the core **polls to reconcile** its view with the backend
  and treats inbound backend webhooks (where they exist) as *hints* that trigger a
  re-fetch. This keeps the canonical view correct regardless of backend push quality.
- **Client-facing unified push (v1).** kanban-pro exposes its **own** event surface,
  hiding the per-backend zoo:
  - **MCP notifications** — harness-native push (a subscribed harness gets change events).
  - **kanban-pro webhooks** — HTTP push for non-MCP clients and the UI (live board).
  - **Pull change-feed** — `changes since <cursor>` for clients that prefer polling.

  All three are fed by one **core change-log (append-only, cursored)** covering
  card/column/board create·update·move·archive·delete. The change-log is the single
  source; MCP/webhook/feed are thin projections of it (same no-drift principle as the
  interface layers).

**Listener registry.** Push delivery is driven by registered listeners:

- **Webhook listeners — persistent & filtered.** A client registers
  `{callback_url, secret, filter}` (filter = boards / event types). The registry
  survives restarts; the core fans a change-log entry out to matching listeners,
  HMAC-signs each payload, and **retries with backoff**. Each listener stores its
  **last-delivered cursor**, so one that was down **resumes from its cursor** rather than
  dropping events — deliberately better than the surveyed backends, whose webhooks drop
  silently with no catch-up.
- **MCP subscriptions — session-scoped.** An MCP client subscribes within its session; the
  listener is live for the session and removed when it ends (no persistence).
- **Change-feed — no registration.** Pure pull: the client holds a cursor and asks for
  `changes since <cursor>`.

## Tech Stack

- **Python 3.12+**
- **MCP server** (Python MCP SDK) — the primary, harness-native interface (`kanban_pro/mcp/`)
- **CLI** (typer/click) — the primary shell interface (`kanban_pro/cli/`)
- **FastAPI** — secondary HTTP API layer (`kanban_pro/api/`)
- **Pydantic v2** — canonical model + validation (`kanban_pro/domain/`)
- **httpx** — async HTTP client for adapters that call remote backends
- **uv** — dependency & environment management
- **ruff** (lint + format), **mypy** (strict), **pytest** (tests)

## Project Structure

```
kanban_pro/
  domain/      # canonical Pydantic models: Board, Column, Card, Label, Comment, Relation
  ports/       # KanbanBackend Protocol (the contract) + Capabilities + Fulfilment + errors
  adapters/    # one module per backend (native.py = own store + overlay; hermes.py first)
  core/        # the one service: augmenting dispatch, retry/dedupe, reconciliation
  mcp/         # MCP server — ops as tools, capabilities as a resource (PRIMARY interface)
  cli/         # shell CLI — ops as subcommands (PRIMARY interface)
  api/         # FastAPI routes (secondary interface)
  config.py    # profile selection + per-profile settings
  app.py       # entrypoint wiring core -> interfaces
tests/
```

The three interface layers (`mcp/`, `cli/`, `api/`) are thin and stateless — all behavior
lives in `core/` over the `ports/` contract.

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
