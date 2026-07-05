"""The core service layer over the port.

Interfaces (mcp/cli/api) call core, never an adapter directly. v0 seeds it with the
archive-first delete guard; the augmenting dispatch (adapter + overlay), idempotency
dedupe, workflow/WIP enforcement, and the event change-log all land here per the SPEC
roadmap (v1/v2).
"""

from __future__ import annotations

from kanban_pro.ports import Conflict, KanbanBackend


async def delete_card_guarded(backend: KanbanBackend, card_id: str) -> None:
    """Permanent purge, permitted only on an already-archived card (SPEC decision 7).

    Adapters purge unconditionally; this guard is core's, so no interface can bypass
    the archive-first rule.
    """
    card = await backend.get_card(card_id)
    if not card.archived:
        raise Conflict(f"card {card_id!r} is not archived — archive_card it first, then delete")
    await backend.delete_card(card_id)
