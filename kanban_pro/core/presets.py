"""Board presets — starting shapes for `init_board` onboarding.

A preset is columns + a board flow, defined together in code (not a config file), so a
fresh board comes up governed and consistent. Column ids are minted as
``f"{board_id}:{slug}"`` and the flow's edges reference those ids — columns and flow are
built as one unit, so a preset flow can never dangle.

Presets:
- ``blank``          — a board with no columns and no flow (build it yourself; free-roam).
- ``simple-kanban``  — todo → doing → done.
- ``docs``           — todo → ready → running → done (no review gate).
- ``agent-lifecycle``— the Hermes swarm lifecycle (triage … done + review / waiting-for-mr
  / staging / won't-do), the shape the shared board runs today.

Onboarding's third option, IMPORT (from Hermes or another store), is `kanban-pro-migrate`
— not a preset.
"""

from __future__ import annotations

from kanban_pro.domain import Board, BoardFlow, Column, ColumnCategory

_C = ColumnCategory

#: preset -> ordered [(slug, display name, category)]
_COLUMNS: dict[str, list[tuple[str, str, ColumnCategory]]] = {
    "blank": [],
    "simple-kanban": [
        ("todo", "todo", _C.BACKLOG),
        ("doing", "doing", _C.STARTED),
        ("done", "done", _C.DONE),
    ],
    "docs": [
        ("todo", "todo", _C.BACKLOG),
        ("ready", "ready", _C.UNSTARTED),
        ("running", "running", _C.STARTED),
        ("done", "done", _C.DONE),
    ],
    "agent-lifecycle": [
        ("triage", "triage", _C.TRIAGE),
        ("todo", "todo", _C.BACKLOG),
        ("scheduled", "scheduled", _C.BACKLOG),
        ("ready", "ready", _C.UNSTARTED),
        ("running", "running", _C.STARTED),
        ("blocked", "blocked", _C.STARTED),
        ("review", "review", _C.STARTED),
        ("waiting-for-mr", "waiting for mr", _C.STARTED),
        ("staging", "staging", _C.BACKLOG),
        ("wont-do", "won't do", _C.CANCELED),
        ("done", "done", _C.DONE),
    ],
}

#: preset -> {from slug: [to slugs]} (empty ⇒ free-roam)
_TRANSITIONS: dict[str, dict[str, list[str]]] = {
    "blank": {},
    "simple-kanban": {
        "todo": ["doing"],
        "doing": ["done", "todo"],
        "done": ["todo"],
    },
    "docs": {
        "todo": ["ready"],
        "ready": ["running"],
        "running": ["done", "ready"],
        "done": ["ready"],
    },
    "agent-lifecycle": {
        "triage": ["todo", "ready", "wont-do"],
        "todo": ["ready", "scheduled", "triage", "staging", "wont-do"],
        "scheduled": ["ready", "todo", "wont-do"],
        "ready": ["running", "todo", "blocked", "staging", "wont-do"],
        "running": ["done", "review", "blocked", "ready", "waiting-for-mr", "staging", "wont-do"],
        "blocked": ["ready", "todo", "running", "wont-do"],
        "review": ["done", "running", "ready", "waiting-for-mr", "staging", "wont-do"],
        "waiting-for-mr": ["done", "blocked", "ready"],
        "staging": ["ready", "todo", "running", "review"],
        "wont-do": ["triage", "todo"],  # reopen
        "done": ["ready"],  # deliberate reopen
    },
}

PRESETS: tuple[str, ...] = tuple(_COLUMNS)


def build_preset_board(board_id: str, name: str, preset: str) -> Board:
    """Materialise a preset into a Board (columns + flow), ids namespaced by board_id."""
    if preset not in _COLUMNS:
        raise ValueError(f"unknown preset {preset!r} (known: {', '.join(PRESETS)})")
    columns = [
        Column(id=f"{board_id}:{slug}", name=display, order=i, category=category)
        for i, (slug, display, category) in enumerate(_COLUMNS[preset])
    ]
    transitions = {
        f"{board_id}:{src}": [f"{board_id}:{dst}" for dst in dsts]
        for src, dsts in _TRANSITIONS[preset].items()
    }
    flow = BoardFlow(transitions=transitions) if transitions else None
    return Board(id=board_id, name=name, columns=columns, flow=flow)
