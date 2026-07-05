"""HTTP API tests — snapshot, move, comments, change feed, SSE push, error mapping."""

from __future__ import annotations

import asyncio
import json

import httpx

from kanban_pro.adapters.memory import MemoryBackend
from kanban_pro.api import create_app
from kanban_pro.core import AugmentingBackend, ChangeLog, RecordingBackend
from kanban_pro.domain import Board, Card, Column, Placement
from kanban_pro.ports import KanbanBackend


def _client(backend: KanbanBackend) -> httpx.AsyncClient:
    app = create_app(backend=backend, profile="memory", actor="human:test")
    return httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://t")


def _stack() -> RecordingBackend:
    return RecordingBackend(AugmentingBackend(MemoryBackend()), ChangeLog(), "human:test")


async def _seed(backend: KanbanBackend) -> tuple[Board, Card]:
    board = await backend.create_board(
        Board(name="B", columns=[Column(name="todo"), Column(name="doing")])
    )
    card = await backend.create_card(
        Card(title="T", placements=[Placement(board_id=board.id, column_id=board.columns[0].id)])
    )
    return board, card


def test_snapshot_move_comment_feed() -> None:
    asyncio.run(_snapshot_move_comment_feed())


async def _snapshot_move_comment_feed() -> None:
    backend = _stack()
    board, card = await _seed(backend)
    async with _client(backend) as client:
        meta = (await client.get("/api/meta")).json()
        assert meta["actor"] == "human:test"
        assert meta["capabilities"]["wip_limits"] == "polyfilled"

        snap = (await client.get(f"/api/boards/{board.id}")).json()
        assert [c["title"] for c in snap["cards"]] == ["T"]
        assert snap["cursor"] == 2  # board.created + card.created already logged

        moved = await client.post(
            f"/api/cards/{card.id}/move",
            json={"to_board_id": board.id, "to_column_id": board.columns[1].id},
        )
        assert moved.status_code == 200

        posted = await client.post(f"/api/cards/{card.id}/comments", json={"body": "hi"})
        assert posted.json()["author"] == "human:test"  # defaults to the server actor

        changes = (await client.get(f"/api/changes?since={snap['cursor']}")).json()
        assert [c["op"] for c in changes] == ["moved", "added"]
        assert all(c["actor"] == "human:test" for c in changes)

        detail = (await client.get(f"/api/cards/{card.id}")).json()
        assert [c["body"] for c in detail["comments"]] == ["hi"]


def test_error_mapping() -> None:
    asyncio.run(_error_mapping())


async def _error_mapping() -> None:
    async with _client(_stack()) as client:
        missing = await client.get("/api/cards/nope")
        assert missing.status_code == 404
        assert missing.json()["error"] == "not_found"


def test_sse_pushes_events() -> None:
    # httpx.ASGITransport buffers whole responses, so an endless SSE stream needs a
    # REAL server: run uvicorn in-process on an ephemeral port and stream over TCP.
    asyncio.run(_sse_pushes_events())


async def _sse_pushes_events() -> None:
    import uvicorn

    backend = _stack()
    board, card = await _seed(backend)
    app = create_app(backend=backend, profile="memory", actor="human:test")
    server = uvicorn.Server(uvicorn.Config(app, host="127.0.0.1", port=0, log_level="error"))
    serve_task = asyncio.create_task(server.serve())
    try:
        async with asyncio.timeout(10):
            while not server.started:
                await asyncio.sleep(0.01)
            port = server.servers[0].sockets[0].getsockname()[1]

            async with (
                httpx.AsyncClient() as client,
                client.stream("GET", f"http://127.0.0.1:{port}/api/events?since=0") as stream,
            ):
                lines = stream.aiter_lines()

                async def next_event() -> tuple[int, dict[str, object]]:
                    event_id = 0
                    async for line in lines:
                        if line.startswith("id: "):
                            event_id = int(line[4:])
                        elif line.startswith("data: "):
                            return event_id, json.loads(line[6:])
                    raise AssertionError("stream ended")

                seq1, first = await next_event()  # backlog replays from cursor 0
                assert (seq1, first["entity"], first["op"]) == (1, "board", "created")
                await next_event()  # card.created
                # a NEW write while connected must be pushed with no client action
                await backend.archive_card(card.id)
                seq3, pushed = await next_event()
                assert (seq3, pushed["op"]) == (3, "archived")
    finally:
        server.should_exit = True
        await serve_task
