# kanban-pro — methods & calls reference

Every operation exists once in `core/` over the `KanbanBackend` port and is projected onto
three surfaces (SPEC decision 5 + "Consuming kanban-pro"):

- **MCP tools** (primary) — one tool per operation, schema generated from the domain
  models. **37 tools + 9 resources today.**
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
| add placement | `add_placement(card_id, placement)` — one placement per board | `Card` | `MULTI_BOARD_MEMBERSHIP` |
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
- **User lookup** — `list_users()` / `get_user(user_id)`. Without it a caller can't
  discover valid ids for `assignees[]` / `Comment.author`.

### Work distribution (core convenience — not port ops)

| Operation | Signature | Notes |
|---|---|---|
| work queue | `list_work(assignee?, include_unassigned=True)` | default assignee = the connection's actor; workable = backlog/unstarted/started, cards leased to others excluded, own leases marked; **each item carries its legal transitions inline** |
| claim | `claim_card(card_id, ttl_seconds=3600, owner?)` | atomic CAS lease (competing consumers); expired leases are silently reclaimable; recorded as `card.claimed` |
| heartbeat | `heartbeat_claim(card_id, ttl_seconds=3600, owner?)` | renew own live lease (not recorded) |
| release | `release_claim(card_id, owner?)` | idempotent; recorded as `card.released` |
| raise attention | `raise_attention(card_id, reason, for_actor?)` | sets `ext["kanban_pro.attention"]` + `attention.raised` event (routable: notifiers read reason/target from the feed) |
| clear attention | `clear_attention(card_id, resolution?)` | removes the flag + `attention.cleared` event |

`owner` defaults to the connection's actor — pass it only when one process holds leases
on behalf of another identity.

Claiming does NOT move or assign — the convention "claim → assign yourself → move to
a started column" stays visible in the change-log.

### Flow (core convenience — not port ops)

| Operation | Signature | Notes |
|---|---|---|
| legal moves | `list_transitions(card_id, board_id?)` | the card's legal target columns *right now* + the resolved scheme and where it came from (inline `ext["kanban_pro.flow"]` → named scheme → backend workflow → free) |
| list schemes | `list_flows()` | every `flow.yaml` scheme + built-in `free-roam`, with states, transitions, and which is default |

`move_card` enforces the resolved scheme; `force=true` overrides it and stamps
`forced: true` on the `card.moved` event — never silent.

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

## Card `ext` conventions (reserved namespaces)

`ext` is free-form, but these keys have pinned meanings (writers use the shallow-merge
patch semantics, Q17):

| Key | Owner | Meaning |
|---|---|---|
| `kanban_pro.scheme` | flow engine | the card's workflow scheme name (`"docs"`, `"free-roam"`; unset = default) |
| `kanban_pro.flow` | flow engine | inline ONE-card flow `{states, transitions}` — precedence over `scheme`, enforced even without flow.yaml; malformed → default scheme + warning |
| `kanban_pro.attention` | attention signal (queued) | `{reason, raised_by, for}` — needs a decision/input |
| `kanban_pro.copied_from` | cross-mount copy (queued) | provenance link `"<mount>/<card-id>"` |
| `kanban_pro.migrated_from` | `kanban-pro-migrate` | import provenance `"<profile>/<board-id>"` |
| `work_report` | work-report ops | current structured card state (sections above) — write via `record_work_report`, never by hand |
| `session` | worker/harness | `{actor, log, kind}` — pointer to the agent's session transcript (`*.jsonl`/`*.log` under `$HOME`/tmp), tailed by the UI's session-log viewer |
| `work` | kanban-dispatcher (agreed 2026-07-05) | executor metadata: `{workspace_kind, branch, skills[], max_runtime}`; its `log` is the fallback source for the session-log viewer |
| `hermes` | hermes adapter | the backend's harness-specific fields, verbatim |

Rule: `kanban_pro.*` is reserved for kanban-pro's own features; adapters use their
backend's name as the namespace; the dispatcher owns `work`.

Liveness is **derived**, never stored: a card reads as "running" because a live claim
(`ext._claim`) exists, so a crashed lease correctly reads as done once it expires. The
log pointer lives on `ext.session` (it must outlive the claim); liveness comes from the
claim.

## MCP projection

kanban-pro runs as an MCP server (`stdio` local / HTTP+SSE remote). A harness discovers
everything at connect time — no per-backend code.

### Tools (one per operation)

Snake-case names matching the operations above — **37 tools**, the live surface:

```
list_boards, get_board, create_board, update_board, delete_board,
list_columns, create_column, update_column, delete_column,
list_cards, get_card, create_card, update_card,
record_work_report, answer_work_report_question,
move_card, list_transitions, list_flows,
add_placement, remove_placement, archive_card, unarchive_card, delete_card,
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
| `kanban://workflow` | Flow schemes, states, transitions. |
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
