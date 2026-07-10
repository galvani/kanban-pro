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
from kanban_pro.core.work_report import WORK_REPORT_VERSION
from kanban_pro.domain import Board, Card, CardPatch, Column, Placement


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
        "record_work_report", "answer_work_report_question",
        "add_placement", "remove_placement",
        "archive_card", "unarchive_card", "delete_card",
        "list_comments", "add_comment", "delete_comment",
        "list_relations", "add_relation", "delete_relation",
        "list_changes", "list_transitions", "list_flows",
        "set_flow", "set_transitions", "clear_flow", "init_board",
        "list_work", "claim_card", "heartbeat_claim", "release_claim",
        "raise_attention", "clear_attention", "wait_changes",
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


async def _record_work_report_updates_current_state_and_changelog() -> None:
    _, card = await _make_board_with_card()
    await kmcp.record_work_report(
        card.id,
        "questions",
        {"id": "q1", "text": "Which database?", "status": "open"},
        idempotency_key="prep:q1",
    )
    await kmcp.record_work_report(
        card.id,
        "questions",
        {"id": "q1", "status": "answered", "answer": "Postgres"},
    )
    updated = await kmcp.get_card(card.id)
    assert updated.ext["work_report"]["questions"] == [
        {
            "id": "q1",
            "text": "Which database?",
            "status": "answered",
            "answer": "Postgres",
        }
    ]
    events = await kmcp.list_changes(since=0)
    assert [e.kind for e in events][-4:] == [
        "card.updated",
        "work_report.updated",
        "card.updated",
        "work_report.updated",
    ]
    assert events[-1].data == {
        "card_id": card.id,
        "section": "questions",
        "op": "upsert",
        "item_id": "q1",
    }


def test_record_work_report_updates_current_state_and_changelog() -> None:
    asyncio.run(_record_work_report_updates_current_state_and_changelog())


async def _answer_work_report_question_mirrors_comment() -> None:
    _, card = await _make_board_with_card()
    await kmcp.record_work_report(
        card.id,
        "questions",
        {"id": "q2", "text": "Which slug?", "status": "open"},
    )
    await kmcp.answer_work_report_question(card.id, "q2", "Use the existing estimate slug.")
    updated = await kmcp.get_card(card.id)
    (question,) = updated.ext["work_report"]["questions"]
    assert question["status"] == "answered"
    assert question["answer"] == "Use the existing estimate slug."
    comments = await kmcp.list_comments(card.id)
    assert comments[-1].body == "Answer to q2: Use the existing estimate slug."


def test_answer_work_report_question_mirrors_comment() -> None:
    asyncio.run(_answer_work_report_question_mirrors_comment())


async def _work_report_is_stamped_with_format_version() -> None:
    _, card = await _make_board_with_card()
    await kmcp.record_work_report(card.id, "plan", {"id": "p1", "text": "ship", "status": "todo"})
    report = (await kmcp.get_card(card.id)).ext["work_report"]
    assert report["_v"] == WORK_REPORT_VERSION


def test_work_report_is_stamped_with_format_version() -> None:
    asyncio.run(_work_report_is_stamped_with_format_version())


async def _legacy_unversioned_report_migrates_on_write() -> None:
    """A report written before versioning has no `_v`; it means v1 and is stamped."""
    _, card = await _make_board_with_card()
    await kmcp.update_card(card.id, CardPatch(ext={"work_report": {"about": "legacy"}}))
    await kmcp.record_work_report(card.id, "plan", {"id": "p1", "text": "x", "status": "todo"})
    report = (await kmcp.get_card(card.id)).ext["work_report"]
    assert report["_v"] == WORK_REPORT_VERSION
    assert report["about"] == "legacy"  # pre-existing content survives the migration


def test_legacy_unversioned_report_migrates_on_write() -> None:
    asyncio.run(_legacy_unversioned_report_migrates_on_write())


async def _newer_report_version_refuses_overwrite() -> None:
    """Old code must never silently clobber a format it cannot represent."""
    _, card = await _make_board_with_card()
    await kmcp.update_card(
        card.id, CardPatch(ext={"work_report": {"_v": WORK_REPORT_VERSION + 1, "about": "future"}})
    )
    with pytest.raises(ToolError, match="refusing to overwrite"):
        await kmcp.record_work_report(card.id, "plan", {"id": "p1", "text": "x"})
    # the future report is untouched
    assert (await kmcp.get_card(card.id)).ext["work_report"]["about"] == "future"


def test_newer_report_version_refuses_overwrite() -> None:
    asyncio.run(_newer_report_version_refuses_overwrite())


async def _underscore_section_is_reserved() -> None:
    _, card = await _make_board_with_card()
    with pytest.raises(ToolError, match="reserved"):
        await kmcp.record_work_report(card.id, "_v", {"id": "x"})


def test_underscore_section_is_reserved() -> None:
    asyncio.run(_underscore_section_is_reserved())


async def _capabilities_resource_reports_fulfilment() -> None:
    payload = json.loads(await kmcp.capabilities_resource())
    assert payload["profile"] == "memory"
    caps = payload["capabilities"]
    assert caps["comments"] == "native"
    assert caps["wip_limits"] == "polyfilled"  # Tier-1 core enforcement
    assert caps["workflow"] == "polyfilled"  # Tier-1: per-board flow engine


def test_capabilities_resource_reports_fulfilment() -> None:
    asyncio.run(_capabilities_resource_reports_fulfilment())


def test_server_ships_orientation_instructions() -> None:
    """The `initialize` result must carry the agent's operating rules — a connecting
    harness reads these before its first call. Guards against the instructions being
    dropped when FastMCP is reconfigured."""
    assert kmcp.mcp.instructions == kmcp.INSTRUCTIONS
    for rule in ("claim_card", "archive", "force=true", "raise_attention", "wait_changes"):
        assert rule in kmcp.INSTRUCTIONS
