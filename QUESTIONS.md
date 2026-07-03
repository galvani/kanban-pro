# kanban-pro — Open Questions (Q&A)

Answer at your pace: fill the **A:** line under any question (free text — pick an option,
write your own, or "defer"). I'll fold answers into SPEC.md / TODO.md and delete them from
here as they're resolved. Nothing here is committed as decided until you answer.

Legend: 🔵 clarification · 🟢 data model (Step ①) · 🟡 operations (Step ②) ·
🟠 capabilities (Step ③) · 🔴 proxy core (Step ④) · 🟣 profiles/config (Step ⑤)

---

## 🔵 Q1 — What is "openclaw", and integration roles?

**Question:** You listed Hermes, openclaw, Claude, GPT as must-be-native. What is
*openclaw*? And for each of the four: is it a **consumer** (drives kanban-pro), a
**backend adapter** (kanban-pro proxies to it), or **both**?
**Why it matters:** decides whether each becomes an MCP/client integration, an adapter
module, or both — different work in different places.
**A:** agent harness

---

## 🟢 Q2 — Is the minimal entity set right?

**Question:** Entities = Board, Column (+category, wip_limit), Card (+placements[], ext),
Label, Comment, Relation. Good as the core, or missing something you'll actually use?
**Why it matters:** the core model is the contract every adapter implements; adding later
is more expensive than getting it right now. Deliberately minimal to avoid a Jira clone.
**A:** ✅ Core approved + added a minimal **User** entity (id, display_name, ext),
referenced by Card.assignees[] and Comment.author. → SPEC "Canonical domain model".

## 🟢 Q3 — Due dates as a first-class Card field?

**Question:** Add `due_date` (and maybe `start_date`) to Card, or leave it in `ext`?
**Why it matters:** nearly every surveyed tool has due dates; it's a strong candidate for
the core rather than passthrough. Cost: another field every adapter must map.
**A:** ✅ (b) both `start_date?` + `due_date?` in core Card (nullable). → SPEC Card model.

## 🟢 Q4 — Checklists / subtasks: first-class, or via relations/ext?

**Question:** Three options — (a) first-class `Checklist`/`ChecklistItem` on a Card;
(b) model subtasks as `parent`/`child` **relations** between cards; (c) leave in `ext`.
**Why it matters:** backends split hard here (Trello checklists vs Jira subtasks vs
Kanboard's two systems). (b) reuses the relation model; (a) is richer but heavier.
**A:** ✅ Both, split: subtasks = child cards via PARENT/CHILD relations (reuse);
checklists = first-class minimal `Card.checklists[]` + `CHECKLISTS` capability
(polyfill via write-through). → SPEC Card/Checklist + ports Capability.

## 🟢 Q5 — Attachments as a first-class entity?

**Question:** Add an `Attachment` entity (file/url on a Card), or `ext` for now?
**Why it matters:** attachments imply file storage/proxying — a real scope bump. Fine to
defer to `ext`/later if you won't use it soon.
**A:** ✅ (b) link-only first-class `Attachment {id, url, title}` + `ATTACHMENTS`
capability; file uploads deferred behind a future `ATTACHMENTS_FILES` cap (no blob
storage in v1). → SPEC Attachment + ports Capability.

---

## 🟡 Q6 — Delete vs archive?

**Question:** Should destructive ops be **hard delete**, **soft archive** (recoverable),
or both? Several backends default to archive/undelete (Focalboard, Trello).
**Why it matters:** shapes the port's delete methods and the native store's schema.
**A:** ✅ Archive-first: `archive`/`unarchive` default (recoverable); `delete` = permanent
purge but **guarded to already-archived cards** (agent-safety). `ARCHIVE` capability,
polyfilled as a flag. → SPEC decision 7. *(Confirm if you meant strict archive-only —
no permanent delete at all.)*

## 🟡 Q7 — Bulk operations in the API?

**Question:** Expose batch create/move (e.g. move many cards at once), or single-item
only for v1?
**Why it matters:** batching helps clients and cuts rate-limit pressure, but complicates
adapters (some backends have no batch endpoint → the proxy loops).
**A:** ✅ (b) Bulk in v1 (create/move/update/archive) at the API/MCP surface, implemented
as a `core/` loop over single-item port methods with **partial-success per-item results**.
Port stays single-item; adapters MAY add native batch later. → SPEC "Canonical operations".

---

## 🟠 Q8 — Which capabilities are must-polyfill for v1?

**Question:** Of WORKFLOW, WIP_LIMITS, RELATIONS, SUBTASKS, CUSTOM_FIELDS, COMMENTS,
MULTI_ASSIGNEE — which MUST work (via polyfill) in v1 even if the backend lacks them, vs.
which can be `unavailable` at first?
**Why it matters:** scopes the first augmentation build. My default: v1 polyfills Tier-1
(WORKFLOW + WIP enforcement) + RELATIONS; defers the rest.
**A:** _..._

## 🟠 Q9 — Write-through encoding: confirm the approach

**Question:** Confirm polyfill data should prefer **write-through into the backend**
(hidden-marker comment / custom field), overlay store only as fallback (SPEC decision 2).
**Why it matters:** it's the call that keeps the backend authoritative (your correction);
just want it explicitly ratified.
**A:** _..._

---

## 🔴 Q10 — Idempotency keys on writes?

**Question:** Expose an idempotency key on kanban-pro's create/write API so client retries
don't duplicate cards (no backend offers this natively)?
**Why it matters:** the research showed zero backends have idempotency keys; if we want
safe retries, the proxy must own dedupe. Small now, painful to retrofit.
**A:** _..._

## 🔴 Q11 — Reconciliation / event surface for v1?

**Question:** For v1, is polling-based reconciliation enough, or do you want kanban-pro to
expose its own unified **webhooks/events** (and/or MCP notifications) to clients from day
one?
**Why it matters:** the unified event surface is powerful but non-trivial; polling-only is
a simpler v1.
**A:** _..._

---

## 🟣 Q12 — Profile config: where and how many?

**Question:** Profiles configured via (a) env vars, (b) a config file, or (c) both? And:
exactly **one active profile** per run, or multiple mounted at once (e.g. `/hermes/...`,
`/jira/...`)?
**Why it matters:** "one active `--profile`" is simplest and matches the original concept;
multi-mount is more flexible but changes routing and the API shape.
**A:** _..._

---

_Add your own questions below as they come up:_
