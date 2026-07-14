# kanban-pro — methods & calls reference

Every operation exists once in `core/` over the `KanbanBackend` port and is projected onto
three surfaces (SPEC decision 5 + "Consuming kanban-pro"):

- **MCP tools** (primary) — one tool per operation, schema generated from the domain
  models. **41 tools + 9 resources today.**
- **CLI** (primary) — `kanban-pro <resource> <verb> [flags]`. _(planned — no `cli/` yet)_
- **HTTP** (secondary) — `kanban_pro/api/` exists but serves the **web UI** (board
  snapshot, SSE, card detail, move/comment/answer/retry), not the full canonical
  surface. One route per operation is _(planned)_.

This doc lists the canonical operations, then their MCP projection. Status: reflects the
wired port (`kanban_pro/ports`) and the implemented MCP projection (`kanban_pro/mcp`).
Ops marked _(planned)_ aren't implemented yet.

Configuring the behaviour these ops enforce — profiles, actors, flow rules, WIP limits,
attention, listeners — is in [configuration.md](configuration.md).

Conventions: `Card`/`Board`/… are the [domain models](../SPEC.md#canonical-domain-model).
`*Patch` = partial update (only set fields apply). **†** = accepts an idempotency key
(create/add ops, SPEC decision 8 — implemented 2026-07-05 as an **optional** param: same
key on retry returns the ORIGINAL result, no duplicate, no second change-log event).
Each op notes the `Capability` that gates it.

---

## Operations

### Boards

| Operation | Signature | Returns | Capability |
|---|---|---|---|
| list boards | `list_boards()` | `list[Board]` | — |
| get board | `get_board(board_id)` | `Board` | — |
| create board **†** | `create_board(board: Board)` | `Board` | — |
| update board | `update_board(board_id, patch: BoardPatch)` | `Board` | — |
| delete board | `delete_board(board_id)` — core-guarded: empty-only (Q14) | `None` | — |

### Columns

| Operation | Signature | Returns | Capability |
|---|---|---|---|
| list columns | `list_columns(board_id)` | `list[Column]` | — |
| create column **†** | `create_column(board_id, column: Column)` | `Column` | — |
| update column | `update_column(column_id, patch: ColumnPatch)` | `Column` | — |
| delete column | `delete_column(column_id)` — core-guarded: empty-only (Q14) | `None` | — |
| reorder column | via `update_column` (`order` field) | `Column` | `REORDER_COLUMNS` |
| set WIP limit | via `update_column` (`wip_limit` field) | `Column` | `WIP_LIMITS` |

### Cards

| Operation | Signature | Returns | Capability |
|---|---|---|---|
| list cards | `list_cards(board_id, include_archived=False)` | `list[Card]` | — |
| get card | `get_card(card_id)` | `Card` | — |
| create card **†** | `create_card(card: Card)` — `placements[]` ≥ 1 | `Card` | — |
| update card | `update_card(card_id, patch: CardPatch)` | `Card` | — |
| move card | `move_card(card_id, to_board_id, to_column_id, position)` — strict within-board (Q16): errors if the card isn't on `to_board_id` | `Card` | `REORDER_CARDS` |
| add placement | `add_placement(card_id, placement)` — one placement per board; SHARES the card | `Card` | `MULTI_BOARD_MEMBERSHIP` |
| copy card | `copy_card(card_id, to_board_id, to_column_id, position, link)` — DETACHED duplicate, nothing flows back | `Card` | — |
| remove placement | `remove_placement(card_id, board_id)` — last placement protected (archive instead) | `Card` | `MULTI_BOARD_MEMBERSHIP` |
| archive card | `archive_card(card_id)` | `Card` | `ARCHIVE` |
| unarchive card | `unarchive_card(card_id)` | `Card` | `ARCHIVE` |
| delete card | `delete_card(card_id)` — guarded to archived (decision 7) | `None` | — |

### Comments

| Operation | Signature | Returns | Capability |
|---|---|---|---|
| list comments | `list_comments(card_id)` | `list[Comment]` | `COMMENTS` |
| add comment **†** | `add_comment(comment: Comment)` | `Comment` | `COMMENTS` |
| delete comment | `delete_comment(comment_id)` | `None` | `COMMENTS` |

### Relations (typed card↔card edges)

| Operation | Signature | Returns | Capability |
|---|---|---|---|
| list relations | `list_relations(card_id)` | `list[Relation]` | `RELATIONS` |
| add relation **†** | `add_relation(relation: Relation)` | `Relation` | `RELATIONS` |
| delete relation | `delete_relation(relation_id)` | `None` | `RELATIONS` |

Subtasks = child cards via `PARENT`/`CHILD` relations (`SUBTASKS`).

### Not yet in the port _(planned expansion)_

- Label-registry ops, assignee attach/detach, checklist item add/toggle, attachment
  add/remove — currently these ride on `Card`/`Board` at **create time only**
  (`CardPatch` doesn't cover them); dedicated ops land in the next port expansion.

  **Checklists are therefore write-once.** `Card.checklists[]` (a "definition of done":
  `{title, items: [{text, done, order}]}`) is accepted at `create_card`, is persisted, and
  round-trips on read — but **no API can tick an item afterwards**, because `CardPatch`
  has no `checklists` field and no `checklist_*` tool exists. Treat them as a static
  acceptance-criteria list until the port expands.

  For a **live, updatable to-do list on a card, use the work report** instead:
  `plan[]` items carry `status: todo|doing|done|blocked`, and `checks[]` carry
  reviewer/verification gates — both upserted by item id through `record_work_report`,
  and each write emits a `work_report.updated` event. That is the mechanism agents should
  use to track their own steps.
- **User lookup** — `list_users()` / `get_user(user_id)`. Without it a caller can't
  discover valid ids for `assignees[]` / `Comment.author`.

### Work distribution (core convenience — not port ops)

| Operation | Signature | Notes |
|---|---|---|
| work queue | `list_work(assignee?, include_unassigned=True)` | default assignee = the connection's actor; workable = backlog/unstarted/started, cards leased to others excluded, own leases marked; **each item carries its legal transitions inline** |
| claim | `claim_card(card_id, ttl_seconds=3600, owner?)` | atomic CAS lease (competing consumers); expired leases are silently reclaimable; recorded as `card.claimed` |
| heartbeat | `heartbeat_claim(card_id, ttl_seconds=3600, owner?)` | renew own live lease (not recorded) |
| release | `release_claim(card_id, owner?)` | idempotent; recorded as `card.released` |
| raise attention | `raise_attention(card_id, reason, for_actor?, severity="block")` | sets `ext["kanban_pro.attention"]` + `attention.raised` event (routable: notifiers read reason/target/severity from the feed). `severity` ∈ `block` (halts the card) / `warn` / `info` (visible, card keeps flowing); a `block` is refused on a card resting in a board `auto_clear_attention_columns` lane — it would be stranded |
| clear attention | `clear_attention(card_id, resolution?)` | removes the flag + `attention.cleared` event |

`owner` defaults to the connection's actor — pass it only when one process holds leases
on behalf of another identity.

Claiming does NOT move or assign — the convention "claim → assign yourself → move to
a started column" stays visible in the change-log.

### Flow (core convenience — not port ops)

| Operation | Signature | Notes |
|---|---|---|
| legal moves | `list_transitions(card_id, board_id?)` | the card's legal target columns *right now* + the resolved flow and where it came from (inline `ext["kanban_pro.flow"]` → `board.flow` by column id → backend workflow → free) |
| list flows | `list_flows()` | each board's flow — `{boards: {board_id: {transitions}\|null}, free_roam_scheme, scheme_ext_key, presets}`; a board with no flow is free-roam |
| set board flow | `set_flow(board_id, transitions)` | replace the whole board flow (`{from_col_id: [to_col_id, …]}`); every referenced column id must exist on the board — a dangling ref is refused. `{}` clears it |
| set one lane | `set_transitions(board_id, from_column_id, to_column_ids)` | set just one lane's out-edges, leaving the rest; `[]` clears that lane |
| clear board flow | `clear_flow(board_id)` | drop the flow → free-roam board |
| onboard a board | `init_board(board_id, name?, preset="agent-lifecycle", id_scheme?, anonymous_writes="refuse")` | create a NEW board from a preset (`blank`, `simple-kanban`, `docs`, `agent-lifecycle`) — columns + a matching flow, built together so they can't dangle. `id_scheme` fixes the shape of this board's card ids; `anonymous_writes` is the board's identity policy (below). Import onboarding is the `kanban-pro-migrate` CLI, not this tool |

Flow is **board data** (`board.flow`, keyed by the board's own column ids), administered
over MCP via `set_flow`/`set_transitions`/`clear_flow` — not a YAML file. A flow edit
emits a `board.updated` event. `move_card` enforces the resolved flow; `force=true`
overrides it and stamps `forced: true` on the `card.moved` event — never silent.

### Work reports (core convenience — not port ops)

Current structured card state, held in `ext["work_report"]`. The changelog is the audit
trail; the report itself is **current truth, not an append-only log**.

| Operation | Signature | Notes |
|---|---|---|
| record | `record_work_report(card_id, section, item, op="upsert", idempotency_key?)` | one section/item per call; emits a `work_report.updated` event |
| answer a question | `answer_work_report_question(card_id, question_id, answer)` | resolves the question **and** mirrors the answer as a normal comment |

Sections — **list** (upserted by stable item `id`, `op` ∈ `upsert`/`replace`/`remove`):
`questions`, `findings`, `plan`, `needs`, `analysis_log`, `checks`.
**Singleton** (replaced as current state): `about`, `verdict`, `handoff`.

**Format version (`_v`).** Each report carries `_v` — currently `1`. See
[ext versioning](#ext-versioning) below. A section may not be named with a leading
underscore; underscore keys are reserved metadata, never content.

Rules: never rewrite the whole `ext.work_report` blob by hand; `raise_attention` is only
the *signal* — the actual question belongs in `questions[]`. Machine-readable schema:
the `kanban://work-report-schema` resource.

### Change feed

| Operation | Signature | Notes |
|---|---|---|
| pull | `list_changes(since=0, limit=100)` | every recorded write after cursor `since` |
| long-poll | `wait_changes(since=-1, timeout_seconds=25, limit=100)` | returns as soon as events exist after `since` (instant for writes through this server, ~2s for other processes), else empty at timeout. `since=-1` probes the current cursor without replaying history — call once, then loop with the returned cursor |

### Bulk _(planned — not implemented)_

`bulk_create` · `bulk_move` · `bulk_update` · `bulk_archive` — will accept a list, run a
`core/` loop over the single-item ops, and return **per-item results with partial success**
(each item reports ok/error). The port stays single-item.

---

## Cards across boards — share, never copy

A card that must be worked on a second board is **placed** there (`add_placement`), not
copied. There is no `copy_card`/`duplicate_card`, deliberately — see
[SPEC decision 4](../SPEC.md#4-card-placement-is-a-set-not-a-single-column) for the full
argument. What you get and what you give up:

| | shared placement |
|---|---|
| lanes | **independent** — `in-progress` on board A and `in-review` on board B at once, each validated against that board's own flow |
| title, description, checklists, labels, comments, relations | **shared** — one record, so nothing can drift and nothing needs syncing |
| work report, attention flag, claim | **shared, globally one per card** — the origin board sees the receiver's plan/findings/verdict live; but two boards can therefore never hold two *states* |
| id | minted once, from the card's **first** board — a `KAN-7` placed on a `seq:OPS` board still reads `KAN-7` |

Consequences worth knowing before you reach for it:

- **Don't place a card someone else is actively working.** You share their lease, their
  attention flag and their work report — one claim per card id, globally. Sharing suits
  handing work *over*, not two actors working it in parallel.
- **"Done, your turn" is just `raise_attention(card_id, reason, for_actor=…)`** on the same
  card. No propagation channel exists or is needed; they are looking at the record you wrote.
- **Multi-placed cards are routine, so `placements[0]` is always a bug.** Select the
  placement by the board being acted on. `list_transitions` requires an explicit `board_id`
  once a card sits on more than one board — the legal moves differ per board.
- `remove_placement` takes the card off one board and leaves the others intact; the last
  placement cannot be removed (`archive_card` instead).

## Card `ext` conventions (reserved namespaces)

`ext` is free-form, but these keys have pinned meanings (writers use the shallow-merge
patch semantics, Q17):

| Key | Owner | Meaning |
|---|---|---|
| `kanban_pro.scheme` | flow engine | only `"free-roam"` is meaningful now (frees the card); named schemes are gone — the board's flow (`board.flow`) governs otherwise |
| `kanban_pro.flow` | flow engine | inline ONE-card flow `{states, transitions}` (name-based) — precedence over the board flow, enforced even on a flowless board; malformed → falls back to the board flow + warning |
| `kanban_pro.attention` | attention signal | `{reason, raised_by, for, severity}` — needs a decision/input. `severity` ∈ `block` (default; halts the card) / `warn` / `info`; absent = `block` (pre-severity flags). Advisory: kanban-pro exposes it (`core.recording.attention_blocks`), consumers act on it |
| `kanban_pro.origin` | whoever creates the card | **where this card came from**, in any external system: `{id, url}` — e.g. `{"id": "PROJ-123", "url": "https://…/browse/PROJ-123"}`. Deliberately system-agnostic (Jira, GitHub, Linear, a doc) and self-contained: the card carries its own url, so kanban-pro needs no per-tracker config and knows nothing about any tracker. The UI renders it as a link (http/https only). **Provenance, not state** — kanban-pro never fetches the origin's status; see below |
| `kanban_pro.copied_from` | cross-mount copy (queued) | provenance link `"<mount>/<card-id>"` |
| `kanban_pro.migrated_from` | `kanban-pro-migrate` | import provenance `"<profile>/<board-id>"` |
| `work_report` | work-report ops | current structured card state (sections above) — write via `record_work_report`, never by hand |
| `session` | worker/harness | `{actor, log, kind}` — pointer to the agent's session transcript (`*.jsonl`/`*.log` under `$HOME`/tmp), tailed by the UI's session-log viewer |
| `work` | kanban-dispatcher (agreed 2026-07-05) | executor metadata: `{workspace_kind, branch, skills[], max_runtime}`; its `log` is the fallback source for the session-log viewer |
| `hermes` | hermes adapter | the backend's harness-specific fields, verbatim |

Rule: `kanban_pro.*` is reserved for kanban-pro's own features; adapters use their
backend's name as the namespace; the dispatcher owns `work`.

### `kanban_pro.origin` — link out, never fetch

The card records where it came from; it does **not** mirror what has happened there since.
kanban-pro links to the origin and never reads it, on purpose:

- **Reading it would make kanban-pro a client of a system it does not own** — transport,
  auth, pagination, rate limits, caching, error mapping. That is an adapter, and it is a far
  bigger commitment than a link (see the multi-mount / ext-persistence problems in `TODO.md`).
- **A cached status is a stale lie that reads as authoritative.** A badge saying
  `In Progress` keeps saying it after the ticket moves, and a wrong status shown confidently
  is worse than no status: the origin's own UI is one click away and is always right.

Whoever creates the card from an external item (an agent, the dispatcher) already talks to
that system, so it stamps `{id, url}` at creation. kanban-pro only ever displays a value
someone else wrote. If you ever *do* want the origin's state inline, it must be a snapshot
with its age visible (`checked_at`, rendered as "In Progress · 2h ago") and refreshed from
outside the core — never a silent read behind a badge.

## Board `ext` conventions

Two board-level policies live in `board.ext` (set at `init_board`, changed with
`update_board(ext=…)`) — see [configuration.md §3](configuration.md#3-board-settings-boardext):

| Key | Values | Meaning |
|---|---|---|
| `anonymous_writes` | `"refuse"` (default) / `"allow"` | may a connection with no actor write to this board? `refuse` → every write from an unidentified connection is a `conflict`; reads always work |
| `auto_clear_attention_columns` | `[column_id, …]` (default none) | the resting lanes: arriving there clears the card's attention flag. A `block` flag cannot be raised on a card already resting in one (it would be stranded). Never list a lane where cards WAIT on a human (e.g. `scheduled`) — the move would wipe the flag asking for the decision |

**The resting lanes are NOT `category: done`, and the two must not be merged.** They answer
different questions:

| | governs | asks |
|---|---|---|
| `Column.category` | the **queue** (`list_work`) | *may a worker be handed this card?* — backlog/unstarted/started yes, done/canceled/triage no |
| `auto_clear_attention_columns` | **attention** | *is anyone waiting on anybody here?* — if not, arriving clears the flag, and a `block` raised here is refused |

A `done` column happens to answer yes to both, which is what makes them look like one
concept called "done". They aren't: a `ready` lane **rests without being done** (nobody is
waiting, but the work isn't finished), and a `scheduled` lane is the reverse — it looks
restful and is not, because a card sits there precisely until a human decides something.
Deriving one from the other would cost you exactly those two cases.

Liveness is **derived**, never stored: a card reads as "running" because a live claim
(`ext._claim`) exists, so a crashed lease correctly reads as done once it expires. The
log pointer lives on `ext.session` (it must outlive the claim); liveness comes from the
claim.

## ext versioning

`ext` is a **bag with independent writers** — kanban-pro owns `work_report` and
`kanban_pro.*`, each adapter owns its own namespace (`hermes`), the dispatcher owns
`work`. No single version number can describe it: bumping one would imply a change to
namespaces its owner never touched.

So **each structured namespace carries its own `_v`, inside itself**, and the version
travels with the data when a namespace is copied to another card, exported, or migrated
between backends.

| Rule | |
|---|---|
| `_v` | int, the namespace's format version. Underscore-prefixed keys are reserved metadata (cf. the injected `_claim`, `_last_comment`), never content. |
| Missing `_v` | means **version 1** — the shape that existed before versioning. No data migration was needed; readers migrate on read, writers stamp on write. |
| A newer `_v` than the code understands | **refused for writing** (`conflict`), never silently rewritten: old code must not clobber a format it cannot represent. Reads pass it through untouched. |
| Bumping a version | requires a migration step in that namespace's `_migrate_*` chain, in the same change. |

Implemented today for `work_report` (`WORK_REPORT_VERSION`, `kanban_pro/core/work_report.py`).
Adapter- and dispatcher-owned namespaces version themselves under the same convention;
unnamespaced scalar keys (`branch`, `ticket`, …) can't carry a version and shouldn't be
relied on as a format.

## MCP projection

kanban-pro runs as an MCP server (`stdio` local / HTTP+SSE remote). A harness discovers
everything at connect time — no per-backend code.

### Tools (one per operation)

Snake-case names matching the operations above — **42 tools**, the live surface:

```
list_boards, get_board, create_board, update_board, delete_board,
list_columns, create_column, update_column, delete_column,
list_cards, get_card, create_card, update_card,
record_work_report, answer_work_report_question,
move_card, list_transitions, list_flows,
set_flow, set_transitions, clear_flow, init_board,
add_placement, copy_card, remove_placement, archive_card, unarchive_card, delete_card,
list_comments, add_comment, delete_comment,
list_relations, add_relation, delete_relation,
list_work, claim_card, heartbeat_claim, release_claim,
raise_attention, clear_attention,
list_changes, wait_changes
```

Each event on the feed carries `seq` (next cursor), `ts`, `actor` (decision 10),
`entity.op` (e.g. `card.moved`), and a slim payload.

The generated tool reference embedded in `examples/skills/*/SKILL.md` is the machine
source of truth — regenerate it with `uv run python -m tests.toolref --write` after any
change here, or `tests/test_toolref.py` fails.

- **Input schema** for each tool is generated from the domain / `*Patch` model (or the
  path params). Example — `create_card`:

  ```jsonc
  {
    "idempotency_key": "string (optional — create/add ops, decision 8)",
    "card": {                       // the Card model
      "title": "string (required)",
      "description": "string|null",
      "placements": [{"board_id": "…", "column_id": "…", "position": 0}],  // >=1
      "labels": ["labelId"], "assignees": ["userId"],
      "start_date": "iso8601|null", "due_date": "iso8601|null",
      "checklists": [{"title": "…", "items": [{"text": "…", "done": false}]}],
      "attachments": [{"url": "https://…", "title": "…"}],
      "ext": {}                     // backend-specific passthrough
    }
  }
  ```

- **Result** is the canonical entity (JSON of the domain model). Enums serialize to their
  string value (`ColumnCategory` → `"done"`, `RelationKind` → `"blocks"`).
- **Errors** map to the canonical taxonomy: `not_found`, `conflict`, `unauthorized`,
  `not_supported`, `backend_unavailable`.

### Resources

All under the `kanban://` scheme — **9 today**:

| Resource | Purpose |
|---|---|
| `kanban://capabilities` | Active provider's `Capability` set, each with its `Fulfilment` (`native` / `polyfilled` / `unavailable`) — **how a harness learns what this kanban can do.** |
| `kanban://boards` / `board/{board_id}` / `card/{card_id}` | Read-through canonical data. |
| `kanban://domain` | The canonical domain model. |
| `kanban://workflow` | Per-board flow — the legal column→column moves and how a card's flow resolves. |
| `kanban://work-distribution` | How work is claimed and routed. |
| `kanban://work-report-schema` | Sections + write rules for `record_work_report`. |
| `kanban://event-schema` | Change-log event shape. |

The change feed is a **tool** (`list_changes` / `wait_changes`), not a resource.

### Notifications (decision 9) _(planned)_

A subscribed MCP client will receive push events for card/column/board
`create·update·move·archive·delete`, fed by the core change-log. Not implemented — today
the push story is `wait_changes` (long-poll) for harnesses and SSE for the web UI;
kanban-pro webhooks are the future non-MCP equivalent.

### Capability gating in practice

Before offering a tool the active provider can't do, `capabilities` reports its
`Fulfilment`. If `unavailable`, the tool returns `not_supported`; if `polyfilled`,
kanban-pro fulfils it itself (write-through into the backend, or the overlay) — the client
calls it the same way regardless.
