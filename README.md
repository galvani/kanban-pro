# kanban-pro

**One canonical kanban API over any backend — built agent-first.**

kanban-pro speaks a single canonical kanban model (boards, columns, cards, relations,
comments) and routes every operation to a swappable **backend adapter** — the native
SQLite store, the Hermes harness kanban, Jira next. Its primary interface is **MCP**:
any agent harness (Claude Code, Codex, Hermes, OpenCode, …) connects, introspects the
tools and capabilities, and drives a real kanban with zero bespoke integration.

The punchline: **agents and humans work the same board.** Every connection declares an
actor (`agent:hermes-engineer`, `human:jan`); every write lands in an append-only
change-log with full attribution. You always know who moved what, and a live web UI
watches the same log — no polling, ever.

The endgame is bigger than a proxy: kanban-pro is becoming the **system of record**
that replaces the Hermes built-in kanban, with Hermes demoted to just another MCP
consumer — and any other backend (Jira) linked in beside it.

## Features

✅ shipped · 🔜 queued (see [TODO.md](TODO.md) / [SPEC.md roadmap](SPEC.md#roadmap))

- ✅ **Canonical model + capability augmentation.** A small, strict core model with an
  `ext` passthrough for backend richness. Every capability is fulfilled the best way
  available — **delegated** to the backend, **polyfilled** by kanban-pro's overlay, or
  reported **unavailable**. Query `capabilities` and it never lies: each one reports
  `native` / `polyfilled` / `unavailable`. WIP-limit enforcement and comments/relations
  polyfill are live; write-through encoding into backend containers is 🔜.
- ✅ **MCP-first** — 28 tools + capability/board resources over stdio, one per canonical
  op, schemas generated from the domain models. 🔜 **CLI** for shell-first harnesses,
  🔜 full REST surface.
- ✅ **Push-fed web UI** — optional and on-demand only (`kanban-pro-ui`, never started
  by default): SSE off the change-log, DnD moves, card modal with comments, zero
  browser polling. Reconnect resumes via `Last-Event-ID`.
- ✅ **Actor identity + append-only change-log.** Per-connection `--actor kind:name`;
  every successful write is recorded (seq-cursored, per profile). `list_changes` is the
  pull feed; MCP notifications ride the same log 🔜.
- ✅ **Agent-safety semantics.** A misfiring agent can't one-shot destroy data:
  deletes are **archive-first** (purge only an already-archived card), board/column
  deletes are **empty-only**, WIP limits are enforced on every profile. Idempotency
  keys + retry dedupe (no surveyed backend has them — the proxy owns it) land in 🔜.
- ✅ **Flow engine.** Declarative `flow.yaml` per profile: transition state-machines
  with **named schemes per card** (a docs task skips the code-review steps) and the
  reserved **`free-roam`** scheme — assign it and that card moves unrestricted while
  the board default stays enforced. Rules guide, they don't imprison: config mistakes
  degrade to freedom + loud logging, `list_transitions(card_id)` answers "what moves
  are legal from here", and `move_card(force=true)` deliberately bypasses a denial —
  always flagged in the change-log, never silent. 🔜 pre-transition validators +
  post-transition hooks.
- ✅ **Swappable adapters.** `native` (SQLite system of record), `memory`, and
  `hermes` (SQLite reads + CLI writes, verified against the live 64-card board) — all
  behind one shared contract test suite. 🔜 `jira`, **MCP-backed**: kanban-pro connects
  as an MCP *client* to the Atlassian MCP, so it never holds Jira credentials.
- 🔜 **Smart remote caching.** Per-profile read cache for remote adapters only, kept
  fresh by per-adapter change detection (JQL delta-poll for Jira, `task_events` tail
  for Hermes) — local SQLite reads stay uncached.
- 🔜 **Confirmed two-way sync.** Cross-mount copy + provenance link first; then both
  boards sync both ways via proposed change-sets you approve — never silent
  replication.
- 🔜 **The work queue.** `list_work` answers an agent's "what should I work on?" —
  cards assigned to *me* (the connection's actor) or unassigned, in ready-ish columns.
  Plus an atomic **claim/lease** op (CAS + TTL + heartbeat, proven by the Hermes
  dispatcher) so two agents never grab the same card.
- 🔜 **Human-readable card keys** — Jira-style `PRO-12` per-board keys instead of uuid
  hex, so agents and humans address `jira/TASK-001`, not `eda39e7b…`.

## Why not X?

The concept combo — self-hosted backend-agnostic proxy + honest capability polyfill +
MCP-first + agent-safety — has no direct prior art (web survey, 2026-07-05; see
[JOURNAL.md](JOURNAL.md)). The nearest neighbors each miss a piece:

| | kanban-pro | Unified task APIs (Unified.to-style) | MCP aggregators (Composio Rube) | Agent boards (Agent Kanban, Flux) | Per-backend MCP (Atlassian, Linear) | Classic kanbans (Planka, Vikunja) |
|---|---|---|---|---|---|---|
| Self-hosted | ✅ | ❌ SaaS | ❌ SaaS | varies | varies | ✅ |
| Backend-agnostic | ✅ one model, any adapter | ✅ normalize-only | ➖ many apps, per-app tools | ❌ own store only | ❌ one backend | ❌ own store only |
| MCP-native | ✅ primary interface | ❌ | ✅ | ✅ | ✅ | ❌ |
| Capability polyfill | ✅ delegate → polyfill → honest `unavailable` | ❌ gaps are just missing | ❌ | ➖ n/a | ❌ | ❌ |
| Agent-safety semantics | ✅ archive-first, guarded deletes, WIP | ❌ | ❌ | ➖ partial | ❌ raw backend semantics | ❌ |
| Actor audit trail | ✅ per-connection actor + change-log | ❌ | ❌ | ➖ | ➖ backend's own | ➖ |
| Push-fed UI | ✅ SSE, on-demand | ❌ | ❌ | ➖ | ❌ | ➖ web UI |

## Quick Start

```bash
uv sync                                  # install deps (incl. dev tools)
uv run kanban-pro-mcp                    # MCP server (stdio) over the native SQLite store
uv run kanban-pro-mcp --profile memory   # ... over an ephemeral in-memory board
uv run kanban-pro-ui --profile hermes    # OPTIONAL web board (on demand only) -> :8747
```

Pass `--actor kind:name` (e.g. `agent:hermes-engineer`, `human:jan`) so every write is
attributed in the change-log. The web UI is **push-fed** (SSE off the change-log — no
browser polling) and never starts unless you run it.

The store lives at `~/.local/share/kanban-pro/kanban.db` (override: `KANBAN_PRO_DB`).

## Install into your harness

The server is stdio-spawned by the harness — no daemon, no port. Get the exact
registration snippet for your harness:

```bash
uv run kanban-pro-mcp --print-config claude     # or: codex | opencode | hermes
```

e.g. Claude Code, with attribution:

```bash
claude mcp add kanban-pro -s user -- \
  uv run --directory /path/to/kanban-pro kanban-pro-mcp --actor agent:claude-code
```

Multiple harnesses can register the same server — each spawns its own process (with its
own actor); they share the SQLite store safely.

**Any OS (mac/Windows/Linux), no clone needed** once the repo has a remote: install
[uv](https://docs.astral.sh/uv/), then `uvx --from git+<repo-url> kanban-pro-mcp`, or
`uv tool install` to put `kanban-pro-mcp` on PATH.

Pick the backend with a **profile** — `--profile hermes` / `--profile jira` /
`--profile default` (or `KANBAN_PRO_PROFILE`). A profile bundles an adapter with its
settings; kanban-pro always exposes the **full canonical surface** regardless.

## What it looks like in practice

An agent session over MCP (all real today except the `PRO-12` human-readable card
keys, which are 🔜 — ids are uuid hex for now):

```
agent> list_boards
  → [{id: "b1", name: "kanban-pro"}]
agent> create_card {title: "Port the Hermes board plugin",
                    placements: [{board_id: "b1", column_id: "todo", position: 0}]}
  → Card PRO-12 created                       (actor agent:claude-code, logged)
agent> move_card PRO-12 → doing
  → conflict: WIP limit reached on 'doing' (3/3)
agent> list_transitions PRO-12
  → scheme 'default' (source: flow) — legal from todo: [doing]
agent> move_card PRO-12 → done
  → conflict: scheme 'default' does not allow todo -> done; use force=true to override
agent> move_card PRO-12 → done, force=true
  → Card moved. The event carries forced=true — never silent.
human> list_changes since=41
  → [{seq: 42, actor: "agent:claude-code", op: "card.moved", forced: true, …}]
```

The flow config that drives it (`~/.config/kanban-pro/flows.yaml` or
`$KANBAN_PRO_FLOWS`; `hooks`/`wip_limits` keys are reserved 🔜):

```yaml
flows:
  default:                       # code tasks: gated pipeline (the enforced default)
    states: [backlog, todo, doing, review, done]
    transitions:
      - { from: todo,   to: doing }
      - { from: doing,  to: [review, todo] }
      - { from: review, to: [done, doing] }
  docs:                          # documentation tasks skip the review gate
    states: [todo, doing, done]
    transitions:
      - { from: todo,  to: doing }
      - { from: doing, to: done }
# a card picks its scheme via ext["kanban_pro.scheme"]: "docs", or the reserved
# "free-roam" for unrestricted movement; unset = the default scheme above.
# No flows.yaml at all -> the whole board behaves as free-roam (opt-in engine).
```

And when you want eyes on the board:

```bash
uv run kanban-pro-ui --profile hermes --actor human:jan   # -> http://localhost:8747
```

One snapshot, then SSE deltas. Drag a card in the browser, watch the agent's
`list_changes` cursor pick it up; let an agent move a card, watch it slide live.

## Architecture

Ports & adapters (hexagonal), consumed **MCP-first / shell-first** (agent harnesses are
the primary clients; HTTP is secondary):

```
harnesses / clients
   │   MCP (primary) · CLI (primary) · HTTP (secondary) — thin, stateless
   ▼
core/  — Recording(Augmenting(adapter)): actor stamping + change-log,
         delegate/polyfill routing, guards, dedupe
   ▼
KanbanBackend port ──▶ adapter ──▶ backend
          ▲
canonical domain model (Pydantic)
```

Interfaces never talk to an adapter directly — everything goes through `core/`, so no
interface can bypass the guards or the audit trail. Directory layout:
[AGENTS.md](AGENTS.md#architecture-ports--adapters); design: [SPEC.md](SPEC.md).

## Documentation

- [SPEC.md](SPEC.md) — what and why (canonical model, the core+passthrough decision,
  capability model)
- [JOURNAL.md](JOURNAL.md) — decisions and rationale
- [AGENTS.md](AGENTS.md) — conventions & hard rules for coding agents, incl. how to
  author a new adapter
- [docs/methods.md](docs/methods.md) — every operation + its MCP projection
- [docs/hermes-kanban.md](docs/hermes-kanban.md) — Hermes ground truth & migration map

## Status / Roadmap

**v0 + v1 core shipped:** domain models, the port, three adapters (`memory`, `native`
SQLite, `hermes`), the augmenting layer (WIP enforcement + comments/relations
polyfill + flow engine), the MCP server (28 tools + resources), actor identity + append-only
change-log with the `list_changes` pull feed, and the push-fed web UI — all tested,
verified live against the real Hermes board.

**Next:** CLI, idempotency keys, flow hooks/validators, the Hermes → native migration
+ cutover, the Jira MCP-backed adapter with cross-board copy/link, work queue +
claim/lease. Roadmap: [SPEC.md](SPEC.md#roadmap); the full
queue: [TODO.md](TODO.md). Anything marked 🔜 above does not run today.

## License

All rights reserved (personal project).
