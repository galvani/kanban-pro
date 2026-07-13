---
name: kanban-retro
description: Forensic retrospective on how work actually FLOWED through the kanban-pro board — reconstruct each card's timeline from the change-log (lane moves, bounces, forced moves, attention, dispatch loops, rounds, cycle time), attribute it to the model that really served it (model-pro requested-vs-resolved, context overflow, quota, cold loads), verify what agents CLAIMED against ground truth (git, tests, the running app), classify the failure modes, and propose concrete diffs to the Hermes SOULs, skills and project knowledge that caused them. Use when work shipped late/broken, a ticket bounced back repeatedly, cards sat blocked, agents duplicated each other, or Jan asks "what went wrong", "why did this take N rounds", "analyse the flow", "post-mortem the board". Read-only on the board, git and the model gateway; writes only an analysis document, and applies SOUL/skill changes ONLY on explicit approval.
user-invocable: true
---

# Kanban retro — what the board actually did, and why

A retrospective on the *system*, not the code. The code is a symptom; the flow is the
disease. You reconstruct how work moved (or failed to move) through the board, and turn
that into changes to the **instructions, the knowledge, or the model routing** that
produced it.

**The change-log cannot lie about what happened.** Agents' `work_report`s, comments and
verdicts *can*, and routinely do. The whole method is: build the timeline from the
change-log, attribute it to the model that actually ran, then check every claim against
ground truth.

## HARD rules

1. **A `verdict: PASS` is a claim, not a fact.** Verify it. Real incident: a builder
   recorded `PASS` with `type-check` and `lint` green on code that crashed the page on
   render (default import from a module with no default export). Both gates pass on dead
   code. **If a card claims done — is it committed? merged? does the app render?**
2. **"Done" ≠ merged ≠ deployed.** Check each independently. The most expensive failure
   this board has produced: *seven cards marked `done` with zero commits anywhere*. The
   customer retested an old build and reported everything as still broken. Twice.
3. **Diagnose the layer before you blame it.** A failure is one of:
   **model-starved** (context overflow, quota, truncation, wrong tier) ·
   **instruction** (SOUL/skill told it to do the wrong thing, or permitted the shortcut) ·
   **knowledge** (the fact existed nowhere the agent could find it) ·
   **infra** (ports, containers, dead services, missing fixtures) ·
   **routing** (dispatcher loop, no dedupe, wrong worker).
   **Each has a different fix. Rewriting a SOUL because the model ran out of context is
   pure waste** — and it is the easiest mistake to make in a retro.
4. **Every finding ends in a diff.** To a SOUL, a skill, a knowledge file, a script, or a
   model route. "The agent should have been more careful" is not a finding.
5. **Never fix product code in a retro.** Note bugs; do not chase them.
6. **Quote the evidence.** Every claim cites a `seq`, a timestamp, a commit, a log line
   or a `history.db` row. No vibes.

## Sources — read ALL of these before concluding

You cannot attribute a failure you cannot see. Gather first, conclude second.

### The board (what happened)
- `list_changes(since=…)` — the append-only feed. **The spine of the timeline.**
- `get_card` → `ext.work_report` (plan / findings / checks / verdict / handoff),
  `ext.work` (attempts, retry_at), `ext.session` (log paths), `ext["kanban_pro.attention"]`.
- `list_comments` — dispatcher backstops, worker progress, the quota/gateway errors.
- `list_relations` — `parent`/`child` splits, and **`duplicates`** = parallel work nobody
  deduped.

### The workers — SESSION LOGS (what they actually did and thought)
A card tells you *that* it moved. Only the session logs tell you **why** — what the agent
believed, what it tried, what it skipped. **Read them. Do not conclude from the board
alone.** The card itself points at its own logs: `ext.work.log`, `ext.session.log`,
`ext.session.jsonl`.

- `~/.local/share/kanban-pro/sessions/<card_id>.log` — worker stdout (the tail the
  dispatcher quotes in its backstop comments).
- `~/.local/share/kanban-pro/sessions/<card_id>.jsonl` — the worker transcript.
- `~/.local/share/kanban-dispatcher/workspaces/<card_id>/.kanban-dispatcher/<card_id>.log`
  — dispatch attempts. **Count the `--- worker start …` markers: N markers with no lane
  change = a dispatch loop.** Also where quota / gateway / 503 errors surface verbatim.
- **Claude Code transcripts** — `~/.claude/projects/<path-slug>/<session-uuid>.jsonl`.
  The slug is the working directory with `/`→`-`; **dispatcher workspaces get their own
  slug**, so a worker's full reasoning is there (e.g.
  `-home-jan--local-share-kanban-dispatcher-workspaces-<card_id>`). This is the richest
  source for *why* an agent believed something false.
