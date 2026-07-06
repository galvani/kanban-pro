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


def test_card_detail_endpoints() -> None:
    asyncio.run(_card_detail_endpoints())


async def _card_detail_endpoints() -> None:
    backend = _stack()
    board, card = await _seed(backend)
    # a subcard + attention + a move give the detail endpoints something to show
    from kanban_pro.domain import Relation
    from kanban_pro.domain import RelationKind as RK

    child = await backend.create_card(
        Card(title="sub", placements=[Placement(board_id=board.id, column_id=board.columns[0].id)])
    )
    await backend.add_relation(Relation(kind=RK.PARENT, from_card=card.id, to_card=child.id))
    await backend.raise_attention(card.id, "which db?", for_actor="human:jan")
    await backend.move_card(card.id, board.id, board.columns[1].id, 0)

    async with _client(backend) as client:
        detail = (await client.get(f"/api/cards/{card.id}")).json()
        (rel,) = detail["relations"]
        assert (rel["kind"], rel["other_id"], rel["other_title"]) == ("parent", child.id, "sub")
        assert detail["card"]["ext"]["kanban_pro.attention"]["reason"] == "which db?"

        trans = (await client.get(f"/api/cards/{card.id}/transitions")).json()
        assert trans["source"] == "free"  # no flows configured in this stack
        assert any(o["name"] == "todo" for o in trans["options"])

        activity = (await client.get(f"/api/cards/{card.id}/activity")).json()
        kinds = [f"{e['entity']}.{e['op']}" for e in activity]
        assert "card.created" in kinds and "attention.raised" in kinds and "card.moved" in kinds
        # the subcard's own creation is NOT this card's activity
        assert all(e["entity_id"] != child.id or e["entity"] == "relation" for e in activity)

        missing = await client.get("/api/cards/nope/activity")
        assert missing.status_code == 404


def test_worker_log_endpoint(tmp_path) -> None:  # type: ignore[no-untyped-def]
    asyncio.run(_worker_log(tmp_path))


async def _worker_log(tmp_path) -> None:  # type: ignore[no-untyped-def]
    backend = _stack()
    board, card = await _seed(backend)
    log = tmp_path / f"{card.id}.log"
    log.write_text("worker says hi\nline two\n")
    async with _client(backend) as client:
        missing = await client.get(f"/api/cards/{card.id}/worker-log")
        assert missing.status_code == 404  # nothing linked yet

        await backend.update_card(
            card.id,
            __import__("kanban_pro.domain", fromlist=["CardPatch"]).CardPatch(
                ext={"work": {"log": str(log)}}
            ),
        )
        served = await client.get(f"/api/cards/{card.id}/worker-log")
        assert served.status_code == 200 and "worker says hi" in served.text

        # a linked path outside home/tmp or non-.log is refused
        await backend.update_card(
            card.id,
            __import__("kanban_pro.domain", fromlist=["CardPatch"]).CardPatch(
                ext={"work": {"log": "/etc/passwd"}}
            ),
        )
        refused = await client.get(f"/api/cards/{card.id}/worker-log")
        assert refused.status_code == 404
