# kanban-pro — methods & calls reference

Every operation exists once in `core/` over the `KanbanBackend` port and is projected onto
three surfaces (SPEC decision 5 + "Consuming kanban-pro"):

- **MCP tools** (primary) — one tool per operation, schema generated from the domain models.
- **CLI** (primary) — `kanban-pro <resource> <verb> [flags]`.
- **HTTP/REST** (secondary) — one route per operation.

This doc lists the canonical operations, then their MCP projection. Status: reflects the
wired port (`kanban_pro/ports`); the MCP projection is **implemented** (`kanban_pro/mcp`,
v0 — idempotency keys and notifications follow core in v1/v2). Ops marked _(planned)_
aren't implemented yet.

Conventions: `Card`/`Board`/… are the [domain models](../SPEC.md#canonical-domain-model).
`*Patch` = partial update (only set fields apply). **†** = requires an idempotency key
(create/add ops, SPEC decision 8). Each op notes the `Capability` that gates it.

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
| list cards | `list_cards(board_id)` | `list[Card]` (excl. archived) | — |
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
- **Archived listing** — `list_cards(board_id, include_archived=False)`; today archived
  cards are reachable only by id, so unarchive/purge targets aren't discoverable.

### Bulk (API/MCP convenience — SPEC "Canonical operations")

`bulk_create` · `bulk_move` · `bulk_update` · `bulk_archive` — accept a list, run a
`core/` loop over the single-item ops, and return **per-item results with partial success**
(each item reports ok/error). The port stays single-item.

---

## MCP projection

kanban-pro runs as an MCP server (`stdio` local / HTTP+SSE remote). A harness discovers
everything at connect time — no per-backend code.

### Tools (one per operation)

Snake-case names matching the operations above:

```
list_boards, get_board, create_board, update_board, delete_board,
list_columns, create_column, update_column, delete_column,
list_cards, get_card, create_card, update_card, move_card,
add_placement, remove_placement, archive_card, unarchive_card, delete_card,
list_comments, add_comment, delete_comment,
list_relations, add_relation, delete_relation,
bulk_create, bulk_move, bulk_update, bulk_archive
```

- **Input schema** for each tool is generated from the domain / `*Patch` model (or the
  path params). Example — `create_card`:

  ```jsonc
  {
    "idempotency_key": "string (required — create/add ops, decision 8)",
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

| Resource | Purpose |
|---|---|
| `capabilities` | Active provider's `Capability` set, each with its `Fulfilment` (`native` / `polyfilled` / `unavailable`) — **how a harness learns what this kanban can do.** |
| `boards` / `board/{id}` / `card/{id}` | Read-through canonical data. |
| `changes?since=<cursor>` | Pull change-feed (decision 9). |

### Notifications (decision 9)

A subscribed MCP client receives push events for card/column/board
`create·update·move·archive·delete`, fed by the core change-log. (kanban-pro webhooks +
the pull change-feed are the non-MCP equivalents.)

### Capability gating in practice

Before offering a tool the active provider can't do, `capabilities` reports its
`Fulfilment`. If `unavailable`, the tool returns `not_supported`; if `polyfilled`,
kanban-pro fulfils it itself (write-through into the backend, or the overlay) — the client
calls it the same way regardless.