- **OpenCode transcripts** — sqlite `~/.local/share/opencode/opencode.db`
  (`part`/`message` tables, keyed by `session_id`; `message.modelID` = the model).

**If you are not sure where a log lives, CHECK — don't assume.** Paths move. Start from
the card's own `ext` pointers, then `ls` the two `~/.local/share/...` roots and
`~/.claude/projects/`. A retro that skips the logs because it couldn't find them is a
retro that will blame the wrong layer.

### The model (who actually served it) — `~/.local/share/model-pro/history.db`
**A profile's `config.yaml` says which model it *asked* for. It does not tell you which
model *ran*.** Virtual models (`coder`, `architect`, `agent`) resolve through a
`prefers:` list, and model-pro **adopts an already-loaded model** rather than reloading
the first preference — and a virtual with a cloud entry can silently route off-box.

```sql
-- who really served this card's window, and how did it go?
SELECT ts, requested_model, resolved_model, backend, status_code, error_type,
       required_ctx, context_window, cold_load, finish_reason,
       prompt_tokens, completion_tokens, duration_ms
FROM requests
WHERE ts BETWEEN '<card first event>' AND '<card last event>'
ORDER BY ts;
```
Read it for:
- **`requested_model` ≠ `resolved_model`** → the SOUL's assumed model is not what ran.
- **`error_type='context_overflow'`, or `required_ctx > context_window`** → *model-starved*.
  The fix is a bigger context / a split task, **not** a SOUL edit.
- **`status_code` 429/503, `error_type`** → quota or gateway. Capacity, not competence.
- **`finish_reason='length'`** → the answer was truncated. Anything downstream that
  looks like the agent "gave up" or "forgot a step" may be this.
- **`cold_load=1`** → an eviction churned the GPU; explains latency, not correctness.
- `events` table (`action`, `model`, `requested_model`) → load/unload timeline.

### The model's own memory (calibration)
- `<repo>/.agents/agents.json` — **model-stamped**: `model_context` (known limits),
  per-agent `current_model`, `task_history[]` (duration, fix_cycles, estimated_tokens,
  `context_overflow`), recorded failures and lessons.
- `<repo>/.agents/learned.md` — accumulated lessons. **Check whether the lesson that
  would have prevented this incident was already in here and the agent didn't read it
  (→ SOUL fix: make it read) or was never written (→ knowledge fix: write it).**
- `~/.hermes/profiles/<role>/config.yaml` — the model each role *requests*, and its
  route. `~/.hermes/profiles/*/SOUL.md` + `BUILDER-CONTRACT.md` — the instructions.

### The knowledge (did the agent have a chance?)
Knowledge is **tool-agnostic and shared** across Claude / Codex / OpenCode. It lives in
`.agents`, never in a tool-specific file:

- **Portable, cross-project** — `~/.agents/skills/knowledge/notes/` (domain folders +
  generated `_INDEX.md`), curated via the **`knowledge`** skill.
- **This project** — `<repo>/.agents/knowledge/*.md`, plus `<repo>/.agents/tasks/<KEY>/`
  (`task.md`, `fix-N.md` — the spec the worker was actually handed).
- `AGENTS.md` — the repo's agent router. `JOURNAL.md` — what happened and why. `docs/`.

`CLAUDE.md` is a **tool-specific entry point, not a knowledge store** — if you find
project knowledge in it, that is itself a finding: it is invisible to Codex/OpenCode.
Propose moving it to `.agents/`.

**A fact that lives only where the agent never looks is, operationally, a fact that does
not exist.** When you find the agent failed for want of a fact, always ask *both*: did it
exist anywhere, and was it somewhere the agent reads?
- The card's own **description** — the spec the worker was handed. **A false constraint
  here poisons every worker who reads it.** (Real incident: *"host→container e2e is
  unreachable, verify statically"* — it was reachable the whole time; the API answered on
  a published port. No worker questioned it, for days.)

### Ground truth (did it actually land?)
- `git log --all --grep=<KEY>` · `git merge-base --is-ancestor <branch> <integration>` ·
  `git worktree list` (leaked worktrees) · `docker ps` (leaked stacks squatting ports).

## Working-set discipline — REDUCE as you go, never hoard the corpus

**A retro is a reduce, not a recall.** The evidence (transcripts, logs, the change-feed,
`history.db`) is far larger than any context window, and **you do not need to remember any
of it** — only what you *extracted* from it. Hoarding raw data is how this analysis dies
of context exhaustion on a smaller model, and it buys nothing on a larger one.

**Never read a whole transcript into context.** Grep it for the few things that matter, keep
the line, drop the file:

