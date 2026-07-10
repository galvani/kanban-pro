"""Flow engine tests — per-board flow (board.flow, by column id): set_flow validation,
enforcement, force, per-card free-roam, unmodeled lanes, inline one-card flow,
list_transitions, and the delete_column cascade."""

from __future__ import annotations

import asyncio

import pytest

from kanban_pro.adapters.memory import MemoryBackend
from kanban_pro.core import (
    FREE_ROAM,
    SCHEME_EXT_KEY,
    AugmentingBackend,
    ChangeLog,
    RecordingBackend,
)
from kanban_pro.domain import Board, BoardFlow, Card, Column, Placement
from kanban_pro.ports import Conflict


def _stack() -> tuple[RecordingBackend, ChangeLog]:
    log = ChangeLog()
    return RecordingBackend(AugmentingBackend(MemoryBackend()), log, "agent:t"), log


async def _board(be: RecordingBackend, *lanes: str) -> Board:
    return await be.create_board(Board(name="B", columns=[Column(name=n) for n in lanes]))


def _col(board: Board, name: str) -> str:
    return next(c.id for c in board.columns if c.name == name)


def _card(board: Board, lane: str, scheme: str | None = None) -> Card:
    ext = {SCHEME_EXT_KEY: scheme} if scheme else {}
    return Card(
        title="T", ext=ext, placements=[Placement(board_id=board.id, column_id=_col(board, lane))]
    )


async def _set_default_flow(be: RecordingBackend, board: Board) -> Board:
    """todo->doing, doing->{review,todo}, review->{done,doing} (staging left unmodeled)."""
    t = {
        _col(board, "todo"): [_col(board, "doing")],
        _col(board, "doing"): [_col(board, "review"), _col(board, "todo")],
        _col(board, "review"): [_col(board, "done"), _col(board, "doing")],
    }
    return await be.set_flow(board.id, BoardFlow(transitions=t))


def test_set_flow_validation() -> None:
    asyncio.run(_set_flow_validation())


async def _set_flow_validation() -> None:
    be, log = _stack()
    board = await _board(be, "todo", "doing", "done")
    # a dangling reference (column not on the board) is refused at the write
    with pytest.raises(Conflict, match="not on board"):
        await be.set_flow(board.id, BoardFlow(transitions={_col(board, "todo"): ["ghost"]}))
    # a valid flow persists on the board doc and emits a board.updated event
    saved = await be.set_flow(
        board.id, BoardFlow(transitions={_col(board, "todo"): [_col(board, "doing")]})
    )
    assert saved.flow is not None and saved.flow.transitions[_col(board, "todo")] == [
        _col(board, "doing")
    ]
    assert (await be.get_board(board.id)).flow is not None
    assert [e for e in await log.since(0) if e.entity == "board" and e.op == "updated"]


def test_enforcement_force_and_freeroam() -> None:
    asyncio.run(_enforcement())


async def _enforcement() -> None:
    be, log = _stack()
    board = await _board(be, "todo", "doing", "review", "done", "staging")
    await _set_default_flow(be, board)

    card = await be.create_card(_card(board, "todo"))
    await be.move_card(card.id, board.id, _col(board, "doing"), 0)  # todo->doing: allowed
    with pytest.raises(Conflict, match="does not allow"):
        await be.move_card(card.id, board.id, _col(board, "done"), 0)  # doing->done: denied

    # force: goes through AND the event is flagged
    await be.move_card(card.id, board.id, _col(board, "done"), 0, force=True)
    forced = [e for e in await log.since(0) if e.data.get("forced")]
    assert len(forced) == 1 and forced[0].op == "moved"

    # unmodeled endpoint: staging is named in no edge -> free both ways
    await be.move_card(card.id, board.id, _col(board, "staging"), 0)
    await be.move_card(card.id, board.id, _col(board, "todo"), 0)

    # free-roam card ignores the flow entirely
    roam = await be.create_card(_card(board, "todo", scheme=FREE_ROAM))
    await be.move_card(roam.id, board.id, _col(board, "done"), 0)


def test_no_flow_means_free() -> None:
    asyncio.run(_no_flow())


async def _no_flow() -> None:
    be, _ = _stack()  # board has no flow -> unrestricted
    board = await _board(be, "todo", "done")
    card = await be.create_card(_card(board, "todo"))
    await be.move_card(card.id, board.id, _col(board, "done"), 0)
    info = await be.transitions(card.id)
    assert info.source == "free"
    assert [o.name for o in info.options] == ["todo"]


def test_clear_flow_frees_the_board() -> None:
    asyncio.run(_clear_flow())


