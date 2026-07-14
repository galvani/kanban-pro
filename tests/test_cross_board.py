"""Cards that live on TWO boards — sharing (add_placement) vs copying (copy_card).

SPEC decision 4: a card taken on by another board is SHARED (one record, two lanes); a card
merely tried out by another board is COPIED (independent, nothing flows back). Multi-placed
cards are routine, so the "which placement?" bugs these tests pin are real.
"""

from __future__ import annotations

import asyncio
from pathlib import Path

import pytest

from kanban_pro.adapters.native import NativeStore
from kanban_pro.core.copy import copy_card
from kanban_pro.domain import (
    Board,
    Card,
    Checklist,
    ChecklistItem,
    Column,
    ColumnCategory,
    Label,
    Placement,
)
from kanban_pro.ports import Conflict, NotFound


async def _two_boards(tmp_path: Path) -> tuple[NativeStore, Board, Board]:
    store = await NativeStore.open(tmp_path / "k.db")
    origin = await store.create_board(
        Board(
            name="Theirs",
            labels=[Label(name="bug"), Label(name="ops-only")],
            columns=[
                Column(name="Todo", category=ColumnCategory.UNSTARTED),
                Column(name="Done", category=ColumnCategory.DONE),
            ],
        )
    )
    mine = await store.create_board(
        Board(
            name="Mine",
            labels=[Label(name="bug")],  # deliberately lacks `ops-only`
            columns=[
                Column(name="Doing", category=ColumnCategory.STARTED),
                Column(name="Shipped", category=ColumnCategory.DONE),
            ],
        )
    )
    return store, origin, mine


def test_add_placement_rejects_a_column_the_board_does_not_have(tmp_path: Path) -> None:
    asyncio.run(_test_add_placement_rejects_a_column_the_board_does_not_have(tmp_path))


async def _test_add_placement_rejects_a_column_the_board_does_not_have(tmp_path: Path) -> None:
    # without this guard the card is ON the board but off every lane view — present in the
    # data, invisible on the wall. move_card has always refused this; add_placement didn't.
    store, origin, mine = await _two_boards(tmp_path)
    card = await store.create_card(
        Card(
            title="t",
            placements=[Placement(board_id=origin.id, column_id=origin.columns[0].id)],
        )
    )
    with pytest.raises(NotFound):
        await store.add_placement(card.id, Placement(board_id=mine.id, column_id="typo"))


def test_shared_card_holds_a_different_lane_per_board(tmp_path: Path) -> None:
    asyncio.run(_test_shared_card_holds_a_different_lane_per_board(tmp_path))


async def _test_shared_card_holds_a_different_lane_per_board(tmp_path: Path) -> None:
    store, origin, mine = await _two_boards(tmp_path)
    card = await store.create_card(
        Card(
            title="shared",
            placements=[Placement(board_id=origin.id, column_id=origin.columns[0].id)],
        )
    )
    await store.add_placement(card.id, Placement(board_id=mine.id, column_id=mine.columns[0].id))
    await store.move_card(card.id, mine.id, mine.columns[1].id, 0)

    got = await store.get_card(card.id)
    lanes = {p.board_id: p.column_id for p in got.placements}
    assert lanes[mine.id] == mine.columns[1].id  # Shipped on my board
    assert lanes[origin.id] == origin.columns[0].id  # still Todo on theirs — lanes are independent


def test_copy_is_detached_and_carries_only_the_work_statement(tmp_path: Path) -> None:
    asyncio.run(_test_copy_is_detached_and_carries_only_the_work_statement(tmp_path))


async def _test_copy_is_detached_and_carries_only_the_work_statement(tmp_path: Path) -> None:
    store, origin, mine = await _two_boards(tmp_path)
    theirs = await store.create_card(
        Card(
            title="Fix the thing",
            description="steps",
            labels=[lbl.id for lbl in origin.labels],  # bug + ops-only
            assignees=["user:someone-else"],
            checklists=[
                Checklist(title="DoD", items=[ChecklistItem(text="tests", done=True)]),
            ],
            ext={"work_report": {"verdict": "theirs"}},
            placements=[Placement(board_id=origin.id, column_id=origin.columns[0].id)],
        )
    )

    copy = await copy_card(store, theirs.id, mine.id, mine.columns[0].id)

    assert copy.id != theirs.id  # fresh id, minted by MY board's scheme
    assert copy.title == "Fix the thing"
    assert copy.description == "steps"
    assert [p.board_id for p in copy.placements] == [mine.id]  # only on my board
    assert copy.assignees == []  # not carrying their assignee
    assert copy.ext == {}  # no work report, no attention, no claim
    # labels travel by NAME (ids are board-scoped): `bug` maps, `ops-only` doesn't exist here
    assert copy.labels == [next(lbl.id for lbl in mine.labels if lbl.name == "bug")]
    # their definition of done, not their progress
    item = copy.checklists[0].items[0]
    assert (item.text, item.done) == ("tests", False)
    assert item.id != theirs.checklists[0].items[0].id  # nested ids freshly minted

    # the original is untouched — that is the whole point
    after = await store.get_card(theirs.id)
    assert after.ext == {"work_report": {"verdict": "theirs"}}
    assert len(after.placements) == 1

    # ...tied only by traceability metadata
    kinds = {(r.kind, r.from_card, r.to_card) for r in await store.list_relations(theirs.id)}
    assert ("duplicates", copy.id, theirs.id) in kinds


def test_copy_rejects_an_unknown_column(tmp_path: Path) -> None:
    asyncio.run(_test_copy_rejects_an_unknown_column(tmp_path))


async def _test_copy_rejects_an_unknown_column(tmp_path: Path) -> None:
    store, origin, mine = await _two_boards(tmp_path)
    card = await store.create_card(
        Card(
            title="t",
            placements=[Placement(board_id=origin.id, column_id=origin.columns[0].id)],
        )
    )
    with pytest.raises(NotFound):
        await copy_card(store, card.id, mine.id, "nope")


def test_copy_without_link_leaves_no_relation(tmp_path: Path) -> None:
    asyncio.run(_test_copy_without_link_leaves_no_relation(tmp_path))


async def _test_copy_without_link_leaves_no_relation(tmp_path: Path) -> None:
    store, origin, mine = await _two_boards(tmp_path)
    card = await store.create_card(
        Card(
            title="t",
            placements=[Placement(board_id=origin.id, column_id=origin.columns[0].id)],
        )
    )
    await copy_card(store, card.id, mine.id, mine.columns[0].id, link=False)
    assert await store.list_relations(card.id) == []


def test_last_placement_cannot_be_removed(tmp_path: Path) -> None:
    asyncio.run(_test_last_placement_cannot_be_removed(tmp_path))


async def _test_last_placement_cannot_be_removed(tmp_path: Path) -> None:
    store, origin, _mine = await _two_boards(tmp_path)
    card = await store.create_card(
        Card(
            title="t",
            placements=[Placement(board_id=origin.id, column_id=origin.columns[0].id)],
        )
    )
    with pytest.raises(Conflict):
        await store.remove_placement(card.id, origin.id)
