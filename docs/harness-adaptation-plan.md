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

### A — Foundations — ✅ DONE 2026-07-05
1. ✅ **`flow.yaml`** installed (`~/.config/kanban-pro/flows-default.yaml`; committed
   example in `docs/examples/`): `default` scheme = the migrated lifecycle (verified
   live: todo/blocked constrained, ad-hoc `staging` free per rule 4), `docs` scheme,
   `free-roam` built-in. WIP limits: **none initially** (key reserved, one line to
   add — Jan can set values anytime).
2. ✅ **Actor granularity:** (a) shared `agent:hermes` now; per-spawn actors arrive
   with kanban-dispatcher (its SPEC already carries the requirement).
3. ✅ **Workspace metadata namespace pinned** in docs/methods.md ("Card ext
   conventions"): `ext["work"]` = dispatcher's, `kanban_pro.*` reserved, adapters
   use their backend name.

### B — Read-path adoption — ✅ DONE 2026-07-05 (with one correction)
4. ✅ `lane-watch` rewritten as a **change-feed consumer** (cursor file replaces the
   snapshot diff; sees every intermediate move + the acting agent + forced flags;
   resumes after downtime). ✅ `context-watch` comments via `add_comment` (attributed
   as `agent:context-watch`; CLI fallback kept). ✅ `visualize-skill` prefers the
   feed as its side-effect signal. `doc-audit`: no change needed (incidental mention).
   **Correction:** `api-verify`/`browser-verify` are NOT Claude-side — they run
   inside Hermes reviewer workers on the hermes `kanban_comment` tool; updating them
   now would break workers still on the old board → moved to phase C.
5. ✅ **`kanban-lite`:** not actively registered anywhere (not in Hermes
   `mcp_servers`, not in Claude) — nothing to unwire; delete the directory at
   phase E.
6. → moved to phase C (Hermes-side skills change together, after the gateway loads
   the kanban-pro MCP registration).

### C — Write-path adoption — ✅ largely DONE 2026-07-05
0. ✅ **Sample skills shipped in the repo** (`examples/skills/kanban-{worker,
   orchestrator}`) with a GENERATED tool-reference block (drift test:
   `tests/test_toolref.py`; regenerate `uv run python -m tests.toolref --write`).
   Installed via `~/.claude/skills` — which is a symlink to **`~/.agents/skills`**
   (Jan's synced cross-tool assets), so they're portable AND Hermes loads them too
   (`skills.external_dirs` in its config). One source, every harness.
0b. ✅ **All three harness configs carry the MCP + skills** (Jan's ruling): Claude
   Code (`agent:claude-code`, user scope), Hermes (`agent:hermes` in config.yaml +
   external skills dir), OpenCode (`agent:opencode` in opencode.json, backup taken).
7. ✅ **Hermes kanban skills updated as the TWO-LAYER structure** (not a rewrite —
   legacy-dispatched workers still need the old-board content until phase D):
   each got a prepended "the board moved" routing section (legacy worker → old
   tools, everyone else → kanban-pro MCP + the shared skills, with the old→new tool
   mapping, attention-over-blocked, idempotency keys, no-dual-write rule); all
   legacy guidance preserved below the fold, marked legacy-only.
8. **`kanban_tools.py` / KANBAN_GUIDANCE (prompt_builder.py)**: unchanged — they
   serve legacy-dispatched workers and retire WITH the legacy dispatcher (phase E).
   Direct-MCP won over wrappers (Jan's rec confirmed): new-board work uses the MCP
   tools, no wrapper layer.
9. ✅ **Idempotency keys shipped** (optional param v1; required flips on when
   workers all send them). `api-verify`/`browser-verify` skills: still pending —
   they follow their workers (phase D switch).

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
