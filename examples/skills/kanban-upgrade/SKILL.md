---
name: kanban-upgrade
description: Bring a kanban-pro card up to the CURRENT board structure and make its recorded state TRUE — plan steps, finding statuses, the verification contract (checks), verdict. Evidence-first: never claim a status the card cannot prove. Use after a board/schema change (e.g. `checks` are new), when a card's report looks stale or misleading, or when an agent is about to re-derive work that is already done.
---

# kanban-upgrade — make the card's state true

You are upgrading ONE card (given its id) so that what the board says matches what actually
happened. A card whose plan reads `0/4 done` on work that is committed and approved will be
**re-done by the next agent** — that is the failure this skill exists to prevent. A card whose
findings are all still "open" after their fixes shipped will be re-fixed.

You are not doing the work. You are making the record honest.

## The one rule

**Every status you write must be justified by evidence ON the card or in the repo.**

- A false `done` **hides work**. An honest `todo` merely fails to claim it. When you cannot prove a
  step happened, leave it `todo` and say so.
- Never mark a check `passed` that you did not see pass. `passed` means "I saw the evidence",
  not "it probably passed".
- Never write a verdict the worker never rendered. A missing verdict is a *finding about the
  worker*, not a gap for you to paper over.

If you find yourself reasoning "it was probably done" — stop. That is the exact move that put a
PASS verdict on top of checks nobody ran.

## TWO things are called "checks". Confusing them is the failure this skill exists to prevent

- **`card.checks`** — the **verification CONTRACT**. Declared by whoever specified the work,
  resolved with evidence by whoever did it, and **the only card state that gates the flow**.
  Read it with `get_card`; each entry is `{key, text, required, status, evidence}` where status is
  `pending|passed|failed|skipped|blocked`.
- **`ext.work_report.checks`** — the worker's **SELF-REPORT**. Free text it wrote *about itself*.
  It gates nothing, it is often stale, and it is frequently green when the contract is not.

**Never treat the self-report as evidence for the contract.** On AIR-2915 the report carried six
green `pass` rows from an old run while the contract said `browser-verify: blocked` — and every
reader, human and agent, concluded the card was verified. A `pass` an agent wrote about its own run
is a claim, not a proof. If the two disagree, the contract wins and **the disagreement is itself a
finding**: say so in your comment.

**A missing contract is not proof that none was declared.** Until 2026-07-14 a stale MCP server
could silently ERASE `card.checks` on any write (it ran a `Card` class predating the field). If a
card's checks look absent but its comments or spec talk about a required check, read the change-log
(`list_changes`) for `check.declared` / `check.resolved` events before concluding there was no gate.

## What counts as evidence

Strongest first. Cite the one you used, in the item's `evidence` (or in your comment):

1. **Git**: a commit SHA in `handoff.commit`, or the branch/worktree in `handoff` — read the actual
   diff. If the plan step says "add `hydrateReviews` to useReviewStoreBase.ts" and the diff adds
   it, the step is `done`.
2. **An approving verdict** (`APPROVE`, `pass`) — the reviewer signed off on a change, so the plan
   that produced it ran.
3. **The report's own words**: `handoff.commit: "uncommitted (fix-1 applied)"`, an `analysis_log`
   entry saying what was applied.
4. **The session log** (`Session log` tab / `ext["kanban_pro.sessions"]`) — what the agent actually
   did, in its own transcript.
5. **The change-log** (`list_changes`) — moves, claims, report writes, with actors and times.

A plan step with none of these stays `todo`.

## Procedure

1. **Read the card**: `get_card`, its `work_report`, its comments (`list_comments`), its activity.
2. **Gather evidence** as above. Actually open the diff / the log — do not skim the summary.
3. **Plan** (`plan[]`, `status: todo|doing|done|blocked`): one item per step (if you find a legacy
   item holding a nested `steps[]`, split it — a step is a step). Set each status from evidence.
   `blocked` only when something outside the worker's control stopped it, and say what.
4. **Findings** (`findings[]`): set `status: fixed` on findings whose fix is in the diff or which
   the report says were applied. Leave the rest open. The UI strikes fixed ones through, so the
   open ones are the ones that still matter.
5. **Checks** — the verification contract, and the only card state that GATES the flow:
   - If the card has no checks and its task.md/spec names how to verify, ask whoever specified the
     work to `declare_checks` (you must not declare the checks you are judged against — the board
     refuses it if you hold the claim).
   - For each check, `record_check_result(card_id, key, status, evidence)` from what you can prove.
     `passed` = you saw it pass. `failed` = it ran and failed. `blocked` = it COULD NOT RUN and you
     investigated why (read the logs, tried to bring the stack up). `skipped` = someone chose not
     to. Never use `skipped` for "could not".
6. **Verdict**: leave a missing verdict missing. A verdict must be consistent with the **contract**,
   never with the self-report. If the existing verdict contradicts the contract (an `APPROVE` sitting
   on top of a `pending`/`blocked` required check), that verdict is false: say so in a comment,
   `raise_attention`, and route it — do not silently rewrite it into something defensible, and do
   not delete it as though it never happened.
7. **Leave a REMEDY, or you have not finished.** An upgraded record that says what is wrong but not
   what to do next just hands the next agent the same puzzle. Every run ends with a `handoff`:
   `next_actor` (who moves next) and `explicit_instruction` (what they must actually do, concretely
   — the command, the check key, the file). "Needs verification" is not an instruction; "run
   `browser-verify` against the compose stack and `record_check_result` with the console output" is.
   If nobody can proceed until a human decides, that IS the remedy — `raise_attention` for them and
   say what you need.
8. **Report what you did**: one comment listing every status you changed and the evidence for it,
   and — just as important — **every item you could NOT prove**, left as it was. That list is the
   real output: it tells the next agent exactly what is still unknown.

## What you must not do

- Do not fabricate a status to make a card look finished.
- Do not copy the self-report into the contract. `record_check_result(key, "passed")` because
  `work_report.checks` says `pass` is laundering a claim into evidence — and it green-lights the
  gate on work nobody ran. Only record what YOU can point at.
- Do not clear an attention flag you did not resolve. Clearing means *the problem is gone*, not
  *it is now someone else's*. If the next move belongs to another actor, **re-route** it:
  `raise_attention(card_id, reason, for_actor=<them>)`.
- Do not touch a card in a terminal lane (done / won't do / merged). Finished work is a record of
  what was actually filed; rewriting it to match a newer schema destroys that record and buys
  nothing.
- Do not rewrite `ext["work_report"]` by hand through `update_card` — it deletes every section you
  did not include. Use `record_work_report`, which upserts by item id.

## Scope

Given a card id: upgrade that card. Given a lane or "the board": `list_cards`, skip archived and
terminal lanes, and work the rest one at a time — each with its own evidence. Report a summary at
the end: cards upgraded, statuses changed, and **what remains unproven**.
