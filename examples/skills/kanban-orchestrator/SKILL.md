---
name: kanban-orchestrator
description: Manage the shared kanban-pro board (MCP server "kanban-pro") as an orchestrator — turn goals into cards (subcards via parent/child relations), set up each board's workflow (columns + the legal transitions between them), assign and prioritize, watch progress via the change-feed cursor, unblock. Never works cards itself. Use when asked to plan work onto the board, decompose a project into cards, report board status, or route/unblock work.
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
3. **Cards inherit the board's flow** — there is no per-card scheme to pick. A genuinely
   rule-free card can opt out with `ext["kanban_pro.scheme"] = "free-roam"`, or carry its
   own one-off flow in `ext["kanban_pro.flow"]`. See *Setting up a board's workflow*.
4. Place cards in a backlog/ready column (`list_columns` shows categories); set
   assignees only when you know who should pull it — unassigned cards appear in every
   worker's `list_work`.

## Setting up a board's workflow

The workflow lives ON the board (`board.flow`): the legal column→column moves, keyed by
column id. You administer it — workers only follow it.

- **New board?** `init_board(board_id, name?, preset)` scaffolds columns + a matching flow
  in one call. Presets: `agent-lifecycle` (the full swarm pipeline), `simple-kanban`
  (todo/doing/done), `docs` (no review gate), `blank` (columns-only, free movement). To
  onboard from an existing board instead, use the `kanban-pro-migrate` CLI.
- **Existing board?** `set_transitions(board_id, from_column_id, to_column_ids)` sets one
  lane's out-edges; `set_flow(board_id, transitions)` replaces the whole map;
  `clear_flow(board_id)` drops it (free movement). Every edge must reference a real column
  — a dangling id is refused, and deleting a column strips edges that named it, so the flow
  can't drift from the columns.
- A column named in no edge stays free (an ungoverned scratch lane). Read any board's flow
  with `list_flows()`.

### Two board settings you own, and must not guess at

- **`anonymous_writes`** (`init_board(anonymous_writes=...)`, or `update_board(ext=...)` later)
  — whether the board accepts writes from a connection with no identity (one started without
  `--actor kind:name`, whose events land as `actor: unknown`). **ASK the user, don't assume:**
  `refuse` (default) means every write names who made it, so the change-log can still answer
  "who did this" a year from now — right for any board that more than one person or agent
  touches. `allow` suits a personal, single-user board where there is nobody to attribute to.
  An unattributable event looks audited but isn't, and nobody notices until they need history.
- **`auto_clear_attention_columns`** (`update_board(ext=...)`) — the RESTING lanes: arriving in
  one clears the card's attention flag, because a card that reached it is waiting on nobody.
  Only list lanes where that's true. A lane where cards WAIT on a human (e.g. `scheduled`,
  parked until someone sets a retry) must NOT be here — the move would clear the very flag that
  asks for the decision. kanban-pro refuses a blocking flag on a card resting in one of these
  lanes, so a mislisted lane shows up as a `Conflict`, not as a silently stranded card.

## Watching and steering

- **Consume the change-feed, don't poll the board:** keep the last `seq` you saw and
  call `list_changes(since=<cursor>)` — you get exactly what happened (who moved what,
  forced moves flagged, claims/releases). This is also how you report progress.
- A card sitting claimed with no movement → its worker may have died; the lease
  expires on its own, no action needed. A card in a blocked column → read its last
  comment (`list_comments`) and either resolve the blocker, reassign
  (`update_card`), or move it back to ready.
- **A blocking attention flag is the only thing that halts a card** (`severity="block"`;
  `warn`/`info` are visible but keep it flowing). Blocked cards do not appear in anyone's
  `list_work`, so nothing frees them but you: watch `wait_changes` for `attention.raised`,
  answer via `answer_work_report_question`, then `clear_attention`. A flag nobody is watching
  is a stalled card, and it will not announce itself.
- Refused moves are information: `list_transitions(card_id)` shows what the board's flow
  allows. Use `force=true` only for deliberate exceptions, always with a
  comment saying why.
- WIP-limit conflicts on a column mean the team is overloaded there — rebalance
  (move something out) instead of forcing in.
- Use the `kanban-pro-work-reporting` skill when you summarize a card, answer or route
  questions, or check whether a worker left enough structured state for the next actor.
  Do not infer current truth only from a comment thread when `work_report` exists.

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
- `answer_work_report_question(card_id, question_id, answer)` — Answer one work_report question and mirror the answer as a normal comment.
- `archive_card(card_id)` — Archive a card (soft, recoverable — the default way to remove one).
- `claim_card(card_id, ttl_seconds?, owner?)` — Atomically lease a card so no other agent picks it up (visible in list_work).
- `clear_attention(card_id, resolution?)` — Clear a card's attention flag (question answered / decision made). Put the
- `clear_flow(board_id)` — Drop a board's workflow entirely — it becomes free-roam (any move allowed).
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
- `init_board(board_id, name?, preset?, id_scheme?, anonymous_writes?)` — Onboard a NEW board pre-seeded from a preset — columns + a matching workflow, built
- `list_boards()` — List all boards.
- `list_cards(board_id, include_archived?)` — List a board's cards. Archived cards are hidden unless include_archived=true
- `list_changes(since?, limit?)` — Change feed: every recorded write after cursor `since` (audit trail + sync).
- `list_columns(board_id)` — List a board's columns (name, order, semantic category, wip_limit).
- `list_comments(card_id)` — List a card's comments.
- `list_flows()` — Every board's workflow — the allowed column->column moves (by column id) that
- `list_relations(card_id)` — List a card's typed relations (blocks, parent/child, duplicates, ...).
- `list_transitions(card_id, board_id?)` — What moves are legal for this card right now, and under which resolved flow.
- `list_work(assignee?, include_unassigned?)` — What should I work on? Workable cards for `assignee` (default: YOU, this
- `move_card(card_id, to_board_id, to_column_id, position?, force?)` — Move a card within a board it's already on (re-column / re-position).
- `raise_attention(card_id, reason, for_actor?, severity?)` — Flag a card as needing a decision or input (e.g. a question only a human or a
- `record_work_report(card_id, section, item, op?, idempotency_key?)` — Update one structured work_report section/item on a card.
- `release_claim(card_id, owner?)` — Release your lease (done or giving up). `owner` overrides the actor
- `remove_placement(card_id, board_id)` — Take a card off one board (its other placements stay). The last placement can't
- `set_flow(board_id, transitions)` — Replace a board's whole workflow. `transitions` maps a from-column id to the list
- `set_transitions(board_id, from_column_id, to_column_ids)` — Set the out-edges for ONE lane, leaving the rest of the board's flow untouched.
- `unarchive_card(card_id)` — Restore an archived card.
- `update_board(board_id, patch)` — Partially update a board — only the fields set in `patch` are applied.
- `update_card(card_id, patch)` — Partially update a card — only the fields set in `patch` are applied.
- `update_column(column_id, patch)` — Partially update a column (rename, reorder via `order`, set `wip_limit`...).
- `wait_changes(since?, timeout_seconds?, limit?)` — Long-poll change feed: returns AS SOON AS events exist after cursor `since`

<!-- /generated:tool-reference -->
