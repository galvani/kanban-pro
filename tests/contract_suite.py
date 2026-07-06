"""Shared adapter contract suite (docs/adapter-structure.md).

Every adapter must behave identically at the port. A test module subclasses
`KanbanContract` (name starting with `Test`), implements `_backend()`, and inherits
the whole behavioral suite. Adapter-specific behavior (e.g. native persistence across
reopen) stays in the adapter's own test module.
"""

from __future__ import annotations

import asyncio

import pytest

from kanban_pro.domain import (
    Board,
    BoardPatch,
    Card,
    CardPatch,
    Column,
    ColumnCategory,
    Comment,
    Placement,
    Relation,
    RelationKind,
)
from kanban_pro.ports import Conflict, KanbanBackend, NotFound


class KanbanContract:
    """The behavioral contract every KanbanBackend implementation must pass."""

    async def _backend(self) -> KanbanBackend:
        raise NotImplementedError

    # --- lifecycle ---

    def test_board_and_card_lifecycle(self) -> None:
        asyncio.run(self._board_and_card_lifecycle())

    async def _board_and_card_lifecycle(self) -> None:
        be = await self._backend()
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
        assert upd.placements[0].column_id == done.id  # unset fields untouched

        # archive hides from listing but stays discoverable via include_archived
        await be.archive_card(card.id)
        assert await be.list_cards(board.id) == []
        assert [c.id for c in await be.list_cards(board.id, include_archived=True)] == [card.id]
        assert (await be.get_card(card.id)).archived is True
        await be.unarchive_card(card.id)
        assert len(await be.list_cards(board.id)) == 1

        await be.delete_card(card.id)
        with pytest.raises(NotFound):
            await be.get_card(card.id)

    # --- comments & relations ---

    def test_comments_and_relations_cascade_on_delete(self) -> None:
        asyncio.run(self._comments_and_relations())

    async def _comments_and_relations(self) -> None:
        be = await self._backend()
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

        rel = await be.add_relation(
            Relation(kind=RelationKind.BLOCKS, from_card=c1.id, to_card=c2.id)
        )
        assert [r.id for r in await be.list_relations(c2.id)] == [rel.id]

        # deleting a card cascades its comments + relations
        await be.delete_card(c1.id)
        assert await be.list_comments(c1.id) == []
        assert await be.list_relations(c2.id) == []

    # --- errors ---

    def test_missing_lookups_raise_not_found(self) -> None:
        asyncio.run(self._missing_lookups())

    async def _missing_lookups(self) -> None:
        be = await self._backend()
        with pytest.raises(NotFound):
            await be.get_board("nope")
        with pytest.raises(NotFound):
            await be.get_card("nope")

    def test_create_card_requires_a_placement(self) -> None:
        asyncio.run(self._create_needs_placement())

    async def _create_needs_placement(self) -> None:
        be = await self._backend()
        with pytest.raises(ValueError, match="placement"):
            await be.create_card(Card(title="nowhere"))

    # --- placement set (Q15/Q16) ---

    def test_placement_ops(self) -> None:
        asyncio.run(self._placement_ops())

    async def _placement_ops(self) -> None:
        be = await self._backend()
        b1 = await be.create_board(Board(name="A", columns=[Column(name="todo")]))
        b2 = await be.create_board(Board(name="B", columns=[Column(name="inbox")]))
        card = await be.create_card(
            Card(title="c", placements=[Placement(board_id=b1.id, column_id=b1.columns[0].id)])
        )

        with pytest.raises(NotFound):  # strict move (Q16): not on b2 yet
            await be.move_card(card.id, b2.id, b2.columns[0].id, 0)

        with pytest.raises(NotFound):  # target column must exist on the board
            await be.move_card(card.id, b1.id, "no-such-column", 0)

        card = await be.add_placement(
            card.id, Placement(board_id=b2.id, column_id=b2.columns[0].id)
        )
        assert {p.board_id for p in card.placements} == {b1.id, b2.id}
        with pytest.raises(Conflict):  # one placement per board
            await be.add_placement(card.id, Placement(board_id=b2.id, column_id=b2.columns[0].id))

        card = await be.move_card(card.id, b2.id, b2.columns[0].id, 5)
        assert next(p for p in card.placements if p.board_id == b2.id).position == 5

        card = await be.remove_placement(card.id, b1.id)
        assert [p.board_id for p in card.placements] == [b2.id]
        with pytest.raises(Conflict):  # last placement is protected
            await be.remove_placement(card.id, b2.id)
        with pytest.raises(NotFound):  # already removed
            await be.remove_placement(card.id, b1.id)

    # --- ext patch semantics (Q17) ---

    def test_ext_patch_shallow_merges(self) -> None:
        asyncio.run(self._ext_patch_shallow_merges())

    async def _ext_patch_shallow_merges(self) -> None:
        be = await self._backend()
        board = await be.create_board(Board(name="A", columns=[Column(name="t")], ext={"keep": 1}))
        card = await be.create_card(
            Card(
                title="c",
                ext={"kanban_pro.copied_from": "jira/T-1", "drop": True},
                placements=[Placement(board_id=board.id, column_id=board.columns[0].id)],
            )
        )
        updated = await be.update_card(card.id, CardPatch(ext={"mine": "x", "drop": None}))
        # foreign keys survive, patched key added, None-key removed (Q17)
        assert updated.ext == {"kanban_pro.copied_from": "jira/T-1", "mine": "x"}
        ub = await be.update_board(board.id, BoardPatch(ext={"new": 2}))
        assert ub.ext == {"keep": 1, "new": 2}
