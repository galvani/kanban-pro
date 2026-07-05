# kanban-pro — TODO

Durable backlog. Newest ideas at top; move items into JOURNAL when decided/done.

## Decided — next up

*(Done & journaled: domain models, wired port, `memory` adapter, `native` SQLite store,
**v0 MCP server** (`kanban-pro-mcp`), **Q14–Q17 port expansion** (placement ops, strict
move, empty-only delete guards, ext shallow-merge) — see JOURNAL 2026-07-05.)*

- [ ] **Human-readable card keys** (from the Q16 brainstorm) — native store mints
  Jira-style per-board keys (`PRO-12`: board prefix + counter) as first-class card ids
  instead of uuid hex; adapters with native keys (Jira) map theirs. Agents and humans
  address `jira/TASK-001`, not `eda39e7b…`. Decide: replace `id` vs a `key` alias field.
- [ ] **Augmentation layer — remaining slices** (core exists 2026-07-05:
  `AugmentingBackend` + `BaseAdapter` + contract suite; WIP enforcement (Tier 1) +
  comments/relations overlay polyfill (Tier 2) + fulfilment reporting + delete-GC of
  overlay rows all live). Still to build:
  - WORKFLOW transition enforcement — blocked on the flow-YAML design (below), incl.
    the force override.
  - ARCHIVE flag polyfill for backends without archive (needs shadow-flag storage).
  - Write-through encoding (persist polyfill data into backend containers) + the
    per-adapter/per-capability persistence-strategy choice.
  - Reconciliation polling: GC overlay rows orphaned by out-of-band backend deletes
    (delete-through-us already GCs).

## Harness-native interfaces (must-have) — MCP-first, shell-first

- [x] **MCP server** (`kanban_pro/mcp/`) — PRIMARY interface. DONE 2026-07-05 (v0):
  23 tools + `kanban://capabilities`/`boards`/`board/{id}`/`card/{id}` resources, stdio,
  `kanban-pro-mcp --profile <name>`. Idempotency keys + notifications follow core (v1/v2).
- [ ] **CLI** (`kanban_pro/cli/`) — PRIMARY interface. Same ops as subcommands for
  shell-first harnesses (Codex/Claude Code shelling out) + humans.
- [ ] **HTTP/REST** (`kanban_pro/api/`) — secondary, for programmatic clients.
- [ ] Keep all three thin over `core/` — no drift.
- [ ] Hermes: also a **backend adapter** (the first), not just a consumer.

## Planned (from SPEC)

- [x] `hermes` adapter — DONE 2026-07-05 (`adapters/hermes/`: SQLite reads + CLI
  writes per docs/hermes-kanban.md; `--profile hermes`; smoke-tested read-only against
  the live board). Known limits (CLI-bound): no unarchive, update = assignee only,
  move enters only ready/blocked/done. Next feeds the migration item below.
- [ ] **Hermes → native migration + cutover** (goal: kanban-pro replaces the Hermes
  kanban). Import boards/cards/comments from Hermes into the native store via the
  canonical model (hermes adapter reads, native store writes); then point the Hermes
  harness at kanban-pro's MCP/CLI and retire the built-in kanban. Transitional
  coexistence = the confirmed two-way sync item (above); decide cutover moment with
  Jan.
- [ ] `--profile` selection + profile registry in `config.py`.
- [ ] FastAPI routes + `GET /capabilities` in `kanban_pro/api/`; `app.py` entrypoint.

## Flow management (workflow engine) — design area

