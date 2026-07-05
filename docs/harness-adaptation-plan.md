# Harness adaptation plan — teaching the agents the new board

Cutover phase 1 made kanban-pro the system of record. This plan covers what happens
NEXT: every part of the harness ecosystem that grew around the old kanban — skills,
worker loops, watchers, the dispatcher, dashboards — adapted to *exploit* the new
capabilities, not just tolerate the switch. **Status: DRAFT for Jan's review.**
Decision points are marked ⚖️.

## Inventory — what touches the old kanban today

| Touchpoint | Where | Old dependency |
|---|---|---|
| Worker/orchestrator skills | `~/.hermes/skills/devops/kanban-{worker,orchestrator}` | `kanban_*` LLM tools + `hermes kanban` CLI |
| In-agent LLM tools | `~/.hermes/hermes-agent/tools/kanban_tools.py` | direct engine calls |
| Dispatcher | `hermes-kanban-dispatcher.service` + `gateway/kanban_watchers.py` | own DB, CAS claims, worker spawn |
| Watchers / notifications | `kanban_notify_subs`, `board-notifications.db`, gateway loops | polls own DB |
| Claude-side skills | `lane-watch`, `context-watch`, `api-verify`, `browser-verify`, `visualize-skill`, `doc-audit` | shell out to `hermes kanban` |
| Dashboards | engineer dashboard kanban plugin (+ old `kanban-board` plugin) | own REST/WS |
| Read-only MCP | `~/.hermes/mcp-servers/kanban-lite` | wraps dashboard REST |

## The behavioral upgrade (why this is worth it)

The old contract: workers got spawned WITH a task, reported via `kanban_complete` /
`kanban_block`, and knew nothing else. The new contract lets an agent be a *pull*
worker with full context:

```
old: (spawned with task id) → work → kanban_complete/kanban_block → die
new: list_work                        # my cards + legal moves inline, one call
     → claim_card (TTL lease)         # atomic; no double-pickup
     → assign self + move to started  # visible, attributed intent
     → work … heartbeat_claim
     → comment / add_relation(subcards) / move per list_transitions
     → move to done|blocked + release_claim
```

Plus capabilities the old board never had: flow schemes per card (docs tasks skip
review), audited `force`, `list_changes` cursors instead of board polling, WIP
enforcement, subcard decomposition, one board shared with Claude Code under distinct
actors.

## Tool mapping (old → new)

| Old (`kanban_*` tool / CLI) | New (kanban-pro MCP) | Notes |
|---|---|---|
| `kanban_list` / `list --mine` | `list_work` | transitions inline; leased-to-others excluded |
| `kanban_show` | `get_card` + `list_comments` + `list_relations` | |
| `kanban_create --parent` | `create_card` (+`add_relation` PARENT) | ⚖️ atomic `parent_id` on create is queued |
| `kanban_comment` | `add_comment` | author + actor both kept |
| `kanban_complete --summary` | `add_comment(summary)` + `move_card → done` | summary = comment, not a field |
| `kanban_block --kind` | `move_card → blocked` + `add_comment(kind: reason)` | ⚖️ attention flag will carry `needs_input` |
| `kanban_unblock` | `move_card → ready` | |
| `kanban_link/unlink` | `add_relation` / `delete_relation` | |
| `kanban_heartbeat` | `heartbeat_claim` | |
| (dispatcher CAS claim) | `claim_card` / `release_claim` | now available to ANY agent |
| (none) | `list_transitions`, `list_flows`, `list_changes` | new capabilities |

## Phases

### A — Foundations (config only; do first) — owner: me, ~1 session
1. **`flow.yaml` for the default profile** encoding the migrated lifecycle as the
   enforced `default` scheme (triage→todo→scheduled→ready→running→blocked/review→done,
   matching the migrated columns), a `docs` scheme, `free-roam` available.
   ⚖️ **Decision: WIP limits on `running`/`review`?** (old board had none; we can now.)
