# Kanban backend research — survey for kanban-pro's canonical model

**Date:** 2026-07-03 · **Method:** web research across official API docs, 15 products in
3 clusters. This grounds the canonical model, the port, the `Capability` set, and the
retry/heartbeat concerns. Read alongside [SPEC.md](../../SPEC.md).

**Products surveyed:** Jira, Linear, Asana, monday.com, ClickUp (commercial) · Trello,
GitHub Projects v2, GitLab boards, Notion (mainstream) · Kanboard, Wekan, Focalboard,
Planka, Vikunja, Taiga (self-hosted OSS).

---

## TL;DR — the six findings that shape the design

1. **Only Jira enforces a real workflow state machine** (server-side allowed
   transitions + conditions/validators). Every other product is *free-form* status/
   field assignment. → kanban-pro's own native store enforcing transitions + WIP is a
   genuine **differentiator**, not a lowest-common-denominator feature. Model workflow
   as an optional `WORKFLOW` capability; `move_card` degrades to a plain field-set on
   backends that don't have it.
2. **"Column" is modeled ~9 different ways** (real objects / labels / single-select
   field values / status-property groups / per-team workflow states / view sections).
   The canonical `Column` needs a **category enum** (borrow Linear's:
   triage/backlog/unstarted/started/done/canceled) so "which column means done?"
   survives translation.
3. **One-card-one-column is violated** by Asana, ClickUp, monday, GitLab, Jira — a card
   can sit in several boards/lists at once. Canonical placement may need to be a
   **(board → column) membership set**, not a single pointer. This is an open fork.
4. **Typed dependencies (blocks/blocked-by) are common but not universal.** First-class
   in Jira, Linear, GitLab, Asana, monday, ClickUp, Kanboard, Vikunja. Absent/convention
   in Trello, Notion, GitHub (parent/child only), Wekan, Focalboard, Planka, Taiga
   (boolean, not an edge). → typed relations behind a `RELATIONS` capability; Vikunja's
   `relation_kind` enum is the model to copy.
5. **Retry signaling is wildly heterogeneous and no backend offers idempotency keys.**
   The proxy must own a normalized retry layer *and* dedupe for create-retries.
6. **Webhooks everywhere, delivery guarantees weak; true heartbeats almost nonexistent.**
   Inbound events are *hints* — reconciliation polling is mandatory. kanban-pro should
   expose ONE unified webhook/heartbeat to its clients and hide the per-backend mess.

---

## A. API type & auth (the "methods they expose", part 1)

| Product | API | Auth |
|---|---|---|
| Jira Cloud | REST v3 (ADF bodies); no public GraphQL for issues | API token (Basic) / OAuth2 3LO; users = accountId only |
| Linear | **GraphQL only** | API key (raw, no `Bearer`) / OAuth2 |
| Asana | REST | PAT / OAuth2 / service accounts (Bearer) |
| monday.com | **GraphQL only**, date-versioned | API token / OAuth2 |
| ClickUp | REST v2 | PAT (`pk_…`) / OAuth2 |
| Trello | REST | key+token pair / OAuth1 |
| GitHub Projects v2 | **GraphQL only** (Issues also REST) | PAT / App token |
| GitLab | REST v4 (+GraphQL) | PAT header / OAuth2 |
| Notion | REST (versioned header) | integration token / OAuth2 |
| Kanboard | **JSON-RPC 2.0** | app token / user token (Basic) |
| Wekan | REST (Meteor) | Bearer (login → token) |
| Focalboard | REST (`/api/v2`) | Bearer / PAT |
| Planka | REST (Sails) | JWT (no long-lived PAT yet) |
| Vikunja | REST v1/v2 | scoped API token / JWT |
| Taiga | REST v1 | Bearer token / Application token |