- [ ] **Flow management: transitions + hooks.** Grow `WORKFLOW` from "allowed moves" into
  a small per-board/profile automation engine (kanban-pro Tier-1 polyfill — works over any
  backend since it wraps `move_card`).
  - **Transition graph:** allowed `column→column` edges (a state machine) per board/profile;
    `move_card` validated against it; expose the graph so a harness can ask "what moves are
    legal from here" (like Jira `GET /transitions`).
  - **`list_transitions(card_id)` MCP tool (Jan, 2026-07-05):** any card must be able to
    report its valid next moves easily over MCP — from the flow graph where one is
    configured, else from the backend's native workflow (hermes: the lane-verb map),
    else "all columns" (free-move). Ship WITH the flow engine; the hermes-native case
    could even ship before it.
  - **Per-card workflow schemes (Jan, 2026-07-05):** a card can be assigned a
    transition profile/scheme — e.g. a documentation task skips the coder/review
    steps a code task needs. Like Jira's issue-type schemes: `flows:` in the YAML
    becomes a named-scheme map, a card carries `scheme` (default from board/profile),
    validation + `list_transitions` resolve through the card's scheme. Assignment
    must be easy: settable at create and via update_card.
  - **Hooks:**
    - *pre-transition validators* — can block/deny a move (e.g. "can't reach Done with an
      open checklist" / required field missing). Return allow/deny + reason.
    - *post-transition actions* — fire after a move (set a field, add a comment, create a
      follow-up card, notify, emit a custom event).
  - **START: a single declarative YAML per profile** (states, transitions, WIP limits,
    hooks), loaded at startup. Version-controlled, diffable, no UI needed; fits decision 3
    (config file for definitions) — lives in the profile config or a referenced `flow.yaml`.
    Sketch:
    ```yaml
    flows:
      default:
        states: [backlog, todo, doing, review, done]
        wip_limits: { doing: 3, review: 2 }
        transitions:
          - { from: todo,   to: doing }
          - { from: doing,  to: [review, todo] }
          - { from: review, to: [done, doing] }
        hooks:
          - { on: enter, state: done, require: checklists_complete, else: deny }  # validator
          - { on: exit,  state: doing, do: set_field, field: started_at, value: now }  # action
    ```
  - **Force override (Jan, 2026-07-05):** a denied transition must be bypassable
    deliberately — `move_card(..., force=true)` skips transition/WIP validation, is
    always allowed for humans/agents that opt in, and is **logged/audited + flagged in
    the change event** so a forced move is visible, never silent. Rules guide, they
    don't imprison.
  - **Hooks split into two kinds** (reserve both in the syntax from day one):
    - *declarative built-ins* — fixed vocabulary (`require: …`, `do: set_field|add_comment|
      notify`); covers most cases, zero code.
    - *named code hooks* — escape hatch `do: hook:<name>` → registered Python handler for
      logic YAML can't express. Build the handlers later; reserve the syntax now.
  - **Static-first:** YAML reloads on change/restart. Runtime-editable (store-backed via
    the API) is deferred — the YAML is the seed that loads into the flow engine.
  - **Design questions still to settle:** sync (blocking, validators) vs async (post-actions);
    how hooks integrate with the change-log/event surface (decision 9) and idempotency
    (decision 8); failure semantics (does a failing post-action roll back the move?).
  - Relates to SPEC decision 2 (WORKFLOW polyfill, Tier 1) + decision 9 (events).

## Cross-cutting (queued 2026-07-05, Jan)

- [ ] **Agent-native kanban** (goal follows from replacing the Hermes kanban):
  - **Agent assignees:** an agent is a `User` with `ext.kind="agent"` (works today);
    promote a first-class `User.kind: human|agent` once proven. Prereq: the queued
    `list_users`/`get_user` port ops — assignment needs discoverable ids.
  - [x] **Actor identity** — DONE 2026-07-05 (SPEC decision 10): per-connection
    `--actor kind:name` / `$KANBAN_PRO_ACTOR` on the MCP server, stamped on every
    recorded write. Per-call override deferred until a real need.
  - [x] **Transition log / change-log core** — DONE 2026-07-05: `core/changelog.py`
    (append-only, cursored, SQLite per profile) + `RecordingBackend` decorator +
    `list_changes` MCP tool (pull feed). Still to come: WS/SSE + MCP notifications
    (with the UI build), backend-watcher ingestion (hermes task_events → change-log),
    card timeline projection.
  - **Card-scoped error events, NOT a log sink:** agent failures land as typed
    comments/events on the card ("error: …", actor, timestamp). Raw telemetry
    (stack traces, stdout, tokens) stays outside; the card carries a reference
    (attachment link / session id in ext). HARD boundary against drifting into an
    observability platform.
  - **Work-queue query — "what's available for me?":** a core projection (no port
    change): scan live cards, filter assignee == actor (or Jan) OR unassigned,
    column category in ready-ish states (backlog/unstarted/started), return with
    board/column context. MCP tool (e.g. `list_work(assignee?, include_unassigned)`),
    default assignee = the connection's actor. Adapters may later add native
    filtering for efficiency.
  - **Multi-assignee:** already in the model (`Card.assignees[]` list +
    `MULTI_ASSIGNEE` capability, native in both stores) — nothing to build; single-
    owner backends map via capability honesty (Hermes is single-assignee). Convention
    for agent collision-avoidance: claiming a card = assign yourself + move to a
    started column in one action, visible in the actor-stamped change-log.
  - **Claim/lease op** (proven needed by Hermes's dispatcher: `claim_lock` CAS + TTL +
    heartbeat + reclaim-on-crash): atomic "this worker owns this card until <expiry>"
    so two agents never pick the same card. Design as a core op once the Hermes
    dispatcher becomes a kanban-pro consumer (see docs/hermes-kanban.md).
  - **`priority` core promotion candidate:** Hermes and Jira both have it (≥2 backends
    rule met) — decide when the hermes adapter lands.

- [ ] **Backlog support (Jira-style)** — a board's backlog (in Jira it lives OUTSIDE the
  board's columns) must be visible/manageable through kanban-pro too. We have
  `ColumnCategory.BACKLOG`; decide the mapping: Jira backlog ⇄ a canonical
  backlog-category column (adapter maps issues without a board column into it), so
  "see the backlog" works uniformly on native + Jira. Check how Hermes models backlog.
- [ ] **Two-way sync — after confirmation (Jan, supersedes "copy-once only" as the end
  state):** the Jira board and the Hermes board should each sync **both ways** with the
  linked native/local board, but changes apply only **after confirmation** (a proposed
  change-set the user approves, not silent replication). Sequencing stays: copy+link
  first (decided earlier today), confirmed sync builds on it via the v2
  change-log/reconciliation. NOTE: SPEC "What This Project Is NOT" currently rules out
  two-way sync — revise that section when this lands (confirmation-gated sync is the
  compromise that keeps it sane). Caching: **only remote backends get the smart cache**
  (Jira; below) — Hermes is local/fast, no cache layer.

- [ ] **Good logging** — consistent, structured op logging across core/adapters/
  interfaces: profile, operation, entity ids, outcome/error-code, duration; forced
  transitions and destructive ops always logged. Seeded in `mcp/` (stderr logger,
  taxonomy-coded warnings); design the real story with core (log file/rotation? JSON
  lines? correlate with the change-log of decision 9 — one event, two sinks?).
- [ ] **Remote-adapter read cache + change detection** (generalized from "smart Jira
  caching", 2026-07-05). A core-level read-cache decorator (same wrapper pattern as
  `AugmentingBackend`) for **remote adapters only** — local SQLite reads (native,
  hermes) are ~ms and stay uncached (Jan's earlier ruling: cache only remote).
  Per-adapter **change-detection descriptor** keeps the cache fresh cheaply instead
  of full refetches:
  - `jira`: no push through the Atlassian MCP → delta-poll (`updated >= <cursor>`
    JQL / version compare); native Jira webhooks (30-day expiry) optional later.
  - `hermes`: `task_events` is an append-only id-cursored table → tail
    `WHERE id > cursor` = near-push change feed for free (also feeds decision-9
    reconciliation + our change-log import).
  Client-side 2s-polling is solved separately by v2 push (MCP notifications /
  webhooks / cursored feed) — clients subscribe to kanban-pro, kanban-pro watches
  the backend. Decide staleness policy (serve-stale + refresh vs block) when built.
  **Config-controlled (Jan, 2026-07-05):** cache is per-profile in the YAML config
  file (SPEC decision 3 — profile definitions live there), on/off + timing, e.g.:
  ```yaml
  profiles:
    jira:
      adapter: jira
      cache:
        enabled: true          # off = every read hits the backend
        refresh_seconds: 30    # change-detection poll cadence (delta-poll / events tail)
        ttl_seconds: 300       # hard staleness ceiling — full refetch past this
  ```
  Defaults: remote adapters on, store adapters off/absent; `enabled: false` must
  fully bypass the decorator (not just shorten TTLs).
- [ ] **Monitoring HTTP server via shell argument** — e.g. `--monitor [port]` on the
  server/CLI starts a small read-only HTTP dashboard (live board view; later fed by the
  v2 change-feed instead of polling). OPEN: exactly what to show (board view? op log?
  health/metrics?) — clarify before building.
  **UI is OPTIONAL and on-demand (Jan, 2026-07-05):** never started by default — the
  MCP server stays UI-free; the web UI (incl. the ported Hermes board plugin) runs
  only when explicitly asked for via a flag/subcommand (`--serve-ui` / `--monitor`),
  and stops with the process. Applies to every UI surface this project grows.

## UI (to explore)

- [ ] **UI is push-fed, never browser-refresh (Jan, 2026-07-05):** the backend pushes
  changes to the browser (WebSocket/SSE off the core change-log — the UI is just
  another decision-9 projection, like MCP notifications). No client polling loops.
  The ported Hermes plugin already expects a WS events feed, so the port keeps that
  shape. Consequence — build order for the UI: actor identity → core change-log +
  WS endpoint → UI port. Initial page load = one REST snapshot; everything after =
  pushed deltas keyed by change-log cursor (reconnect resumes from cursor, no
  full reload).

- [ ] **Check the Hermes board plugin we built** — see whether its board UI can be reused
  / easily wired into kanban-pro's own UI (as a front-end consumer of the canonical API).
  - Locate the plugin in the Hermes workspace; assess coupling to Hermes vs. reusability.
  - NOTE: current SPEC says "not a kanban UI." If we adopt a UI, update *What This Project
    Is NOT* + add a UI interface note (it'd be another consumer of the MCP/HTTP surface,
    not core logic).

## Later (roadmap)

- [ ] Workflow control: allowed column→column transitions (state machine), `WORKFLOW`
  capability, `move_card` validated against the transition graph.
- [ ] Additional profiles: Jira, Trello, …
- [ ] **Jira adapter + local board, cross-board copy/link/transition** (2026-07-04,
  expanded 2026-07-05 — Jan: "jira adapter with its own board + a local-only kanban
  adapter, where I can copy and link and transition both boards"). Pieces:
  - `jira` adapter (remote, package layout per docs/adapter-structure.md); the local
    board is the existing `native` store.
  - **Transport = Atlassian MCP, not raw REST (Jan, 2026-07-05):** the adapter is an
    **MCP-backed adapter** — kanban-pro connects as an MCP *client* (same `mcp` SDK) to
    the Atlassian MCP server whenever available; if the `jira` profile is selected and
    no Atlassian MCP is reachable/configured, fail with an actionable error that
    **suggests installing/enabling it** (remote official server or local
    mcp-atlassian). Benefits: Atlassian owns the OAuth/token dance (kanban-pro never
    holds Jira creds — credential-holder pattern), no REST client to maintain. Caveat:
    board-admin ops (column CRUD, rank/position) likely aren't MCP tools → declare
    non-native (capability honesty), overlay polyfills; raw REST only as a targeted
    fallback if a coverage gap hurts. Pattern generalizes: any backend with an MCP
    server can get an adapter this way.
  - **Pulls multi-mount forward** (SPEC decision 3 deferred it): two live profiles at
    once (`jira` + `native`), mount-prefixed. The mount-prefix layer was designed to be
    addable without core rework.
  - **Cross-mount copy** = core op: read from mount A, create in mount B, stamp
    provenance (`ext["kanban_pro.copied_from"]`) + a cross-mount **link**. Cross-mount
    relations can't live in either backend → they live in the **overlay** (Tier-2
    polyfill, keyed `(mount, card_id)`).
  - **Transition semantics DECIDED 2026-07-05: copy-once + link first.** Each board
    transitions independently; both are driven through the one API. Mirrored
    transitions on linked cards = later follow-up on top of the same links, once the
    v2 change-log/reconciliation exists. Full two-way sync stays out of scope.
