# kanban-pro — TODO

**Open work only.** Nothing here is done. What shipped is in
[CHANGELOG.md](CHANGELOG.md); *why* it shipped that way is in [JOURNAL.md](JOURNAL.md).
When an item lands, delete it from this file — don't tick it.

## Known gaps in what already ships

Small, sharp, and each one currently surprises somebody. Documented in CHANGELOG's
"Known limitations".

- [ ] **Checklists are write-once.** `Card.checklists[]` persists but no API can tick an
  item — `CardPatch` has no `checklists` field and there's no `checklist_*` tool. Either
  add the port ops (below) or drop checklists from the model in favour of the work
  report's `plan[]`/`checks[]`. Decide which; the current halfway state is a trap.
- [ ] **`list_work` doesn't surface attention.** An agent that only polls its queue never
  learns a question was raised for it. Add a "needs my attention" section to `list_work`
  (Jan asked for this 2026-07-05) so a worker doesn't have to also watch the feed.
- [ ] **Attention `severity` is advisory.** kanban-pro exposes the `severity` field and the
  `attention_blocks()` helper but does not gate `list_work` on it — nothing in this repo
  calls the helper; the consumer (the dispatcher) is what refuses to work a blocked card.
  Decide whether the board should enforce its own signal.
- [ ] **`clear_attention` is not access-controlled** — any actor may clear a flag raised
  for someone else. Recorded, so auditable; decide whether to enforce `for_actor`.
- [ ] **Idempotency keys are optional**, but SPEC decision 8 specifies them as required on
  create/add. Either make them required (breaks every existing caller — needs a
  deprecation path) or amend the decision. Currently the docs and the code disagree.
- [ ] **`ext` carries no store-version.** The format can't evolve compatibly. Decide the
  granularity (whole blob / per reserved namespace / per card) and write the migration
  path before any reader depends on today's shape.
- [ ] **Bulk ops** (`bulk_create`/`bulk_move`/`bulk_update`/`bulk_archive`) — specified in
  SPEC as a core-loop with partial-success results; never implemented. The port stays
  single-item.
- [ ] **`kanban://work-distribution` and the other doc resources** should be regenerated
  from code, not hand-written, so they can't drift like methods.md did.

## Flow-in-DB follow-ups

The core rework SHIPPED on branch `flow-in-db` (see JOURNAL 2026-07-10 +
[docs/flow-in-db-plan.md](docs/flow-in-db-plan.md)). Remaining:

- [ ] **Merge `flow-in-db` and reload the running MCP server(s).** Until reload the live
  server runs old code — enforcement is off AND a column edit through it would drop the new
  `board.flow` field. Reload before touching columns on the `default` board.
- [ ] **(optional) YAML-file import for onboarding** — `init_board(preset=import-file)` that
  reads a flows.yaml and name-maps it onto a new board's columns. Dropped from the initial
  cut (D3); build only if someone wants git-versioned preset files.

## Unbuilt interfaces

- [ ] **CLI** (`kanban_pro/cli/`) — the last unbuilt PRIMARY interface. Same ops as
  subcommands for shell-first harnesses (Codex/Claude Code shelling out) and humans.
  SPEC and AGENTS both describe it as if it exists; it does not.
- [ ] **HTTP/REST** — `api/` today serves the web UI (snapshot, SSE, card detail, move,
  comment, answer-question, retry). The full canonical one-route-per-op surface plus
  `GET /capabilities` is still open. There is no `app.py`; entry points live in
  `pyproject.toml`.
- [ ] Keep every interface thin over `core/` — no drift, no adapter called directly.

## Events — the unfinished half of decision 9

- [ ] **MCP push notifications** — a subscribed client receives card/column/board events.
  Today's push story is `wait_changes` (long-poll) plus SSE to the browser.
- [ ] **Persistent webhook listener registry** — `{callback_url, secret, filter}`,
  HMAC-signed payloads, retry with backoff, and a per-listener cursor so a listener that
  was down resumes rather than dropping events.
- [ ] **Reconciliation polling** — GC overlay rows orphaned by out-of-band backend deletes
  (deletes routed through kanban-pro already GC).
- [ ] **Backend-watcher ingestion** — hermes `task_events` (append-only, id-cursored) tails
  cheaply into our change-log; also feeds the read cache below.
