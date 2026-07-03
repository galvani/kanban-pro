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
