# kanban-pro — Journal

## 2026-07-05 — Port expansion: Q14–Q17 implemented

- **Did:** implemented the morning's rulings across port + both adapters + core + MCP
  + tests + methods.md (25 tests green):
  - `add_placement` / `remove_placement` in the port and both store adapters (Q15);
    one placement per board; removing the last placement raises `conflict` (archive
    instead); `add_placement` verifies the target board exists.
  - `move_card` strict within-board (Q16): raises `not_found` if the card isn't on
    `to_board_id`; the silent placement-add is gone. Error message points at
    `add_placement`.
  - `core.delete_board_guarded` / `delete_column_guarded` (Q14, empty-only, live
    cards block; archived leftovers cascade on board delete). MCP delete tools route
    through them. Note: column→board lookup lives in core (the port has none).
  - `domain.apply_patch` — the single implementation of patch semantics incl. the
    Q17 `ext` shallow-merge (`None` removes a key); both adapters' six update methods
    now use it (was: whole-dict replace via `model_copy`).
- MCP surface: 25 tools (+`add_placement`, `remove_placement`).

## 2026-07-05 — Q13–Q17 ruled (delete guards, placements, move, identifiers, ext)

- **Q13:** guarded delete confirmed (archive → then delete); strict archive-only
  rejected — agent boards accumulate garbage, a deliberate two-step purge stays.
- **Q14:** `delete_board`/`delete_column` get **empty-only guards** (refuse while live
  cards remain; archived leftovers cascade on board delete). No board-archive ops —
  one core guard, no new port surface.
- **Q15:** `add_placement`/`remove_placement` join the port **now** (Jan's call over
  deferring) — multi-board membership becomes explicitly editable.
- **Q16 (+ identifier brainstorm):** `move_card` is **strict within-board** — never
  creates a placement (the silent-add quirk goes); cross-board = add/remove_placement.
  **Mount-qualified addressing** decided for multi-mount (`jira/TASK-001`); **no
  lineage-encoded ids** (provenance in link/ext, identity stable). **Human-readable
  card keys** (per-board `PRO-12` style) queued as own TODO item.
- **Q17:** patching `ext` = **shallow merge**, `null` removes a key (replace rejected —
  it would let any lazy client clobber other writers' keys and kanban-pro's own
  `kanban_pro.*` provenance metadata). Pinned in SPEC decision 1 + domain patch-model
  comment; adapters currently replace — fix queued.
- Implementation of Q14–Q17 queued in TODO ("Port expansion"); QUESTIONS.md is empty —
  all Q1–Q17 resolved.

## 2026-07-05 — v0 MVP: MCP server over the store adapters

- **Did:** built the v0 milestone — `kanban_pro/mcp/` (FastMCP, stdio): 23 tools (one
  per port op, schemas generated from the domain models) + resources
  (`kanban://capabilities` with per-capability fulfilment, `boards`, `board/{id}`,
  `card/{id}`). Entry: `kanban-pro-mcp [--profile]` / `python -m kanban_pro.mcp`.
  Verified end-to-end over a real stdio client session.
- **Decision:** seeded `core/` with `delete_card_guarded` (decision 7) — adapters purge
  unconditionally, the guard lives in core so no interface can bypass archive-first.
  MCP dispatches destructive ops through core from day one.
- **Decision:** canonical error classes carry a stable `code` (`not_found`, `conflict`,
  …); the MCP layer surfaces `"{code}: {message}"` tool errors. Tools carry MCP
  annotations (readOnly/destructive/idempotent hints) for harness UX.
- **Decision:** `config.py` registry implemented: profiles `default`→native (SQLite at
  `$KANBAN_PRO_DB` / XDG data dir), `native`, `memory`; `$KANBAN_PRO_PROFILE` selects.
  Profile files + secrets handling deferred to the first remote adapter.
- **Scope note:** idempotency keys (decision 8) intentionally NOT on the v0 tools — a
  required key without the core dedupe cache would be a false promise; both land in v1.
- **Queued (Jan, this session):** flow-YAML **force-transition** override (logged, never
  silent), **good logging** story, **smart Jira caching** (local cache + delta fetch by
  updated-since/hash), **monitoring HTTP server** flag — all in TODO.md.
- **Decision (Jan):** the `jira` adapter will be **MCP-backed** — kanban-pro connects as
  an MCP client to the Atlassian MCP when available, else errors with an install
  suggestion; raw REST only as targeted fallback. Details + caveats in TODO.
- **Research (background agent):** web survey suggests the concept combo (self-hosted
  backend-agnostic kanban proxy + capability polyfill/fulfilment + MCP-first +
  agent-safety semantics) has no direct prior art; nearest neighbors: Unified.to-style
  task APIs (SaaS, normalize-only), MCP mega-aggregators (Composio Rube), agent-native
  boards (Agent Kanban, Flux — worth a look for tool-design ideas), per-backend MCP
  servers (Atlassian/Linear). Differentiators confirmed: augmentation + fulfilment
  reporting, write-through polyfill, archive-first deletion, proxy-owned idempotency.