```bash
grep -nE "worker start|error|Error|quota|limit|403|500|refus|skip|PASS|FAIL" <log> | head -40
sqlite3 history.db "SELECT ... WHERE ts BETWEEN ... "   -- aggregate in SQL, not in your head
```

Process **one card at a time**, and after each card write a compact **evidence record** —
then discard everything raw behind it:

```
card: <id> <title>
lanes:      created→ready→running→blocked→ready→done   (rounds=3, bounces=2, forced=1)
dispatches: 11 worker-starts, 0 lane changes  [dispatcher log]
model:      requested=coder resolved=Qwen3-Coder-30B  | 6× 429, 1× context_overflow
claims:     verdict=PASS, checks: type-check ✓ lint ✓ browser-verify SKIPPED
truth:      no commit anywhere (git log --all --grep) → claim FALSE
findings:   [verification-theatre] [inherited-false-constraint]
evidence:   seq 4412; 2026-07-09T22:17Z; log line 88; commit —
```

That record is a few hundred bytes. Ten cards still fit in any context. **The report is
written from the records, never from the raw evidence.**

Rules:
- **Extract, then forget.** Once a fact is in a record with its citation, the source is
  dead to you. Do not re-read it "to be safe".
- **Aggregate in the tool, not in the model.** Counting, summing and grouping belong in
  `sqlite3`, `grep -c`, `git log --oneline | wc -l` — not in your context.
- **If you are compacting or running low, you already have what you need** — write the
  remaining records and produce the report. Losing raw logs costs nothing; losing the
  records costs everything, so write them as you go, not at the end.
- **Never truncate the card SET to save room.** Drop detail, never coverage: a retro that
  silently skipped three cards is worse than useless. If you must cut, say what you cut.

## Method

### 1. Scope
Resolve to a **card set**: parent + children + the `[BUILD]`/`[REVIEW]` split cards.
Walk `parent`/`child` and `duplicates` edges. A duplicate pair is itself a finding.

### 2. Timeline
Fold `list_changes` into a per-card lane history:

| Event | What it tells you |
|---|---|
| `card.created` | when work entered, who filed it |
| `card.moved` | every transition — **`data.forced` = someone overrode the flow** |
| `attention.raised`/`.cleared` | where it stuck, who was asked, how long until answered |
| `comment.added` | dispatcher backstops, quota/gateway errors, worker narration |
| `relation.added` | splits; `duplicates` = uncoordinated parallel work |

### 3. Numbers (per card, then rolled up)
- **Rounds** — entries into a *started* column (re-entry = rework).
- **Bounces** — `ready → running → blocked → ready` cycles.
- **Cycle vs touch time** — created→done wall-clock vs. time actually in a started lane.
  A big gap means it was *waiting*, not *hard*.
- **Dead time** — longest gap with no events; then ask why.
- **Forced moves**, **attention latency**, **dispatch loops** (worker-start markers with
  no lane change).
- **Token/£ burn** — sum `prompt_tokens`/`completion_tokens` over the card's window.
- **Verification depth** — for each done card: any `checks` marked `skipped` next to a
  `PASS` verdict? That combination is itself the defect.

### 4. Classify (known taxonomy — extend it when you find a new class)

| Class | Signature | Layer / fix |
|---|---|---|
| **Undeployed done** | `done`, but no commit / not merged | instruction — worker SOUL defines done as *approved*, not *landed* |
| **Verification theatre** | `PASS` beside `browser-verify: skipped`; only type-check/lint in `checks` | instruction — gates that cannot catch the bug class |
| **Inherited false constraint** | every worker repeats a claim from the card description; none tests it | instruction + the `prepare` skill that wrote it |
| **Knowledge gap** | the fact existed only in a README/doc no agent reads | knowledge — put it in `CLAUDE.md`/`.agents/knowledge/` |
| **Model-starved** | `context_overflow`, `finish_reason=length`, 429/503 in `history.db` | routing — bigger context, split the task, change tier. **NOT a SOUL edit.** |
| **Silent model swap** | `requested_model` ≠ `resolved_model` | routing — pin a concrete model, or fix the `prefers:` list |
| **Infra leakage** | a worker blocked by a *sibling worker's* leftovers (port squat, stale worktree) | scripts — setup/teardown |
| **Dispatch loop** | N worker-starts, no lane change; backstop comments | routing — assignee left set on a terminal card |
| **Duplicate work** | two agents build the same item on different branches | routing — no dedupe/claim check before dispatch |
| **Escalation black hole** | long `attention.raised` → `.cleared` latency | instruction — SOUL says "record BLOCKED" instead of `raise_attention` |

### 5. Verify the claims — where the real findings are
For every card that reached `done`: does the commit exist? is it merged? did the claimed
checks actually *execute* (a test file whose helper throws a `TypeError` reports nothing
and looks green)? If it's a UI change — **does it render?** A retro that trusts
`type-check` is committing the very sin it is investigating.

