# kanban-pro — internals

**Audience: an AI agent (or human) about to change this code.** This is the map you would
otherwise rebuild by reading every module. It states the layering, the invariants that
must not break, and the traps that have already bitten someone.

Companions: [SPEC.md](../SPEC.md) is *what and why*; [methods.md](methods.md) is the
operation reference; [adapter-structure.md](adapter-structure.md) is the adapter recipe;
[AGENTS.md](../AGENTS.md) is the short list of hard rules. This file is *how it actually
fits together*.

---

## 1. The one diagram that matters

Every interface calls `core/`. Nothing calls an adapter directly. That is what makes the
guards and the audit trail unbypassable.

```
mcp/  ──┐                            (37 tools + 9 kanban:// resources)
api/  ──┼──▶  RecordingBackend       outermost: actor stamp + change-log,
cli/  ──┘         │                  dedupe, claims, attention, list_work
 (🔜)             ▼
            AugmentingBackend        capability dispatch: delegate / polyfill /
                  │                  refuse; Tier-1 WIP + flow enforcement
                  ▼
              adapter               native · memory · hermes
                  │                  (declares only what it does NATIVELY)
                  ▼
               backend
```

Built by `config.build_backend()` (`kanban_pro/config.py:112`), which resolves
`--profile` → factory → `RecordingBackend(AugmentingBackend(adapter, flows=…), ChangeLog,
actor, claims=…, dedupe=…)`.

**Layer coupling you must know about:** `RecordingBackend` reaches *through* to the
`AugmentingBackend` with `isinstance` checks to expose `flows`, `transitions`,
`list_work`, and forced moves (`core/recording.py:68,72,138,315`). It is not a pure
decorator. If you insert a layer between them, those features break.

Similarly, `core/work_report.py` is **not** a backend method — it's a free function that
reaches into `backend.dedupe` and `backend.changelog`, guarded by
`isinstance(backend, RecordingBackend)` (`work_report.py:116-138`). Called with a bare
adapter, it silently records nothing.

---

## 2. What each layer adds

### `RecordingBackend` (`core/recording.py`) — outermost

Delegates everything; records successful writes. Also *hosts* the features that have no
place in the port: claim/lease, attention, `list_work`, dedupe.

The stamp: `_record()` (`recording.py:177`) appends a `ChangeEvent` carrying `actor`,
`entity`, `entity_id`, `op`, `board_id`, and a slim `data` payload with `None` values
stripped.

### `AugmentingBackend` (`core/augment.py`) — capability dispatch

Per capability, decides:

| | |
|---|---|
| **NATIVE** | the adapter declared it → delegate |
| **POLYFILLED** | kanban-pro fulfils it: *Tier-1 enforcement* (WIP, flow — pure rules, **nothing stored**) or *Tier-2 overlay data* (comments, relations, keyed to backend ids) |
| **UNAVAILABLE** | neither → canonical `not_supported` |

`WIP_LIMITS` is always polyfilled. `WORKFLOW` is polyfilled only when `flows is not None`.
`COMMENTS`/`RELATIONS` are polyfilled only when an overlay store exists (`_OVERLAY_CAPS`,
`augment.py:47`). Without an overlay you get Tier-1 enforcement only.

### The adapter

Implements the port. Declares **only what the backend does natively** — honesty here is
what makes `kanban://capabilities` meaningful. `BaseAdapter` (`adapters/_base.py`) stubs
all 25 port methods to raise `NotSupported`, so a thin adapter overrides just what it has.

---

## 3. Invariants — break these and something silently rots

1. **Reads and failed writes are never recorded. Heartbeats are not recorded.**
   `_record()` runs *after* the inner call returns, so an exception skips it. `claim_card`
   and `release_claim` record; `heartbeat_claim` deliberately does not (noise).
2. **A dedupe hit must return the original result and emit no second event.** Retrying a
   create with the same idempotency key is a no-op, not an append.
