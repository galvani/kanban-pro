# kanban-pro — Specification

## Identity

- **Name:** kanban-pro
- **Type:** Backend-agnostic kanban proxy (MCP server + importable library; optional web UI)
- **Language:** Python 3.12+ (FastAPI, Pydantic, httpx)
- **Created:** 2026-07-03

## Purpose

kanban-pro exposes **one canonical kanban API** and routes every operation to a
pluggable **backend adapter**. Point it at Hermes today, at Trello or a local
SQLite store tomorrow — the clients calling kanban-pro never change.

**Goal update (2026-07-05):** kanban-pro's endgame is to **replace the Hermes kanban**
(this was not the original goal — it is now). The native store becomes the system of
record and Hermes, like every other harness, consumes kanban-pro over MCP/CLI. The
`hermes` adapter is still built first — it is the **discovery + migration vehicle**
(learn the harness's real data shapes, proxy during the transition, then import into
the native store and cut over).

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

- **Board** — a container. `id`, `name`, `description`, `columns[]`, `labels[]` (the
  board-scoped Label registry — labels are owned here and referenced by `Card.labels`
  via id), `ext`.
- **Column** (a.k.a. list/lane/status) — `id`, `name`, `order`, **`category`** (fixed
  semantic enum: triage/backlog/unstarted/started/done/canceled, from Linear — names are
  free-form per backend, the category is what "done-ness" queries and workflow rules key
  off), optional `wip_limit`.
- **Card** — `id`, `title`, `description`, `labels[]`, `assignees[]` (User ids),
  `start_date?`, `due_date?` (both nullable), `checklists[]`, `attachments[]`,
  `archived` (bool — archive-first, decision 7), `created_at`, `updated_at`, `ext` (see
  passthrough), and **`placements[]`** — a set of
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
  decision 7), **move** (within-board: column + position),
  **add_placement / remove_placement** (put a card on / take it off a board — Q15)
- Labels / assignees / comments: attach, detach, list
- **Bulk** (API/MCP surface, *planned*): batch `create` / `move` / `update` / `archive` —
  e.g. move many cards at once (agents reorganizing a board).

Beyond the port, `core/` adds convenience surfaces the backend never sees: the change
feed (`list_changes` / `wait_changes`), work distribution (`list_work`, claim/lease,
attention), the flow engine (`list_transitions` / `list_flows`), and structured **work
reports** (`record_work_report` / `answer_work_report_question` over `ext["work_report"]`
— current task state for humans and workers, with the change-log as its audit trail).
Full list: [docs/methods.md](docs/methods.md).

The authoritative interface lives in code as a `Protocol` in
`kanban_pro/ports/` — that Protocol IS the contract every adapter implements.

**Bulk will be a core convenience, not part of the port** (not yet implemented). The port
stays single-item; the `core/` service will implement bulk by looping over single-item
port methods and returning **per-item results with partial-success** (some succeed, some
fail — each reported). Adapters MAY later override a bulk op with a native batch endpoint
for efficiency, but none is required to. This keeps the adapter contract simple while
giving clients batching.

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

**Patching `ext` is a shallow merge (Q17, decided 2026-07-05):** a patch's `ext` merges
at key level; a key set to `null` is removed. Concurrent writers (agents, kanban-pro's
own `kanban_pro.*` metadata like copy provenance) each touch their keys without wiping
the others'. Full replace = send every key explicitly.

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

**Mount-qualified addressing (decided 2026-07-05).** When multi-mount lands, public
identifiers are mount-qualified — `jira/TASK-001`, `local-default/board-x` — the prefix
picks the adapter. Ids **never encode lineage**: the local twin of a Jira card is its own
card whose provenance lives in the cross-mount link + `ext["kanban_pro.copied_from"]`,
not in a compound id (lineage-encoded ids break on unlink/relink; identity stays stable,
lineage is metadata).

**Config location.** Profile *definitions* (adapter + non-secret settings) live in a
**config file**; **secrets** (backend tokens) come from **env / secret store**, never a
committed file (matches the credential-holder pattern — keys out of committed configs);
env also selects the active profile. User-facing guide:
[docs/configuration.md](docs/configuration.md).

### 4. Card placement is a set, not a single column

A card carries `placements[]` (`{board_id, column_id, position}`), not one `column_id`.
Research showed one-card-one-column is violated by Asana, ClickUp, monday, GitLab, and
Jira — a card can sit on several boards at once. The set models this faithfully;
single-board backends and the native store use the **degenerate one-entry** case so the
common path stays trivial. `MULTI_BOARD_MEMBERSHIP` capability advertises whether a
backend supports >1 placement.

