# kanban-pro — Journal

## 2026-07-03 — Project Initialized

- **Decision:** Name `kanban-pro` — a backend-agnostic kanban proxy.
- **Decision:** Architecture = ports & adapters (hexagonal / anti-corruption layer).
  A canonical kanban model + a `KanbanBackend` port; each backend is a swappable
  adapter. **Rationale:** switching backend must be a config/adapter change, never a
  caller rewrite.
- **Decision:** Stack = Python 3.12+ / FastAPI / Pydantic v2 / httpx, uv-managed,
  ruff + mypy(strict) + pytest. **Rationale:** matches Hermes (Python, first adapter
  target) and the rest of the local tooling.
- **Decision:** Canonical **core + `ext` passthrough** rather than strict LCD or a
  union-of-everything model. **Rationale:** keep the common path clean while not
  discarding backend-specific richness. **Alternatives considered:** strict
  lowest-common-denominator (throws away Jira epics/sprints/custom fields);
  superset model (unusable, every backend implements a fraction).
- **Decision:** Adapters **declare capabilities**; core returns `501 Not Supported`
  instead of leaking opaque backend errors. **Rationale:** not every backend supports
  WIP limits / comments / reordering — make that explicit and queryable.
- **Decision:** Treat as a **personal/self-hosted tool** — skipped hiding AI-tooling
  files from git (Phase 8).
- **Note:** First real work = confirm Hermes kanban's API surface, then write the
  `hermes` adapter. A local/in-memory reference adapter is recommended first as the
  port's proving ground and test fixture.

## 2026-07-03 — Profile-gated surface & workflow-control roadmap

- **Decision:** The exposed API is **gated to the locked-in provider** — kanban-pro
  advertises only the ops the active provider supports, not a fixed superset. It's a
  *normalizing* proxy, not a lowest-common-denominator one. **Rationale:** full
  fidelity per backend. **Tradeoff accepted:** the surface changes per profile, in
  mild tension with "callers never change" — mitigated by the capability check +
  `GET /capabilities`. (Raised as a pushback; Jan chose fidelity over a frozen
  universal surface.)
- **Decision:** Provider chosen via **`--profile`** (`hermes` / `jira` / `default`),
  a named bundle of adapter + settings; env `KANBAN_PRO_PROFILE`. **Rationale:**
  callers pick a profile, not a code path.
- **Decision:** **v1 = Hermes parity** — support Hermes's full kanban method set
  first; other providers implement their subset and declare the rest unsupported.
- **Decision (roadmap):** **Workflow control via allowed transitions** — a later
  phase models permitted column→column transitions as a per-board state machine,
  declared by a `WORKFLOW` capability; `move_card` validated against it. Free-move
  stays the default for backends without a workflow.

---

## 2026-07-03 — Backend API research (15 products) + native store decided next

- **Did:** surveyed 15 kanban/tracker APIs (Jira, Linear, Asana, monday, ClickUp,
  Trello, GitHub Projects, GitLab, Notion, Kanboard, Wekan, Focalboard, Planka,
  Vikunja, Taiga) across auth, methods, workflow, relations, retry, webhooks/heartbeat,
  custom fields. Full writeup: `docs/research/kanban-backends.md`.
- **Decision:** **Native store is the next build**, sequenced after this research
  (before the Hermes adapter). References: Planka (schema + realtime), Vikunja
  (relations + WIP). It doubles as the port's reference/proving-ground.
- **Finding → design:** only Jira enforces workflow transitions server-side →
  kanban-pro's own transition/WIP enforcement is a differentiator, not LCD.
- **Finding → design:** `Column` gains a **category enum** (Linear's
  triage/backlog/unstarted/started/done/canceled) for portable "done-ness."
- **Decision:** card placement is a **`placements[]` set** of `{board_id, column_id,
  position}`, not a single `column_id` — one-card-one-column is violated by Asana/
  ClickUp/monday/GitLab/Jira. Single-board backends + native store use the degenerate
  one-entry case; `move_card` now takes `(board_id, column_id, position)`;
  `MULTI_BOARD_MEMBERSHIP` capability advertises >1 placement. (SPEC decision 4.)
- **Finding → design:** typed relations behind `RELATIONS` cap + `RelationKind` enum
  (modeled on Vikunja, inverse-paired). Expanded the `Capability` enum in `ports/`.
- **Finding → design:** no backend has idempotency keys and retry signals differ
  (Linear returns HTTP 400 not 429) → the proxy core owns normalized retry + create-
  dedupe + reconciliation polling + per-adapter keepalive/refresh (Jira 30-day webhook
  expiry, Asana 8h heartbeat) + a unified event surface for clients.

## Template for future entries

## YYYY-MM-DD — {title}

- **Decision:** {what was decided}
- **Rationale:** {why}
- **Alternatives considered:** {what else was on the table}
