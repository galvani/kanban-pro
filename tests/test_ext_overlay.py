"""Tier-2 ext overlay: kanban-pro stores `ext` for backends that have nowhere to put it.

Hermes's `tasks` table is fixed columns with no JSON bag, so `raise_attention` and
`record_work_report` — which both patch `ext` — used to die with `not_supported` on that
profile. Now the core holds that ext itself, keyed to the backend's own card ids, and merges
it back on read so it behaves exactly as if the backend had stored it.
"""

from __future__ import annotations

import asyncio

import pytest

from kanban_pro.adapters.memory import MemoryBackend
from kanban_pro.core import AugmentingBackend, ChangeLog, ExtStore, RecordingBackend
from kanban_pro.core.ext_store import merge_ext
from kanban_pro.domain import Board, Card, CardPatch, Column, Placement
from kanban_pro.ports import Capability, Fulfilment, NotSupported


class _NoExtBackend(MemoryBackend):
    """A backend like hermes: no home for ext, and it refuses any patch carrying one."""

    capabilities = MemoryBackend.capabilities - {Capability.CUSTOM_FIELDS}

    async def create_card(self, card: Card, *, overwrite: bool = False) -> Card:
        if card.ext:
            raise NotSupported("this backend cannot store ext")
        return await super().create_card(card, overwrite=overwrite)

    async def update_card(self, card_id: str, patch: CardPatch) -> Card:
        if patch.model_dump(exclude_unset=True).get("ext") is not None:
            raise NotSupported("this backend cannot store ext")
        return await super().update_card(card_id, patch)


def _stack(ext: bool = True) -> RecordingBackend:
    inner = AugmentingBackend(_NoExtBackend(), ext_store=ExtStore() if ext else None)
    return RecordingBackend(inner, ChangeLog(), "agent:t")


async def _card(be: RecordingBackend) -> tuple[Card, Board]:
    board = await be.create_board(Board(name="B", columns=[Column(name="todo")]))
    card = await be.create_card(
        Card(title="t", placements=[Placement(board_id=board.id, column_id=board.columns[0].id)])
    )
    return card, board


def test_merge_ext_removes_on_none() -> None:
    # same shallow-merge semantics as a native ext patch (Q17): None deletes the key
    assert merge_ext({"a": 1, "b": 2}, {"b": None, "c": 3}) == {"a": 1, "c": 3}


def test_custom_fields_is_polyfilled_not_unavailable() -> None:
    with_store = AugmentingBackend(_NoExtBackend(), ext_store=ExtStore())
    without = AugmentingBackend(_NoExtBackend())
    assert with_store.fulfilments()[Capability.CUSTOM_FIELDS] is Fulfilment.POLYFILLED
    assert without.fulfilments()[Capability.CUSTOM_FIELDS] is Fulfilment.UNAVAILABLE


def test_attention_and_work_report_survive_on_an_ext_less_backend() -> None:
    asyncio.run(_attention_and_work_report_survive_on_an_ext_less_backend())


async def _attention_and_work_report_survive_on_an_ext_less_backend() -> None:
    be = _stack()
    card, _ = await _card(be)

    # the exact call that used to raise not_supported on hermes
    await be.raise_attention(card.id, "need a decision", for_actor="human:jan", severity="warn")
    got = await be.get_card(card.id)
    flag = got.ext["kanban_pro.attention"]
    assert (flag["reason"], flag["severity"]) == ("need a decision", "warn")

    # and it survives a re-read through list_cards (the bulk path)
    listed = await be.list_cards(got.placements[0].board_id)
    assert listed[0].ext["kanban_pro.attention"]["reason"] == "need a decision"

    await be.clear_attention(card.id, resolution="answered")
    assert "kanban_pro.attention" not in (await be.get_card(card.id)).ext  # None removed it


def test_without_the_store_it_fails_honestly() -> None:
    asyncio.run(_without_the_store_it_fails_honestly())


async def _without_the_store_it_fails_honestly() -> None:
    # no overlay -> the capability reads UNAVAILABLE and the write is refused. It must never
    # silently succeed-and-drop: a work report that vanishes is worse than one that errors.
    be = _stack(ext=False)
    card, _ = await _card(be)
    with pytest.raises(NotSupported):
        await be.raise_attention(card.id, "x", severity="warn")


def test_ext_at_create_is_kept_out_of_the_adapter_and_stored_by_us() -> None:
    asyncio.run(_ext_at_create_is_kept_out_of_the_adapter_and_stored_by_us())


async def _ext_at_create_is_kept_out_of_the_adapter_and_stored_by_us() -> None:
    be = _stack()
    board = await be.create_board(Board(name="B", columns=[Column(name="todo")]))
    # _NoExtBackend raises if handed ext at create; the overlay must strip it and store it
    card = await be.create_card(
        Card(
            title="t",
            ext={"kanban_pro.origin": {"id": "PROJ-1", "url": "https://x/PROJ-1"}},
            placements=[Placement(board_id=board.id, column_id=board.columns[0].id)],
        )
    )
    assert card.ext["kanban_pro.origin"]["id"] == "PROJ-1"
    assert (await be.get_card(card.id)).ext["kanban_pro.origin"]["id"] == "PROJ-1"


def test_a_non_ext_patch_still_reaches_the_backend() -> None:
    asyncio.run(_a_non_ext_patch_still_reaches_the_backend())


async def _a_non_ext_patch_still_reaches_the_backend() -> None:
    # the overlay must not swallow the rest of the patch: ext goes to us, fields go to it
    be = _stack()
    card, _ = await _card(be)
    updated = await be.update_card(card.id, CardPatch(title="renamed", ext={"a": 1}))
    assert updated.title == "renamed"  # backend took the title
    assert updated.ext["a"] == 1  # we took the ext


def test_deleting_a_card_purges_its_overlay_ext() -> None:
    asyncio.run(_deleting_a_card_purges_its_overlay_ext())


async def _deleting_a_card_purges_its_overlay_ext() -> None:
    # else a recycled id inherits a dead card's work report
    store = ExtStore()
    be = RecordingBackend(AugmentingBackend(_NoExtBackend(), ext_store=store), ChangeLog(), "a:t")
    card, _ = await _card(be)
    await be.update_card(card.id, CardPatch(ext={"work_report": {"verdict": "ok"}}))
    assert await store.get(card.id) != {}

    await be.archive_card(card.id)
    await be.delete_card(card.id)
    assert await store.get(card.id) == {}
