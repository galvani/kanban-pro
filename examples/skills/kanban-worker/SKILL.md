---
name: kanban-worker
description: Work cards from the shared kanban-pro board (MCP server "kanban-pro") as a pull worker — list_work to get your queue, claim_card before touching anything, move/comment per the card's flow, release when done. Use when asked to "pick up work", "work the board", process kanban cards, or when a task mentions being tracked on the board.
user-invocable: true
---

# Kanban worker — the pull loop

You are one of several agents (and humans) sharing this board. Every write you make is
attributed to your connection's actor and recorded in the board's change-log — work as
if your moves are being watched, because they are.

## The loop

1. **`list_work()`** — your queue: cards assigned to you or unassigned, in workable
   columns, cards leased to other agents already excluded. Each item carries its
   **legal transitions inline** — plan from this one call. Items come ordered:
   in-progress first, then actionable, then backlog.
2. **Pick ONE card** (top of the queue unless told otherwise) and **`claim_card`** it.
   A `conflict` means someone beat you to it — move on to the next card, never retry
   the same claim in a loop.
3. **Make your intent visible:** `update_card` to assign yourself (if unassigned) and
   `move_card` to a started-category column. Now the board shows who is doing what.
   **Link your session log** so a watcher can follow you live from the card: stamp
   `update_card(ext={"session": {...}})` (shallow-merged, won't clobber other ext) with
   your transcript path — the board tails it in a modal (live while your claim is held,
   final log once released). See *Linking your session log* below.
4. **Work it.** While running long: `heartbeat_claim` every few minutes — a silent
   lease expires and the card becomes claimable by others (that's the crash-recovery
   feature, don't fight it). Sub-steps worth tracking? Create subcards
   (`create_card` + `add_relation` kind `parent`) instead of a private todo list.
5. **Land the outcome:**
   - Keep the structured card report current with the `kanban-pro-work-reporting`
     skill (`record_work_report`) before moving or raising attention.
   - done → `record_work_report` the handoff, `add_comment` a short result summary,
     then `move_card` to the done column.
   - stuck → `record_work_report` the open question/need/finding, `add_comment` WHY
     (typed: `blocked: <kind> — <reason>`), then `move_card` to the blocked column.
   - Always check `list_transitions(card_id)` if a move is refused — the card's flow
     scheme decides what's legal, and the error names the allowed targets.
6. **`release_claim(card_id)`** — always, done or stuck.

## Discipline

- **Never work a card you haven't claimed.** The claim is what stops two agents from
  doing the same job twice.
- **Never `force=true` silently.** A forced move is allowed but always audited — if
  you must force, the same action needs an `add_comment` saying why.
- **Respect the scheme.** A card may carry its own workflow (`docs` skips review;
  `free-roam` is unrestricted). `list_transitions` tells you which rules apply.
- **Deletes are archive-first** by design: `archive_card` is the way to remove;
  `delete_card` only purges already-archived cards. Board/column deletes refuse while
  live cards remain. Don't fight the guards — they're for your own crashes.
- Comments are the durable record; your final session output is not. If a human should
  know it tomorrow, it goes in a comment.
- `work_report` is the current structured state. Use the `kanban-pro-work-reporting`
  skill whenever you ask questions, record findings, update a plan, or hand off.

## Linking your session log

The board can show a live view of the agent working a card — every claimed card gets a
▶ badge, and its detail modal has a **session log** button that tails your log (live
while your claim is held, final log once you release). To feed it, stamp `ext.session`
right after you claim:

```
update_card(card_id, {"ext": {"session": {
    "actor": "<your actor, e.g. agent:claude-code>",
    "log":   "<absolute path to your session log>",
    "kind":  "transcript"   # "transcript" for a Claude Code .jsonl, "log" for a plain .log
}}})
```

- **Path guard (why it may not show):** the server only serves `*.jsonl` / `*.log` files
  under `$HOME` or the tmp dir. A path outside that is silently refused — nothing
  sensitive is exposed by a mistyped `log`.
- **Claude Code transcript path** is best-effort derivable: it's
  `~/.claude/projects/<cwd-with-/-and-.-turned-to-->/<your-session-id>.jsonl`. If you
  can't determine your session id, skip the stamp — the card still shows the ▶ claim
  badge, just without a log link.
- **Optional:** on release you may set `ext.session.ended_at`; it's cosmetic — the board
  derives running-vs-done from whether your claim is still live, not from a stored flag,
  so a crashed lease correctly reads as "done" once it expires.
- **Dispatcher-run cards** don't need this: their `ext.work.log` is used as a fallback
  source automatically.

## Tool reference

<!-- generated:tool-reference — regenerate: uv run python -m tests.toolref --write -->

- `add_comment(comment, idempotency_key?)` — Add a comment to a card (`card_id`, `author` = User id, `body`).
- `add_placement(card_id, placement)` — Put a card on an additional board (one placement per board; errors if already on it).
- `add_relation(relation, idempotency_key?)` — Link two cards with a typed relation. Subtask = kind 'child' from parent card.
- `answer_work_report_question(card_id, question_id, answer)` — Answer one work_report question and mirror the answer as a normal comment.
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
- `record_work_report(card_id, section, item, op?, idempotency_key?)` — Update one structured work_report section/item on a card.
- `release_claim(card_id, owner?)` — Release your lease (done or giving up). `owner` overrides the actor
- `remove_placement(card_id, board_id)` — Take a card off one board (its other placements stay). The last placement can't
- `unarchive_card(card_id)` — Restore an archived card.
- `update_board(board_id, patch)` — Partially update a board — only the fields set in `patch` are applied.
- `update_card(card_id, patch)` — Partially update a card — only the fields set in `patch` are applied.
- `update_column(column_id, patch)` — Partially update a column (rename, reorder via `order`, set `wip_limit`...).
- `wait_changes(since?, timeout_seconds?, limit?)` — Long-poll change feed: returns AS SOON AS events exist after cursor `since`

<!-- /generated:tool-reference -->
