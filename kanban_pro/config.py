"""Profile-based backend selection.

A *profile* bundles a chosen adapter with its settings (base URL, token, …). The
active profile is picked at startup via `--profile` (CLI) / `KANBAN_PRO_PROFILE`
(env), e.g. `--profile hermes`, `--profile jira`, `--profile default`.

The active profile also determines the *exposed API surface*: kanban-pro advertises
only the operations the locked-in provider actually supports (see the adapter's
declared Capability set). See SPEC.md, decisions 2 & 3.

TODO: define Profile (name -> adapter + settings), a registry, and resolution from
CLI/env. Ground the `hermes` profile in Hermes's real kanban API.
"""
