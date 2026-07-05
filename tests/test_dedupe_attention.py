"""Idempotency dedupe + attention flag tests."""

from __future__ import annotations

import asyncio
import sqlite3
from datetime import UTC, datetime
from pathlib import Path

from kanban_pro.adapters.memory import MemoryBackend
from kanban_pro.core import (
    ATTENTION_EXT_KEY,
    AugmentingBackend,
    ChangeLog,
    DedupeStore,
    RecordingBackend,
)
from kanban_pro.domain import Board, Card, Column, Comment, Placement


def _stack(dedupe: DedupeStore | None = None) -> tuple[RecordingBackend, ChangeLog]:
    log = ChangeLog()
    backend = RecordingBackend(AugmentingBackend(MemoryBackend()), log, "agent:test", dedupe=dedupe)
    return backend, log


async def _seed(be: RecordingBackend) -> Board:
    return await be.create_board(Board(name="B", columns=[Column(name="todo")]))


def test_create_card_dedupes_on_key() -> None:
    asyncio.run(_create_dedupes())


async def _create_dedupes() -> None:
    be, log = _stack()
    board = await _seed(be)
    placement = Placement(board_id=board.id, column_id=board.columns[0].id)

    first = await be.create_card(Card(title="T", placements=[placement]), idempotency_key="k1")
    retry = await be.create_card(Card(title="T", placements=[placement]), idempotency_key="k1")
    assert retry.id == first.id  # the ORIGINAL result, not a twin

    assert len(await be.list_cards(board.id)) == 1  # no duplicate on the board
    created_events = [e for e in await log.since(0) if e.kind == "card.created"]
    assert len(created_events) == 1  # and no duplicate history

    other = await be.create_card(Card(title="T", placements=[placement]), idempotency_key="k2")
    assert other.id != first.id  # different key = genuinely new card

    # comment dedupe: same key returns the original comment
    c1 = await be.add_comment(
        Comment(card_id=first.id, author="a", body="hi"), idempotency_key="c1"
    )
    c2 = await be.add_comment(
        Comment(card_id=first.id, author="a", body="hi"), idempotency_key="c1"
    )
    assert c1.id == c2.id
    assert len(await be.list_comments(first.id)) == 1


def test_dedupe_kind_namespacing_and_ttl(tmp_path: Path) -> None:
    asyncio.run(_kinds_and_ttl(tmp_path / "dedupe.db"))


async def _kinds_and_ttl(db: Path) -> None:
    store = DedupeStore(db)
    await store.put("card", "k", '{"card": true}')
    assert await store.get("comment", "k") is None  # same key, different kind: no clash
    assert await store.get("card", "k") == '{"card": true}'

    fresh = DedupeStore(db)  # persists across reopen
    assert await fresh.get("card", "k") == '{"card": true}'

    # expired entries are misses (simulate by rewriting expiry into the past)
    conn = sqlite3.connect(db)
    conn.execute(
        "UPDATE idempotency SET expires_at=?", (datetime(2000, 1, 1, tzinfo=UTC).isoformat(),)
    )
    conn.commit()
    conn.close()
    assert await fresh.get("card", "k") is None


def test_attention_raise_and_clear() -> None:
    asyncio.run(_attention())


async def _attention() -> None:
    be, log = _stack()
    board = await _seed(be)
    card = await be.create_card(
        Card(
            title="T",
            ext={"keep": 1},
            placements=[Placement(board_id=board.id, column_id=board.columns[0].id)],
        )
    )

    flagged = await be.raise_attention(card.id, "which auth provider?", for_actor="human:jan")
    assert flagged.ext[ATTENTION_EXT_KEY] == {
        "reason": "which auth provider?",
        "raised_by": "agent:test",
        "for": "human:jan",
    }
    assert flagged.ext["keep"] == 1  # shallow-merge left the rest alone

    cleared = await be.clear_attention(card.id, resolution="use oauth")
    assert ATTENTION_EXT_KEY not in cleared.ext  # None removed the key (Q17)
    assert cleared.ext["keep"] == 1

    events = [(e.kind, e.data) for e in await log.since(0) if e.entity == "attention"]
    assert events == [
        ("attention.raised", {"reason": "which auth provider?", "for_actor": "human:jan"}),
        ("attention.cleared", {"resolution": "use oauth"}),
    ]
    assert all(e.actor == "agent:test" for e in await log.since(0) if e.entity == "attention")
