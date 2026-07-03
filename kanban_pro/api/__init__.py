"""FastAPI layer: maps the canonical REST API onto the active KanbanBackend.

Talks to the port only — never imports a specific adapter. Adapter selection happens
in kanban_pro.config; this layer receives whatever the port resolves to.

TODO: routers for boards / columns / cards + GET /capabilities.
"""