- [ ] **Card timeline projection** over the change-log.

## Augmentation layer — remaining slices

Core exists: `AugmentingBackend` + `BaseAdapter` + contract suite, WIP enforcement
(Tier 1), comments/relations overlay polyfill (Tier 2), fulfilment reporting, delete-GC.

- [ ] **Write-through encoding** — persist polyfilled data into the backend's own
  containers (a comment, the description, a custom field) behind a marker, so the backend
  stays authoritative. Plus the per-adapter/per-capability persistence-strategy choice.
  Until this lands, Tier-2 polyfills live only in our overlay.
- [ ] **ARCHIVE flag polyfill** for backends without archive (needs shadow-flag storage).
- [ ] **Port expansion:** label-registry ops, assignee attach/detach, checklist item
  add/toggle, attachment add/remove — these ride on `Card`/`Board` at create time only.
- [ ] **`list_users()` / `get_user(id)`** — without them a caller can't discover valid ids
  for `assignees[]` or `Comment.author`. Prereq for agent assignees.

## Flow engine — hooks and the rest

Shipped: the YAML loader (fail-fast), named schemes, per-card scheme, inline one-card
flows, the full resolution chain, transition enforcement, audited `force`,
`list_transitions`/`list_flows`, the hermes native-transitions hook.

- [ ] **Hooks**, reserved in the syntax from day one, split into two kinds:
  - *pre-transition validators* — block a move with a reason ("can't reach Done with an
    open checklist"). Return allow/deny.
  - *post-transition actions* — fire after a move (set a field, add a comment, create a
    follow-up card, notify, emit a custom event).
  - *declarative built-ins* (`require:`, `do: set_field|add_comment|notify`) for the common
    cases, plus a named-code escape hatch `do: hook:<name>` → registered Python handler.
  ```yaml
  hooks:
    - { on: enter, state: done,  require: checklists_complete, else: deny }
    - { on: exit,  state: doing, do: set_field, field: started_at, value: now }
  ```
  **Design questions still open:** sync (blocking validators) vs async (post-actions); how
  hooks interact with the change-log and idempotency; failure semantics — does a failing
  post-action roll back the move?
- [ ] **Flow-level `wip_limits:` key** — currently parsed and *silently ignored*; column
  `wip_limit` is the enforced one. Either implement or reject the key loudly.
- [ ] **Runtime-editable flows** (store-backed via the API). YAML stays the seed.
- [ ] **UI:** flows view, scheme badge on the card modal, legal-target highlighting during
  drag, and a `scheme=` filter on list surfaces.

## Agent-native kanban

- [ ] **Agent assignees** — an agent is a `User` with `ext.kind="agent"` (works today);
  promote to a first-class `User.kind: human|agent` once proven. Blocked on `list_users`.
- [ ] **Card-scoped error events, NOT a log sink** — agent failures land as typed
  comments/events on the card ("error: …", actor, timestamp). Raw telemetry (stack traces,
  stdout, tokens) stays outside; the card carries a reference. **Hard boundary** against
  drifting into an observability platform.
- [ ] **`ColumnCategory.BLOCKED`** — hermes `blocked` + monday "stuck" meet the ≥2-backend
  rule; hermes `blocked` currently maps lossily to STARTED. Add when the enum is next
  touched. Answers "which column means blocked"; complements the attention flag, never
  replaces it.
- [ ] **`priority` core promotion** — hermes and Jira both have it (≥2-backend rule met).
- [ ] **Human-readable card keys** — `PRO-12` (board prefix + counter) instead of uuid hex;
  adapters with native keys (Jira) map theirs. Decide: replace `id`, or add a `key` alias.
  The README's examples already pretend this exists.
- [ ] **Subcard ergonomics** (subcards work today via PARENT/CHILD relations): (a) atomic
  one-call spawn — `parent_id` on `create_card`, since two-call create+relate can orphan on
  a crash between them; (b) `subcards(card_id)` returning child *cards* with board/column
  context, not relation edges; (c) roll-up as a flow hook — "all children done → move the
  parent".
- [ ] **Tags = the existing Labels** (ruled: one concept, no parallel entity). Build
  label-registry port ops + `tag_card`/`untag_card` + a label filter on listings, with
  **auto-create-on-use**. Boundary: tags classify (`backend`, `bug`), they never carry
  state — attention stays the flag, blocked stays the column.
- [ ] **Backlog support (Jira-style)** — a Jira backlog lives outside the board's columns.
  Map it onto a `ColumnCategory.BACKLOG` column so "see the backlog" works uniformly.

## Backends

- [ ] **`jira` adapter + cross-mount copy/link.**
  - Transport is the **Atlassian MCP, not raw REST**: kanban-pro connects as an MCP
    *client*. Atlassian owns the OAuth dance, so kanban-pro never holds Jira credentials.
    If the profile is selected and no Atlassian MCP is reachable, fail with an actionable
    error suggesting how to enable one. Caveat: board-admin ops (column CRUD,
    rank/position) likely aren't exposed as MCP tools → declare them non-native and
    overlay-polyfill. The pattern generalizes: any backend with an MCP server can get an
    adapter this way.
  - **Pulls multi-mount forward** — two live profiles at once, mount-prefixed
    (`jira/TASK-14`, `local/PRO-12`). The mount-prefix layer was designed to be addable
    without core rework.
  - **Cross-mount copy** is a core op: read from mount A, create in mount B, stamp
    `ext["kanban_pro.copied_from"]` + a cross-mount link. Cross-mount relations can't live
    in either backend, so they live in the overlay, keyed `(mount, card_id)`.
  - **Copy-once + link first.** Each board transitions independently. Mirrored transitions
    on linked cards come later, on top of the same links.
- [ ] **Two-way sync, confirmation-gated** — changes apply only after the user approves a
  proposed change-set; never silent replication. Builds on copy+link and the change-log.
  **When this lands, revise SPEC's "What This Project Is NOT"**, which currently rules out
  two-way sync outright.
- [ ] **Remote-adapter read cache + change detection** — a core-level cache decorator (same
  wrapper pattern as `AugmentingBackend`) for **remote adapters only**; local SQLite reads
  stay uncached. Per-adapter change-detection: `jira` delta-polls (`updated >= <cursor>`
  JQL); `hermes` tails `task_events` for a near-push feed. Config-controlled per profile;
  `enabled: false` must fully bypass the decorator, not merely shorten TTLs.
  ```yaml
  profiles:
    jira:
      adapter: jira
      cache: { enabled: true, refresh_seconds: 30, ttl_seconds: 300 }
  ```
- [ ] Additional profiles: Trello, Linear, GitHub Projects.

## Operational

- [ ] **Structured logging** — consistent op logging across core/adapters/interfaces:
  profile, operation, entity ids, outcome/error-code, duration. Forced transitions and
  destructive ops always logged. Seeded in `mcp/` (stderr, taxonomy-coded warnings).
  Decide: JSON lines? rotation? Does it correlate with the change-log — one event, two
  sinks?
- [ ] **Richer UI — remaining.** The page is a single self-contained `board.html` (no
  build, no SDK). Revisit a React/shadcn host only if it stops scaling.

## Cutover (this machine)

- [ ] **Phase 2 of the Hermes cutover** — apply the Hermes MCP registration, register
  OpenCode, switch card execution to `~/workspace/kanban-dispatcher`, stop the Hermes
  dispatcher, retire its built-in kanban toolset. Until then kanban-pro is primary for new
  work and the Hermes board is legacy (re-run `kanban-pro-migrate` to absorb changes).
  Optional follow-up: import `task_events` history into the change-log.
- [ ] **`kanban-dispatcher` (separate subproject, not a `kanban_pro` module).** A thin
  daemon that turns the board into a running agent fleet: `list_work` → route → `claim_card`
  → spawn an agent platform → `heartbeat_claim` while it runs → report (comment + move, or
  raise attention on failure) → `release_claim`. A crash becomes lease expiry becomes
  automatic redelivery. It provides **no** skills/MCP/agents itself — the spawned platforms
  bring their own; the dispatcher stays a dumb loop, because the board *is* the queue.
  Real design work: the routing table (card → platform via assignee / ext skills / labels /
  scheme), concurrency caps, workspace management, spawn-failure handling.
  **Boundary already ruled:** work execution ≠ kanban data. kanban-pro stays the board and
  the bus; the dispatcher is just another MCP client.