2. **Actor granularity.** Today ALL Hermes traffic is `agent:hermes` (one MCP
   registration). Options: (a) keep shared actor until the dispatcher lands —
   simplest; (b) per-profile env `KANBAN_PRO_ACTOR` injected by whatever spawns the
   worker (the dispatcher does this naturally later). ⚖️ **Decision: (a) now, (b)
   with kanban-dispatcher — my recommendation.**
3. **Workspace metadata convention** for new cards: `ext["work"] = {workspace_kind,
   branch, skills[], max_runtime}` (the fields the executor needs; migrated cards
   already carry them under `ext["hermes"]`). Pin the namespace with the
   kanban-dispatcher SPEC so both projects agree.

### B — Read-path adoption (safe, no behavior risk) — owner: me, ~1 session
4. **Claude-side skills** (`lane-watch`, `context-watch`, `api-verify`,
   `browser-verify`, `visualize-skill`, `doc-audit`): replace `hermes kanban` shell
   calls with kanban-pro MCP reads. Big win: `lane-watch` stops snapshot-diffing —
   it becomes a `list_changes` cursor consumer (exactly what the feed is for).
5. **Retire `kanban-lite`** (the read-only MCP) — fully superseded.
6. Hermes **orchestrator skill**: board overview via `list_cards`/`list_changes`.

### C — Write-path adoption (the agent behavior upgrade) — owner: me + review, ~1-2 sessions
7. **Rewrite `kanban-worker` skill** around the pull-worker loop above; document the
   claim→assign→move convention, `list_transitions` before moving, force discipline
   ("never force unless the card says why"), subcard decomposition.
8. **`kanban_tools.py` v2 in Hermes**: thin wrappers over kanban-pro MCP (or drop —
   workers can use MCP directly; ⚖️ **decision: wrap vs direct** — direct is less
   code, wrap keeps old prompts working during transition. Recommendation: direct
   for new skill, keep old tools functioning against old board until E).
9. **Gap to close first (kanban-pro side): idempotency keys** — old `create` had
   native dedup; worker retries need it. Small build (core TTL cache, decision 8).

### D — Execution switch — owner: kanban-dispatcher project
10. Build `~/workspace/kanban-dispatcher` v0 (claude launcher first, per its SPEC);
    run **side-by-side**: Hermes dispatcher keeps owning cards on the OLD board;
    kanban-dispatcher owns kanban-pro cards. No big-bang switch.
11. Route Hermes profiles through the routing table; per-spawn `--actor` lands here.

### E — Retirement — owner: Jan + me, after D proves stable
12. Final `kanban-pro-migrate` run; stop `hermes-kanban-dispatcher.service`;
    remove `kanban` from Hermes `platform_toolsets.cli`; old board becomes read-only
    archive. Retire the dashboard kanban plugin (kanban-pro UI replaces; the richer
    plugin port stays optional).
13. Optional follow-up: import `task_events` history into the change-log.

## What the new board does NOT replace (kept in Hermes deliberately)
- **Run history / worker logs** (`task_runs`, `task_log`): execution telemetry, not
  kanban data (our ruled boundary). Cards carry references (session id / log path in
  `ext`); the dispatcher owns run bookkeeping.
- **Worker spawning itself** — that's kanban-dispatcher, not kanban-pro.

## Risks & rollback
- **Dual-write drift (phase B–D window):** policy = new work on kanban-pro; the old
  board absorbed via idempotent re-migrate. Cards must live on ONE board at a time.
- **Old workers on new cards:** until D, Hermes-dispatched workers only see the old
  board — new kanban-pro cards are NOT executed automatically. Interim: manual
  dispatch or Claude Code sessions work them.
- **Rollback:** config backup exists; the old board is untouched; removing the MCP
  registrations restores the pre-cutover world in minutes.

## ⚖️ Decisions for Jan (summary)
1. WIP limits in the default flow.yaml — values, or none initially?
2. Actor granularity now vs with dispatcher (rec: shared now, per-spawn later).
3. `kanban_tools.py`: wrap kanban-pro vs let workers use MCP directly (rec: direct).
4. Phase order OK? (A+B safe immediately; C after idempotency keys; D its own repo.)