**Move vs. membership (Q15/Q16, decided 2026-07-05):** each op does one thing.
`move_card` is **strict within-board** — it re-columns/re-positions the placement on
`to_board_id` and errors (`not_found`) if the card isn't on that board; it never creates
a placement. The placement *set* changes only via the explicit `add_placement` /
`remove_placement` ops.

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

**Boards & columns get the same two-step spirit (Q14, decided 2026-07-05):**
`delete_board` / `delete_column` are guarded **empty-only** — they refuse while *live*
(non-archived) cards remain; move or archive the cards first. Archived leftovers cascade
away on board delete. Guarded card delete confirmed over strict archive-only (Q13).

### 8. Idempotency & dedupe (no backend provides it)

The research found **zero backends offer idempotency keys**, so the proxy owns dedupe.
Clients are harnesses that retry on timeout/error, so duplicate-on-retry is a frequent
failure mode, not an edge case. Design:

- **Naturally-idempotent ops need no key** — `update`, `move`, `archive`, set-field.
  Repeating them converges to the same state (move-to-C twice = still in C).
- **Create/add ops take a client-supplied idempotency key** — every create/add
  (board, column, card, comment, checklist item, relation, attachment). `core/` keeps a short-TTL
  key→result cache; a retry with the same key returns the original result instead of
  appending a duplicate. A harness generates one key per logical action and reuses it on
  retry — the only thing that actually dedupes. *(Shipped 2026-07-05 as an **optional**
  param, not required as originally specified: making it mandatory would break every
  existing caller, and the no-key fallback below covers the accidental double-fire.
  Revisit if duplicates show up in practice.)*
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
  card/column/board create·update·move·archive·delete (plus `work_report.updated`,
  claim/release, and attention events). The change-log is the single source;
  MCP/webhook/feed are thin projections of it (same no-drift principle as the interface
  layers). *Delivery is phased (see Roadmap). SHIPPED: the change-log core, the pull feed
  (`list_changes`, actor-stamped — decision 10), the `wait_changes` long-poll (push
  semantics without a polling loop), and SSE to the web UI. PENDING: MCP notifications
  and the persistent webhook registry.*

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

### 10. Actor identity on operations (2026-07-05)