3. **`ext` patching is a shallow merge; a key set to `None` is deleted.** (`domain.apply_patch`.)
   Concurrent writers each touch their own key without wiping siblings. `clear_attention`
   relies on this: `CardPatch(ext={ATTENTION_EXT_KEY: None})`.
4. **Liveness is derived, never stored.** A card reads as "running" because a live claim
   exists (`Claim.expired` compares `expires_at` at read time). A crashed worker's lease
   expires and its card is reclaimable — there is no stale flag to clean up. The session
   log pointer lives on `ext.session` because it must *outlive* the claim.
5. **Claiming does not move or assign.** The convention "claim → assign yourself → move"
   stays visible in the change-log as separate events.
6. **A forced move is never silent.** `force=true` skips flow and WIP validation, but the
   recorded event carries `forced: true` forever.
7. **Guards live in `core/`, not adapters.** Adapters purge unconditionally;
   `delete_card_guarded` / `delete_board_guarded` / `delete_column_guarded`
   (`core/__init__.py`) enforce archive-first and empty-only. An interface that called an
   adapter directly would bypass them — which is why interfaces never do.
8. **A board flow governs only the columns it names.** A column in no edge is *unmodeled*:
   moves in and out of it stay free. The flow is stored on the board by column id and
   validated at the write (`set_flow` refuses dangling refs; `delete_column` cascades), so
   it cannot drift from the columns — no config typo can freeze or mislead the board.
9. **The work report is current truth, not an append-only log.** History belongs in the
   change-log via `work_report.updated`. Never rewrite the whole `ext["work_report"]` blob
   through `update_card`.
10. **`_`-prefixed keys are reserved metadata, never content** — `_v` (format version),
    and the injected `_claim` / `_last_comment`. A work-report section may not start with
    `_`, and the UI's generic renderer skips them.

---

## 4. The change-log

`core/changelog.py`. Table `changes(seq INTEGER PRIMARY KEY AUTOINCREMENT, ts, actor,
entity, entity_id, op, board_id, data JSON)`. SQLite when a path is given, an in-memory
list otherwise (the `memory` profile). `ChangeEvent.kind` == `f"{entity}.{op}"`.

**Cursors.** `seq` is the cursor. `list_changes(since)` returns `seq > since`, oldest
first. **`since=-1` is a probe**: it returns the current head with *no* events, so a new
consumer joins at the tail instead of replaying all history. Persist the returned cursor
and a consumer that was down resumes exactly where it stopped.

**How `wait_changes` wakes.** `append()` calls `_wake()`, which sets every registered
`asyncio.Event` — so **same-process writes wake waiters instantly**. A write from *another
process* (a different harness, the UI, the dispatcher) never fires this process's event, so
`wait_since` caps each internal wait at `min(2.0, remaining)` and re-queries. That is the
entire explanation for the "~2s for foreign writes" you see in the docs.

**The 23 event kinds** — this is the complete set a consumer can ever see:

```
board.created  board.updated  board.deleted
column.created column.updated column.deleted
card.created   card.updated   card.moved     card.placed   card.unplaced
card.archived  card.unarchived card.deleted
card.claimed   card.released
comment.added  comment.deleted
relation.added relation.deleted
attention.raised attention.cleared
work_report.updated
```

---

## 5. Idempotency (`core/dedupe.py`)

Keyed by `(kind, key)` where kind ∈ `board|column|card|comment|relation|work_report`, so
the same key on a card-create and a comment-add cannot collide. The **serialized original
result** is cached; a retry returns it verbatim, skipping both the write and the event.
TTL is 24h, stored as an absolute `expires_at`; `put()` opportunistically GCs expired rows.

Deliberately absent: a server-generated key when the client omits one. It would differ on
every retry and dedupe nothing — a false comfort.

**Known divergence:** SPEC decision 8 says create/add ops *require* a key. They ship
optional. Documented in CHANGELOG's known limitations; making it required breaks every
existing caller.

---

## 6. Flow engine (`core/flow.py` + `domain.BoardFlow`)

