"""Flow engine tests — YAML loading, scheme resolution chain, enforcement, force,
free-roam, list_transitions."""

from __future__ import annotations

import asyncio
from pathlib import Path

import pytest

from kanban_pro.adapters.memory import MemoryBackend
from kanban_pro.core import (
    FREE_ROAM,
    SCHEME_EXT_KEY,
    AugmentingBackend,
    ChangeLog,
    FlowConfig,
    RecordingBackend,
    load_flows,
)
from kanban_pro.domain import Board, Card, Column, Placement
from kanban_pro.ports import Conflict

_YAML = """
flows:
  default:
    states: [todo, doing, review, done]
    transitions:
      - { from: todo,   to: doing }
      - { from: doing,  to: [review, todo] }
      - { from: review, to: [done, doing] }
  docs:
    states: [todo, doing, done]
    transitions:
      - { from: todo,  to: doing }
      - { from: doing, to: done }
"""


@pytest.fixture
def flows(tmp_path: Path) -> FlowConfig:
    path = tmp_path / "flows.yaml"
    path.write_text(_YAML)
    return load_flows(path)


def test_load_validation(tmp_path: Path, flows: FlowConfig) -> None:
    assert set(flows.flows) == {"default", "docs"}
    assert flows.default == "default"
    assert flows.flows["default"].allowed["doing"] == ["review", "todo"]

    dangling = tmp_path / "bad.yaml"
    dangling.write_text(
        "flows:\n  default:\n    states: [a]\n    transitions:\n      - {from: a, to: b}\n"
    )
    with pytest.raises(ValueError, match="undeclared state 'b'"):
        load_flows(dangling)

    reserved = tmp_path / "reserved.yaml"
    reserved.write_text("flows:\n  free-roam:\n    states: [a]\n")
    with pytest.raises(ValueError, match="reserved"):
        load_flows(reserved)

    no_default = tmp_path / "nodefault.yaml"
    no_default.write_text("flows:\n  docs:\n    states: [a]\n")
    with pytest.raises(ValueError, match="default"):
        load_flows(no_default)


def test_resolution_chain(flows: FlowConfig) -> None:
    assert flows.resolve(None).resolved == "default"  # rule 2: unset -> default
    assert flows.resolve("docs").resolved == "docs"
    fallback = flows.resolve("nope")  # rule 3: unknown -> default, flagged
    assert (fallback.resolved, fallback.fell_back) == ("default", True)
    roam = flows.resolve(FREE_ROAM)  # reserved built-in
    assert (roam.resolved, roam.flow) == (FREE_ROAM, None)


async def _board(be: RecordingBackend, *lanes: str) -> Board:
    return await be.create_board(Board(name="B", columns=[Column(name=n) for n in lanes]))


def _col(board: Board, name: str) -> str:
    return next(c.id for c in board.columns if c.name == name)


def _card(board: Board, lane: str, scheme: str | None = None) -> Card:
    ext = {SCHEME_EXT_KEY: scheme} if scheme else {}
    return Card(
        title="T",
        ext=ext,
        placements=[Placement(board_id=board.id, column_id=_col(board, lane))],
    )


def _stack(flows: FlowConfig | None) -> tuple[RecordingBackend, ChangeLog]:
    log = ChangeLog()
    return RecordingBackend(AugmentingBackend(MemoryBackend(), flows=flows), log, "agent:t"), log


def test_enforcement_force_and_freeroam(flows: FlowConfig) -> None:
    asyncio.run(_enforcement(flows))


async def _enforcement(flows: FlowConfig) -> None:
    be, log = _stack(flows)
    board = await _board(be, "todo", "doing", "review", "done", "staging")

    card = await be.create_card(_card(board, "todo"))
    await be.move_card(card.id, board.id, _col(board, "doing"), 0)  # todo->doing: allowed
    with pytest.raises(Conflict, match="does not allow doing -> done"):
        await be.move_card(card.id, board.id, _col(board, "done"), 0)

    # force: goes through AND the event is flagged
    await be.move_card(card.id, board.id, _col(board, "done"), 0, force=True)
    forced = [e for e in await log.since(0) if e.data.get("forced")]
    assert len(forced) == 1 and forced[0].op == "moved"

    # unmodeled endpoint (rule 4): staging is not a flow state -> free both ways
    await be.move_card(card.id, board.id, _col(board, "staging"), 0)
    await be.move_card(card.id, board.id, _col(board, "todo"), 0)

    # docs scheme: doing -> done IS allowed
    docs_card = await be.create_card(_card(board, "doing", scheme="docs"))
    await be.move_card(docs_card.id, board.id, _col(board, "done"), 0)

    # unknown scheme falls back to default (rule 3): doing -> done denied again
    odd = await be.create_card(_card(board, "doing", scheme="nope"))
    with pytest.raises(Conflict, match="'default'"):
        await be.move_card(odd.id, board.id, _col(board, "done"), 0)

    # free-roam card ignores the flow entirely
    roam = await be.create_card(_card(board, "todo", scheme=FREE_ROAM))
    await be.move_card(roam.id, board.id, _col(board, "done"), 0)


def test_no_flows_means_free(flows: FlowConfig) -> None:
    asyncio.run(_no_flows())


async def _no_flows() -> None:
    be, _ = _stack(None)  # rule 1: engine absent -> unrestricted
    board = await _board(be, "todo", "done")
    card = await be.create_card(_card(board, "todo"))
    await be.move_card(card.id, board.id, _col(board, "done"), 0)
    info = await be.transitions(card.id)
    assert info.source == "free"
    assert [o.name for o in info.options] == ["todo"]


def test_list_transitions(flows: FlowConfig) -> None:
    asyncio.run(_list_transitions(flows))


async def _list_transitions(flows: FlowConfig) -> None:
    be, _ = _stack(flows)
    board = await _board(be, "todo", "doing", "review", "done", "staging")

    card = await be.create_card(_card(board, "doing"))
    info = await be.transitions(card.id)
    assert (info.source, info.resolved_scheme) == ("flow", "default")
    assert sorted(o.name for o in info.options) == ["review", "todo"]

    odd = await be.create_card(_card(board, "doing", scheme="nope"))
    info = await be.transitions(odd.id)
    assert info.resolved_scheme == "default"
    assert info.note is not None and "fallback" in info.note

    parked = await be.create_card(_card(board, "staging"))
    info = await be.transitions(parked.id)  # unmodeled current column -> free
    assert len(info.options) == 4 and info.note is not None

    roam = await be.create_card(_card(board, "todo", scheme=FREE_ROAM))
    info = await be.transitions(roam.id)
    assert (info.source, len(info.options)) == ("free-roam", 4)