### 6. Report
Write to the **project being analysed** (not into kanban-pro):
`docs/retro/<scope>-<YYYY-MM-DD>.md`. Structure: **what shipped → timeline → numbers →
failure classes with evidence → proposed changes.** Lead with the conclusion.

### 7. Propose the fixes — approve-first
The output that matters is **diffs**, grouped by layer:
- **instruction** — `~/.hermes/profiles/<role>/SOUL.md`, `BUILDER-CONTRACT.md`,
  `~/.agents/skills/*/SKILL.md` (skills are shared; `~/.claude/skills` symlinks here)
- **knowledge** — see below; this is usually the biggest and most durable win
- **routing** — profile `config.yaml` model, dispatcher route, model-pro `prefers:`
- **infra** — `~/.hermes/scripts/*` (setup/teardown)
- **card template** — what the `prepare` skill writes into a spec

For each: quote the current text, show the replacement, name the incident it prevents.
**Change nothing until Jan approves that specific item.** Number them so he can accept
individually.

### 7b. Propose KNOWLEDGE — the fix that stops the *next* agent, not just this one

Most retro findings are not "the agent was careless" — they are **"the agent could not
have known."** For each such finding, propose a knowledge entry, and **route it by
portability**:

**Knowledge is shared across tools (Claude / Codex / OpenCode). It lives in `.agents` —
NEVER in `CLAUDE.md`**, which only Claude reads. Propose by scope:

| The lesson is… | Goes to | Test |
|---|---|---|
| **Portable** across projects/stacks (a framework trap, a tool's silent failure mode) | global KB — `~/.agents/skills/knowledge/notes/<domain>/`, curated via the **`knowledge`** skill (regenerate `_INDEX.md`) | "Would this bite me on an unrelated repo with the same stack?" |
| **This project only** (a quirk of *this* codebase / env / config) | `<repo>/.agents/knowledge/<topic>.md` | "Is it useless outside this repo?" |
| **Where things live / how to run it** (routing an agent to the right entry point) | `<repo>/AGENTS.md` — the router, kept lean; it *points at* `.agents/`, it is not the store | one line + a link |
| **What happened & why** (the decision, the incident) | `<repo>/JOURNAL.md` | it's history, not a rule |
| **Model calibration** (this model overflows at N, needs a fix cycle for X) | `<repo>/.agents/agents.json` + `.agents/learned.md` | it's about the model, not the code |

If you find project knowledge sitting in `CLAUDE.md`, **propose moving it to
`.agents/knowledge/`** — as it stands it is invisible to every non-Claude agent, which
means half the swarm is flying blind on it.

Rules for the entries you propose:
- **Write the trap, not the fix.** "API Platform silently ignores an undeclared filter
  param and returns the FULL set — no error" is worth more than "we changed the source to
  `post.article.brand`." The next agent hits a *different* filter.
- **State the false belief it corrects.** The entry exists because someone believed
  something wrong. Say what.
- **Put it where it is READ, not where it is tidy.** A fact in a README the agent never
  opens does not exist. If the agent reads `CLAUDE.md` first, it goes in `CLAUDE.md`.
- **Check it isn't already there.** If the lesson *was* already written and the agent
  still failed, the fix is **not** another knowledge entry — it is an *instruction* fix
  (make the SOUL read it) or a *placement* fix (move it somewhere read). Say which.
- Cite the incident (`seq`/commit) so the entry can be revisited when it goes stale.

## Traps

- **Don't rewrite a SOUL for a model failure.** Check `history.db` first. A worker that
  "gave up mid-task" may simply have been truncated (`finish_reason=length`).
- **A `forced` move is not misbehaviour by default.** Forcing is legal and audited — it
  signals the *flow* may be wrong, not the agent.
- **Actors are not people.** `agent:prepare` running six times is one dispatcher bug, not
  six mistakes.
- **A duplicate is not automatically waste.** Real case: two parallel implementations,
  each holding something the other lacked (one had the root-cause fix, the other the
  tests). Say what should have been *salvaged*, not just that it was wasted.
- **Quota-blocked ≠ reviewer-blocked.** Both sit in `blocked`. Read the comments before
  classifying, or your headline metric is meaningless.
- **`list_changes` is cursor-paged.** Walk the cursor; never snapshot-poll.

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
- `init_board(board_id, name?, preset?, id_scheme?)` — Onboard a NEW board pre-seeded from a preset — columns + a matching workflow, built
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
- `raise_attention(card_id, reason, for_actor?)` — Flag a card as needing a decision or input (e.g. a question only a human or a
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