async def _clear_flow() -> None:
    be, _ = _stack()
    board = await _board(be, "todo", "doing", "review", "done", "staging")
    await _set_default_flow(be, board)
    await be.set_flow(board.id, BoardFlow())  # clear -> free-roam
    card = await be.create_card(_card(board, "doing"))
    await be.move_card(card.id, board.id, _col(board, "done"), 0)  # now permitted


def test_list_transitions() -> None:
    asyncio.run(_list_transitions())


async def _list_transitions() -> None:
    be, _ = _stack()
    board = await _board(be, "todo", "doing", "review", "done", "staging")
    await _set_default_flow(be, board)

    card = await be.create_card(_card(board, "doing"))
    info = await be.transitions(card.id)
    assert (info.source, info.resolved_scheme) == ("flow", "board")
    # explicit edges (review, todo) PLUS the unmodeled lane (staging, free to enter)
    assert sorted(o.name for o in info.options) == ["review", "staging", "todo"]

    parked = await be.create_card(_card(board, "staging"))
    info = await be.transitions(parked.id)  # unmodeled current column -> all moves free
    assert len(info.options) == 4 and info.note is not None

    roam = await be.create_card(_card(board, "todo", scheme=FREE_ROAM))
    info = await be.transitions(roam.id)
    assert (info.source, len(info.options)) == ("free-roam", 4)


_INLINE = {"states": ["todo", "done"], "transitions": [{"from": "todo", "to": "done"}]}


def test_inline_one_card_flow() -> None:
    asyncio.run(_inline())


async def _inline() -> None:
    be, _ = _stack()
    board = await _board(be, "todo", "doing", "review", "done")
    await _set_default_flow(be, board)
    card = await be.create_card(
        Card(
            title="T",
            ext={"kanban_pro.flow": _INLINE},
            placements=[Placement(board_id=board.id, column_id=_col(board, "todo"))],
        )
    )
    # inline allows todo->done though the board flow forbids it
    await be.move_card(card.id, board.id, _col(board, "done"), 0)
    info = await be.transitions(card.id)
    assert (info.source, info.resolved_scheme) == ("inline", "inline")
    with pytest.raises(Conflict, match="inline flow"):  # inline has no done->todo edge
        await be.move_card(card.id, board.id, _col(board, "todo"), 0)

    # malformed inline -> falls back to the board flow, flagged, never frozen
    bad = await be.create_card(
        Card(
            title="B",
            ext={"kanban_pro.flow": {"states": "nope"}},
            placements=[Placement(board_id=board.id, column_id=_col(board, "doing"))],
        )
    )
    info = await be.transitions(bad.id)
    assert info.resolved_scheme == "board"
    assert info.note is not None and "fell back" in info.note
    with pytest.raises(Conflict, match="board flow"):  # board flow forbids doing->done
        await be.move_card(bad.id, board.id, _col(board, "done"), 0)


def test_inline_flow_enforced_on_flowless_board() -> None:
    asyncio.run(_inline_no_flow())


async def _inline_no_flow() -> None:
    be, _ = _stack()  # board has no flow: free — but an inline flow still binds its card
    board = await _board(be, "todo", "doing", "done")
    card = await be.create_card(
        Card(
            title="T",
            ext={"kanban_pro.flow": _INLINE},
            placements=[Placement(board_id=board.id, column_id=_col(board, "todo"))],
        )
    )
    await be.move_card(card.id, board.id, _col(board, "done"), 0)  # inline permits
    with pytest.raises(Conflict, match="inline flow"):
        await be.move_card(card.id, board.id, _col(board, "todo"), 0)
    # a sibling card without an inline flow stays free
    free = await be.create_card(
        Card(title="F", placements=[Placement(board_id=board.id, column_id=_col(board, "done"))])
    )
    await be.move_card(free.id, board.id, _col(board, "todo"), 0)


def test_delete_column_cascades_into_flow() -> None:
    asyncio.run(_delete_column_cascade())


async def _delete_column_cascade() -> None:
    be, _ = _stack()
    board = await _board(be, "todo", "doing", "review", "done", "staging")
    await _set_default_flow(be, board)
    # deleting 'review' must strip it as both an edge source and a target
    await be.delete_column(_col(board, "review"))
    flow = (await be.get_board(board.id)).flow
    assert flow is not None
    review_id = next((c.id for c in board.columns if c.name == "review"), None)
    assert review_id not in flow.transitions  # source edge gone
    assert all(review_id not in tos for tos in flow.transitions.values())  # target refs gone
    # 'doing' kept its other edge (todo); only the review target was stripped
    assert flow.transitions[_col(board, "doing")] == [_col(board, "todo")]
