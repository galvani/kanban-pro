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

## Template for future entries

## YYYY-MM-DD — {title}

- **Decision:** {what was decided}
- **Rationale:** {why}
- **Alternatives considered:** {what else was on the table}
