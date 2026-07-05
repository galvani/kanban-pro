# Hermes kanban — ground truth & canonical mapping

Discovery 2026-07-05 (read-only sweep of `~/.hermes`). Grounds the `hermes` adapter and
the migration (SPEC goal update: kanban-pro replaces the Hermes kanban).

## Where it lives

- Engine: `~/.hermes/hermes-agent/hermes_cli/kanban_db.py` (~8.7k LOC — schema,
  dataclasses, transitions, dispatcher CAS). CLI: `hermes_cli/kanban.py`
  (`hermes kanban <cmd>`; no standalone binary). Agent-facing LLM tools:
  `tools/kanban_tools.py`. HTTP: `plugins/kanban/dashboard/plugin_api.py`
  (FastAPI, base `/api/plugins/kanban`, engineer dashboard :8789, default :9119).
  Read-only MCP wrapper: `~/.hermes/mcp-servers/kanban-lite/`.
- Storage: **SQLite per board** — default board at `~/.hermes/kanban.db` (legacy path),
  others at `~/.hermes/kanban/boards/<slug>/kanban.db`. Resolution: `--board` >
  `HERMES_KANBAN_BOARD` > `HERMES_KANBAN_DB` > `active_board` file > `default`.
- Auth: CLI/SQLite — none (file perms). HTTP — dashboard session bearer token
  (single-user). Dispatcher runs as `hermes-kanban-dispatcher.service` (CAS-claims
  `ready` tasks, spawns profile workers, heartbeats, reclaims).

## Data model → canonical mapping

Lanes ARE statuses (no separate columns): `triage todo scheduled ready running blocked
review done archived` (+ observed ad-hoc `staging` → the lane vocab is per-board
extensible, not a closed enum). Ids: tasks `t_` + 8 hex; everything else autoincrement
ints. Timestamps: unix epoch seconds (int).

| Hermes | Canonical | Notes |
|---|---|---|
| board (SQLite file) | `Board` | adapter enumerates default + `boards/<slug>/` |
| status lane | `Column` (synthesized) | fixed set per board + observed extras |
| `tasks` row | `Card` | `body`→description; single `assignee`→`assignees[0]` |
| status `archived` | `Card.archived=true` | a lane there, a flag here — NOT a column |
| `task_comments` | `Comment` | `author` = profile name |
| `task_links` (parent/child DAG) | `Relation` PARENT/CHILD | = our subtasks model |
| assignee profiles | `User` (`ext.kind="agent"`) | builder, engineer, reviewer, … |
| `task_events` | (v2 change-log equivalent) | append-only audit — validates decision 9 |
| `task_runs`, claims, heartbeats | `ext` | work-execution layer, NOT canonical data |
| `priority` | `ext` (core candidate) | Jira+Hermes both have it → qualifies for core |
| `created_by` | (actor identity) | validates the actor-on-ops design |
| `idempotency_key` | decision 8 | first surveyed backend with NATIVE idempotency! |
| `task_attachments` (real files) | deferred | ours is link-only v1; files reachable via dashboard `GET /attachments/{id}` |
| workspace/branch/skills/goal_mode | `ext` | harness concerns |

Lane → `ColumnCategory`: triage→TRIAGE · todo,scheduled→BACKLOG (waiting on
parents/time) · ready→UNSTARTED (actionable) · running,blocked,review→STARTED ·
done→DONE · archived→(flag) · unknown lanes→UNSTARTED.

No labels, no due dates, no position within a lane (ordering = priority DESC +
created_at) — so no REORDER_CARDS.

## HermesAdapter capability declaration (honest)

Native: `COMMENTS`, `ASSIGNEES`, `RELATIONS` (+`SUBTASKS`), `ARCHIVE`, `CUSTOM_FIELDS`,
`WORKFLOW` (the engine really enforces lifecycle transitions — e.g. complete only from
running/ready). Not native (polyfill/unavailable): `MULTI_ASSIGNEE` (single
`assignee`), `LABELS`, `CHECKLISTS`, `REORDER_*`, `WIP_LIMITS`, `MULTI_BOARD_MEMBERSHIP`,
`ATTACHMENTS` (file-based, revisit), `WEBHOOKS` (notify-subs exist; not our push shape).

## Adapter access path (proposal)

**Reads: direct SQLite** (local, fast, no token, richest). **Writes: `hermes kanban`
CLI with `--json`** — raw SQL writes would bypass the engine's invariants
(task_events emission, `recompute_ready` parent promotion, CAS claims); the CLI is the
public contract that preserves them. HTTP+token only if the CLI proves insufficient.

## Migration & replacement notes

- Live default board today: 108 archived / 32 done / 15 todo / 15 blocked / 2 staging;
  608 comments. Import = adapter reads → native store writes, via the canonical model.
- The Hermes **dispatcher** stays Hermes's (work execution ≠ kanban): post-cutover it
  consumes kanban-pro (work-queue query + a claim op) instead of its own DB.
- **Claim/lease is a real requirement** (proven here: `claim_lock` CAS + TTL +
  heartbeat + reclaim): multi-agent dispatch needs atomic claiming. → agent-native TODO.
- Skills warn the CLI is unavailable inside containerized workers — kanban-pro's MCP
  solves exactly this (workers speak MCP, no CLI install).
