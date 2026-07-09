---
name: kanban-orchestrator
description: Manage the shared kanban-pro board (MCP server "kanban-pro") as an orchestrator — turn goals into cards (subcards via parent/child relations, per-card workflow schemes), assign and prioritize, watch progress via the change-feed cursor, unblock. Never works cards itself. Use when asked to plan work onto the board, decompose a project into cards, report board status, or route/unblock work.
user-invocable: true
---

# Kanban orchestrator — goals in, cards out

You manage the board; workers (agents or humans) execute it. Your writes are attributed
and recorded like everyone's. **You never do the cards' work yourself** — if you catch
yourself implementing, stop and create a card instead.

## Decomposing a goal

1. Read the board first: `list_boards`, `list_cards(board_id)` — don't create
   duplicates of cards that already exist (check titles).
2. One **parent card** for the goal; **subcards** for independently workable pieces:
   `create_card` each, then `add_relation {kind: "parent", from_card: <parent>,
   to_card: <child>}`. A piece someone will move/own/comment = a subcard; a mere
   tick-box = a checklist item on the parent.
3. **Pick the scheme per card** (`ext["kanban_pro.scheme"]` at create): code work =
   default (review-gated), documentation = `docs`, genuinely rule-free experiments =
   `free-roam`. Check available schemes with `list_flows()`.
4. Place cards in a backlog/ready column (`list_columns` shows categories); set
   assignees only when you know who should pull it — unassigned cards appear in every
   worker's `list_work`.

## Watching and steering

- **Consume the change-feed, don't poll the board:** keep the last `seq` you saw and
  call `list_changes(since=<cursor>)` — you get exactly what happened (who moved what,
  forced moves flagged, claims/releases). This is also how you report progress.
- A card sitting claimed with no movement → its worker may have died; the lease
  expires on its own, no action needed. A card in a blocked column → read its last
  comment (`list_comments`) and either resolve the blocker, reassign
  (`update_card`), or move it back to ready.
- Refused moves are information: `list_transitions(card_id)` shows what the card's
  scheme allows. Use `force=true` only for deliberate exceptions, always with a
  comment saying why.
- WIP-limit conflicts on a column mean the team is overloaded there — rebalance
  (move something out) instead of forcing in.

## Discipline

- Prefer many small, claimable cards over one mega-card — the claim/lease mechanics
  only help when work is split.
- Never delete to "clean up": `archive_card` is removal; purge only archived cards.
- The board is shared with humans — titles and comments are read by people in a web
  UI. Write them for people.

## Tool reference

<!-- generated:tool-reference — regenerate: uv run python -m tests.toolref --write -->

- `add_comment(comment, idempotency_key?)` — Add a comment to a card (`card_id`, `author` = User id, `body`).
- `add_placement(card_id, placement)` — Put a card on an additional board (one placement per board; errors if already on it).
- `add_relation(relation, idempotency_key?)` — Link two cards with a typed relation. Subtask = kind 'child' from parent card.
- `archive_card(card_id)` — Archive a card (soft, recoverable — the default way to remove one).
- `claim_card(card_id, ttl_seconds?, owner?)` — Atomically lease a card so no other agent picks it up (visible in list_work).
- `clear_attention(card_id, resolution?)` — Clear a card's attention flag (question answered / decision made). Put the
- `create_board(board, idempotency_key?)` — Create a board. Omit `id` to have one generated; columns/labels may be inlined.
- `create_card(card, idempotency_key?)` — Create a card. `placements` must have >=1 entry (board_id, column_id, position).
- `create_column(board_id, column, idempotency_key?)` — Add a column to a board. `category` gives it portable semantics (e.g. 'done').
- `delete_board(board_id)` — Delete a board permanently. Refused while live cards remain — move/archive first.
- `delete_card(card_id)` — Permanently purge a card. Only allowed on an ARCHIVED card — archive_card first.
- `delete_column(column_id)` — Delete a column permanently. Refused while live cards sit in it — move/archive first.
- `delete_comment(comment_id)` — Delete a comment permanently.
- `delete_relation(relation_id)` — Delete a relation permanently.
- `get_board(board_id)` — Get one board (includes its columns and label registry).
- `get_card(card_id)` — Get one card (works for archived cards too).
- `heartbeat_claim(card_id, ttl_seconds?, owner?)` — Renew your live lease on a card while still working it. `owner` must match
- `list_boards()` — List all boards.
- `list_cards(board_id, include_archived?)` — List a board's cards. Archived cards are hidden unless include_archived=true
- `list_changes(since?, limit?)` — Change feed: every recorded write after cursor `since` (audit trail + sync).
- `list_columns(board_id)` — List a board's columns (name, order, semantic category, wip_limit).
- `list_comments(card_id)` — List a card's comments.
- `list_flows()` — Available workflow schemes: every flow.yaml scheme (+ built-in 'free-roam'),
- `list_relations(card_id)` — List a card's typed relations (blocks, parent/child, duplicates, ...).
- `list_transitions(card_id, board_id?)` — What moves are legal for this card right now, and under which resolved scheme.
- `list_work(assignee?, include_unassigned?)` — What should I work on? Workable cards for `assignee` (default: YOU, this
- `move_card(card_id, to_board_id, to_column_id, position?, force?)` — Move a card within a board it's already on (re-column / re-position).
- `raise_attention(card_id, reason, for_actor?)` — Flag a card as needing a decision or input (e.g. a question only a human or a
- `release_claim(card_id, owner?)` — Release your lease (done or giving up). `owner` overrides the actor
- `remove_placement(card_id, board_id)` — Take a card off one board (its other placements stay). The last placement can't
- `unarchive_card(card_id)` — Restore an archived card.
- `update_board(board_id, patch)` — Partially update a board — only the fields set in `patch` are applied.
- `update_card(card_id, patch)` — Partially update a card — only the fields set in `patch` are applied.
- `update_column(column_id, patch)` — Partially update a column (rename, reorder via `order`, set `wip_limit`...).
- `wait_changes(since?, timeout_seconds?, limit?)` — Long-poll change feed: returns AS SOON AS events exist after cursor `since`

<!-- /generated:tool-reference -->
