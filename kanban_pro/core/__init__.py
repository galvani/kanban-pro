"""The core service layer over the port.

Interfaces (mcp/cli/api) call core, never an adapter directly. v0 seeds it with the
archive-first delete guard; the augmenting dispatch (adapter + overlay), idempotency
dedupe, workflow/WIP enforcement, and the event change-log all land here per the SPEC
roadmap (v1/v2).
"""

from __future__ import annotations

from kanban_pro.core.augment import AugmentingBackend, fulfilments
from kanban_pro.ports import Conflict, KanbanBackend, NotFound

__all__ = [
    "AugmentingBackend",
    "fulfilments",
    "delete_card_guarded",
    "delete_board_guarded",
    "delete_column_guarded",
]

# Adapters purge unconditionally; the guards below are core's, so no interface can
# bypass them (SPEC decision 7 + Q14).


async def delete_card_guarded(backend: KanbanBackend, card_id: str) -> None:
    """Permanent purge, permitted only on an already-archived card (decision 7)."""
    card = await backend.get_card(card_id)
    if not card.archived:
        raise Conflict(f"card {card_id!r} is not archived — archive_card it first, then delete")
    await backend.delete_card(card_id)


async def delete_board_guarded(backend: KanbanBackend, board_id: str) -> None:
    """Empty-only board delete (Q14): refuse while LIVE cards remain.

    Archived leftovers cascade away with the board (their placements are dropped by
    the adapter).
    """
    live = await backend.list_cards(board_id)  # excludes archived by port contract
    if live:
        raise Conflict(
            f"board {board_id!r} still has {len(live)} live card(s) — move or archive them first"
        )
    await backend.delete_board(board_id)


async def delete_column_guarded(backend: KanbanBackend, column_id: str) -> None:
    """Empty-only column delete (Q14): refuse while LIVE cards sit in the column."""
    # the port has no column->board lookup, so find the owning board here.
    for board in await backend.list_boards():
        if not any(c.id == column_id for c in board.columns):
            continue
        live = [
            card
            for card in await backend.list_cards(board.id)
            if any(p.column_id == column_id for p in card.placements)
        ]
        if live:
            raise Conflict(
                f"column {column_id!r} still has {len(live)} live card(s)"
                " — move or archive them first"
            )
        await backend.delete_column(column_id)
        return
    raise NotFound(f"column {column_id!r} not found")
