"""MCP interface tests — tools dispatch over the port and enforce core rules.

Adapter behavior is covered by the adapter suites; here we test the MCP layer's own
contract: tool registration, dispatch, the taxonomy-coded error surface, the delete
guard (decision 7), and the capabilities resource.
"""

from __future__ import annotations

import asyncio
import json

import pytest
from mcp.server.fastmcp.exceptions import ToolError

import kanban_pro.mcp as kmcp
from kanban_pro.adapters.memory import MemoryBackend
from kanban_pro.core import AugmentingBackend, ChangeLog, RecordingBackend
from kanban_pro.domain import Board, Card, Column, Placement


@pytest.fixture(autouse=True)
def fresh_backend() -> None:
    """Each test gets its own full core stack (recording > augmenting > memory)."""
    kmcp.configure(
        RecordingBackend(AugmentingBackend(MemoryBackend()), ChangeLog(), "agent:test"),
        "memory",
    )


async def _make_board_with_card() -> tuple[Board, Card]:
    board = await kmcp.create_board(Board(name="B", columns=[Column(name="todo")]))
    card = await kmcp.create_card(
        Card(
            title="C",
            placements=[Placement(board_id=board.id, column_id=board.columns[0].id)],
        )
    )
    return board, card


def test_tools_registered_match_methods_doc() -> None:
    tools = {t.name for t in asyncio.run(kmcp.mcp.list_tools())}
    expected = {
        "list_boards", "get_board", "create_board", "update_board", "delete_board",
        "list_columns", "create_column", "update_column", "delete_column",
        "list_cards", "get_card", "create_card", "update_card", "move_card",
        "add_placement", "remove_placement",
        "archive_card", "unarchive_card", "delete_card",
        "list_comments", "add_comment", "delete_comment",
        "list_relations", "add_relation", "delete_relation",
        "list_changes", "list_transitions", "list_flows",
    }  # fmt: skip
    assert tools == expected


async def _create_and_list_roundtrip() -> None:
    board, card = await _make_board_with_card()
    assert [b.id for b in await kmcp.list_boards()] == [board.id]
    assert [c.id for c in await kmcp.list_cards(board.id)] == [card.id]


def test_create_and_list_roundtrip() -> None:
    asyncio.run(_create_and_list_roundtrip())


async def _canonical_error_is_taxonomy_coded() -> None:
    with pytest.raises(ToolError, match=r"^not_found: "):
        await kmcp.get_card("nope")


def test_canonical_error_is_taxonomy_coded() -> None:
    asyncio.run(_canonical_error_is_taxonomy_coded())


async def _delete_card_guarded_archive_first() -> None:
    _, card = await _make_board_with_card()
    with pytest.raises(ToolError, match=r"^conflict: "):
        await kmcp.delete_card(card.id)  # live card -> refused
    await kmcp.archive_card(card.id)
    assert "deleted" in await kmcp.delete_card(card.id)
    with pytest.raises(ToolError, match=r"^not_found: "):
        await kmcp.get_card(card.id)


def test_delete_card_guarded_archive_first() -> None:
    asyncio.run(_delete_card_guarded_archive_first())


async def _delete_board_guarded_empty_only() -> None:
    board, card = await _make_board_with_card()
    with pytest.raises(ToolError, match=r"^conflict: "):
        await kmcp.delete_board(board.id)  # live card -> refused (Q14)
    await kmcp.archive_card(card.id)
    assert "deleted" in await kmcp.delete_board(board.id)


def test_delete_board_guarded_empty_only() -> None:
    asyncio.run(_delete_board_guarded_empty_only())


async def _change_feed_records_actor() -> None:
    board, card = await _make_board_with_card()
    await kmcp.move_card(card.id, board.id, board.columns[0].id, 3)
    events = await kmcp.list_changes(since=0)
    assert [e.kind for e in events] == ["board.created", "card.created", "card.moved"]
    assert all(e.actor == "agent:test" for e in events)
    # cursoring: resume from the last seen seq
    assert await kmcp.list_changes(since=events[-1].seq) == []


def test_change_feed_records_actor() -> None:
    asyncio.run(_change_feed_records_actor())


async def _capabilities_resource_reports_fulfilment() -> None:
    payload = json.loads(await kmcp.capabilities_resource())
    assert payload["profile"] == "memory"
    caps = payload["capabilities"]
    assert caps["comments"] == "native"
    assert caps["wip_limits"] == "polyfilled"  # Tier-1 core enforcement
    assert caps["workflow"] == "unavailable"  # flow-YAML design pending


def test_capabilities_resource_reports_fulfilment() -> None:
    asyncio.run(_capabilities_resource_reports_fulfilment())
