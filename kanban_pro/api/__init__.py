"""HTTP API + on-demand web UI (the SECONDARY interface, SPEC decision 5).

Started ONLY explicitly via `kanban-pro-ui` — never by default (Jan's rule: any UI is
optional and on-demand). The browser is PUSH-fed (Jan's rule #2): one REST snapshot at
load, then Server-Sent Events projected off the core change-log — no client polling
loops. Same-process writes push instantly (ChangeLog wakeup); writes from other
processes sharing the store surface within the SSE re-check window (~2s).

Thin like every interface: all behavior lives in core; routes just translate HTTP.
"""

from __future__ import annotations

import argparse
import logging
from collections.abc import AsyncIterator
from pathlib import Path

from fastapi import FastAPI, Request
from fastapi.responses import HTMLResponse, JSONResponse, StreamingResponse
from pydantic import BaseModel

from kanban_pro import core
from kanban_pro.config import ACTOR_ENV, PROFILE_ENV, build_backend
from kanban_pro.domain import Board, Card, Comment
from kanban_pro.ports import KanbanBackend, KanbanError, NotSupported

logger = logging.getLogger("kanban_pro.api")

_STATUS = {
    "not_found": 404,
    "conflict": 409,
    "unauthorized": 401,
    "not_supported": 501,
    "backend_unavailable": 503,
    "error": 500,
}

_UI_FILE = Path(__file__).parent / "board.html"


class MoveRequest(BaseModel):
    to_board_id: str
    to_column_id: str
    position: int = 0


class CommentRequest(BaseModel):
    body: str
    author: str | None = None  # defaults to the server's actor


class BoardSnapshot(BaseModel):
    board: Board
    cards: list[Card]
    cursor: int  # change-log position AT snapshot time — SSE resumes from here


