# Ask an AI agent about this — don't read the repo

You don't have to read this repository to find out whether it's for you. Ask an agent that
can browse — Claude Code, Codex, OpenCode, Hermes, ChatGPT, whatever you use.

## Do I need this?

Paste this into your agent:

```text
Do I need this? https://github.com/galvani/kanban-pro
```

Any decent agent will fetch the repo and find [`llms.txt`](llms.txt), which is written for
it rather than for you: what kanban-pro is, what genuinely works today versus what's still
planned, who should use it, **who should walk away**, how it compares to a plain kanban /
Jira+MCP / your agent's own to-do list, what adopting it costs, and what AGPL-3.0
means for what you're planning to build.
It's told not to sell you anything and to say plainly if you don't need it.

If your agent doesn't go looking for `llms.txt` on its own, point it there:

```text
Read https://raw.githubusercontent.com/galvani/kanban-pro/main/llms.txt
and tell me honestly whether I need this. I don't want a sales pitch.
```

## Now install it

Once you've decided, in the same conversation:

```text
Yes — install it for me and prove it works.
```

`llms.txt` carries the install path for each harness (Claude Code, Codex, OpenCode,
Hermes), so the agent runs the commands rather than handing them to you. It needs
[`uv`](https://docs.astral.sh/uv/) and Python 3.12+; no clone is required, because `uvx`
builds straight from git. It's told to verify the package builds *before* editing any
config of yours, to back up anything it touches, and to prove the server works by calling
the tools — creating a board, moving a card, and checking the change-log carries its actor
— rather than by trusting that a config entry means success.

Nothing runs in the background afterwards. Your harness spawns the server over stdio only
while it's in use, the board is a SQLite file under `~/.local/share/kanban-pro/`, and
uninstalling means removing one config entry and deleting that directory.

## Prefer to read it yourself?

The [README](README.md) is the human version, and the
[configuration guide](docs/configuration.md) covers workflow rules, WIP limits, the
attention flag, and change-feed listeners.
