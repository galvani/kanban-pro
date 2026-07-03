"""Tests for the in-memory reference adapter — exercises the whole KanbanBackend port."""

from __future__ import annotations

import asyncio

import pytest

from kanban_pro.adapters.memory import MemoryBackend
from kanban_pro.domain import (
    Board,
    Card,
    CardPatch,
    Column,
    ColumnCategory,
    Comment,
    Placement,
    Relation,
    RelationKind,
)
from kanban_pro.ports import Capability, KanbanBackend, NotFound


def test_conforms_to_port() -> None:
    # Structural conformance check (mypy verifies MemoryBackend satisfies the Protocol).
    backend: KanbanBackend = MemoryBackend()
    assert Capability.ARCHIVE in backend.capabilities


def test_board_and_card_lifecycle() -> None:
    asyncio.run(_board_and_card_lifecycle())


async def _board_and_card_lifecycle() -> None:
    be = MemoryBackend()
    board = await be.create_board(
        Board(
            name="Work",
            columns=[
                Column(name="Todo", category=ColumnCategory.UNSTARTED),
                Column(name="Done", category=ColumnCategory.DONE),
            ],
        )
    )
    todo, done = await be.list_columns(board.id)

    card = await be.create_card(
        Card(title="task", placements=[Placement(board_id=board.id, column_id=todo.id)])
    )
    assert card.created_at is not None
    assert [c.id for c in await be.list_cards(board.id)] == [card.id]

    moved = await be.move_card(card.id, board.id, done.id, 0)
    assert moved.placements[0].column_id == done.id

    upd = await be.update_card(card.id, CardPatch(title="task2"))
    assert upd.title == "task2"
    # unset fields untouched
    assert upd.placements[0].column_id == done.id

    # archive hides from listing but the card is still directly gettable
    await be.archive_card(card.id)
    assert await be.list_cards(board.id) == []
    assert (await be.get_card(card.id)).archived is True
    await be.unarchive_card(card.id)
    assert len(await be.list_cards(board.id)) == 1

    await be.delete_card(card.id)
    with pytest.raises(NotFound):
        await be.get_card(card.id)


def test_comments_and_relations_cascade_on_delete() -> None:
    asyncio.run(_comments_and_relations())


async def _comments_and_relations() -> None:
    be = MemoryBackend()
    board = await be.create_board(Board(name="B", columns=[Column(name="C")]))
    (col,) = await be.list_columns(board.id)
    c1 = await be.create_card(
        Card(title="a", placements=[Placement(board_id=board.id, column_id=col.id)])
    )
    c2 = await be.create_card(
        Card(title="b", placements=[Placement(board_id=board.id, column_id=col.id)])
    )

    cm = await be.add_comment(Comment(card_id=c1.id, author="u1", body="hi"))
    assert [x.id for x in await be.list_comments(c1.id)] == [cm.id]

    rel = await be.add_relation(Relation(kind=RelationKind.BLOCKS, from_card=c1.id, to_card=c2.id))
    assert [r.id for r in await be.list_relations(c2.id)] == [rel.id]

    # deleting a card cascades its comments + relations
    await be.delete_card(c1.id)
    assert await be.list_comments(c1.id) == []
    assert await be.list_relations(c2.id) == []


def test_missing_lookups_raise_not_found() -> None:
    asyncio.run(_missing_lookups())


async def _missing_lookups() -> None:
    be = MemoryBackend()
    with pytest.raises(NotFound):
        await be.get_board("nope")
    with pytest.raises(NotFound):
        await be.get_card("nope")


def test_create_card_requires_a_placement() -> None:
    asyncio.run(_create_needs_placement())


async def _create_needs_placement() -> None:
    be = MemoryBackend()
    with pytest.raises(ValueError, match="placement"):
        await be.create_card(Card(title="nowhere"))