Every write knows **who did it** — kanban-pro's consumers are agents, so attribution
is a first-class concern (audit, the change-log, the work-queue's "me"). Identity is
**per-connection**: the MCP server (and later CLI/HTTP) is started with
`--actor <kind:name>` (`agent:hermes-engineer`, `human:jan`; env `KANBAN_PRO_ACTOR`);
everything that connection does is stamped with it. Actors are plain strings by
convention, not User references — a User row is not required to act. A per-call
override is deferred until a concrete need appears. Implemented by
`core.RecordingBackend`, the outermost decorator of the core stack: it records every
*successful* write into the change-log (decision 9); reads and failed writes are never
recorded.

## Consuming kanban-pro (consumption model)

A consumer never integrates per-backend — it talks to the canonical surface and
**discovers everything at runtime**. Three entry points, one `core/` underneath.

**MCP (primary — agent harnesses: Claude Code, Codex, Hermes, OpenCode, GPT agents):**

1. kanban-pro runs as an **MCP server** — `stdio` for local harnesses, HTTP/SSE for remote.
2. The harness adds it to its MCP config and connects.
3. **Discovery:** the harness lists **tools** (one per canonical op — `create_card`,
   `move_card`, `list_boards`… — each JSON-schema'd from the domain models) and
   **resources** (`capabilities` = the active provider's `Capability` + `Fulfilment`;
   board/card data; the change-feed).
4. It reads `capabilities` to learn what's available (native / polyfilled / unavailable)
   — that is how it "understands this kanban."
5. It calls tools; `core/` dispatches to the active adapter (delegate or polyfill) and
   returns a canonical result.
6. Live updates: the harness subscribes to **MCP notifications** (decision 9).

**Shell / CLI (primary — shell harnesses + humans):**
`kanban-pro card create --board B --title "…"`; discovery via `kanban-pro --help` and
`kanban-pro capabilities --json` (machine-readable).

**HTTP (secondary):** REST + OpenAPI for programmatic clients.

All three project the same `core/` operations. The MCP tool schemas are generated **from
the domain models** — which is why the port and interfaces are thin over `domain` +
`core`: one definition of an operation, three ways to call it, zero per-harness code.

## Tech Stack

- **Python 3.12+**
- **MCP server** (Python MCP SDK) — the primary, harness-native interface (`kanban_pro/mcp/`)
- **CLI** (typer/click) — the primary shell interface (`kanban_pro/cli/`) — *planned*
- **FastAPI** — secondary HTTP layer (`kanban_pro/api/`); today it serves the web UI
- **Pydantic v2** — canonical model + validation (`kanban_pro/domain/`)
- **httpx** — async HTTP client for adapters that call remote backends
- **uv** — dependency & environment management
- **ruff** (lint + format), **mypy** (strict), **pytest** (tests)

## Project Structure

```
kanban_pro/
  domain/      # canonical Pydantic models: Board, Column, Card, Label, Comment, Relation
  ports/       # KanbanBackend Protocol (the contract) + Capabilities + Fulfilment + errors
  adapters/    # one module per backend (native.py = own store + overlay; hermes/ first)
  core/        # the one service: augmenting dispatch, dedupe, change-log (recording),
               # flow engine, work reports
  mcp/         # MCP server — ops as tools, capabilities as a resource (PRIMARY interface)
  api/         # FastAPI — serves the web UI (snapshot + SSE) (secondary interface)
  migrate.py   # kanban-pro-migrate — profile-to-profile board copy
  config.py    # profile selection + per-profile settings
  cli/         # shell CLI — ops as subcommands (PRIMARY interface) — PLANNED
tests/
```

The interface layers (`mcp/`, `api/`, and the planned `cli/`) are thin and stateless — all
behavior lives in `core/` over the `ports/` contract. Entry points are declared in
`pyproject.toml` (`kanban-pro-mcp`, `kanban-pro-ui`, `kanban-pro-migrate`); there is no
`app.py`.

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
- **One-card-one-column is violated** by Asana/ClickUp/monday/GitLab/Jira → placement is
  a `(board → column)` **membership set**, not a single pointer (decided — decision 4).
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

Milestones, deliberately thin-first — every decision above stands, but the expensive
halves are phased so a usable tool exists before the plumbing:

- **v0 — usable skeleton.** ✅ *Done 2026-07-05.* MCP server (tools + `capabilities`
  resource) directly over the **native store**. Any MCP harness can drive a real kanban.
- **v1 — Hermes parity.** ✅ *Done 2026-07-05, except the CLI.* `core/` augmenting layer
  (Tier 1: workflow + WIP enforcement) + `BaseAdapter` + shared contract suite + `hermes`
  adapter + `--profile` selection + idempotency keys (optional, not required — see
  decision 8). **CLI still outstanding.**
- **v2 — events.** ✅ *Mostly done 2026-07-05/06.* Core append-only change-log + pull
  change-feed + the `wait_changes` long-poll + SSE to the web UI. **MCP notifications and
  reconciliation polling still outstanding.**
- **v3 — the human's half.** ✅ *Done 2026-07-06/09.* Push-fed web board, rich card
  detail (activity, relations, legal moves), live session-log tail, card retry, and
  structured **work reports** with human-answerable questions.
- **Later.** CLI; full canonical HTTP surface; bulk ops; persistent webhook listener
  registry (HMAC, retries, per-listener cursors); content-hash dedupe fallback; Tier-2
  write-through polyfills; flow hooks/validators; human-readable card keys; additional
  profiles (Jira, Trello, …); multi-mount + confirmation-gated sync.

## What This Project Is NOT

- Not a kanban **UI** — it's the API/proxy layer. *(Nuance since 2026-07-06: `api/` ships
  an optional, on-demand web board so a human can watch and steer the agents. It is a
  thin **consumer** of `core/` like any other interface — it holds no logic and starts
  only when you run `kanban-pro-ui`. The product is still the API; the board is not a
  general-purpose kanban frontend.)*
- Not a **sync engine** — it proxies to one active backend at a time, it does not
  two-way-replicate between backends (that's a possible future, explicitly out of
  scope for v1).
- Not a **superset** of every backend's features — the canonical core is minimal;
  richness lives in `ext`.

## Open Questions

Live Q&A (with options and recommendations) is in [QUESTIONS.md](QUESTIONS.md) —
**nothing is currently open.** Q1–Q17 are answered and folded into the Key Design
Decisions above (see JOURNAL 2026-07-05): delete guards, placement add/remove ops,
`move_card` source disambiguation, and `ext` shallow-merge patch semantics.

*(Also resolved: ordering = integer positions + periodic rebalancing, not naive floats —
`Placement.position` is an int. Native store: built — `adapters/native.py`. Hermes's
actual API surface: confirmed and documented in
[docs/hermes-kanban.md](docs/hermes-kanban.md); the adapter and a 172-card live migration
are done.)*
