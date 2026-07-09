"""HTTP API tests — snapshot, move, comments, change feed, SSE push, error mapping."""

from __future__ import annotations

import asyncio
import json

import httpx

from kanban_pro.adapters.memory import MemoryBackend
from kanban_pro.api import create_app
from kanban_pro.core import AugmentingBackend, ChangeLog, RecordingBackend
from kanban_pro.domain import Board, Card, CardPatch, Column, Placement
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
        assert "report_required_by_assignee" in meta

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


def test_session_log_endpoint(tmp_path) -> None:  # type: ignore[no-untyped-def]
    asyncio.run(_session_log(tmp_path))


def test_answer_work_report_question_endpoint() -> None:
    asyncio.run(_answer_work_report_question())


def test_retry_endpoint_moves_card_to_ready() -> None:
    asyncio.run(_retry_endpoint_moves_card_to_ready())


async def _retry_endpoint_moves_card_to_ready() -> None:
    backend = _stack()
    board = await backend.create_board(
        Board(name="B", columns=[Column(name="ready"), Column(name="running")])
    )
    card = await backend.create_card(
        Card(
            title="T",
            placements=[Placement(board_id=board.id, column_id=board.columns[1].id)],
            ext={
                "work": {"attempts": 2, "retry_at": 123},
                "kanban_pro.attention": {"reason": "needs answers"},
            },
        )
    )
    async with _client(backend) as client:
        res = await client.post(
            f"/api/cards/{card.id}/retry",
            json={"resolution": "answered"},
        )
        assert res.status_code == 200
        moved = res.json()
        assert moved["placements"][0]["column_id"] == board.columns[0].id
        assert "kanban_pro.attention" not in moved["ext"]
        assert moved["ext"]["work"] == {}


async def _answer_work_report_question() -> None:
    backend = _stack()
    _, card = await _seed(backend)
    from kanban_pro.core.work_report import record_work_report

    await record_work_report(
        backend,
        card.id,
        "questions",
        {"id": "q1", "text": "Which database?", "status": "open"},
    )
    async with _client(backend) as client:
        res = await client.post(
            f"/api/cards/{card.id}/work-report/questions/q1/answer",
            json={"answer": "Postgres"},
        )
        assert res.status_code == 200
        detail = (await client.get(f"/api/cards/{card.id}")).json()
        (question,) = detail["card"]["ext"]["work_report"]["questions"]
        assert question["status"] == "answered"
        assert question["answer"] == "Postgres"
        assert detail["comments"][-1]["body"] == "Answer to q1: Postgres"