**Implication:** adapters span REST, GraphQL, and JSON-RPC. The port must be transport-
agnostic (it already is — it's a Python Protocol). Auth is per-adapter config inside
each profile.

## B. Methods / resources exposed (part 2) & the "move" verb

Every product has boards/lists/cards + comments + labels + assignees in some form.
The **move operation is different everywhere** — the single biggest translation cost:

| Product | How you move a card between columns |
|---|---|
| Trello | `PUT /cards/{id}` `idList` + `pos` |
| Jira | `POST /issue/{k}/transitions` (a *transition*, not a field set) |
| Linear | `issueUpdate(stateId)` (field assignment) |
| Asana | `POST /sections/{gid}/addTask` |
| monday | `change_column_value` (status) **or** `move_item_to_group` (two axes) |
| ClickUp | `PUT /task/{id}` `{status: "<name>"}` (name string, scoped to list) |
| GitHub | `updateProjectV2ItemFieldValue` (single-select option id) |
| GitLab | edit the issue's **labels** |
| Notion | `PATCH /pages/{id}` status property |
| Kanboard | `moveTaskPosition(project,task,column,position,swimlane)` |
| Wekan | `PUT` card with new `listId`/`swimlaneId` |
| Planka | `PATCH /cards/{id}` `listId`+`position` (or list `sort`) |
| Vikunja | assign to a bucket + position |
| Focalboard | PATCH card property + PATCH view block's `cardOrder` |

**Implication:** `move_card(card_id, to_column_id, position)` in the port is the right
canonical verb; each adapter owns the translation. Ordering is a first-class concern
(see ordering note below).

## C. Workflow control — transitions (your "how do they control workflow")

| Product | Workflow model | Server-enforced transitions? | WIP limits |
|---|---|---|---|
| **Jira** | **Full state machine** (statuses + transitions + conditions/validators) | **YES** — only valid transitions allowed; `GET /transitions` lists legal moves | board column min/max, **advisory only** |
| Linear | `WorkflowState` per team, with a **`type` enum** | No — free `stateId` set | unknown |
| Asana | sections / enum custom field | No | none |
| monday | groups + status columns (2 axes) | No | none |
| ClickUp | statuses scoped to list/folder/space, `type` enum | No | none |
| Trello / GitHub / GitLab / Notion | list / field / label / property | No | Trello/GitLab UI-only |
| Kanboard/Wekan/Planka/Focalboard | columns/buckets | No | soft UI only (Vikunja bucket `limit` = server-stored) |
| Taiga | **configurable status sets per item type** | No (any→any) | none |
| **Vikunja** | buckets + `done` flag | No | **bucket `limit` server-enforced** |

**Implication (big one):** kanban-pro's `WORKFLOW` capability means "this backend can
express allowed transitions." Today only Jira maps to it (query legal moves +
execute a transition, which may require fields). For everyone else, `move_card` is a
free field set. **kanban-pro's native store can enforce transitions + WIP itself** —
the roadmap workflow-control feature is thus a real feature nobody but Jira offers.
Linear's `WorkflowState.type` enum (triage/backlog/unstarted/started/completed/
canceled) is the best canonical **column category** anchor.

## D. Relations / dependencies (your "do they allow relations")

| Product | Typed links | Blocks / blocked-by | Parent/child (subtask/epic) | Cross-board |
|---|---|---|---|---|
| Jira | `issueLink` types (custom) | ✅ | via `parent` field (not a link) | ✅ |
| Linear | `issueRelation` | ✅ (+ duplicate/related) | `parentId` | team-scoped |
| GitLab | `issue links` | ✅ (`blocks`/`is_blocked_by`) | epics (Premium) | ✅ |
| Asana | dependencies/dependents | ✅ | subtasks + `setParent` | multi-project |
| monday | dependency + board_relation cols | ✅ | subitems (5 levels) | ✅ |
| ClickUp | dependency + link | ✅ | `parent` | multi-list |
| **Kanboard** | **11 typed link types** | ✅ | parent/child link | via links |
| **Vikunja** | **`relation_kind` enum** | ✅ (+precedes/follows/duplicates) | subtask=relation | ✅ |
| Taiga | structural only | ❌ (`is_blocked` boolean, not an edge) | Epic→Story→Task FK | ❌ |
| Trello | ❌ (attach card URL) | ❌ | checklists ≈ subtasks | manual |
| Notion | user-defined `relation` prop | ❌ (untyped) | self-relation | ✅ |
| GitHub | sub-issues (parent/child) | ❌ (limited) | ✅ sub-issues | same-owner |
| Wekan/Focalboard/Planka | `parentId` at most | ❌ | containment only | ❌/limited |

