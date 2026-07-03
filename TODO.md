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

## Later (roadmap)

- [ ] Workflow control: allowed column→column transitions (state machine), `WORKFLOW`
  capability, `move_card` validated against the transition graph.
- [ ] Additional profiles: Jira, Trello, …
