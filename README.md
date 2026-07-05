# kanban-pro

**A kanban board your coding agents natively understand.**

You run coding agents every day — Claude Code, Codex, whatever comes next. They do
real work: fix bugs, ship features, review each other's changes. But their *tasks*
live in chat scrollback. You come back to your desk asking: what is my agent doing
right now? What's blocked? What did it finish while I slept? There is no board that
both you and the agents can see and update.

kanban-pro is that board. It's a real kanban — boards, columns, cards, comments —
served over **MCP**, the protocol your agent harness already speaks. Register it once
and every agent session can read the board, pick up cards, move them, and comment,
with no bespoke integration. Concretely, you can:

- **Give all your agents one shared board.** One `claude mcp add` line per harness;
  multiple harnesses share the same store safely, each under its own identity.
- **Always know who did what.** Every connection declares an actor
  (`agent:claude-code`, `human:jan`); every write lands in an append-only change-log.
  Ask `list_changes` and see exactly which agent moved which card, and when.
- **Sleep through agent mistakes.** A misfiring agent can't one-shot destroy data:
  deletes are archive-first (purge only what's already archived), board/column deletes
  refuse while cards remain, WIP limits are enforced on every move.
- **Set the rules of the game.** A declarative `flow.yaml` defines which column moves
  are legal; agents ask `list_transitions` for their options, and a deliberate
  `force=true` override is always allowed — and always flagged in the log, never silent.
- **Watch it live.** An optional web UI streams the board over SSE — drag a card in
  the browser, or watch an agent's move slide across the screen. Zero polling.

## Quick Start

```bash
uv sync                                  # install deps (incl. dev tools)
uv run kanban-pro-mcp                    # MCP server (stdio) over the native SQLite store
uv run kanban-pro-mcp --profile memory   # ... over an ephemeral in-memory board
uv run kanban-pro-ui                     # OPTIONAL web board (on demand only) -> :8747
```

Pass `--actor kind:name` (e.g. `agent:claude-code`, `human:jan`) so every write is
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

## What it looks like in practice

An agent session over MCP (all real today except the `PRO-12` human-readable card
keys, which are 🔜 — ids are uuid hex for now):

```
agent> list_boards
  → [{id: "b1", name: "kanban-pro"}]
agent> create_card {title: "Add retry logic to the sync worker",
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
uv run kanban-pro-ui --actor human:jan   # -> http://localhost:8747
```

One snapshot, then SSE deltas. Drag a card in the browser, watch the agent's
`list_changes` cursor pick it up; let an agent move a card, watch it slide live.

## One board API, many backends, gaps polyfilled

Out of the box, kanban-pro *is* the board — cards live in its own SQLite store. But
the board API is deliberately separated from where cards are stored, via **adapters**.

The scenario that motivates this: your team tracks work in Jira. You point kanban-pro
at Jira, and your agents work real Jira tickets through the exact same safe,
attributed kanban tools — no agent ever learns the Jira API or holds a Jira token.
And where Jira lacks something kanban-pro offers (WIP limits, flow schemes,
checklists), kanban-pro **fills the gap itself** from its own overlay — and tells you
honestly which is which: query `capabilities` and each one reports **`native`**
(the backend does it), **`polyfilled`** (kanban-pro does it on top), or
**`unavailable`**. It never lies about what's real.

```
  your agents (Claude Code, Codex, …)          you (browser)
        │  MCP tools (28)                        │  live UI (SSE)
        ▼                                        ▼
  ┌──────────────────── kanban-pro core ────────────────────┐
  │ actor stamping · change-log · delete guards ·           │
  │ WIP + flow enforcement · capability polyfills           │
  └───────┬───────────────────┬────────────────────┬────────┘
     native SQLite         memory            jira  🔜
     (default system      (ephemeral,     (via the official
      of record)           for tests)      Atlassian MCP)
```

Adapters today: **`native`** (the default SQLite system of record), **`memory`**, and
one harness adapter (`hermes` — the pattern for wiring in your own harness's built-in
kanban). All pass one shared contract test suite. The **`jira`** adapter is upcoming
and will consume the official Atlassian MCP as a client — Atlassian owns the OAuth
dance, so kanban-pro never holds Jira credentials.

Pick the backend with a **profile** — `--profile default` / `--profile memory` (or
`KANBAN_PRO_PROFILE`). A profile bundles an adapter with its settings; kanban-pro
always exposes the **full canonical surface** regardless of the backend's gaps.

Boards move, too: a generic **migration tool** (`kanban-pro-migrate`) copies any
profile into any other — idempotent, dry-run first, provenance-stamped, the import
itself attributed in the change-log. It has run for real: a 172-card board with 608
comments imported port-to-port.

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

## Architecture

Ports & adapters (hexagonal), consumed **MCP-first / shell-first** (agent harnesses
are the primary clients; HTTP is secondary):

```
harnesses / clients
   │   MCP (primary) · CLI (🔜) · HTTP (secondary) — thin, stateless
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
- [docs/hermes-kanban.md](docs/hermes-kanban.md) — ground truth for the first harness
  adapter & its migration map

## Status / Roadmap

**Working today:** the canonical model and port, three adapters behind one contract
suite, the augmenting layer (WIP enforcement, comments/relations polyfill, honest
capability reporting), the MCP server (28 tools + resources), actor identity + the
append-only change-log with the `list_changes` pull feed, the flow engine (named
schemes, free-roam, audited force), the push-fed web UI, and the generic migration
tool — all tested, and verified live against a real production board.

**Next (🔜):** the CLI, idempotency keys + retry dedupe, flow hooks/validators, the
MCP-backed `jira` adapter with cross-board copy/link, smart remote caching,
confirmation-gated two-way sync, the work queue (`list_work` + atomic claim/lease so
two agents never grab the same card), human-readable card keys (`PRO-12`), MCP push
notifications, and a richer UI. Roadmap: [SPEC.md](SPEC.md#roadmap); the full queue:
[TODO.md](TODO.md). Anything marked 🔜 does not run today.

## License

All rights reserved (personal project).