The workflow lives **on the board** — `board.flow` (`domain.BoardFlow`): a map of allowed
column→column moves keyed by column **ID**, stored in the board doc, administered over MCP
(`set_flow` / `set_transitions` / `clear_flow`). Because edges reference the same board's
column ids, a flow can never dangle (contrast the old name-matched `flows.yaml`, retired
2026-07-10 — see JOURNAL). No config file is read at runtime.

Resolution chain for a card (`AugmentingBackend._resolve_flow`), first match wins:

1. `ext["kanban_pro.scheme"] == "free-roam"` — a per-card escape; the card is unrestricted.
2. `ext["kanban_pro.flow"]` — a full inline `{states, transitions}` for this ONE card
   (name-based, may span boards). Wins over the board flow; malformed → board flow, flagged.
3. `board.flow` — the board's own transitions, enforced by column id (the normal path).
4. No board flow (absent / empty `transitions`) → free movement.

The board's own workflow is native for a workflow-owning backend (hermes) — kanban-pro
does not administer that one (`set_flow` → `NotSupported`).

**A column named in no edge is *unmodeled*** — moves in and out of it stay free (a flow
governs only the columns it names). This is how a board keeps an ad-hoc scratch lane
ungoverned while the rest is enforced. `list_transitions` reflects this: from a modeled
lane it offers the lane's explicit edges **plus** every unmodeled column (each is free to
enter).

**Write-side drift guards (the reason flow-on-the-board beats a config file):**
`set_flow` refuses any edge that references a column not on the board; `delete_column`
**cascades**, stripping edges that reference the removed lane. So the flow and the columns
cannot drift apart.

A flow edit is a board-doc write → it emits `board.updated` (no new event kind).
`board.flow.auto_reset_attempts_on_reassign` (default true) is honoured on reassign/re-lane
(`recording.py`). WIP limits live on the *column* (`update_column`), never the flow.

---

## 7. Adapters at a glance

| | native | memory | hermes |
|---|---|---|---|
| Storage | SQLite | dicts | Hermes's own SQLite + CLI |
| Native capabilities | 13 | 13 | 7 |
| Passes `KanbanContract` | yes | yes | **no** (own test module) |
| Use for | the default board | tests, scratch | fronting a Hermes harness |

**native** (`adapters/native.py`): the card is stored as a JSON `doc` — schema is
`cards(id, doc, archived)`. Placements are *not* in the doc; they live in a `placements`
table which is the source of truth for location and is rebuilt on every card write.
`archived` is a real column. `delete_card` purges unconditionally; the archive-first guard
is core's job. Do **not** open it with `":memory:"` — every connection would get its own
database; use a file.

**hermes** (`adapters/hermes/`): **reads go direct to SQLite** (fast, no auth); **writes go
through the `hermes kanban` CLI** so the engine's invariants (event emission, ready
recompute, CAS claims) still hold. Lossy by nature, and the losses are declared:
`move_card` reaches only `done`/`blocked`/`ready` (the three CLI verbs) and raises
`NotSupported` otherwise; there is **no unarchive**; `update_card` accepts assignee changes
only; relations are parent/child only; archived cards are parked on the `done` column
because their real lane is unknowable. Lane→category collapses `todo`+`scheduled`→BACKLOG
and `running`+`blocked`+`review`→STARTED.

**The registry lives in `kanban_pro/config.py`**, not in `adapters/__init__.py`. Factories
take no settings argument today.

### The contract suite

`tests/contract_suite.py` defines `KanbanContract`. An adapter opts in by subclassing it in
a `Test*` class that implements `async def _backend(self)`. It asserts the lifecycle,
comment/relation cascade-on-delete, `NotFound` on unknown ids, `create_card` requiring a
placement, the strict placement/move semantics, and `ext` shallow-merge. Hermes cannot pass
it (no unarchive, restricted moves) and therefore has its own tests — that is expected, not
a gap to fix by weakening the suite.

---

## 8. Domain model

