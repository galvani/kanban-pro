# Changelog

What changed, for people who *use* kanban-pro. The reasoning behind each decision —
what was rejected, what broke, what's still a lie — lives in [JOURNAL.md](JOURNAL.md).

Format follows [Keep a Changelog](https://keepachangelog.com/en/1.1.0/); this project
uses [Semantic Versioning](https://semver.org/). Until 1.0.0 the canonical model, the MCP
tool surface, and the `ext` conventions may change without a major bump — pin a commit if
you need stability.

## [Unreleased]

Everything below has landed on `main` but is not tagged. The package still reports
`0.0.1`.

### Changed

- **Workflow is now board data, not a config file.** Transition rules moved out of
  `flows-<profile>.yaml` (retired, along with `$KANBAN_PRO_FLOWS`) and onto the board as
  `board.flow` — allowed moves keyed by **column id**, administered over MCP. New tools:
  `set_flow`, `set_transitions`, `clear_flow`, and `init_board` (onboard a new board from a
  preset: `blank` / `simple-kanban` / `docs` / `agent-lifecycle`). `list_flows` now reports
  each board's flow instead of YAML schemes. Because edges reference the board's own
  columns, a flow can't dangle: `set_flow` refuses unknown column ids and `delete_column`
  cascades to strip edges. A per-card inline flow (`ext["kanban_pro.flow"]`) and the
  `"free-roam"` per-card escape are unchanged. `WORKFLOW` is now reported `polyfilled`
  wherever the backend doesn't own its own workflow.

### Added

- **`kanban-pro-mcp --install-skills [DIR]`** — copy the example agent skills
  (`kanban-orchestrator`, `kanban-worker`, `kanban-pro-work-reporting`) into a skills dir
  (default `~/.claude/skills`); never overwrites an existing one. Surfaced by
  `--print-config claude`.
- **MCP server** over stdio — 41 tools and 9 `kanban://` resources. No daemon, no port;
  your harness spawns it. `--print-config claude|codex|opencode|hermes` prints the
  registration snippet.
- **Orientation instructions** in the MCP `initialize` result, so a connecting agent
  learns the board's rules (claim before touching, archive-first deletes, audited
  `force`) before its first call rather than after its first rejected one.
- **Native SQLite store** as the default backend, plus a `memory` profile (ephemeral) and
  a `hermes` adapter. All three pass one shared contract suite.
- **Actor identity** — `--actor agent:claude-code` / `human:jan`, stamped on every write.
- **Append-only change-log** with cursors: `list_changes(since)` to pull, and
  `wait_changes(since)` to long-poll (blocks until events land; `since=-1` probes the
  head without replaying history). A consumer that was down resumes from its cursor.
- **Claim/lease work distribution** — `list_work` returns your cards with their legal
  moves inline; `claim_card` is an atomic CAS lease with TTL, `heartbeat_claim` renews,
  and a crashed worker's lease expires so its card is reclaimable.
- **Flow engine** — `flows.yaml` declares named schemes and legal column transitions;
  `list_transitions` / `list_flows` expose them. Per-card schemes via
  `ext["kanban_pro.scheme"]`, one-off inline flows via `ext["kanban_pro.flow"]`, a
  built-in `free-roam`, and a `force=true` override that stamps `forced: true` on the
  event. No `flows.yaml` → the whole board is free-roam.
- **Structured work reports** — `record_work_report` upserts sections (`about`, `plan`,
  `findings`, `needs`, `analysis_log`, `checks`, `verdict`, `handoff`, `questions`) by
  item id, emitting a `work_report.updated` event. `answer_work_report_question` resolves
  a question and mirrors the answer as a comment. This is the handoff contract between
  one agent and the next.
- **Attention flag** — `raise_attention(card_id, reason, for_actor)` routes a decision to
  any actor, `agent:architect` as readily as `human:jan`, through the change-feed.
  `clear_attention` retires it.
- **Agent-safety semantics** — archive-first deletes (a live card cannot be purged),
  empty-only board/column deletes, WIP limits enforced on every move, and idempotency
  keys so a retried create returns the original instead of a duplicate.
- **Capability reporting** — `kanban://capabilities` reports each capability as `native`,
  `polyfilled`, or `unavailable` for the active backend.
- **Optional web board** (`kanban-pro-ui`, never auto-starts) — SSE-fed, drag-and-drop,
  card detail with activity timeline and relations, a live tail of the running agent's
  session log, and card retry.
- **Migration tool** (`kanban-pro-migrate`) — copies any profile into any other;
  idempotent, dry-run first, provenance-stamped. Has moved a real 172-card board.
- `docs/configuration.md`, `llms.txt`, `CONTRIBUTING.md`.

### Fixed

- The web board no longer freezes on writes from another process. A closed `EventSource`
  (server restart, laptop sleep, dropped connection) was never replaced; the stream now
  reconnects with backoff, refreshes on regaining focus or connectivity, and the server
  emits an idle heartbeat so a dead connection surfaces instead of hanging silent.
- The `done` lane sorts by recency rather than creation order.

### Known limitations

Stated plainly, because the docs used to imply otherwise:

- **No CLI.** It's described as a primary interface throughout the design docs; it does
  not exist.
- **Checklists are write-once.** `Card.checklists[]` is accepted at `create_card` and
  persists, but no API can tick an item — `CardPatch` has no `checklists` field. Use the
  work report's `plan[]` / `checks[]` for a live to-do list on a card.
- **`list_work` does not surface attention.** An agent that only polls its work queue
  will never see a question raised for it; watch `wait_changes` for `attention.raised`.
- **`clear_attention` is not access-controlled.** Any actor may clear any flag. The
  clearing actor is recorded, so it is auditable rather than prevented.
- **No bulk operations**, despite `bulk_create`/`bulk_move`/`bulk_update`/`bulk_archive`
  appearing in earlier docs. They were never implemented.
- **No Jira or Trello adapter, no multi-mount, no cross-backend sync.**
- **No MCP push notifications and no webhook listener registry.** `wait_changes` plus a
  stored cursor is the supported way to receive events.
- **No human-readable card keys.** Ids are uuid hex; `PRO-12` in the docs is aspirational.
- **The HTTP layer serves the web UI**, not a full canonical REST surface.
- **Write-through encoding is designed, not built.** With a backend attached, Tier-1 rules
  (WIP, flow) store nothing anywhere, but polyfilled comments and relations live in
  kanban-pro's overlay rather than in the backend's own containers.
- **Idempotency keys are optional**, though SPEC decision 8 specifies them as required.

### Security

- Licensed **AGPL-3.0-only** as of 2026-07-10 (previously unlicensed, i.e. all rights
  reserved). If you distribute a modified version, or run one as a service others can
  reach, you must publish your source.

[Unreleased]: https://github.com/galvani/kanban-pro/commits/main
