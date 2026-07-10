# kanban-pro vs. the field

Researched 2026-07-10. Star counts and dates verified against the GitHub API on that day;
every other claim carries a source link. Where something could not be confirmed it says
**unverified** rather than asserting it.

This document exists to keep the README honest. **If you are a human choosing a kanban
board, you almost certainly want one of the alternatives below, not this.** Read
[§ Who should walk away](#who-should-walk-away) first.

## The four capabilities that actually distinguish kanban-pro

Everything else it does — cards, columns, comments, labels, archive — is table stakes.
These four are the ones nothing else in the survey combines:

1. **Enforced flow** — the server *refuses* an illegal column transition, with an audited
   `force` override. (Not a lint. Not an automation that fires afterwards.)
2. **WIP limits enforced on write** — the move is rejected, not highlighted in red.
3. **Resumable cursor change-feed** — an append-only log a consumer tails from a stored
   cursor, with long-poll, missing nothing while it was down.
4. **Atomic claim/lease** — TTL + heartbeat visibility timeout, so two agents can never
   hold the same card, and a crashed worker's card returns to the queue by itself.

## The matrix

✅ yes · ⚠️ partial or soft · ❌ no

| Product | Licence | Self-host | Multi-user | MCP | Enforced flow | WIP on write | Resumable cursor | Claim/lease |
|---|---|---|---|---|---|---|---|---|
| **kanban-pro** | AGPL-3.0 | ✅ | ❌ single | ✅ native | ✅ | ✅ | ✅ | ✅ |
| [Jira](https://developer.atlassian.com/cloud/jira/platform/rest/v3/) | Proprietary | ⚠️ DC, [EOL 2029](https://www.atlassian.com/licensing/data-center-end-of-life) | ✅ | ✅ [official GA](https://github.com/atlassian/atlassian-mcp-server) | ✅ validators | ⚠️ soft | ⚠️ org audit only | ❌ |
| [Linear](https://linear.app/docs/mcp) | Proprietary | ❌ | ✅ | ✅ official | ❌ categories | ❌ | ⚠️ GraphQL cursor | ❌ |
| [Trello](https://support.atlassian.com/trello/docs/connect-trello-to-ai-assistants-with-trello-mcp/) | Proprietary | ❌ | ✅ | ✅ official | ❌ | ⚠️ soft | ⚠️ `since` polling | ❌ |
| [GitHub Projects v2](https://github.com/github/github-mcp-server) | Proprietary | ⚠️ GHES | ✅ | ✅ official | ❌ automation | ⚠️ soft | ⚠️ webhooks | ❌ |
| [Vikunja](https://github.com/go-vikunja/vikunja) | AGPL-3.0 | ✅ | ✅ | ⚠️ community | ❌ | ⚠️ visual | ❌ | ❌ |
| [Wekan](https://github.com/wekan/wekan) | MIT | ✅ | ✅ | ⚠️ tiny wrapper | ❌ | ⚠️ blocks; server-side **unverified** | ❌ | ❌ |
| [Kanboard](https://docs.kanboard.org/v1/user/boards/) | MIT | ✅ | ✅ | ⚠️ community | ❌ actions fire *after* | ❌ red only | ❌ | ❌ |
| [Planka](https://github.com/plankanban/planka) | fair-code | ✅ | ✅ | ⚠️ community | ❌ | ❌ | ❌ | ❌ |
| [Taiga](https://github.com/kaleidos-ventures/taiga-back) | MPL-2.0 | ✅ | ✅ | ⚠️ community | ❌ | ❌ display only | ❌ | ❌ |
| [Focalboard](https://github.com/mattermost-community/focalboard) | NOASSERTION | ✅ | ✅ | ⚠️ community | ❌ | ❌ | ❌ | ❌ |
| [sirsjg/flux](https://github.com/sirsjg/flux) ★91 | MIT | ✅ | ⚠️ | ✅ | ❌ | ❌ | ⚠️ SSE only | ❌ |
| [multidimensionalcats/kanban-mcp](https://github.com/multidimensionalcats/kanban-mcp) ★75 | MIT | ✅ | ❌ | ✅ | ⚠️ status ops | ❌ | ⚠️ timeline | ❌ |
| [eyalzh/kanban-mcp](https://github.com/eyalzh/kanban-mcp) ★40 | MIT | ✅ | ❌ | ✅ | ❌ | ✅ | ❌ | ❌ |
| [Backlog.md](https://github.com/MrLesk/Backlog.md) ★6.0k | MIT | ✅ | ⚠️ | ✅ | ⚠️ statuses | ❌ | ❌ | ❌ |
| [Composio/Rube](https://github.com/composiohq/rube), [Unified.to](https://unified.to/) | mixed / SaaS | ⚠️/❌ | ✅ | ✅ | ❌ | ❌ | ❌ | ❌ |

**Claim/lease appears in nothing else surveyed.** Not in Jira, not in Linear, not in the
27k-star agent orchestrators. Every other tool's "assignment" is a last-write-wins field
that two agents can both grab.

## Who should walk away

- **Any team of humans.** kanban-pro is single-user with no auth, no permissions, no
  hosting, at version 0.0.1. Trello, Linear, Jira, Planka, Vikunja, Wekan, Kanboard,
  Taiga and GitHub Projects each beat it on UX, mobile, multi-user and maturity. This
  isn't close.
- **You already live in GitHub.** Use GitHub Projects v2 with the official
  [github-mcp-server](https://github.com/github/github-mcp-server) (★31k, MIT, local and
  hosted transports). Your agents get issues, PRs and the board through one GA server.
- **You want enforced workflows *and* an official MCP.** Use Jira. It is the only
  mainstream product with a genuine server-side transition state machine, and it has had
  one for two decades.
- **You want a fully open-source self-hosted team board.** Vikunja (AGPL, multi-view) or
  Wekan (MIT, actively released). Avoid Focalboard — its README says *"This repository is
  currently not maintained"*, its last release was v8.0.0 in June 2024, and its successor
  [Mattermost Boards is itself in maintenance mode](https://support.mattermost.com/hc/en-us/articles/19614000831252-Ending-support-for-Mattermost-Boards).
  Note also that Planka is **fair-code, not OSI open source**, and Kanboard is in
  maintenance mode.
- **You want agents to help run a light personal board.** [Flux](https://github.com/sirsjg/flux)
  (git-native, active), [Backlog.md](https://github.com/MrLesk/Backlog.md) (★6k,
  markdown-in-git), or [multidimensionalcats/kanban-mcp](https://github.com/multidimensionalcats/kanban-mcp)
  (richest small MCP board — it already ships structured work reports with cycle-time
  metrics).

## Where kanban-pro's differentiation is real

1. **Atomic claim/lease with TTL and heartbeat.** Found in *zero* other surveyed products.
   The strongest and least-replicated idea here.
2. **Resumable cursor + long-poll feed.** Nothing in the open-source or agent set has a
   no-miss tailable cursor. Trello's `since` polling and Linear's GraphQL `updatedAt`
   cursor are the nearest equivalents, and both are proprietary SaaS.
3. **Capability-honest adapters** — every capability reported as `native`, `polyfilled`, or
   `unavailable`. Even the multi-backend proxies (Composio, Unified.to,
   bradrisse/kanban-mcp) expose no such honesty layer.
4. **The combination of all four**, self-hosted, MCP-first.

## Where its differentiation is weak, or already commoditised

Stated plainly, because the README used to imply an empty field:

- **"MCP-first kanban for agents" is crowded.** At least ten MCP kanban/task servers exist
  (Flux, two different `kanban-mcp` projects, Kaban, Backlog.md, claude-task-master,
  shrimp, …). There is no novelty in the category itself.
- **Self-hosted is table stakes.** Every open-source competitor is self-hosted.
- **WIP limits are not unique.** eyalzh/kanban-mcp enforces them; Wekan blocks on a full
  list (whether server-side is unverified).
- **The enforced flow state machine is not a new idea** — Jira has shipped exactly this for
  ~20 years. The novelty is having it in a self-hosted, MCP-first agent board.
- **Structured work reports are not unique.** multidimensionalcats/kanban-mcp already ships
  them with cycle-time metrics.
- **Archive-first delete is not unique.** Backlog.md does it.
- **The backend proxy concept is not new.** Composio/Rube and Unified.to proxy many
  backends; Composio even has a self-host path. The *capability-honesty layer* is the
  differentiator, not the proxying.
- **Single-user with no permissions is a liability**, not a simplification.

## Correction to the previous "no prior art" claim

The README used to assert the concept combination "has no direct prior art". That is
**true only of the precise bundle** — claim/lease + resumable cursor + enforced flow +
capability-honest adapters — and the old wording overreached by implying a green field.

Closest prior art, which the README should name rather than imply away:
[multidimensionalcats/kanban-mcp](https://github.com/multidimensionalcats/kanban-mcp),
[sirsjg/flux](https://github.com/sirsjg/flux), and
[Backlog.md](https://github.com/MrLesk/Backlog.md).

The older claim that classic kanbans are not MCP-native is also **now outdated**. Trello,
Jira, Linear and GitHub all ship *official* MCP servers; Planka, Vikunja, Kanboard, Taiga,
Wekan and Focalboard all have community ones. Practically every classic kanban is
MCP-reachable today. What they lack is not MCP — it is the coordination and safety
semantics above.

## Unverified

Focalboard's exact SPDX licence (GitHub reports `NOASSERTION`); whether Wekan's WIP block
is enforced server-side or only in the client; Kanboard's latest release *year* (sources
conflict between 2024 and 2026); whether Taiga was relicensed from AGPL to MPL-2.0; Linear
having any WIP-limit feature (none found). Several "no claim/lease" entries are inferences
from absent APIs and documentation, not vendor denials.
