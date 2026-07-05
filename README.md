# kanban-pro

A backend-agnostic **kanban proxy**: one canonical kanban API, swappable backend
adapters. Point it at Hermes today, at another kanban tomorrow — your callers never
change.

## What It Does

kanban-pro exposes a canonical kanban model (boards, columns, cards, labels,
comments) over **MCP, a CLI, and REST**, and routes every operation to a pluggable
**adapter**. Each
adapter translates the canonical model to and from a specific backend (Hermes,
Trello, a local store, …). Switching backend is a config change, not a rewrite —
the proxy is an anti-corruption layer between your tools and whatever kanban is
underneath.

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

e.g. Claude Code: `claude mcp add kanban-pro -s user -- uv run --directory
/path/to/kanban-pro kanban-pro-mcp`. Multiple harnesses can register the same server —
each spawns its own process; they share the SQLite store safely.

**Any OS (mac/Windows/Linux), no clone needed** once the repo has a remote: install
[uv](https://docs.astral.sh/uv/), then `uvx --from git+<repo-url> kanban-pro-mcp`, or
`uv tool install` to put `kanban-pro-mcp` on PATH.

Pick the backend with a **profile** — `--profile hermes` / `--profile jira` /
`--profile default` (or `KANBAN_PRO_PROFILE`). A profile bundles an adapter with its
settings. kanban-pro always exposes the **full canonical surface**: each capability is
**delegated** to the backend, **polyfilled** by kanban-pro itself, or reported
**unavailable** — query `capabilities` to see which. See
[SPEC.md](SPEC.md#key-design-decisions).

## Architecture

Ports & adapters (hexagonal), consumed **MCP-first / shell-first** (agent harnesses are
the primary clients; HTTP is secondary):

```
harnesses / clients
   │   MCP (primary) · CLI (primary) · HTTP (secondary) — thin, stateless
   ▼
core/  — augmenting service: adapter + overlay, dedupe, events
   ▼
KanbanBackend port ──▶ adapter ──▶ backend
          ▲
canonical domain model (Pydantic)
```

Interfaces never talk to an adapter directly — everything goes through `core/`.
Directory layout: see [AGENTS.md](AGENTS.md#architecture-ports--adapters); design:
[SPEC.md](SPEC.md).

## Documentation

- [SPEC.md](SPEC.md) — what and why (canonical model, the core+passthrough decision,
  capability model)
- [JOURNAL.md](JOURNAL.md) — decisions and rationale
- [AGENTS.md](AGENTS.md) — conventions & hard rules for coding agents, incl. how to
  author a new adapter

## Status

**v0 is usable:** domain model, port, two store adapters (`memory`, `native` SQLite),
and the MCP server (`kanban-pro-mcp` — 23 tools + capability/board resources) are built
and tested. Next: augmenting layer + CLI + Hermes adapter (v1). See the roadmap in
[SPEC.md](SPEC.md#roadmap).

## License

All rights reserved (personal project).