**Implication:** canonical typed-relation edge with a small enum —
`{relates, blocks, blocked_by, duplicates, parent, child, precedes, follows}` — gated
behind a `RELATIONS` capability. **Model it after Vikunja's `relation_kind`** (inverse-
paired edges). Keep parent/child as relation kinds too, but note many backends model
hierarchy via a `parent` field rather than a link — the adapter reconciles.

## E. Rate limiting & retry (your "retry strategy")

| Product | Limit model | 429 signal | `Retry-After`? | Pagination |
|---|---|---|---|---|
| Jira | points/hr + burst/s + per-issue | 429 `RateLimit-Reason` | ✅ | offset → cursor (migrating) |
| Linear | request + complexity/hr | **HTTP 400** w/ `RATELIMITED` in body | ❌ (headers only) | Relay cursor |
| Asana | RPM + cost-based | 429 | ✅ | offset-token |
| monday | complexity budget (5M/query) | error `retry_in_seconds` in body | ✅ (body field) | cursor (`items_page`) |
| ClickUp | RPM per plan | 429 `X-RateLimit-Reset` | ❌ (reset ts only) | page-based (from 0) |
| Trello | 300/10s key, 100/10s token | 429 | ❌ | inline / `before`/`since` |
| GitHub | GraphQL point budget 5k/hr | 403/200 `retry-after` on secondary | partial | cursor (required) |
| GitLab | req/min | 429 `Retry-After` (omitted on some APIs) | ⚠️ partial | offset or keyset |
| Notion | ~3 req/s + workspace | 429 (+529 overload) | ✅ | cursor |
| OSS (Kanboard/Wekan/Focalboard/Planka) | usually **none** | — | — | mostly none |
| Vikunja | rate-limited → 429 | 429 | ⚠️ | header-based |
| Taiga | throttle → 429 | 429 | ❌ | header-based |

**Implications:**
- **Normalize retry in the core.** Each adapter declares *where* the rate-limit signal
  lives (status code / header name / body field) + how to read the wait hint; the core
  runs exponential backoff + jitter honoring that hint. Linear is the trap: it returns
  **HTTP 400, not 429** — the adapter must inspect the GraphQL error body.
- **No backend offers idempotency keys** (Jira explicitly none). kanban-pro must own
  **create-dedupe**: expose an idempotency key on *its* write API, keep a short-lived
  request→result cache, so a client retry doesn't create duplicate cards even though the
  backend can't guarantee it.
- Pagination style is per-adapter (cursor / offset / keyset / none) — the port returns
  full canonical lists; adapters page internally.

## F. Realtime / webhooks / heartbeat (your "keepalive heartbeats")

| Product | Push mechanism | Delivery guarantee | Heartbeat / liveness | Handshake / auth |
|---|---|---|---|---|
| Jira | webhooks | best-effort, no ordering | **webhook expires in 30 days → must refresh** | — |
| Linear | webhooks | 3 retries (1m/1h/6h) | none | HMAC-SHA256 + 60s replay window |
| **Asana** | webhooks | **at-most-once (can drop)** | **~8h heartbeats; auto-delete after 24h fail** | echo `X-Hook-Secret`, HMAC |
| monday | webhooks | 1/min for 30min | none | echo `challenge` |
| ClickUp | webhooks | health-tracked, auto-suspend | none | per-webhook secret sig |
| Trello | webhooks | 3 retries 30/60/120s | callback HEAD 200 = liveness | HMAC-SHA1 |
| GitHub | webhooks | at-least-once | none | ETag/If-Modified-Since poll fallback |
| GitLab | webhooks | retried | none | `X-Gitlab-Token` |
| Notion | webhooks (2025+) | aggregated/delayed | none | echo `verification_token`, HMAC-256 |
| **Focalboard** | **WebSocket (core)** | live | Socket transport ping | WS subscribe |
| **Planka** | **WebSocket (Socket.IO)** | live | engine.io ping-pong | — |
| Wekan | Meteor DDP WS + outbound webhooks | — | — | — |
| Vikunja | webhooks | **single delivery, NO retry** | none | HMAC-256 |
| Taiga | webhooks | sync/async | none | HMAC-SHA1 |