async def _session_log(tmp_path) -> None:  # type: ignore[no-untyped-def]
    backend = _stack()
    board, card = await _seed(backend)
    async with _client(backend) as client:
        missing = await client.get(f"/api/cards/{card.id}/session-log")
        assert missing.status_code == 404  # nothing linked yet

        # dispatcher ext.work.log fallback: a plain .log is served line-wise
        wlog = tmp_path / f"{card.id}.log"
        wlog.write_text("worker says hi\nline two\n")
        await backend.update_card(card.id, CardPatch(ext={"work": {"log": str(wlog)}}))
        served = (await client.get(f"/api/cards/{card.id}/session-log")).json()
        assert served["kind"] == "log"
        assert [e["text"] for e in served["entries"]] == ["worker says hi", "line two"]

        # ext.session with a Claude Code transcript takes precedence and is normalised
        tlog = tmp_path / f"{card.id}.jsonl"
        tlog.write_text(
            json.dumps(
                {
                    "type": "user",
                    "message": {"role": "user", "content": "do it"},
                    "timestamp": "2026-07-08T10:00:00Z",
                }
            )
            + "\n"
            + json.dumps(
                {
                    "type": "assistant",
                    "message": {
                        "role": "assistant",
                        "content": [
                            {"type": "text", "text": "on it"},
                            {"type": "tool_use", "name": "Bash", "input": {"command": "ls -la"}},
                        ],
                    },
                    "timestamp": "2026-07-08T10:00:01Z",
                }
            )
            + "\n"
            + json.dumps(
                {
                    "type": "user",
                    "message": {
                        "role": "user",
                        "content": [
                            {
                                "type": "tool_result",
                                "content": [{"type": "text", "text": "file1 file2"}],
                            }
                        ],
                    },
                    "timestamp": "2026-07-08T10:00:02Z",
                }
            )
            + "\n"
        )
        await backend.update_card(
            card.id, CardPatch(ext={"session": {"log": str(tlog), "kind": "transcript"}})
        )
        got = (await client.get(f"/api/cards/{card.id}/session-log")).json()
        assert got["kind"] == "transcript"
        kinds = [(e["role"], e["kind"], e["text"]) for e in got["entries"]]
        assert kinds == [
            ("user", "text", "do it"),
            ("assistant", "text", "on it"),
            ("assistant", "tool_use", "Bash(ls -la)"),
            ("user", "tool_result", "file1 file2"),
        ]

        # incremental live-tail: ?after=<eof_offset> returns only what was appended
        eof = got["eof_offset"]
        with tlog.open("a") as fh:
            fh.write(
                json.dumps(
                    {
                        "type": "assistant",
                        "message": {
                            "role": "assistant",
                            "content": [{"type": "text", "text": "done"}],
                        },
                        "timestamp": "2026-07-08T10:00:03Z",
                    }
                )
                + "\n"
            )
        delta = (await client.get(f"/api/cards/{card.id}/session-log?after={eof}")).json()
        assert [e["text"] for e in delta["entries"]] == ["done"]
        assert delta["eof_offset"] > eof

        # a linked path outside home/tmp (or a non-log suffix) is refused
        await backend.update_card(card.id, CardPatch(ext={"session": {"log": "/etc/passwd"}}))
        refused = await client.get(f"/api/cards/{card.id}/session-log")
        assert refused.status_code == 404


def test_claim_exposed_in_api() -> None:
    asyncio.run(_claim_exposed())


async def _claim_exposed() -> None:
    backend = _stack()
    board, card = await _seed(backend)
    async with _client(backend) as client:
        # unclaimed: no _claim on the tile, null claim in detail
        snap = (await client.get(f"/api/boards/{board.id}")).json()
        assert "_claim" not in (snap["cards"][0]["ext"] or {})
        assert (await client.get(f"/api/cards/{card.id}")).json()["claim"] is None

        await backend.claim_card(card.id, ttl_seconds=3600, owner="agent:worker")

        snap = (await client.get(f"/api/boards/{board.id}")).json()
        assert snap["cards"][0]["ext"]["_claim"]["owner"] == "agent:worker"
        assert (await client.get(f"/api/cards/{card.id}")).json()["claim"][
            "owner"
        ] == "agent:worker"

        await backend.release_claim(card.id, owner="agent:worker")
        assert (await client.get(f"/api/cards/{card.id}")).json()["claim"] is None


def test_worker_log_readability(tmp_path) -> None:  # type: ignore[no-untyped-def]
    asyncio.run(_worker_log_readability(tmp_path))


async def _worker_log_readability(tmp_path) -> None:  # type: ignore[no-untyped-def]
    # A dispatcher .log where each line is JSON-serialized agent I/O: the viewer must
    # de-escape \n/\" so it reads as text, and tag call/result lines for colouring.
    backend = _stack()
    board, card = await _seed(backend)
    wlog = tmp_path / f"{card.id}.log"
    wlog.write_text(
        '[called terminal({"command":"git commit -m \\"x\\"\\nline2"})]\n'
        'Tool result (terminal): {"output": "ok\\ndone"}\n'
        "plain narration line\n"
    )
    await backend.update_card(
        card.id, CardPatch(ext={"session": {"log": str(wlog), "kind": "log"}})
    )
    async with _client(backend) as client:
        got = (await client.get(f"/api/cards/{card.id}/session-log")).json()
    entries = got["entries"]
    assert entries[0]["kind"] == "tool_use"
    assert "\n" in entries[0]["text"] and "\\n" not in entries[0]["text"]  # de-escaped
    assert '"x"' in entries[0]["text"]  # \" unescaped
    assert entries[1]["kind"] == "tool_result"
    assert entries[2] == {"ts": None, "role": "log", "kind": "line", "text": "plain narration line"}