## 2026-07-05 — Docs unification & milestone rescope (review pass)

- **Did:** full doc↔doc / doc↔code consistency pass (Claude review, applied on Jan's OK).
- **Fixed drift:** README + AGENTS.md still described the superseded gated-surface,
  HTTP-first design → now match SPEC (augmenting proxy, MCP/CLI primary, interfaces call
  `core/`, never adapters). `Fulfilment` docstring in `ports/` corrected to
  write-through-first (was "overlay only"). SPEC's Column model now lists `category`
  (was only in code + research notes). methods.md "decision 7-bulk" mislabel fixed.
- **Decision — milestone rescope (Jan OK'd "basic but usable first"):** Roadmap split
  into v0 (MCP server over the native store — usable, no events/dedupe/augmenting) →
  v1 (Hermes + augmenting Tier 1 + idempotency keys + CLI) → v2 (change-log + pull feed
  + MCP notifications) → later (persistent webhook listener registry, content-hash
  dedupe, Tier 2). **Rationale:** decision 9's full push surface in v1 was
  product-sized plumbing before any real backend worked.
- **Decision:** idempotency keys required on ALL create/add ops (boards/columns
  included) — SPEC decision 8 aligned with methods.md, which already marked them.
- **Decision:** ordering = integer positions + periodic rebalancing (closed the stale
  "open question"; `Placement.position` was already an int).
- **Done earlier, now recorded:** domain models, wired port, `memory` adapter, `native`
  SQLite store (commits 8a0d340, 6ef1013, b9c1a4e) — removed from TODO.
- **Decision — Jira + local cross-board scope:** Jan wants a `jira` adapter alongside
  the local `native` board with cross-board copy/link/transition (pulls multi-mount
  forward). Ruled: **copy-once + provenance link first**, boards transition
  independently; mirrored transitions deferred until the v2 change-log exists; full
  two-way sync stays out of scope. Cross-mount links live in the overlay, keyed
  `(mount, card_id)`. → TODO "Jira adapter + local board".
- **Open → QUESTIONS.md Q13–Q17** (model gaps found in review): strict archive-only vs
  guarded delete; `delete_board`/`delete_column` guards; placement add/remove ops;
  `move_card` source disambiguation with >1 placement; `ext` patch replace-vs-merge.
  Also noted as port gaps: user lookup ops, archived-cards listing (methods.md
  "planned expansion").

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

## 2026-07-03 — Augmenting proxy: polyfill backend gaps (supersedes gated surface)

- **Decision (supersedes decision 2's "gated surface"):** kanban-pro exposes the FULL
  canonical surface and **augments** the backend — delegate what the backend supports,
  **polyfill the rest from its own overlay store**, `not_supported` only when neither is
  possible. `GET /capabilities` reports each capability's `Fulfilment`
  (native/polyfilled/unavailable). Added the `Fulfilment` enum to `ports/`.
- **Rationale:** use everything Hermes offers, fill the gaps ourselves → a uniformly
  rich API regardless of backend richness.
- **Architecture:** `AugmentingBackend = adapter + overlay` decorator over the port; the
  **overlay is the native store** — so that build serves double duty (standalone backend
  + augmentation layer). Adapters stay thin and only declare NATIVE capabilities.
- **Tradeoff accepted:** polyfilled data lives only in kanban-pro → it becomes a
  **partial system of record** (invisible to the backend's own UI; overlay durability +
  orphan-GC reconciliation now matter; polyfilled semantics are shallow). Fine for a
  personal all-through-kanban-pro setup.
- **Polyfill tiers:** T1 pure enforcement (workflow transitions + WIP — no data split;
  where the workflow-control roadmap actually lands), T2 overlay data keyed to backend
  IDs (relations/custom-fields/comments), T3 hard (ordering/multi-board) last.

## 2026-07-03 — Interfaces are MCP-first + shell-first (harness-native)

- **Decision (SPEC decision 5):** primary consumers are agent harnesses, so kanban-pro is
  **MCP-first and shell-first**, not HTTP-first. MCP server exposes every canonical op as a
  tool ("skill") + the active provider's Capability/Fulfilment as a resource → any harness
  (Hermes, Claude Code, Codex, OpenCode, GPT, …) introspects skills and calls them with no
  bespoke integration. CLI covers shell-first harnesses; HTTP is secondary. All three are
  thin layers over one `core/` service — no drift.
- **Rationale:** "every known harness should natively understand the kanban" (user). MCP +
  shell are the universal harness interfaces; a per-harness client list doesn't scale.
- **Resolves Q1** (the Hermes/openclaw/Claude/GPT list generalizes to all harnesses).
  Added `core/`, `mcp/`, `cli/` to the project structure.

## Template for future entries

## YYYY-MM-DD — {title}

- **Decision:** {what was decided}
- **Rationale:** {why}
- **Alternatives considered:** {what else was on the table}
