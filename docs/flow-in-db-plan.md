# Flow-in-DB plan — transitions become board data, administered over MCP

**Status: IMPLEMENTED 2026-07-10** on branch `flow-in-db` (see JOURNAL for the as-built
notes + resolved decisions). This doc is kept as the design record. Decision points that
were open are marked ⚖️ below; their resolutions (D1 keep-unmodeled-free, D2 board.updated,
D3 drop the YAML importer) are recorded in JOURNAL.

Move the workflow (transition rules) out of the `flows-default.yaml` config file and
into the board document in the store, alongside `columns[]` — and expose MCP tools to
administer it, symmetric with `create_column`/`update_column`. The config file is retired
as a runtime source of truth.

## Why (the problem this closes)

kanban-pro has **two stores for one concept** and nothing links them:

| Concept | Lives in | Editable how | Source of truth? |
|---|---|---|---|
| Columns (lanes) | `boards.doc` JSON in the store | MCP (`create_column`/`update_column`) | yes |
| Transitions (flow) | `flows-default.yaml` config file | hand-edit the file + restart | **separate file** |

The flow scheme is a state-machine matched to columns **by name**, at request time, from
a file the store knows nothing about. Consequences, all observed on the live `default`
board (2026-07-10):

- **Drift.** The board has 11 columns; the scheme models 8. `won't do`, `waiting for mr`,
  and `staging` are real lanes — actively used by the swarm (rebaser parks `[REVIEW]`
  cards in `waiting for mr`; `staging` holds live tickets) — that **no scheme governs**.
  Per invariant #8 they are silently free in/out and never appear in `list_transitions`:
  invisible to agents *and* ungated. The worst of both.
- **No coupling, no validation.** `_build_flow` fails fast only on references dangling
  *within the YAML*; it never checks a declared state against a real column. On a fresh
  install the YAML would reference lanes that **don't exist and won't be created** — the
  scheme just silently no-ops (rule 4 free moves). The file cannot be, and is not checked
  against, the source of truth for board structure.
- **No bootstrap.** Columns only ever appear via a Hermes migration or ad-hoc
  `create_column`. There is no "here is a board with sane lanes + a flow that governs
  them" onboarding for a human or an LLM landing on an empty store.

**Root cause:** transitions are not board data. Fix the category error and the drift,
the missing validation, and the missing bootstrap all resolve together.

## Decisions already taken (Jan, 2026-07-10)

1. **Transitions move into the DB**, administered over MCP — *not* left in a file with a
   "seed on first run" half-measure. A seed keeps the file as a special thing that only
   matters at init; the honest model is one store.
2. **Flow lives on the board** (not as reusable store-level named schemes). Each board's
   document holds its own transitions, referencing **that board's column IDs**. Rationale:
   one document, IDs match by construction → drift is structurally impossible. Cross-board
   scheme *reuse* is sacrificed, but the store has exactly one board and zero per-card
   scheme overrides today (verified), so reuse buys nothing now. If multi-board reuse is
   ever needed, a preset (see Phase 3) re-materialises the same edges — reuse becomes a
   bootstrap concern, not a runtime-store concern.

## Migration reality (verified 2026-07-10)

The `default` store is trivial to migrate:

- **1 board** (`default`), no others.
- **0 cards** carry a per-card scheme override (`ext["kanban_pro.scheme"]`).
- **0 cards** carry an inline flow (`ext["kanban_pro.flow"]`).
- The **`docs` scheme is used by nothing** → it does not need a runtime home; it becomes
  an onboarding preset template only.

So the whole live migration is: map the current `default` scheme's edges to column IDs on
the one board, fold in the three unmodelled lanes, write `board.doc.flow`. Done.

## Target data model

```jsonc
board.doc.flow = {
  // out-edges per lane, by column ID. A lane absent from this map ⇒ free in/out
  // (preserves invariant #8; see ⚖️ below). Empty/absent whole map ⇒ free-roam board.
  "transitions": {
    "default:triage": ["default:todo", "default:ready", "default:won't do"],
    "default:ready":  ["default:running", "default:todo", "default:blocked", "default:won't do"]
    // …
  }
}
```

- References are **column IDs in the same document** → a transition cannot dangle. The
  write-time validator (below) rejects any edge referencing a column not on the board.
- The reserved **`free-roam`** and per-card **inline flow** (`ext["kanban_pro.flow"]`)
  features stay — the resolution chain simplifies to: inline card flow → board flow →
  backend-native workflow → free.

### ⚖️ D1 — unmodelled-lane semantics

Keep invariant #8 (*a lane not in the flow map is free in/out*) or flip it (*not in the
map ⇒ unreachable*).
- **Recommend KEEP.** Non-breaking, never strands a card, matches today. Cost: drift-of-
  omission still possible (add a column, forget its edges → silently free). Mitigated by
  the Phase 3 `doctor`-lite warning, not by stranding cards.

