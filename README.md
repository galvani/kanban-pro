# kanban-pro

**A kanban board your coding agents natively understand — and a state machine they can't
wander out of.**

> **Don't want to read this?** Paste this into any agent that can browse — Claude Code,
> Codex, ChatGPT, whatever you use:
>
> ```text
> Do I need this? https://github.com/galvani/kanban-pro
> ```
>
> It finds [`llms.txt`](llms.txt), which is written for the agent rather than for you:
> what works today versus what's still planned, who should use it, **who should walk
> away**, how it compares to a plain kanban / Jira+MCP / your agent's own to-do list, and
> what AGPL-3.0 means for whatever you're planning to build. It's told not to sell you
> anything and to say plainly if you don't need it.
>
> Then, in the same conversation: `Yes — install it for me and prove it works.` It runs
> the commands itself (needs [uv](https://docs.astral.sh/uv/); no clone), verifies the
> package builds *before* touching your config, and proves the server works by creating a
> board and moving a card — not by trusting that a config entry means success.

You run coding agents every day — Claude Code, Codex, whatever comes next. They do
real work: fix bugs, ship features, review each other's changes. But their *tasks*
live in chat scrollback. You come back to your desk asking: what is my agent doing
right now? What's blocked? What did it finish while I slept? There is no board that
both you and the agents can see and update.

kanban-pro is that board — and, once you have more than one agent, rather more than a
board. It's a real kanban (boards, columns, cards, comments) served over **MCP**, the
protocol your agent harness already speaks, so registering it once turns every agent
session into a worker on a shared, **rule-enforced pipeline**: it pulls its own card,
leases it so nobody else takes it, moves it only along transitions you declared legal,
reports what it found, and asks you when it's stuck. Every one of those steps is stamped
with which agent did it. Concretely:

- **Make the pipeline strict, not suggested.** A declarative `flows.yaml` is a state
  machine over your columns: `ready → running → review → done`, and nothing else. An
  illegal move is *refused*, not logged and allowed. Agents call `list_transitions`
  instead of guessing — and `list_work` inlines each card's legal moves, so a worker
  sees its options without a second call. Schemes are per-card (`docs` tasks can skip the
  review gate; a one-off card can carry its own inline flow), and `force=true` always
  works but stamps `forced: true` on the event. **Overrides are allowed and never
  silent** — the audit trail is the safeguard, not a lock.
- **Let agents pull their own work.** `list_work` answers "what should I work on?" —
  the agent's cards, each with its legal moves inline — and an atomic claim/lease
  (TTL + heartbeat + crash-reclaim) guarantees two agents never grab the same card. A
  crashed worker's lease expires and its card returns to the queue on its own.
- **Give all your agents one shared board.** One `claude mcp add` line per harness;
  multiple harnesses share the same store safely, each under its own identity — a Claude
  Code session, a Codex run, and a Hermes dispatcher all working the same pipeline.
- **Always know who did what.** Every connection declares an actor
  (`agent:claude-code`, `human:jan`); every write lands in an append-only change-log.
  Ask `list_changes` and see exactly which agent moved which card, and when.
- **Sleep through agent mistakes.** A misfiring agent can't one-shot destroy data:
  deletes are archive-first (purge only what's already archived), board/column deletes
  refuse while cards remain, WIP limits are enforced on every move, and retried
  creates with an idempotency key return the original instead of a duplicate. When an
  agent hits a decision it isn't entitled to make, it **raises an attention flag** routed
  through the change-feed — instead of guessing or dying silently.
- **Let the fleet escalate within itself.** An attention flag names *who* should answer,
  and that target is any actor — `agent:architect` as readily as `human:jan`. A coder that
  finds the ticket ambiguous bounces the decision to the agent whose call it is, files the
  question in its work report, and moves on; only what no agent may decide reaches you.
- **Read a status report, not scrollback.** Each card carries a structured
  **work report**: what it's about, the plan, findings, verification checks, the
  verdict, the handoff — and the agent's open questions. Agents write one section at a
  time (`record_work_report`, upserted by item id, never a blind blob rewrite); you
  answer a question with `answer_work_report_question` or in the UI, and the answer is
  mirrored back as a normal comment. This is the handoff contract between one agent and
  the next — a reviewer reads the coder's findings and checks, not its transcript.
- **Watch it live.** An optional web UI streams the board over SSE — drag a card, or
  watch an agent's move slide across the screen. Zero polling: the stream self-heals
  after sleep or a server restart. Open a card for its activity timeline, relations,
  legal moves, and work report; tail the running agent's session log; retry a card.
- **Keep your own board *next to* the team's (🔜 multi-mount).** Your private native
  board beside Jira and Trello, one API over all of them (`local/PRO-12`,
  `jira/TASK-14`), cards copied across with provenance links and synced only after
  your confirmation.

## Built for harness-driven agentic pipelines

One agent working one card doesn't need any of this. The moment a *harness* is dispatching
work — a dispatcher spawning workers, a coder handing to a reviewer, a rebaser retrying a
conflict, all unattended while you sleep — the board stops being a to-do list and becomes
the **control plane**. That is what kanban-pro is shaped for.

The failure modes of an unsupervised agent fleet are specific, and each has a mechanism
here rather than a convention:

| The failure | What stops it |
|---|---|
| Two workers pick up the same card | Atomic claim/lease. The second `claim_card` loses, deterministically. |
| A worker dies holding a card | TTL + heartbeat. The lease expires and the card is reclaimable — no stuck lane, no cleanup job. |
| An agent skips the review gate | The flow scheme refuses the transition. Not a lint, not a prompt instruction — a rejected call. |
| An agent decides to "clean up" the board | Archive-first deletes; a live card cannot be purged; column deletes refuse while cards remain. |
| A retried tool call creates a duplicate card | Idempotency key returns the original result, with no second change-log event. |
| A lane silently fills up | WIP limits are enforced on every move, over any backend. |
| An agent guesses at a decision that was yours | It raises an attention flag and files a question on the card, routed to you through the change-feed. |
| Nobody can reconstruct what happened | Append-only change-log, every write stamped with the acting agent, forced moves flagged `forced: true`. |

Prompt instructions are advisory: an agent that drifts, or a cheap model on a long run,
will step outside them. These are **enforced at the API**, so drift surfaces as a refused
call the agent must handle, and a deliberate override survives as evidence in the log.

The pieces compose into a real pipeline. A dispatcher creates cards in `triage`. Workers
`list_work`, claim, and move along the declared flow — `ready → running`, then `review`.
Each writes its plan, findings, and checks into the card's work report, so the reviewer
inherits a **structured handoff** instead of the previous agent's transcript. A blocked
worker raises attention and waits for your answer rather than inventing one. Meanwhile
`wait_changes` lets a notifier, a dashboard, or the next stage in your harness block on
the feed and wake the instant something moves — a durable, cursored queue where every
"message" is a card you can see, reorder, and answer in a browser.

The [flow config this board actually runs on](docs/examples/flows-default.yaml) —
`triage → todo → scheduled → ready → running → blocked → review → done`, with the reopen
edge and an unmodeled ad-hoc lane — is in the repo, commented, as a starting point.

## Quick Start

```bash
uv sync                                  # install deps (incl. dev tools)
uv run kanban-pro-mcp                    # MCP server (stdio) over the native SQLite store
uv run kanban-pro-mcp --profile memory   # ... over an ephemeral in-memory board
uv run kanban-pro-ui                     # OPTIONAL web board (on demand only) -> :8747
```

Pass `--actor kind:name` (e.g. `agent:claude-code`, `human:jan`) so every write is
attributed in the change-log. The web UI is **push-fed** (SSE off the change-log — no
browser polling) and never starts unless you run it.

The store lives at `~/.local/share/kanban-pro/kanban.db` (override: `KANBAN_PRO_DB`).

## Install into your harness

The server is stdio-spawned by the harness — no daemon, no port. Get the exact
registration snippet for your harness:

```bash
uv run kanban-pro-mcp --print-config claude     # or: codex | opencode | hermes
```

e.g. Claude Code, with attribution:

```bash
claude mcp add kanban-pro -s user -- \
  uv run --directory /path/to/kanban-pro kanban-pro-mcp --actor agent:claude-code
```

Multiple harnesses can register the same server — each spawns its own process (with its
own actor); they share the SQLite store safely.

**Any OS (mac/Windows/Linux), no clone needed** once the repo has a remote: install
[uv](https://docs.astral.sh/uv/), then `uvx --from git+<repo-url> kanban-pro-mcp`, or
`uv tool install` to put `kanban-pro-mcp` on PATH.

## What it looks like in practice

An agent session over MCP (all real today except the `PRO-12` human-readable card
keys, which are 🔜 — ids are uuid hex for now):

```
agent> list_boards
  → [{id: "b1", name: "kanban-pro"}]
agent> create_card {title: "Add retry logic to the sync worker",
                    placements: [{board_id: "b1", column_id: "todo", position: 0}]}
  → Card PRO-12 created                       (actor agent:claude-code, logged)
agent> move_card PRO-12 → doing
  → conflict: WIP limit reached on 'doing' (3/3)
agent> list_transitions PRO-12
  → scheme 'default' (source: flow) — legal from todo: [doing]
agent> move_card PRO-12 → done
  → conflict: scheme 'default' does not allow todo -> done; use force=true to override
agent> move_card PRO-12 → done, force=true
  → Card moved. The event carries forced=true — never silent.
human> list_changes since=41
  → [{seq: 42, actor: "agent:claude-code", op: "card.moved", forced: true, …}]
```

And when you want eyes on the board:

```bash
uv run kanban-pro-ui --actor human:jan   # -> http://localhost:8747
```

One snapshot, then SSE deltas. Drag a card in the browser, watch the agent's
`list_changes` cursor pick it up; let an agent move a card, watch it slide live.

## Configure it

Nothing is required — run `kanban-pro-mcp` with no arguments and you get the native
SQLite board, free movement, and writes attributed to `unknown`. Four settings improve
on that, and the [configuration guide](docs/configuration.md) covers each in full.

| Setting | How | Default |
|---|---|---|
| Which backend | `--profile <name>` / `KANBAN_PRO_PROFILE` | `default` (native SQLite) |
| Who is writing | `--actor <kind:name>` / `KANBAN_PRO_ACTOR` | `unknown` |
| Where the board lives | `KANBAN_PRO_DB` | `~/.local/share/kanban-pro/kanban.db` |
| Which moves are legal | `flows.yaml` / `KANBAN_PRO_FLOWS` | none — free movement |

### Workflow rules

A card can move anywhere until you write a flow file. Then each named **scheme** declares
its states and the legal transitions between them (states are your column names):

```yaml
flows:
  default:                       # code tasks: gated pipeline
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

default_flow: default
```

Drop that at `~/.config/kanban-pro/flows.yaml` (or per-profile,
`flows-default.yaml`). A card overrides the default with
`ext["kanban_pro.scheme"] = "docs"`, or the reserved `"free-roam"` for unrestricted
movement, or carries its own one-off flow inline in `ext["kanban_pro.flow"]`. A column no
scheme mentions stays free, so you can add an ad-hoc lane without editing the rules. **No
file at all → the whole board is free-roam:** the engine is opt-in and never appears
uninvited.

Agents never guess — `list_transitions` (and every item `list_work` returns) carries the
card's legal moves. An illegal move is refused; `force=true` performs it anyway and
stamps `forced: true` on the event. Overrides are always allowed, never silent.

WIP limits are separate: they live **on the column** (`update_column`), not in the flow
file, and kanban-pro enforces them over any backend.

A fully commented real-world example — the agent lifecycle this board runs on — is in
[docs/examples/flows-default.yaml](docs/examples/flows-default.yaml).

### When an agent needs you: the attention flag

`raise_attention(card_id, reason, for_actor="human:jan")` flags the card, shows it on the
board, and puts an `attention.raised` event on the change-feed carrying the reason and
the target — so a listener can deliver the question wherever you are. You answer;
`clear_attention` retires the flag.

Attention is the **signal**, not the content: the question itself goes in the card's work
report under `questions[]`, which you resolve with `answer_work_report_question` (or by
typing into the UI), and which is mirrored back as a normal comment.

### Listeners: getting events out

Every write lands in the append-only change-log. A listener is anything that reads it
from a cursor it stores — no broker, no registration. Probe the head once with
`wait_changes(since=-1)`, then loop: `wait_changes` blocks until events land and returns
the next cursor. Persist that cursor and a listener that was down resumes exactly where
it stopped, dropping nothing and re-delivering nothing.

A runnable one — long-polls the feed, DMs Slack on card moves and on attention raised for
you — is in [examples/notifier/](examples/notifier/README.md).

## One board API, many backends, gaps polyfilled

Out of the box, kanban-pro *is* the board — cards live in its own SQLite store. But
the board API is deliberately separated from where cards are stored, via **adapters**.

The scenario that motivates this: your team tracks work in Jira. You point kanban-pro
at Jira, and your agents work real Jira tickets through the exact same safe,
attributed kanban tools — no agent ever learns the Jira API or holds a Jira token.
And where Jira lacks something kanban-pro offers (WIP limits, flow schemes,
checklists), kanban-pro **fills the gap itself** — and tells you
honestly which is which: query `capabilities` and each one reports **`native`**
(the backend does it), **`polyfilled`** (kanban-pro does it on top), or
**`unavailable`**. It never lies about what's real.

**Your data stays where it belongs.** When a backend is attached, that backend is the
system of record — kanban-pro does not quietly copy your cards into its own SQLite. The
adapter maps canonical fields onto the backend's fields, and everything the backend knows
that the canonical model doesn't rides back out through `ext` (Hermes's harness columns
arrive as `ext["hermes"]`, verbatim). kanban-pro supplies storage **only for what the
backend has nowhere to put** — and only then. Rules that store nothing (WIP limits, flow
enforcement) are pure enforcement: no data, so no split. Data that a backend genuinely
can't hold falls to kanban-pro's **overlay**, keyed to the backend's own ids. Since only
that last case creates a second home for data, the goal is to shrink it: where the
backend has *any* usable container (a comment, a description, a custom field), the
polyfill is **written through** into it so the backend stays authoritative and can show
the data in its own UI. *Write-through encoding is designed, not yet built (🔜) — today
polyfilled comments and relations live in the overlay.* Full breakdown:
[docs/configuration.md](docs/configuration.md#8-where-your-data-lives).

```
  your agents (Claude Code, Codex, …)          you (browser)
        │  MCP tools (37)                        │  live UI (SSE)
        ▼                                        ▼
  ┌──────────────────── kanban-pro core ────────────────────┐
  │ actor stamping · change-log · delete guards ·           │
  │ WIP + flow enforcement · capability polyfills           │
  └───────┬───────────────────┬────────────────────┬────────┘
     native SQLite         memory            jira  🔜
     (default system      (ephemeral,     (via the official
      of record)           for tests)      Atlassian MCP)
```

Adapters today: **`native`** (the default SQLite system of record), **`memory`**, and
one harness adapter (`hermes` — the pattern for wiring in your own harness's built-in
kanban). All pass one shared contract test suite. The **`jira`** adapter is upcoming
and will consume the official Atlassian MCP as a client — Atlassian owns the OAuth
dance, so kanban-pro never holds Jira credentials.

Pick the backend with a **profile** — `--profile default` / `--profile memory` (or
`KANBAN_PRO_PROFILE`). A profile bundles an adapter with its settings; kanban-pro
always exposes the **full canonical surface** regardless of the backend's gaps.

**And you're not limited to one world (🔜 multi-mount).** The destination is several
backends mounted **at the same time**: your own private board (the native store)
living right next to your team's Jira and a Trello, all behind the one API, addressed
by mount — `local/PRO-12`, `jira/TASK-14`, `trello/…`. An agent picks work from your
board, copies a card into Jira with a provenance link when it becomes team-visible,
and the boards stay related through **confirmation-gated sync** — proposed change-sets
you approve, never silent replication. Copy + link ships first; the mount-prefix
addressing is already ruled into the design.

Boards move, too: a generic **migration tool** (`kanban-pro-migrate`) copies any
profile into any other — idempotent, dry-run first, provenance-stamped, the import
itself attributed in the change-log. It has run for real: a 172-card board with 608
comments imported port-to-port.

## The board is also your message bus

Look at the mechanics and you'll notice kanban-pro quietly replaces the queueing
infrastructure an agent fleet would otherwise need:

- The **change-log** is an append-only event stream with consumer cursors — an agent
  (or your Slack notifier) reads `list_changes since=<seq>` and resumes exactly where
  it left off. Kafka-style offsets, no broker to run. ✅
- **Push without polling loops:** `wait_changes` long-polls the same cursor and returns
  the moment events land (instantly for writes through this server), so a consumer
  blocks instead of spinning. ✅
- **Claim/lease** is the competing-consumers pattern: atomic claim with a TTL,
  heartbeats, crash-reclaim = redelivery. Two agents never grab the same card. ✅
- The **attention flag** is routing: "this needs a decision" targeted at a specific
  agent or human, carried in the event stream for notifiers to deliver. ✅
- **Durable subscriptions** (🔜 webhook listeners with per-listener cursors + retry,
  and MCP notifications) round out fan-out.

The difference from a real broker: here every "message" is a **card** — durable,
stateful, attributed, with history — and the queue is a **board a human can see**,
reprioritize, and answer in a browser. Your task queue finally has a UI.

## Why not X?

The concept combo — self-hosted backend-agnostic proxy + honest capability polyfill +
MCP-first + agent-safety — has no direct prior art (web survey, 2026-07-05; see
[JOURNAL.md](JOURNAL.md)). The nearest neighbors each miss a piece:

| | kanban-pro | Unified task APIs (Unified.to-style) | MCP aggregators (Composio Rube) | Agent boards (Agent Kanban, Flux) | Per-backend MCP (Atlassian, Linear) | Classic kanbans (Planka, Vikunja) |
|---|---|---|---|---|---|---|
| Self-hosted | ✅ | ❌ SaaS | ❌ SaaS | varies | varies | ✅ |
| Backend-agnostic | ✅ one model, any adapter | ✅ normalize-only | ➖ many apps, per-app tools | ❌ own store only | ❌ one backend | ❌ own store only |
| MCP-native | ✅ primary interface | ❌ | ✅ | ✅ | ✅ | ❌ |
| Capability polyfill | ✅ delegate → polyfill → honest `unavailable` | ❌ gaps are just missing | ❌ | ➖ n/a | ❌ | ❌ |
| Agent-safety semantics | ✅ archive-first, guarded deletes, WIP | ❌ | ❌ | ➖ partial | ❌ raw backend semantics | ❌ |
| Actor audit trail | ✅ per-connection actor + change-log | ❌ | ❌ | ➖ | ➖ backend's own | ➖ |
| Push-fed UI | ✅ SSE, on-demand | ❌ | ❌ | ➖ | ❌ | ➖ web UI |

## Architecture

Ports & adapters (hexagonal), consumed **MCP-first / shell-first** (agent harnesses
are the primary clients; HTTP is secondary):

```
harnesses / clients
   │   MCP (primary) · CLI (🔜) · HTTP (secondary) — thin, stateless
   ▼
core/  — Recording(Augmenting(adapter)): actor stamping + change-log,
         delegate/polyfill routing, guards, dedupe
   ▼
KanbanBackend port ──▶ adapter ──▶ backend
          ▲
canonical domain model (Pydantic)
```

Interfaces never talk to an adapter directly — everything goes through `core/`, so no
interface can bypass the guards or the audit trail. Directory layout:
[AGENTS.md](AGENTS.md#architecture-ports--adapters); design: [SPEC.md](SPEC.md).

## Documentation

- [llms.txt](llms.txt) — the agent-facing brief. Hand the repo to an AI agent, ask "do I
  need this?", then let it install and verify.
- [docs/configuration.md](docs/configuration.md) — **start here to configure it**:
  profiles, actors, workflow rules, WIP limits, the attention flag, and listeners
- [CHANGELOG.md](CHANGELOG.md) — what changed, for people who *use* it — including a frank
  **known limitations** list
- [SPEC.md](SPEC.md) — what and why (canonical model, the core+passthrough decision,
  capability model)
- [JOURNAL.md](JOURNAL.md) — decisions and rationale: what was rejected, what broke, why
- [TODO.md](TODO.md) — open backlog (nothing in it is done)
- [AGENTS.md](AGENTS.md) — conventions & hard rules for coding agents, incl. how to
  author a new adapter
- [docs/methods.md](docs/methods.md) — every operation + its MCP projection
- [docs/hermes-kanban.md](docs/hermes-kanban.md) — ground truth for the first harness
  adapter & its migration map

## Status / Roadmap

**Working today:** the canonical model and port, three adapters behind one contract
suite, the augmenting layer (WIP enforcement, comments/relations polyfill, honest
capability reporting), the MCP server (37 tools + 9 resources), actor identity + the
append-only change-log with both the `list_changes` pull feed and the `wait_changes`
long-poll, the flow engine (named schemes, inline per-card flows, free-roam, audited
force), structured work reports with human-answerable questions, the push-fed web UI
(card detail, live session-log tail, retry), and the generic migration tool — all
tested, and verified live against a real production board.

**Next (🔜):** the CLI, a full canonical HTTP surface (today's `api/` serves the UI),
bulk operations, flow hooks/validators, the MCP-backed `jira` adapter with cross-board
copy/link, smart remote caching, confirmation-gated two-way sync, human-readable card
keys (`PRO-12`), MCP push notifications, and durable webhook listeners. Roadmap:
[SPEC.md](SPEC.md#roadmap); the full queue: [TODO.md](TODO.md). Anything marked 🔜 does
not run today.

## License

**[AGPL-3.0-only](LICENSE)** — free software. Copyright © 2026 Jan.

Use it, run it, fork it, change it. If you distribute a modified version — or run one as a
service others can reach — you must publish your source (AGPL §13). Fixes and ideas are
asked for, not compelled: see [CONTRIBUTING.md](CONTRIBUTING.md).