def create_app(
    backend: KanbanBackend | None = None,
    profile: str | None = None,
    actor: str | None = None,
) -> FastAPI:
    app = FastAPI(title="kanban-pro", docs_url="/api/docs", openapi_url="/api/openapi.json")
    state: dict[str, KanbanBackend] = {}
    if backend is not None:
        state["backend"] = backend

    async def _backend() -> KanbanBackend:
        if "backend" not in state:
            state["backend"] = await build_backend(profile, actor)
            logger.info("backend built: profile=%s actor=%s", profile or "default", actor)
        return state["backend"]

    def _changelog() -> core.ChangeLog | None:
        be = state.get("backend")
        return be.changelog if isinstance(be, core.RecordingBackend) else None

    @app.exception_handler(KanbanError)
    async def _kanban_error(_request: Request, exc: KanbanError) -> JSONResponse:
        return JSONResponse(
            status_code=_STATUS.get(exc.code, 500),
            content={"error": exc.code, "message": str(exc)},
        )

    # --- UI (one self-contained page) ---

    @app.get("/", response_class=HTMLResponse)
    async def ui() -> str:
        return _UI_FILE.read_text()

    # --- meta / boards ---

    @app.get("/api/meta")
    async def meta() -> dict[str, object]:
        be = await _backend()
        return {
            "profile": profile or "default",
            "actor": actor or "unknown",
            "capabilities": {
                cap.name.lower(): f.name.lower() for cap, f in core.fulfilments(be).items()
            },
        }

    @app.get("/api/boards")
    async def boards() -> list[Board]:
        return await (await _backend()).list_boards()

    @app.get("/api/boards/{board_id}")
    async def board_snapshot(board_id: str) -> BoardSnapshot:
        be = await _backend()
        log = _changelog()
        return BoardSnapshot(
            board=await be.get_board(board_id),
            cards=await be.list_cards(board_id),
            cursor=await log.head() if log else 0,
        )

    # --- cards (the ops the board UI needs; everything else = MCP/CLI) ---

    @app.get("/api/cards/{card_id}")
    async def card_detail(card_id: str) -> dict[str, object]:
        be = await _backend()
        card = await be.get_card(card_id)
        try:
            comments = await be.list_comments(card_id)
        except NotSupported:
            comments = []
        relations: list[dict[str, object]] = []
        try:
            for rel in await be.list_relations(card_id):
                other_id = rel.to_card if rel.from_card == card_id else rel.from_card
                try:
                    other_title = (await be.get_card(other_id)).title
                except KanbanError:
                    other_title = "(missing card)"
                relations.append(
                    {
                        "id": rel.id,
                        "kind": rel.kind.value,
                        "from_card": rel.from_card,
                        "to_card": rel.to_card,
                        "other_id": other_id,
                        "other_title": other_title,
                    }
                )
        except NotSupported:
            pass
        return {"card": card, "comments": comments, "relations": relations}

    @app.get("/api/cards/{card_id}/transitions")
    async def card_transitions(card_id: str) -> core.TransitionInfo:
        be = await _backend()
        if not isinstance(be, core.RecordingBackend | core.AugmentingBackend):
            raise NotSupported("transitions query needs the core stack")
        return await be.transitions(card_id)

    @app.get("/api/cards/{card_id}/activity")
    async def card_activity(card_id: str) -> list[core.ChangeEvent]:
        be = await _backend()
        await be.get_card(card_id)  # 404 for unknown cards
        log = _changelog()
        if log is None:
            raise NotSupported("change-log is not wired for this backend")
        return await log.for_card(card_id)

    @app.post("/api/cards/{card_id}/move")
    async def move(card_id: str, body: MoveRequest) -> Card:
        be = await _backend()
        return await be.move_card(card_id, body.to_board_id, body.to_column_id, body.position)

    @app.post("/api/cards/{card_id}/comments")
    async def add_comment(card_id: str, body: CommentRequest) -> Comment:
        be = await _backend()
        return await be.add_comment(
            Comment(card_id=card_id, author=body.author or actor or "unknown", body=body.body)
        )

    # --- change feed: pull + SSE push ---

    @app.get("/api/changes")
    async def changes(since: int = 0, limit: int = 100) -> list[core.ChangeEvent]:
        await _backend()
        log = _changelog()
        if log is None:
            raise NotSupported("change-log is not wired for this backend")
        return await log.since(since, min(max(limit, 1), 500))

    @app.get("/api/events")
    async def events(request: Request, since: int = 0) -> StreamingResponse:
        """SSE stream of change events. Browser reconnects resume via Last-Event-ID."""
        await _backend()
        log = _changelog()
        if log is None:
            raise NotSupported("change-log is not wired for this backend")
        last_event_id = request.headers.get("last-event-id")
        start = int(last_event_id) if last_event_id else since

        async def stream() -> AsyncIterator[str]:
            cursor = start
            yield "retry: 2000\n\n"
            while not await request.is_disconnected():
                batch = await log.since(cursor, 200)
                if batch:
                    for event in batch:
                        yield f"id: {event.seq}\ndata: {event.model_dump_json()}\n\n"
                    cursor = batch[-1].seq
                else:
                    # instant wake on same-process writes; 2s re-check for foreign ones
                    await log.wait_for_change(2.0)

        return StreamingResponse(stream(), media_type="text/event-stream")

    return app


def main() -> None:
    """Entry point: `kanban-pro-ui` — the ONLY way the UI starts (on-demand rule)."""
    parser = argparse.ArgumentParser(
        prog="kanban-pro-ui",
        description="kanban-pro web UI + HTTP API (push-fed board; started on demand only)",
    )
    parser.add_argument("--profile", default=None, help=f"backend profile (${PROFILE_ENV})")
    parser.add_argument(
        "--actor", default=None, help=f"identity for writes made through the UI (${ACTOR_ENV})"
    )
    parser.add_argument("--host", default="127.0.0.1", help="bind address (default: localhost)")
    parser.add_argument("--port", type=int, default=8747, help="port (default: 8747)")
    parser.add_argument("-v", "--verbose", action="store_true")
    args = parser.parse_args()

    import uvicorn

    logging.basicConfig(level=logging.DEBUG if args.verbose else logging.INFO)
    app = create_app(profile=args.profile, actor=args.actor or "human:ui")
    logger.info("kanban-pro UI on http://%s:%s (profile=%s)", args.host, args.port, args.profile)
    uvicorn.run(
        app,
        host=args.host,
        port=args.port,
        log_level="info",
        # SSE streams never end on their own; without this, Ctrl+C/SIGTERM hangs
        # forever "gracefully" waiting for open event streams. Force-close instead.
        timeout_graceful_shutdown=2,
    )


__all__ = ["create_app", "main"]
