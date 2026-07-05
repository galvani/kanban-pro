"""Tests for the native SQLite store — port behavior + real persistence across reopen."""

from __future__ import annotations

import asyncio
from pathlib import Path

import pytest

from kanban_pro.adapters.native import NativeStore
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
from kanban_pro.ports import Capability, Conflict, KanbanBackend, NotFound


def test_conforms_to_port() -> None:
    # Structural conformance (mypy verifies NativeStore satisfies the Protocol).
    backend: KanbanBackend = NativeStore("unused.db")
    assert Capability.ARCHIVE in backend.capabilities


def test_persists_across_reopen(tmp_path: Path) -> None:
    asyncio.run(_persists_across_reopen(tmp_path / "k.db"))


async def _persists_across_reopen(db: Path) -> None:
    store = await NativeStore.open(db)
    board = await store.create_board(
        Board(
            name="Work",
            columns=[
                Column(name="Todo", category=ColumnCategory.UNSTARTED),
                Column(name="Done", category=ColumnCategory.DONE),
            ],
        )
    )
    todo, done = await store.list_columns(board.id)
    card = await store.create_card(
        Card(title="task", placements=[Placement(board_id=board.id, column_id=todo.id)])
    )
    await store.move_card(card.id, board.id, done.id, 0)

    # reopen a FRESH store on the same file — data must survive
    reopened = await NativeStore.open(db)
    assert len(await reopened.list_boards()) == 1
    cards = await reopened.list_cards(board.id)
    assert len(cards) == 1
    assert cards[0].placements[0].column_id == done.id  # move persisted
    assert cards[0].created_at is not None


def test_archive_hides_and_update_patches(tmp_path: Path) -> None:
    asyncio.run(_archive_and_patch(tmp_path / "k.db"))


async def _archive_and_patch(db: Path) -> None:
    store = await NativeStore.open(db)
    board = await store.create_board(Board(name="B", columns=[Column(name="C")]))
    (col,) = await store.list_columns(board.id)
    card = await store.create_card(
        Card(title="t", placements=[Placement(board_id=board.id, column_id=col.id)])
    )

    upd = await store.update_card(card.id, CardPatch(title="t2"))
    assert upd.title == "t2"
    assert upd.placements[0].column_id == col.id  # placement untouched by patch

    await store.archive_card(card.id)
    assert await store.list_cards(board.id) == []
    assert (await store.get_card(card.id)).archived is True
    await store.unarchive_card(card.id)
    assert len(await store.list_cards(board.id)) == 1


def test_delete_card_cascades(tmp_path: Path) -> None:
    asyncio.run(_delete_cascades(tmp_path / "k.db"))


async def _delete_cascades(db: Path) -> None:
    store = await NativeStore.open(db)
    board = await store.create_board(Board(name="B", columns=[Column(name="C")]))
    (col,) = await store.list_columns(board.id)
    c1 = await store.create_card(
        Card(title="a", placements=[Placement(board_id=board.id, column_id=col.id)])
    )
    c2 = await store.create_card(
        Card(title="b", placements=[Placement(board_id=board.id, column_id=col.id)])
    )
    await store.add_comment(Comment(card_id=c1.id, author="u1", body="hi"))
    await store.add_relation(Relation(kind=RelationKind.BLOCKS, from_card=c1.id, to_card=c2.id))

    await store.delete_card(c1.id)
    assert await store.list_comments(c1.id) == []
    assert await store.list_relations(c2.id) == []
    with pytest.raises(NotFound):
        await store.get_card(c1.id)


def test_create_card_requires_placement(tmp_path: Path) -> None:
    asyncio.run(_requires_placement(tmp_path / "k.db"))


async def _requires_placement(db: Path) -> None:
    store = await NativeStore.open(db)
    with pytest.raises(ValueError, match="placement"):
        await store.create_card(Card(title="nowhere"))


async def _placements_and_ext_merge(path: Path) -> None:
    store = await NativeStore.open(path)
    b1 = await store.create_board(Board(name="A", columns=[Column(name="t")]))
    b2 = await store.create_board(Board(name="B", columns=[Column(name="i")]))
    card = await store.create_card(
        Card(
            title="c",
            ext={"a": 1},
            placements=[Placement(board_id=b1.id, column_id=b1.columns[0].id)],
        )
    )
    with pytest.raises(NotFound):  # strict move (Q16)
        await store.move_card(card.id, b2.id, b2.columns[0].id, 0)
    await store.add_placement(card.id, Placement(board_id=b2.id, column_id=b2.columns[0].id))
    await store.update_card(card.id, CardPatch(ext={"b": 2, "a": None}))

    reopened = await NativeStore.open(path)  # placements + merged ext persist
    got = await reopened.get_card(card.id)
    assert {p.board_id for p in got.placements} == {b1.id, b2.id}
    assert got.ext == {"b": 2}
    got = await reopened.remove_placement(card.id, b1.id)
    assert [p.board_id for p in got.placements] == [b2.id]
    with pytest.raises(Conflict):  # last placement is protected
        await reopened.remove_placement(card.id, b2.id)


def test_placements_and_ext_merge(tmp_path: Path) -> None:
    asyncio.run(_placements_and_ext_merge(tmp_path / "k.db"))
