# kanban-pro — configuration guide

Everything kanban-pro reads at startup, what the defaults are, and the three things most
people actually want to change: **which backend**, **who you are**, and **what moves are
legal**.

There is no config file you *must* write. Run `kanban-pro-mcp` with no arguments and you
get a working board: the native SQLite store, free movement between columns, every write
attributed to `unknown`. The sections below are how you improve on that.

---

## 1. Startup settings (flags and env vars)

Every setting has a flag, an env var, or both. Flags win over env vars.

| What | Flag | Env var | Default |
|---|---|---|---|
| Which backend | `--profile <name>` | `KANBAN_PRO_PROFILE` | `default` (native store) |
| Who is writing | `--actor <kind:name>` | `KANBAN_PRO_ACTOR` | `unknown` |
| Where the board lives | — | `KANBAN_PRO_DB` | `~/.local/share/kanban-pro/kanban.db` |

`~/.local/share` follows `XDG_DATA_HOME`, and `~/.config` follows `XDG_CONFIG_HOME`, if
you set them.

### Profiles — which backend the board lives in

A **profile** bundles an adapter with its settings. One profile is active per process;
switching backend is a restart, not a code change.

| Profile | Backend | Use it for |
|---|---|---|
| `default` / `native` | SQLite at `KANBAN_PRO_DB` | the normal case — kanban-pro *is* the board |
| `memory` | in-process, ephemeral | tests, scratch boards; **everything is lost on exit** |
| `hermes` | the Hermes harness's own board | reading/driving an existing Hermes board |

```bash
uv run kanban-pro-mcp                      # default profile
uv run kanban-pro-mcp --profile memory     # throwaway board
KANBAN_PRO_PROFILE=hermes uv run kanban-pro-mcp
```

An unknown profile name fails immediately and lists the valid ones.

### Actors — who did what

Every write is stamped with the actor of the connection that made it, and that stamp
lands in the append-only change-log. This is the whole audit trail, so it's worth
setting.

```bash
uv run kanban-pro-mcp --actor agent:claude-code
uv run kanban-pro-ui  --actor human:jan
```

The convention is `kind:name` — `agent:claude-code`, `agent:hermes-engineer`,
`human:jan`. Actors are free-form strings, not user accounts: nothing needs to be
registered before it can act. If you skip this, every write is attributed to `unknown`
and `list_work` can't tell which cards are yours.

Each harness registers its own copy of the server with its own `--actor`, and they share
the SQLite store safely.

### Where state is kept

Beside the board itself, kanban-pro keeps three per-profile SQLite files in the same data
directory — `changelog-<profile>.db`, `claims-<profile>.db`, and `dedupe-<profile>.db`
(the change-log, live claims, and the idempotency cache). They're created on demand. The
`memory` profile keeps all three in memory and discards them on exit.

---

## 2. Card ids — what a card is called (`board.id_scheme`)

A card id defaults to a 32-hex uuid, which is a lot to read back or paste into a tool
call. The shape is a **board setting**, not a server flag: it's chosen when the board is
created and lives on the board, like its flow.

| `id_scheme` | Example id | What it is |
|---|---|---|
| unset / `uuid` | `9f3c1a…` (32 chars) | the default |
| `short[:N]` | `k7f3q9xwmb` | N random chars, `N=4..32`, default 10 |
| `prefix:KAN[:N]` | `KAN-k7f3q9` | the same, behind a prefix; `N` default 6 |
| `seq:KAN` | `KAN-1`, `KAN-2` | a per-board counter — shortest, and the only ordered one |

Set it wherever a board is set up — MCP, the HTTP API, or the shell:

```jsonc
init_board(board_id="ops", preset="simple-kanban", id_scheme="seq:OPS")
create_board(board={"name": "notes", "id_scheme": "short:8"})
update_board("ops", {"id_scheme": "prefix:OPS:6"})   // from here on
```

Each board counts on its own, so `KAN-1` and `OPS-1` coexist. Changing a live board's
scheme affects only cards created *afterwards* — existing ids are never rewritten, and
they keep working, because an id is an opaque string.

The random schemes draw from a Crockford-style base32 alphabet with `i`, `l`, `o` and `u`
removed, so an id survives being read aloud or re-typed. Eight characters is 40 bits — far
more than one board needs — and a collision can't corrupt anything anyway: `create_card`
refuses an id that already exists rather than overwriting the card holding it.

