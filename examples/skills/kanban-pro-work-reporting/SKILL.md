---
name: kanban-pro-work-reporting
description: Maintain structured, observable work reports on kanban-pro cards via the kanban-pro MCP work_report tools. Use when an agent works, prepares, reviews, routes, asks questions on, or summarizes a kanban-pro card so the card stays self-explanatory without relying on free-form comments.
user-invocable: true
---

# kanban-pro work reporting

Use the kanban-pro MCP `record_work_report` tool to keep the card's current state in
`ext.work_report`. Comments remain useful conversation, but the report is what the UI
and next worker should read first.

Load `kanban://work-report-schema` when you need the exact field shape.

## Report Sections

- `about` — what the card is about, in current terms.
- `questions[]` — decisions/input needed. Each item has a stable `id` and `status`.
- `findings[]` — issues discovered, with evidence.
- `plan[]` — planned/current steps, with status.
- `needs[]` — required input, access, tool, decision, or unblocker.
- `analysis_log[]` — bounded milestones only, not every thought or edit.
- `checks[]` — verification/review gates and their result.
- `verdict` — current review/result verdict.
- `handoff` — artifacts, next actor, branch/worktree, and final outcome.

## Write Rules

- Use stable item ids: `q1`, `finding-api-contract`, `step-verify`, etc.
- Upsert one item at a time; never rewrite the whole `work_report` blob manually.
- Keep `work_report` concise current state. The kanban-pro changelog is the edit history.
- Use comments for human-readable conversation, but do not leave required state only in comments.
- Before moving a card or raising attention, make sure the relevant report section is current.

## Questions and Attention

`raise_attention` is only the signal. The actual questions go in `questions[]`.

Pattern:

1. `record_work_report(card_id, "questions", {"id": "q1", "text": "...", "status": "open"}, idempotency_key="...")`
2. Add a short comment if useful for the conversation.
3. `raise_attention(card_id, "1 open question", for_actor="human:jan")`

When Jan answers, update the question to `status: answered` with `answer`,
`answered_by`, and optionally `answered_at`. If the MCP/API offers
`answer_work_report_question`, use it; it updates the structured question and mirrors a
normal comment.

## Minimum Handoff

Before declaring work handled, record a `handoff` with:

- result/verdict
- changed artifacts or links
- tests/checks run and their outcome
- next actor or expected human action

