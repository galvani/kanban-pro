"""Card priority (0-10, higher = more urgent) and how it orders the work queue.

Priority orders WITHIN a category tier and never across tiers — started work is still
offered before a shinier unstarted card, or an agent abandons whatever it holds every time
something urgent lands.
"""

from __future__ import annotations

import asyncio

import pytest
from pydantic import ValidationError

from kanban_pro.adapters.memory import MemoryBackend
from kanban_pro.core import AugmentingBackend, ChangeLog, RecordingBackend
from kanban_pro.domain import Board, Card, CardPatch, Column, ColumnCategory, Placement


def _stack() -> RecordingBackend:
    return RecordingBackend(AugmentingBackend(MemoryBackend()), ChangeLog(), "agent:t")


def test_priority_is_bounded_0_to_10() -> None:
    # a free-form int would let a caller "win" the queue forever with 9999
    for bad in (-1, 11):
        with pytest.raises(ValidationError):
            Card(title="t", priority=bad)
        with pytest.raises(ValidationError):
            CardPatch(priority=bad)
    assert Card(title="t").priority == 0  # unprioritised by default


def test_queue_orders_by_priority_within_a_tier_not_across() -> None:
    asyncio.run(_queue_orders_by_priority_within_a_tier_not_across())


async def _queue_orders_by_priority_within_a_tier_not_across() -> None:
    be = _stack()
    board = await be.create_board(
        Board(
            name="B",
            columns=[
                Column(name="todo", category=ColumnCategory.UNSTARTED),
                Column(name="doing", category=ColumnCategory.STARTED),
            ],
        )
    )
    todo, doing = board.columns

    async def card(title: str, column: Column, priority: int) -> Card:
        return await be.create_card(
            Card(
                title=title,
                priority=priority,
                assignees=["agent:t"],
                placements=[Placement(board_id=board.id, column_id=column.id)],
            )
        )

    await card("urgent-but-unstarted", todo, 10)
    await card("in-flight-boring", doing, 2)
    await card("in-flight-urgent", doing, 9)
    await card("queued-mild", todo, 4)

    queue = await be.list_work("agent:t")
    assert [i.card.title for i in queue.items] == [
        "in-flight-urgent",  # started tier, by priority
        "in-flight-boring",
        "urgent-but-unstarted",  # only THEN the unstarted tier, by priority
        "queued-mild",
    ]


def test_priority_is_patchable_and_persists() -> None:
    asyncio.run(_priority_is_patchable_and_persists())


async def _priority_is_patchable_and_persists() -> None:
    be = _stack()
    board = await be.create_board(Board(name="B", columns=[Column(name="todo")]))
    card = await be.create_card(
        Card(title="t", placements=[Placement(board_id=board.id, column_id=board.columns[0].id)])
    )
    assert card.priority == 0

    await be.update_card(card.id, CardPatch(priority=7))
    assert (await be.get_card(card.id)).priority == 7
