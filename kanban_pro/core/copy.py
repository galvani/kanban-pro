"""`copy_card` — a DETACHED duplicate of a card on another board.

The counterpart to `add_placement` (SPEC decision 4). Sharing a card is write-back *by
construction*: one claim, one attention flag, one work report, so whatever you do lands on
the original — and through a mirrored backend, on the original's Jira issue. That is right
when you are taking the work on, and exactly wrong when the card is **not yours to work**
and you just want to try it: a spike, a "how would I have done this", an experiment you may
throw away. For that, the tie to the original must not exist.

So a copy carries the WORK STATEMENT and nothing about who was doing it or how far they
got. Copied: title, description, checklists (items reset to not-done — you want their
definition of done, not their progress), dates, labels (remapped by NAME into the target
board's registry, since label ids are board-scoped; unmatched labels are dropped),
attachments. Dropped: comments, work report, attention flag, claim, assignees, `ext`, and
the change-log. The new card mints a **fresh id from the target board's scheme** and starts
clean.

The only tie is a `duplicates` relation (the copy is the redundant one; the original is the
one to keep) — pure metadata for traceability. Nothing propagates in either direction. If
you want propagation, you wanted `add_placement`.
"""

from __future__ import annotations

from kanban_pro.domain import (
    Attachment,
    Card,
    Checklist,
    ChecklistItem,
    Placement,
    Relation,
    RelationKind,
)
from kanban_pro.ports import KanbanBackend, NotFound


async def _label_names(backend: KanbanBackend, card: Card) -> list[str]:
    """The card's label ids resolved to names, via the registries of the boards it sits on.

    Labels are board-scoped (`Board.labels`), so an id means nothing on another board — a
    copy has to travel by name or arrive with dangling references.
    """
    registry: dict[str, str] = {}
    for placement in card.placements:
        board = await backend.get_board(placement.board_id)
        registry.update({label.id: label.name for label in board.labels})
    return [registry[lid] for lid in card.labels if lid in registry]


async def copy_card(
    backend: KanbanBackend,
    card_id: str,
    to_board_id: str,
    to_column_id: str,
    position: int = 0,
    link: bool = True,
) -> Card:
    """Duplicate `card_id` onto `to_board_id` as an independent card. See the module docstring."""
    origin = await backend.get_card(card_id)
    names = await _label_names(backend, origin)
    target = await backend.get_board(to_board_id)
    if all(c.id != to_column_id for c in target.columns):
        raise NotFound(f"board {to_board_id!r} has no column {to_column_id!r}")
    by_name = {label.name: label.id for label in target.labels}

    copy = Card(
        title=origin.title,
        description=origin.description,
        labels=[by_name[n] for n in names if n in by_name],
        start_date=origin.start_date,
        due_date=origin.due_date,
        # rebuilt, not model_copy'd: nested ids must be freshly minted, not shared with the
        # original's checklist/attachment rows.
        checklists=[
            Checklist(
                title=cl.title,
                items=[ChecklistItem(text=i.text, order=i.order) for i in cl.items],
            )
            for cl in origin.checklists
        ],
        attachments=[Attachment(url=a.url, title=a.title) for a in origin.attachments],
        placements=[Placement(board_id=to_board_id, column_id=to_column_id, position=position)],
    )
    created = await backend.create_card(copy)
    if link:
        await backend.add_relation(
            Relation(kind=RelationKind.DUPLICATES, from_card=created.id, to_card=origin.id)
        )
    return created
