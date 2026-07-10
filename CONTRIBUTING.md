# Contributing

kanban-pro is free software under the **AGPL-3.0**. Use it, run it, fork it, change it,
build on it. The deal is simple, and half of it is a legal obligation while the other half
is a request.

## The obligation (the license)

If you distribute a modified version, or **run one as a service other people can reach**,
you must make your modified source available to those users. That's AGPL §13, and it's the
reason this license was chosen over MIT: improvements to a tool like this shouldn't
disappear into somebody's private fork.

You don't owe anyone your source for changes you only run privately, on your own machine.

## The request (the norm)

**If you fix something, send the fix back.** Open a pull request. If you can't or won't,
open an issue describing what you fixed and how — even a paragraph is worth more than
silence.

No license can compel this, and this one doesn't try. But the project is one person's tool
made public in the hope it's useful; the only thing it asks in return is that the next
person who hits your bug doesn't have to solve it again.

**Ideas count too.** If you used it and something was awkward, missing, or wrong-headed,
say so in an issue. A clear description of a problem is a contribution. So is telling me
the design is wrong — see `AGENTS.md`, which asks the same of the coding agents that work
on this repo.

## Practically

Before opening a PR:

```bash
uv sync
uv run ruff format . && uv run ruff check . && uv run mypy kanban_pro && uv run pytest
```

All four must pass. Then:

- **Read [AGENTS.md](AGENTS.md)** — the conventions are short and they're enforced: only
  canonical models cross the port, backend-specific fields live in `ext`, no adapter is
  called directly by an interface layer, no speculative abstraction.
- **Adding an adapter?** [docs/adapter-structure.md](docs/adapter-structure.md) has the
  full recipe, and your adapter must pass the shared contract suite in `tests/`.
- **Changed the MCP tool surface?** Regenerate the tool reference embedded in the example
  skills: `uv run python -m tests.toolref --write`. `tests/test_toolref.py` fails until you
  do.
- **Record the *why*** in [JOURNAL.md](JOURNAL.md) — newest entry first, what changed and
  what decision or gotcha drove it. A file list is not a journal entry.

Tests come with behaviour changes. A bug fix without a test that would have caught it is
half a fix.

## Contributor licensing

By opening a pull request you agree that your contribution is licensed under the AGPL-3.0,
the same as the rest of the project. There's no CLA and no copyright assignment — you keep
your copyright.