### ⚖️ D2 — event kind for flow edits

Flow lives in `board.doc`, so a flow edit is a board mutation.
- **Recommend: emit `board.updated`** (already one of the 23 kinds) — no new consumer
  contract. Alternative: add a 24th kind `flow.updated` for a precise signal, at the cost
  of every `list_changes` consumer having to learn it. Start coarse; add the precise kind
  only if a consumer needs to react to flow changes specifically.

## MCP tool surface (symmetric with columns)

| Tool | Purpose |
|---|---|
| `set_transitions(board_id, from_column_id, to_column_ids)` | set the out-edges for one lane |
| `set_flow(board_id, transitions)` | replace the whole map (presets / bootstrap) |
| `clear_flow(board_id)` | drop the flow → free-roam board |
| `init_board(preset=…)` | onboarding (Phase 3) |

- Every write **validates all referenced IDs exist on the board** (fail-fast, in-band,
  canonical `Conflict`/`Invalid`).
- **`delete_column` cascades:** removing a lane strips/refuses edges that reference it, so
  the flow can never point at a dead column. This is the drift-proofing, enforced at the
  write path — the same place the guards and audit trail already live (`core/`).
- `list_transitions` and `_check_flow` (`core/augment.py`) read `board.doc.flow` instead
  of a constructor-injected `FlowConfig`.

## Capability note

`WORKFLOW` is polyfilled today only when `flows is not None` (a global, from
`config.py:130 load_flows`). New gate: **polyfilled when the board carries a flow.** For a
workflow-native backend (hermes adapter) the backend's own workflow still wins — unchanged.

## Phased execution

**Phase 1 — model + read path.**
Add `flow` to the `Board` domain model and `board.doc`. Teach `AugmentingBackend`
(`_resolve_flow`/`_check_flow`/`transitions`) to read it. Keep inline-card-flow override.
Feature-flag or dual-read (YAML fallback) so nothing breaks mid-migration.

**Phase 2 — MCP admin tools + write-time validation + delete_column cascade.**
Ship `set_transitions`/`set_flow`/`clear_flow`, the ID-existence validator, and the
cascade. Tests: illegal edge rejected, cascade on column delete, forced-move still stamps
`forced:true`, `board.updated` emitted.

**Phase 3 — onboarding + retire YAML.**
`init_board(preset=…)`:
- **blank** — board + columns, no flow (free-roam).
- **template** — a built-in preset: `agent-lifecycle` (today's board), `simple-kanban`
  (todo/doing/done), `docs` (todo→ready→running→done, no review gate). Presets are
  code/data applied via `set_flow` — no runtime config file.
- **import** — surface the existing `migrate.py` (Hermes / another store) as a first-class
  onboarding path.
Then remove `load_flows`/`FlowConfig`/`KANBAN_PRO_FLOWS` from the runtime.
⚖️ **D3** — keep a thin one-shot YAML importer behind `init_board(preset=import-file)` for
git-versionable presets, or delete entirely. **Recommend keep the importer** (cheap, and
some users will want their workflow in version control) but never read it at runtime.

**Phase 0 folds into Phase 1's migration** — there is no separate "fix the 3 lanes in the
YAML" step; the one-shot migration writes the full 11-lane `board.doc.flow` directly.

## Proposed edges for the migration (confirm before writing)

Existing `default` edges, unchanged, mapped to IDs; plus the three newcomers:

| Lane | Out-edges (→) |
|---|---|
| triage | todo, ready, **won't do** |
| todo | ready, scheduled, triage, **won't do** |
| scheduled | ready, todo, **won't do** |
| ready | running, todo, blocked, **won't do** |
| running | done, review, blocked, ready, **waiting for mr**, **won't do** |
| blocked | ready, todo, running, **won't do** |
| review | done, running, ready, **waiting for mr**, **won't do** |
| **waiting for mr** | done, blocked, ready |
| **staging** | ready, todo, running, review |
| **won't do** | triage, todo *(reopen)* |
| done | ready *(reopen)* |

Into `staging`: from todo, ready, running, review. (Staging was historically the "ad-hoc"
lane; these edges make it a governed holding lane — confirm this matches how it's used, or
leave it free per D1.)

## Docs to update when this lands

- `docs/internals.md` §6 (Flow engine) + invariant #8 + the event-kind set (if D2 adds a
  kind).
- `SPEC.md` (flow is board data now) and `docs/methods.md` reserved-namespace table
  (`kanban_pro.flow` per-card stays; board-level `flow` is a first-class board field).
- `AGENTS.md` "Before changing code, read internals.md" trap list — the `flows.yaml`
  entries go away.
- `TODO.md` — remove the item pointing here.
