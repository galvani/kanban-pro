"""AugmentingBackend tests — capability routing, overlay polyfill, WIP enforcement.

`StubRemote` plays a minimal real-world thin adapter: boards/columns/cards native,
no comments/relations/WIP — the augmenting layer must fill those gaps (or report
them honestly).
"""

from __future__ import annotations

import asyncio

import pytest

from kanban_pro.adapters._base import BaseAdapter
from kanban_pro.adapters.memory import MemoryBackend
from kanban_pro.core import AugmentingBackend
from kanban_pro.domain import Board, Card, Column, Comment, Placement
from kanban_pro.ports import Capability, Conflict, Fulfilment, KanbanBackend, NotSupported
from tests.contract_suite import KanbanContract


class StubRemote(BaseAdapter):
    """Thin fake remote: overrides only what its 'backend' natively does."""

    capabilities = frozenset(
        {Capability.LABELS, Capability.ASSIGNEES, Capability.ARCHIVE, Capability.REORDER_CARDS}
    )

    def __init__(self) -> None:
        self._store = MemoryBackend()

    async def create_board(self, board: Board) -> Board:
        return await self._store.create_board(board)

    async def get_board(self, board_id: str) -> Board:
        return await self._store.get_board(board_id)

    async def list_columns(self, board_id: str) -> list[Column]:
        return await self._store.list_columns(board_id)

    async def list_cards(self, board_id: str) -> list[Card]:
        return await self._store.list_cards(board_id)

    async def get_card(self, card_id: str) -> Card:
        return await self._store.get_card(card_id)

    async def create_card(self, card: Card) -> Card:
        return await self._store.create_card(card)

    async def move_card(
        self, card_id: str, to_board_id: str, to_column_id: str, position: int
    ) -> Card:
        return await self._store.move_card(card_id, to_board_id, to_column_id, position)

    async def delete_card(self, card_id: str) -> None:
        await self._store.delete_card(card_id)


def _augmented() -> AugmentingBackend:
    return AugmentingBackend(StubRemote(), overlay=MemoryBackend())


def test_conforms_to_port() -> None:
    backend: KanbanBackend = _augmented()
    assert Capability.COMMENTS in backend.capabilities  # polyfilled -> callable


def test_fulfilment_reporting() -> None:
    f = _augmented().fulfilments()
    assert f[Capability.LABELS] is Fulfilment.NATIVE
    assert f[Capability.COMMENTS] is Fulfilment.POLYFILLED  # overlay data
    assert f[Capability.RELATIONS] is Fulfilment.POLYFILLED
    assert f[Capability.WIP_LIMITS] is Fulfilment.POLYFILLED  # Tier-1 enforcement
    assert f[Capability.WORKFLOW] is Fulfilment.UNAVAILABLE  # flow YAML pending

    bare = AugmentingBackend(StubRemote(), overlay=None).fulfilments()
    assert bare[Capability.COMMENTS] is Fulfilment.UNAVAILABLE  # no overlay, no data


async def _comments_polyfilled_and_gcd() -> None:
    be = _augmented()
    board = await be.create_board(Board(name="B", columns=[Column(name="c")]))
    card = await be.create_card(
        Card(title="t", placements=[Placement(board_id=board.id, column_id=board.columns[0].id)])
    )
    # the stub raises NotSupported for comments; the overlay serves them instead
    cm = await be.add_comment(Comment(card_id=card.id, author="u", body="hi"))
    assert [c.id for c in await be.list_comments(card.id)] == [cm.id]

    await be.delete_card(card.id)  # purge must GC the overlay rows too
    assert await be.list_comments(card.id) == []


def test_comments_polyfilled_and_gcd() -> None:
    asyncio.run(_comments_polyfilled_and_gcd())


async def _no_overlay_means_not_supported() -> None:
    be = AugmentingBackend(StubRemote(), overlay=None)
    with pytest.raises(NotSupported):
        await be.add_comment(Comment(card_id="x", author="u", body="hi"))


def test_no_overlay_means_not_supported() -> None:
    asyncio.run(_no_overlay_means_not_supported())


async def _wip_limit_enforced() -> None:
    be = _augmented()
    board = await be.create_board(
        Board(name="B", columns=[Column(name="doing", wip_limit=1), Column(name="todo")])
    )
    doing, todo = board.columns
    await be.create_card(
        Card(title="one", placements=[Placement(board_id=board.id, column_id=doing.id)])
    )
    with pytest.raises(Conflict, match="WIP"):  # create into a full column
        await be.create_card(
            Card(title="two", placements=[Placement(board_id=board.id, column_id=doing.id)])
        )
    waiting = await be.create_card(
        Card(title="two", placements=[Placement(board_id=board.id, column_id=todo.id)])
    )
    with pytest.raises(Conflict, match="WIP"):  # move into a full column
        await be.move_card(waiting.id, board.id, doing.id, 0)
    # re-positioning WITHIN the full column is not an entry -> allowed
    (occupant,) = [c for c in await be.list_cards(board.id) if c.title == "one"]
    await be.move_card(occupant.id, board.id, doing.id, 3)


def test_wip_limit_enforced() -> None:
    asyncio.run(_wip_limit_enforced())


class TestAugmentedMemoryContract(KanbanContract):
    """The augmenting layer must be behavior-transparent over a full store adapter."""

    async def _backend(self) -> KanbanBackend:
        return AugmentingBackend(MemoryBackend())