**Implications:**
- **kanban-pro exposes ONE unified event/heartbeat interface to its clients**, hiding
  the zoo above. Clients subscribe to kanban-pro; kanban-pro's adapter layer sources
  changes however the backend allows (webhook ingest, WS, or polling).
- Treat every inbound webhook as a **hint, then re-fetch** — delivery is weak
  (Asana drops, Vikunja never retries, none guarantee ordering). **Reconciliation
  polling is a mandatory, always-on adapter concern**, not optional.
- **Liveness/refresh is a per-adapter chore**: Jira webhooks die at 30 days (auto-
  refresh), Asana monitors an 8h heartbeat and deletes dead endpoints. Model a
  `keepalive()`/`refresh()` hook adapters implement (no-op where not needed).
- A `REALTIME`/`WEBHOOKS` capability tells clients whether the active backend can push
  at all, or whether kanban-pro is polling on their behalf.

## G. Custom fields → validates the `ext` passthrough

Pervasive and tenant-specific in Jira (opaque `customfield_XXXXX`), Asana, monday
(per-type JSON), ClickUp (separate set-value endpoint), Notion (arbitrary schema),
Taiga, Wekan. Essentially **none** in Linear. This strongly validates SPEC decision 1:
a small canonical core + a typed `ext` passthrough per entity, not a universal schema.

---

## Recommendations for kanban-pro (grounded)

**Canonical model additions beyond the SPEC skeleton:**
- `Column.category`: enum `{triage, backlog, unstarted, started, done, canceled}`
  (from Linear) so cross-backend "done-ness" is portable.
- Card placement: decide the **multi-membership fork** — single `column_id` (simple,
  what the native store enforces) vs. a `placements: [{board_id, column_id, position}]`
  set (needed to faithfully represent Asana/ClickUp/monday/Jira). Recommendation: model
  the set, but let single-board backends + the native store use the degenerate
  one-entry case.
- `Relation{kind, from_card, to_card}` with the Vikunja-style inverse-paired enum.
- Ordering: use **rebalanced integer/lexo-rank ordering**, not naive floats (Planka &
  Trello float positions need periodic rebalancing — a documented pain).

**`Capability` set (expand the enum):** `WORKFLOW` (allowed transitions), `WIP_LIMITS`,
`RELATIONS`, `SUBTASKS`, `COMMENTS`, `LABELS`, `MULTI_ASSIGNEE`, `CUSTOM_FIELDS`,
`REORDER_COLUMNS`, `REORDER_CARDS`, `MULTI_BOARD_MEMBERSHIP`, `WEBHOOKS`/`REALTIME`.

**Core cross-cutting layers the proxy owns (not any single adapter):**
- Normalized **retry/backoff** driven by per-adapter rate-limit descriptors.
- **Idempotency/dedupe** for create writes (no backend provides it).
- **Reconciliation polling + a unified event/heartbeat** surface for clients.
- Per-adapter **keepalive/refresh** hook (Jira 30-day, Asana 8h).

**Native-store design references** (for the DECIDED next build — see TODO):
- **Planka** — cleanest relational Project→Board→List→Card + Socket.IO realtime; adopt
  its schema shape and "record change → broadcast CRUD event" model.
- **Vikunja** — best typed-relation + WIP (`bucket.limit`) + honest webhook semantics.
- **Kanboard** — richest link-type vocabulary (11 types) if we want depth.
- **Avoid** Focalboard's view-relative positions (a card must have one intrinsic
  column+position) and Taiga's four-item-type split (too heavy to canonicalize).
- Because it's our store, kanban-pro can natively enforce **transitions + WIP** — the
  differentiator nobody but Jira offers.