`ColumnCategory` (6): `triage`, `backlog`, `unstarted`, `started`, `done`, `canceled`. This
is what "which column means done?" keys off, since column *names* are free-form per backend.

`RelationKind` (8, inverse-paired): `RELATES`, `BLOCKS`↔`BLOCKED_BY`, `DUPLICATES`,
`PARENT`↔`CHILD`, `PRECEDES`↔`FOLLOWS`. Subtasks are child **cards** via PARENT/CHILD — not
checklists.

`Card.placements[]` is a *set* of `{board_id, column_id, position}`, not one column: a card
can sit on several boards. Single-board backends use the degenerate one-entry case.
`move_card` is strict within-board — it re-columns an existing placement and raises
`NotFound` if the card isn't on that board. The placement set changes only via
`add_placement` / `remove_placement`.

**Checklists are write-once.** `Card.checklists[]` persists and round-trips, but `CardPatch`
has no `checklists` field and no checklist tool exists — nothing can tick an item. Use the
work report's `plan[]` / `checks[]` for a live to-do list.

---

## 9. `ext` — a bag with four independent writers

| Namespace | Owner |
|---|---|
| `work_report`, `kanban_pro.*` (`scheme`, `flow`, `attention`, `copied_from`, `migrated_from`) | kanban-pro |
| `hermes` | the hermes adapter |
| `work`, plus unnamespaced `branch`/`ticket`/`spec`/… | the dispatcher |
| `session` | the worker/harness |
| `_claim`, `_last_comment` | injected at read, never stored |

Because the writers are independent, **there is no single `ext` version**. Each structured
namespace carries its own `_v` *inside itself*, so the version travels with the data when
copied or exported. Missing `_v` means version 1. A namespace written by a **newer** version
than the code understands is **refused for writing**, never silently rewritten. Implemented
today for `work_report` (`WORK_REPORT_VERSION`).

---

## 10. Where to change what

| You want to… | Touch |
|---|---|
| Add an operation everyone gets | `ports/` (the Protocol), then **every** adapter, then `core/`, then `mcp/` |
| Add a core-only convenience (no backend involvement) | `core/` + `mcp/`. Don't grow the port. |
| Add a backend | `adapters/<name>/`, register in `config.py`, subclass `KanbanContract` |
| Add an MCP tool | `mcp/__init__.py`, then **regenerate the tool ref**: `uv run python -m tests.toolref --write` (`tests/test_toolref.py` fails until you do) |
| Change what agents are told on connect | `INSTRUCTIONS` in `mcp/__init__.py` (shipped in the `initialize` result) |
| Change the work-report format | `core/work_report.py`; bump `WORK_REPORT_VERSION` **and** add a migration step in `_migrate_report` in the same change |
| Add a backend-specific field | `ext`, under the backend's namespace. It joins the canonical model only when **≥2 backends** need it. |

Verify before every commit — all four must pass:

```bash
uv run ruff format . && uv run ruff check . && uv run mypy kanban_pro && uv run pytest
```

---

## 11. Traps

- `":memory:"` for `NativeStore` gives every connection its own empty database.
- WIP limits live on the *column* (`update_column`), never in `board.flow`. A flow holds
  only transitions.
- `list_work` does **not** surface attention. An agent that only polls its queue never
  learns a question was raised for it; it must also watch `wait_changes`.
- `clear_attention` is not access-controlled — any actor may clear any flag. Recorded, so
  auditable, but not prevented.
- `--print-config` emits a bare `kanban-pro-mcp`, which is on `PATH` only after
  `uv tool install` — not after `uvx`.
- `docs/methods.md`'s tool list is **hand-maintained** and has drifted before. The machine
  source of truth is the generated block in `examples/skills/*/SKILL.md`. Recount with
  `grep -c '^@mcp\.tool' kanban_pro/mcp/__init__.py`.
- `move_card`'s capability gate differs per adapter: `methods.md` gates it on
  `REORDER_CARDS`, but for a workflow-native backend (hermes) it is gated by `WORKFLOW`.
