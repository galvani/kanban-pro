"""Tests for the native SQLite store — the shared contract + real persistence across
reopen (the part only this adapter can prove)."""

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
    Placement,
)
from kanban_pro.ports import Capability, KanbanBackend
from tests.contract_suite import KanbanContract


def test_conforms_to_port() -> None:
    # Structural conformance (mypy verifies NativeStore satisfies the Protocol).
    backend: KanbanBackend = NativeStore("unused.db")
    assert Capability.ARCHIVE in backend.capabilities


class TestNativeContract(KanbanContract):
    @pytest.fixture(autouse=True)
    def _tmp(self, tmp_path: Path) -> None:
        self._dir = tmp_path

    async def _backend(self) -> KanbanBackend:
        return await NativeStore.open(self._dir / "contract.db")


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
        Card(
            title="task",
            ext={"a": 1},
            placements=[Placement(board_id=board.id, column_id=todo.id)],
        )
    )
    await store.move_card(card.id, board.id, done.id, 0)
    b2 = await store.create_board(Board(name="B2", columns=[Column(name="i")]))
    await store.add_placement(card.id, Placement(board_id=b2.id, column_id=b2.columns[0].id))
    await store.update_card(card.id, CardPatch(ext={"b": 2, "a": None}))

    # reopen a FRESH store on the same file — data must survive
    reopened = await NativeStore.open(db)
    got = await reopened.get_card(card.id)
    assert got.created_at is not None
    assert len(got.placements) == 2  # add_placement persisted
    assert next(p for p in got.placements if p.board_id == board.id).column_id == done.id
    assert got.ext == {"b": 2}  # merged ext persisted (Q17)
    assert len(await reopened.list_boards()) == 2
