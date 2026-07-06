"""Change-log + RecordingBackend tests — actor stamping, cursoring, write-only recording."""

from __future__ import annotations

import asyncio
from pathlib import Path

import pytest

from kanban_pro.adapters.memory import MemoryBackend
from kanban_pro.core import (
    AugmentingBackend,
    ChangeEvent,
    ChangeLog,
    RecordingBackend,
    delete_card_guarded,
    fulfilments,
)
from kanban_pro.domain import Board, Card, CardPatch, Column, Comment, Placement
from kanban_pro.ports import Capability, Fulfilment, KanbanBackend, NotFound


def _stack(log: ChangeLog, actor: str = "agent:test") -> RecordingBackend:
    return RecordingBackend(AugmentingBackend(MemoryBackend()), log, actor)


def test_conforms_to_port() -> None:
    backend: KanbanBackend = _stack(ChangeLog())
    assert Capability.ARCHIVE in backend.capabilities
    assert fulfilments(backend)[Capability.WIP_LIMITS] is Fulfilment.POLYFILLED  # unwraps


def test_sqlite_changelog_roundtrip(tmp_path: Path) -> None:
    asyncio.run(_sqlite_roundtrip(tmp_path / "log.db"))


async def _sqlite_roundtrip(db: Path) -> None:
    log = ChangeLog(db)
    first = await log.append(
        ChangeEvent(
            actor="human:jan", entity="card", entity_id="c1", op="created", data={"title": "T"}
        )
    )
    second = await log.append(
        ChangeEvent(actor="agent:x", entity="card", entity_id="c1", op="moved")
    )
    assert (first.seq, second.seq) == (1, 2)

    fresh = ChangeLog(db)  # cursor + payload survive a reopen
    events = await fresh.since(0)
    assert [(e.seq, e.kind, e.actor) for e in events] == [
        (1, "card.created", "human:jan"),
        (2, "card.moved", "agent:x"),
    ]
    assert events[0].data == {"title": "T"}
    assert await fresh.since(cursor=1) == events[1:]
    assert await fresh.since(cursor=2) == []


def test_recording_backend_stamps_writes(tmp_path: Path) -> None:
    asyncio.run(_records_writes())


async def _records_writes() -> None:
    log = ChangeLog()
    be = _stack(log, actor="agent:hermes-engineer")
    board = await be.create_board(Board(name="B", columns=[Column(name="a"), Column(name="b")]))
    col_a, col_b = board.columns
    card = await be.create_card(
        Card(title="T", placements=[Placement(board_id=board.id, column_id=col_a.id)])
    )
    await be.move_card(card.id, board.id, col_b.id, 1)
    await be.update_card(card.id, CardPatch(title="T2"))
    await be.add_comment(Comment(card_id=card.id, author="human:jan", body="hi"))
    await be.archive_card(card.id)
    await delete_card_guarded(be, card.id)  # guard calls through the recorder

    kinds = [(e.kind, e.actor) for e in await log.since(0)]
    assert kinds == [
        ("board.created", "agent:hermes-engineer"),
        ("card.created", "agent:hermes-engineer"),
        ("card.moved", "agent:hermes-engineer"),
        ("card.updated", "agent:hermes-engineer"),
        ("comment.added", "agent:hermes-engineer"),
        ("card.archived", "agent:hermes-engineer"),
        ("card.deleted", "agent:hermes-engineer"),
    ]
    events = await log.since(0)
    assert events[2].data == {"column_id": col_b.id, "position": 1}
    assert events[2].board_id == board.id
    assert events[3].data == {"fields": ["title"]}
    assert events[4].data["author"] == "human:jan"  # comment author != actor, both kept


def test_reads_and_failures_not_recorded() -> None:
    asyncio.run(_reads_and_failures())


async def _reads_and_failures() -> None:
    log = ChangeLog()
    be = _stack(log)
    board = await be.create_board(Board(name="B", columns=[Column(name="a")]))
    await be.list_boards()
    await be.get_board(board.id)
    await be.list_cards(board.id)
    with pytest.raises(NotFound):  # failed write -> nothing recorded
        await be.move_card("missing", board.id, "col", 0)
    assert [e.kind for e in await log.since(0)] == ["board.created"]


def test_wait_since_longpoll() -> None:
    asyncio.run(_wait_since())


async def _wait_since() -> None:
    log = ChangeLog()
    seeded = await log.append(ChangeEvent(actor="a", entity="card", entity_id="c1", op="created"))

    probe = await log.wait_since(-1, timeout=5)  # join at the tail, no replay
    assert (probe.cursor, probe.events) == (seeded.seq, [])

    # events already pending -> returns immediately with them
    ready = await log.wait_since(0, timeout=5)
    assert [e.seq for e in ready.events] == [seeded.seq]
    assert ready.cursor == seeded.seq

    # nothing pending -> blocks, wakes INSTANTLY on a same-process append
    async def append_soon() -> None:
        await asyncio.sleep(0.05)
        await log.append(ChangeEvent(actor="b", entity="card", entity_id="c2", op="moved"))

    start = asyncio.get_running_loop().time()
    task = asyncio.create_task(append_soon())
    woken = await log.wait_since(probe.cursor, timeout=5)
    elapsed = asyncio.get_running_loop().time() - start
    await task
    assert [e.op for e in woken.events] == ["moved"]
    assert elapsed < 1.0  # woke on the append, not the timeout

    # timeout path: empty result, cursor unchanged
    idle = await log.wait_since(woken.cursor, timeout=0.1)
    assert (idle.cursor, idle.events) == (woken.cursor, [])
