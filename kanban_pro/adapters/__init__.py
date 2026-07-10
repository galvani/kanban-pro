"""Backend adapters: one module per backend, each implements KanbanBackend.

An adapter declares only what its backend does NATIVELY. kanban-pro then wraps it in an
`AugmentingBackend` decorator (adapter + overlay store) that POLYFILLS the missing
capabilities from its own store, so callers see the full canonical surface regardless of
the backend's gaps (SPEC decision 2). Adapters therefore stay thin — map the backend, be
honest about capabilities; the overlay handles the rest.

To add a backend, see AGENTS.md ("Authoring a new adapter"). Adapters today:
  - native.py   — kanban-pro's own persistent store (SQLite); ALSO serves as the overlay
                  that polyfills other backends. 13 native capabilities.
  - memory.py   — in-memory reference adapter (the port's proving ground + test fixture)
  - hermes/     — the Hermes harness board: direct SQLite reads, `hermes kanban` CLI
                  writes. Thin, BaseAdapter-based, 7 native capabilities.

The adapter REGISTRY (name -> async factory) lives in `kanban_pro/config.py`, NOT here —
`config.py` resolves `--profile` to a factory and wraps the result in the core stack.
"""
