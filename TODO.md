# kanban-pro — TODO

Durable backlog. Newest ideas at top; move items into JOURNAL when decided/done.

## Decided — next up

*(Done & journaled: domain models, wired port, `memory` adapter, `native` SQLite store,
**v0 MCP server** (`kanban-pro-mcp`) — see JOURNAL 2026-07-05.)*

- [ ] **Port expansion from Q13–Q16 rulings (2026-07-05)** — implement across port +
  memory + native + MCP + tests + docs/methods.md:
  - `add_placement(card_id, placement)` / `remove_placement(card_id, board_id)` (Q15 —
    removing the last placement should error; a card must live somewhere, archive it
    instead).
  - `move_card` strict within-board: error if the card has no placement on
    `to_board_id`; drop the current silent placement-add (Q16).
  - Empty-only guards for `delete_board` / `delete_column` in `core/` (Q14).
  - `ext` patch = **shallow merge**, `null` removes a key (Q17) — today's adapters
    replace the whole dict via `model_copy(update=…)`; fix in memory + native.
- [ ] **Human-readable card keys** (from the Q16 brainstorm) — native store mints
  Jira-style per-board keys (`PRO-12`: board prefix + counter) as first-class card ids
  instead of uuid hex; adapters with native keys (Jira) map theirs. Agents and humans
  address `jira/TASK-001`, not `eda39e7b…`. Decide: replace `id` vs a `key` alias field.
- [ ] **Augmentation layer** (`AugmentingBackend` decorator = adapter + overlay). Wraps
  any adapter, delegates NATIVE capabilities, polyfills the rest from the overlay, merges
  on read. Report per-capability `Fulfilment` via `GET /capabilities`.
  - Tier 1 first (workflow-transition + WIP enforcement — pure logic, no data split).
  - Then Tier 2 (relations/custom-fields/comments in the overlay, keyed to backend IDs).
  - Reconciliation: GC overlay rows orphaned by out-of-band backend deletes.

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

- [ ] Confirm Hermes's real kanban API surface, then write the `hermes` adapter (v1
  target = Hermes parity).
- [ ] `--profile` selection + profile registry in `config.py`.
- [ ] FastAPI routes + `GET /capabilities` in `kanban_pro/api/`; `app.py` entrypoint.
- [ ] Contract test suite (the shared suite every adapter must pass).

## Flow management (workflow engine) — design area

- [ ] **Flow management: transitions + hooks.** Grow `WORKFLOW` from "allowed moves" into
  a small per-board/profile automation engine (kanban-pro Tier-1 polyfill — works over any
  backend since it wraps `move_card`).
  - **Transition graph:** allowed `column→column` edges (a state machine) per board/profile;
    `move_card` validated against it; expose the graph so a harness can ask "what moves are
    legal from here" (like Jira `GET /transitions`).
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
- [ ] **Smart Jira caching** — the `jira` adapter holds a **local cache** (overlay/native
  store rows keyed to Jira ids) and downloads **only what changed**, detected via
  `updated`-since JQL / entity version-hash comparison, instead of full refetches.
  Fits the reconciliation-polling design (decision 9): the poll becomes a cheap delta
  sync. Also the answer to Jira rate limits. Decide staleness policy (serve-stale +
  refresh vs block) when built.
- [ ] **Monitoring HTTP server via shell argument** — e.g. `--monitor [port]` on the
  server/CLI starts a small read-only HTTP dashboard (live board view; later fed by the
  v2 change-feed instead of polling). OPEN: exactly what to show (board view? op log?
  health/metrics?) — clarify before building.

## UI (to explore)

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
