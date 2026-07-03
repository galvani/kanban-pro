# kanban-pro — TODO

Durable backlog. Newest ideas at top; move items into JOURNAL when decided/done.

## Decided — next up

- [ ] **Implement kanban-pro's own native kanban backend** — DECIDED 2026-07-03, and
  sequenced **right after the kanban-solutions web research** (before the Hermes
  adapter). Not just a proxy — a first-class native provider so kanban-pro is usable
  standalone and serves as the reference implementation exercising the whole port.
  - Shape: the `default` profile = a native adapter backed by real persistence
    (SQLite likely), implementing the full `KanbanBackend` port.
  - Upgrades the planned `memory` reference adapter from test-only into a real backend
    (or keep `memory` for tests + add `native`/`local` for persistence — decide).
  - Consequence accepted: we own storage (schema, persistence, migrations).
  - **Design references from the research (to apply here):** model the core relational
    schema + realtime after **Planka** (cleanest Project→Board→List→Card); model typed
    relations + WIP after **Vikunja** (`relation_kind` enum: subtask/blocking/precedes/
    duplicates/related; bucket `limit`). Avoid Focalboard's view-relative position
    model (a card must have one intrinsic column+position). Use integer/rebalanced
    ordering, not naive floats (Planka/Trello float-position rebalancing is a known
    pain).
  - Because it's OUR store, kanban-pro can natively enforce what no backend does:
    server-side **allowed transitions** and **WIP limits** (a genuine differentiator).
  - **Double duty:** the native store is ALSO the **overlay** that polyfills gaps in
    other backends (SPEC decision 2) — design its schema so overlay rows can key to an
    external backend entity ID, not just native IDs.

- [ ] **Augmentation layer** (`AugmentingBackend` decorator = adapter + overlay). Wraps
  any adapter, delegates NATIVE capabilities, polyfills the rest from the overlay, merges
  on read. Report per-capability `Fulfilment` via `GET /capabilities`.
  - Tier 1 first (workflow-transition + WIP enforcement — pure logic, no data split).
  - Then Tier 2 (relations/custom-fields/comments in the overlay, keyed to backend IDs).
  - Reconciliation: GC overlay rows orphaned by out-of-band backend deletes.

## Harness-native interfaces (must-have) — MCP-first, shell-first

- [ ] **MCP server** (`kanban_pro/mcp/`) — PRIMARY interface. Every canonical op = an MCP
  tool ("skill"); active provider `Capability`/`Fulfilment` = an MCP resource. Any harness
  (Hermes, Claude Code, Codex, OpenCode, GPT agents, …) introspects skills + calls them
  natively — no per-harness code. (SPEC decision 5, resolves Q1.)
- [ ] **CLI** (`kanban_pro/cli/`) — PRIMARY interface. Same ops as subcommands for
  shell-first harnesses (Codex/Claude Code shelling out) + humans.
- [ ] **HTTP/REST** (`kanban_pro/api/`) — secondary, for programmatic clients.
- [ ] Keep all three thin over `core/` — no drift.
- [ ] Hermes: also a **backend adapter** (the first), not just a consumer.

## Planned (from SPEC)

- [ ] Define canonical domain models (`kanban_pro/domain/`): Board, Column, Card,
  Label, Comment (+ `ext` passthrough).
- [ ] Flesh out the `KanbanBackend` port with real domain types (replace `Any`
  placeholders).
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
- [ ] **Real-Jira connector / webhook — copy cards between boards** (2026-07-04). A `jira`
  adapter + inbound Jira webhook ingest so cards can be **copied across boards** (Jira ↔
  native/Hermes) through the canonical model. Note: cross-board *copy* leans on the
  `placements[]` model + the change-log (decision 9); a true two-way *sync* between two
  live backends is explicitly out of v1 scope (SPEC "What This Project Is NOT") — decide
  copy-once vs. keep-in-sync when we get here.
