"""Backend adapters: one module per backend, each implements KanbanBackend.

To add a backend, see AGENTS.md ("Authoring a new adapter"). Planned first adapters:
  - hermes.py   — calls the Hermes kanban implementation (confirm its API first)
  - memory.py   — in-memory reference adapter (the port's proving ground + test fixture)
"""