The id is minted by the **store**, which is what lets a `seq:` counter exist at all (it's
a `sequences` table, so it survives restarts, and it skips numbers already taken — cards
migrated in carrying `KAN-1` can't have their id reissued). The `native` and `memory`
profiles do this; a remote backend (`hermes`) mints ids its own way and ignores the scheme.

---

## 3. WIP limits

A WIP limit lives **on the column**, not in the board flow. Set it with `update_column`
(or in the UI), and every subsequent move into a full column is rejected:

```
agent> move_card PRO-12 → doing
  → conflict: column 'doing' is at its WIP limit (3)
```

It's enforced by kanban-pro itself, over any backend — including backends that have no
concept of a WIP limit. Nothing is stored in the backend to make it work.

> A WIP limit is a property of the column, never of the board flow — the flow governs
> *which moves are legal*, the column governs *how many cards may sit in it*. Set limits
> on columns.

---

## 4. Workflow rules (the board flow)

By default a card can move anywhere. A **flow** turns the board into a state machine: it
names which column→column transitions are legal. The flow is **board data**, not a config
file — it lives on `board.flow` and is administered over MCP. There is no `flows.yaml` and
no `KANBAN_PRO_FLOWS` env var (both retired); a board carries its own flow, keyed by that
board's own column ids.

### The shape

`board.flow` is `{transitions: {from_column_id: [to_column_id, …]}, auto_reset_attempts_on_reassign}`.
Each edge references a real column id **on this same board**, so a flow can never dangle
(unlike a name-matched external scheme). **No flow, or an empty `transitions` → the whole
board is free-roam.** The flow engine is opt-in; it never appears uninvited.

### Administering it (over MCP)

| Tool | Effect |
|---|---|
| `set_flow(board_id, transitions)` | replace the whole board flow. Every referenced column id must exist on the board — a dangling ref is refused. `{}` clears it. |
| `set_transitions(board_id, from_column_id, to_column_ids)` | set just one lane's out-edges, leaving the rest. `[]` clears that lane. |
| `clear_flow(board_id)` | drop the flow entirely → free-roam. |

A flow edit emits a `board.updated` event. A **new** board is usually seeded with a flow
rather than built edge-by-edge: `init_board(board_id, name?, preset=…)` materialises
columns + a matching flow together (built as one unit, so they can't dangle). Presets:
`blank` (no columns, free-roam), `simple-kanban` (todo/doing/done), `docs`
(todo/ready/running/done, no review gate), `agent-lifecycle` (the Hermes swarm lanes —
`triage/ready/running/blocked/review/…`, the shape the shared board runs today). To
onboard by IMPORT instead (from Hermes or another store), use the `kanban-pro-migrate`
CLI, not `init_board`.

### How a card picks its flow

Resolution stops at the first match:

1. **`ext["kanban_pro.scheme"] == "free-roam"`** — the reserved escape frees this one card,
   even on a governed board. (Named schemes are gone; `"free-roam"` is the only meaningful
   value now.)
2. **`ext["kanban_pro.flow"]`** — a full inline `{states, transitions}` flow (name-based)
   for this one card. Beats the board flow, and is enforced even on a **flowless** board.
   Malformed → falls back to the board flow with a warning.
3. **The board's own `board.flow`** (by column id), if set.
4. **The backend's own workflow**, if it has one (Hermes does); else free movement.

A column that appears in no edge is *unmodeled*: moves in and out of it stay free. That's
deliberate — you can add an ad-hoc lane without rewriting the flow.

### Asking, and overriding

An agent never has to guess. `list_transitions(card_id)` answers "where can this card go
right now, and under which scheme" — and `list_work` inlines the same answer on every
card it returns, so a worker sees its legal moves without a second call.

An illegal move is refused with a conflict. Passing `force=true` performs it anyway and
stamps `forced: true` on the `card.moved` event. Overrides are always allowed and never
silent — the audit trail is the safeguard, not a lock.

```
agent> move_card PRO-12 → done
  → conflict: scheme 'default' does not allow todo -> done; use force=true to override
agent> move_card PRO-12 → done, force=true
  → Card moved. The event carries forced=true.
```

### Other keys

`board.flow.auto_reset_attempts_on_reassign: true` (per board, default true) clears a
card's attempt counter when it changes assignee or lane, so a retried card starts fresh.
WIP limits live on the column (§3), never on the flow.

---

## 5. The attention flag — asking for a decision instead of guessing

An agent that hits a decision it isn't entitled to make should neither guess nor die
quietly. It raises an attention flag, naming **who** should answer:

```
raise_attention(card_id, reason, for_actor=None)
clear_attention(card_id, resolution=None)
```

Three things happen on a raise. The card gets
`ext["kanban_pro.attention"] = {reason, raised_by, for}`. The board tile shows the flag.
And an `attention.raised` event lands on the change-feed carrying the reason **and the
target**. That last part is what makes it *routable* — a listener reads the target and
delivers the question wherever the answerer actually is.

### It is not only for humans

`for_actor` is a free-form actor string, exactly like the identity a connection declares.
So it addresses anyone in the system:

| `for_actor` | Meaning |
|---|---|
| `human:jan` | a person must decide — a listener DMs them |
| `agent:architect` | **another agent** must decide — a design call the coder shouldn't make |
| `agent:reviewer` | hand a question up the pipeline: "is this scope creep?" |
| `None` | anyone watching; nobody in particular is on the hook |

Agent-to-agent attention is the point of the `for_actor` field, not an afterthought. A
worker that discovers the ticket is ambiguous can bounce the decision to the agent whose
job that is, keep its card, and go on doing something else — instead of inventing an
answer that a reviewer discovers three steps later. Combined with `questions[]` in the
work report, this is how a fleet escalates within itself and only reaches you when no
agent is entitled to decide.

### How the target finds out

This matters, and it's easy to get wrong:

- **The change-feed is the delivery mechanism.** Watch it (§6) and filter for
  `attention.raised` events whose `for_actor` is you. That is how an agent — or a
  notifier acting for a human — learns a question is waiting. `wait_changes` blocks until
  one arrives.
- **`list_work` does NOT surface attention.** An agent that only calls `list_work` will
  never see a question raised for it. The work queue answers "what may I work on"; the
  feed answers "who needs me". A long-running worker should watch both.
- **`clear_attention` is not access-controlled.** Any actor may clear any flag; the
  clearing actor is recorded in the change-log, so this is auditable rather than
  prevented. Don't clear a flag raised for someone else.

### Attention is the signal, not the content

The question itself belongs in the card's work report under `questions[]` — that's what
`answer_work_report_question` resolves (mirroring the answer as a comment), and what the
UI renders as an answerable prompt. Raise attention *because* a question is waiting;
don't smuggle the question into the reason string, where nothing can resolve it and
nothing can tell whether it was ever answered. See
[methods.md](methods.md#work-reports-core-convenience--not-port-ops).

The intended shape of an escalation:

```python
await record_work_report(card_id, "questions", {          # the content, resolvable
    "id": "q1", "status": "open",
    "text": "Ticket says 'cache it' — per-request or per-user? Affects the schema.",
})
await raise_attention(card_id, "blocked on q1 (cache scope)", "agent:architect")
# ... the architect's listener wakes on attention.raised, answers, clears the flag:
await answer_work_report_question(card_id, "q1", "Per-user. Key on session id.")
await clear_attention(card_id, "answered q1")
```

---

## 6. Listeners — getting events out of the board

Every successful write is appended to the change-log with its actor, a monotonic `seq`,
and a slim payload. A **listener** is anything that reads that log from a cursor it
stores. There's no broker, no registration, no subscription to set up.

Two ways to read it:

| | When to use |
|---|---|
| `list_changes(since, limit)` | catch-up, audit, sync. Returns immediately with whatever is there. |
| `wait_changes(since, timeout_seconds, limit)` | live delivery. **Blocks** until events land, then returns them. |

`wait_changes` is the one to build on. It returns the instant a write happens through the
same server (~2s for writes from another process), so a consumer waits instead of
spinning. Call it once with `since=-1` to learn the current cursor **without replaying
history**, then loop on the cursor it gives back:

```python
cursor = (await wait_changes(since=-1)).cursor      # baseline: no backlog, no DMs
while True:
    result = await wait_changes(since=cursor)       # blocks until something happens
    for event in result.events:
        handle(event)                               # card.moved, attention.raised, …
    cursor = result.cursor                          # persist this
```

Persist the cursor and a listener that was down resumes exactly where it stopped —
nothing is dropped, nothing is delivered twice. That property is why the change-log
replaces the polling-and-diffing scripts this project started with.

A complete, runnable listener — it long-polls the feed and DMs Slack on card moves and on
attention raised for a given human — is in
[examples/notifier/](../examples/notifier/README.md). It's ~200 lines and the shape of
every listener you'd write.

**Not implemented yet (🔜):** MCP push notifications, and a persistent **webhook listener
registry** (`{callback_url, secret, filter}` with HMAC-signed payloads, retry with
backoff, and a per-listener cursor so a listener that was down catches up rather than
missing events). Until those land, `wait_changes` + a stored cursor is the supported way
to receive events, and it gives you the same resumability.

---

## 7. Installing into a harness

The MCP server is spawned by the harness over stdio — no daemon, no port. Print the exact
registration snippet:

```bash
uv run kanban-pro-mcp --print-config claude    # or: codex | opencode | hermes
```

For Claude Code, with attribution:

```bash
claude mcp add kanban-pro -s user -- \
  uv run --directory /path/to/kanban-pro kanban-pro-mcp --actor agent:claude-code
```

Register the same server from several harnesses — each spawns its own process under its
own actor, and they share the store.

## 8. The web UI

Optional, on-demand, never auto-started:

```bash
uv run kanban-pro-ui --actor human:jan     # → http://localhost:8747
```

It takes one snapshot and then streams SSE deltas — no browser polling. The stream
self-heals: if the server restarts or the laptop sleeps, the page reconnects with backoff
and refreshes to catch what it missed.

---

## 9. Where your data lives

On the `default` profile kanban-pro *is* the board, so this is simple: everything is in
`kanban.db`.

The moment you attach a real backend (Hermes today, Jira next), the rule that matters is:
**the backend is the system of record, and kanban-pro does not silently become a second
one.** Cards are not copied into kanban-pro's SQLite. Concretely, in order of preference:

1. **The backend has the feature** → the adapter delegates. The data is written to, and
   read from, the backend. Reported as `native`.
2. **The backend has the data but not our name for it** → `ext` carries it. `ext` is
   passthrough: the adapter maps backend fields into `ext` on the way out under the
   backend's own namespace (Hermes's extra task columns arrive verbatim as
   `ext["hermes"]`) and honours them on the way in. The data never leaves the backend.
3. **The backend lacks the feature, but the rule stores nothing** → pure enforcement.
   WIP limits and flow schemes are *rules*, not records: kanban-pro validates the move,
   then delegates it. Nothing is persisted anywhere, so there is nothing to split. This
   is why they work over every backend. Reported as `polyfilled`.
4. **The backend lacks the feature and it has data** → kanban-pro supplies storage,
   because otherwise the feature simply doesn't exist for you. This is the **overlay**: a
   store (the native store, reused) holding that data keyed to the *backend's* entity ids.
   Reported as `polyfilled`.
5. **Neither possible** → canonical `not_supported`. Reported as `unavailable`.

Only case 4 creates a second home for data, so the design works to shrink it. Where the
backend has *any* usable free-form container — a comment, the description, a custom field
— the polyfilled data is meant to be **written through** into that container behind a
marker (e.g. `<!-- kanban-pro:relations {…} -->`), so the backend stores it, stays
authoritative, and can surface it in its own UI. kanban-pro's overlay is the fallback for
backends with nowhere at all to put it.

> **Status, honestly:** write-through encoding is **designed but not implemented** (see
> SPEC decision 2 and TODO). Today, the Tier-2 polyfills — comments and relations over a
> backend that lacks them — are held in the overlay, keyed to backend ids. Tier-1
> enforcement (WIP, flow) stores nothing, as described. Deletes routed through kanban-pro
> garbage-collect the matching overlay rows; reconciling *out-of-band* backend deletes is
> also still to build.

`capabilities` never lies about which case you're in. Query it and every capability
reports `native`, `polyfilled`, or `unavailable` for the profile you're actually running.
