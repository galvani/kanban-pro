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
| Which flow rules | — | `KANBAN_PRO_FLOWS` | see [§3](#3-workflow-rules-flowsyaml) |

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

## 2. WIP limits

A WIP limit lives **on the column**, not in the flow file. Set it with `update_column`
(or in the UI), and every subsequent move into a full column is rejected:

```
agent> move_card PRO-12 → doing
  → conflict: column 'doing' is at its WIP limit (3)
```

It's enforced by kanban-pro itself, over any backend — including backends that have no
concept of a WIP limit. Nothing is stored in the backend to make it work.

> The `wip_limits:` key inside `flows.yaml` is **reserved and currently ignored**. Set
> limits on columns.

---

## 3. Workflow rules (`flows.yaml`)

By default a card can move anywhere. A flow file turns the board into a state machine:
each **scheme** names the states and which transitions between them are legal.

### Where the file goes

The first of these that exists wins:

1. `$KANBAN_PRO_FLOWS` (an explicit path)
2. `~/.config/kanban-pro/flows-<profile>.yaml` (per-profile — e.g. `flows-default.yaml`)
3. `~/.config/kanban-pro/flows.yaml` (shared by all profiles)

**No file at all → the whole board is free-roam.** The flow engine is opt-in; it never
appears uninvited.

### A minimal file

```yaml
flows:
  default:                       # code tasks: a gated pipeline
    states: [backlog, todo, doing, review, done]
    transitions:
      - { from: todo,   to: doing }
      - { from: doing,  to: [review, todo] }
      - { from: review, to: [done, doing] }

  docs:                          # documentation tasks skip the review gate
    states: [todo, doing, done]
    transitions:
      - { from: todo,  to: doing }
      - { from: doing, to: done }

default_flow: default            # which scheme unmarked cards use
```

State names are your **column names**. A dangling reference (a transition to a state not
in `states`) fails at load — loudly, at startup, not on the first move.

A complete, commented example — the real agent lifecycle this board runs on, with
`triage/ready/running/blocked/review` lanes — is in
[docs/examples/flows-default.yaml](examples/flows-default.yaml).

### How a card picks its scheme

Resolution stops at the first match:

1. **`ext["kanban_pro.flow"]`** — a full inline `{states, transitions}` flow for this one
   card. Beats everything, and works even with no `flows.yaml` at all. Malformed → falls
   back to the default scheme with a warning.
2. **`ext["kanban_pro.scheme"]`** — the name of a scheme in `flows.yaml`, or the reserved
   `"free-roam"` for unrestricted movement. `free-roam` is built in and can never be
   defined in YAML.
3. **The backend's own workflow**, if it has one (Hermes does).
4. **`default_flow`**, else free movement.

A column that no scheme mentions is *unmodeled*: moves in and out of it stay free. That's
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

`auto_reset_attempts_on_reassign: true` (per scheme, default true) clears a card's
attempt counter when it changes assignee or lane, so a retried card starts fresh.
`hooks:` and `wip_limits:` are reserved for future use and ignored today.

---

## 4. The attention flag — how an agent asks you a question

An agent that hits a decision only you can make should neither guess nor die quietly. It
raises an attention flag:

```
agent> raise_attention PRO-12, reason: "Staging or prod credentials?", for_actor: "human:jan"
```

Three things happen. The card gets `ext["kanban_pro.attention"] = {reason, raised_by,
for}`, the board tile shows the flag, and an `attention.raised` event lands on the
change-feed carrying both the reason and the target. That last part is what makes it
*routable*: a listener (below) reads the target and delivers the question wherever you
actually are.

You answer, then `clear_attention(card_id, resolution?)` removes the flag and emits
`attention.cleared`.

**Attention is the signal, not the content.** The question itself belongs in the card's
work report, in `questions[]` — that's what `answer_work_report_question` resolves, and
what the UI renders as an answerable prompt. Raise attention *because* a question is
waiting; don't smuggle the question into the reason string. See
[methods.md](methods.md#work-reports-core-convenience--not-port-ops).

---

## 5. Listeners — getting events out of the board

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

## 6. Installing into a harness

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

## 7. The web UI

Optional, on-demand, never auto-started:

```bash
uv run kanban-pro-ui --actor human:jan     # → http://localhost:8747
```

It takes one snapshot and then streams SSE deltas — no browser polling. The stream
self-heals: if the server restarts or the laptop sleeps, the page reconnects with backoff
and refreshes to catch what it missed.

---

## 8. Where your data lives

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
