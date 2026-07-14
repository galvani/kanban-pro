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
import json
import logging
import tempfile
from collections.abc import AsyncIterator
from pathlib import Path

from fastapi import FastAPI, Request, Response
from fastapi.responses import HTMLResponse, JSONResponse, StreamingResponse
from pydantic import BaseModel

from kanban_pro import core
from kanban_pro.config import ACTOR_ENV, PROFILE_ENV, build_backend
from kanban_pro.core.work_report import answer_work_report_question
from kanban_pro.domain import Board, Card, CardPatch, Comment
from kanban_pro.ports import KanbanBackend, KanbanError, NotFound, NotSupported

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
#: animated agent status icons (monochrome SVG, transparent bg, looping,
#: honouring prefers-reduced-motion).
#: Served raw and INLINED by the page, not <img>-embedded, so the UI can recolour their strokes to
#: the agent's own hue — as shipped they are black-on-white, i.e. invisible on a dark board.
_AGENTS_DIR = Path(__file__).parent / "agents"

# --- session-log serving (the "watch an agent work a card" feature) ---
#
# A card points at its agent's session log via ext.session.log (any agent kind may
# stamp it) or, for dispatcher-run cards, the older ext.work.log. The path is card
# data (agent-writable ext), so serving is guarded: only *.log / *.jsonl files under
# $HOME or the tmp dir — good enough for a localhost-only personal tool. A Claude Code
# transcript (.jsonl) is normalised into compact {ts, role, kind, text} entries so the
# browser doesn't have to know the transcript schema; a plain .log is served line-wise.

_LOG_SUFFIXES = (".log", ".jsonl")


def _brief(value: object, limit: int = 140) -> str:
    """One-line gist of a tool input / arbitrary value for the transcript view."""
    if isinstance(value, dict):
        # surface the fields that identify the action, else compact JSON
        for key in ("command", "file_path", "path", "pattern", "query", "url", "description"):
            field = value.get(key)
            if isinstance(field, str):
                return field[:limit]
        text = json.dumps(value, ensure_ascii=False)
    else:
        text = str(value)
    return text[:limit]


def _brief_result(content: object, limit: int = 240) -> str:
    """Flatten a tool_result content (str | list[block]) to a short preview."""
    if isinstance(content, list):
        parts = [
            b.get("text", "") for b in content if isinstance(b, dict) and b.get("type") == "text"
        ]
        content = " ".join(p for p in parts if p) or json.dumps(content, ensure_ascii=False)
    return str(content).strip()[:limit]


def _summarize_transcript_obj(obj: object) -> list[dict[str, object]]:
    """One Claude Code transcript line -> zero or more display entries."""
    if not isinstance(obj, dict):
        return []
    ts = obj.get("timestamp")
    if obj.get("type") == "summary":
        return [{"ts": ts, "role": "summary", "kind": "meta", "text": str(obj.get("summary", ""))}]
    msg = obj.get("message")
    if not isinstance(msg, dict):
        return []
    role = str(msg.get("role") or obj.get("type") or "?")
    content = msg.get("content")
    if isinstance(content, str):
        return (
            [{"ts": ts, "role": role, "kind": "text", "text": content[:4000]}]
            if content.strip()
            else []
        )
    out: list[dict[str, object]] = []
    for block in content if isinstance(content, list) else []:
        if not isinstance(block, dict):
            continue
        bt = block.get("type")
        if bt == "text" and str(block.get("text", "")).strip():
            out.append({"ts": ts, "role": role, "kind": "text", "text": block["text"][:4000]})
        elif bt == "thinking" and str(block.get("thinking", "")).strip():
            out.append(
                {"ts": ts, "role": role, "kind": "thinking", "text": block["thinking"][:2000]}
            )
        elif bt == "tool_use":
            out.append(
                {
                    "ts": ts,
                    "role": role,
                    "kind": "tool_use",
                    "text": f"{block.get('name', 'tool')}({_brief(block.get('input'))})",
                }
            )
        elif bt == "tool_result":
            out.append(
                {
                    "ts": ts,
                    "role": role,
                    "kind": "tool_result",
                    "text": _brief_result(block.get("content")),
                }
            )
    return out


def _log_line_entry(ln: str) -> dict[str, object]:
    """A plain .log line (e.g. a dispatcher worker log): the content is often
    JSON-serialized agent I/O, so de-escape \\n/\\t/\\" to render real line breaks
    instead of a wall of literal escapes, and tag call/result lines so the viewer
    can colour them like transcript tool calls."""
    text = ln.replace("\\n", "\n").replace("\\t", "\t").replace('\\"', '"')
    head = ln.lstrip()
    if head.startswith("[called "):
        kind = "tool_use"
    elif head.startswith(("Tool result", "Tool error")):
        kind = "tool_result"
    else:
        kind = "line"
    return {"ts": None, "role": "log", "kind": kind, "text": text}


def _parse_session_lines(lines: list[str], kind: str) -> list[dict[str, object]]:
    if kind != "transcript":
        return [_log_line_entry(ln) for ln in lines if ln.strip()]
    out: list[dict[str, object]] = []
    for ln in lines:
        ln = ln.strip()
        if not ln:
            continue
        try:
            out.extend(_summarize_transcript_obj(json.loads(ln)))
        except (ValueError, TypeError):
            out.append({"ts": None, "role": "raw", "kind": "text", "text": ln[:500]})
    return out


def _read_session_log(path: Path, kind: str, after: int, tail: int) -> dict[str, object]:
    """Read new bytes since `after` (or the tail when after<0) and parse them.

    Only whole lines (up to the last newline) are consumed, so a half-written last
    line on a live transcript is left for the next poll instead of failing to parse.
    """
    data = path.read_bytes()
    size = len(data)
    start = 0 if after < 0 else min(after, size)
    chunk = data[start:]
    nl = chunk.rfind(b"\n")
    consumed = nl + 1 if nl != -1 else 0
    lines = chunk[:consumed].decode("utf-8", "replace").splitlines()
    entries = _parse_session_lines(lines, kind)
    if after < 0 and tail > 0:
        entries = entries[-tail:]
    return {"kind": kind, "entries": entries, "eof_offset": start + consumed, "size": size}


class MoveRequest(BaseModel):
    to_board_id: str
    to_column_id: str
    position: int = 0
    #: override the board's flow. Never silent: the change-log event carries `forced: true`
    #: forever, and the UI only offers it behind a deliberate ⌘/Ctrl-click on a lane the flow
    #: forbids — an accidental drag can't produce one.
    force: bool = False


class CommentRequest(BaseModel):
    body: str
    author: str | None = None  # defaults to the server's actor


class AnswerQuestionRequest(BaseModel):
    answer: str
    author: str | None = None


class RetryRequest(BaseModel):
    resolution: str | None = None


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
        rec = core.unwrap(be, core.RecordingBackend)
        return rec.changelog if rec else None

    def _claims() -> core.ClaimStore | None:
        be = state.get("backend")
        rec = core.unwrap(be, core.RecordingBackend)
        return rec.claims if rec else None

    @app.exception_handler(KanbanError)
    async def _kanban_error(_request: Request, exc: KanbanError) -> JSONResponse:
        return JSONResponse(
            status_code=_STATUS.get(exc.code, 500),
            content={"error": exc.code, "message": str(exc)},
        )

    # --- UI (one self-contained page) ---

    @app.get("/", response_class=HTMLResponse)
    async def ui() -> HTMLResponse:
        # no-store: the single-file UI carries its own JS, so a cached copy silently pins
        # an old client (e.g. missing an SSE-reconnect fix) — a plain reload must always
        # fetch the current board.html, never a heuristically-cached one.
        return HTMLResponse(_UI_FILE.read_text(), headers={"Cache-Control": "no-store"})

    @app.get("/agents/{name}.svg")
    async def agent_icon(name: str) -> Response:
        """One animated icon per agent ROLE — shown while that agent is actually working a card."""
        path = (_AGENTS_DIR / f"{name}.svg").resolve()
        if not path.is_file() or path.parent != _AGENTS_DIR.resolve():
            raise NotFound(f"no animation for {name!r}")
        return Response(
            path.read_text(), media_type="image/svg+xml", headers={"Cache-Control": "max-age=3600"}
        )

    # --- meta / boards ---

    @app.get("/api/meta")
    async def meta() -> dict[str, object]:
        be = await _backend()
        # known assignees from the dispatcher routing table
        routing_path = Path.home() / "workspace" / "kanban-dispatcher" / "routing.yaml"
        known_assignees: list[str] = []
        report_required_by_assignee: dict[str, list[str]] = {}
        report_section_order: list[str] = [
            "about",
            "needs",
            "questions",
            "verdict",
            "plan",
            "findings",
            "checks",
            "analysis_log",
            "handoff",
        ]
        report_section_order_by_assignee: dict[str, list[str]] = {}
        max_retries = 5
        if routing_path.exists():
            try:
                import yaml as _yaml

                raw = _yaml.safe_load(routing_path.read_text()) or {}
                default_order = (raw.get("defaults") or {}).get("report_section_order") or []
                if isinstance(default_order, list) and default_order:
                    report_section_order = [str(section) for section in default_order if section]
                for route in raw.get("routes") or []:
                    ma = (route.get("match") or {}).get("assignee")
                    if ma:
                        known_assignees.append(ma)
                        required = route.get("report_required") or []
                        if isinstance(required, list):
                            report_required_by_assignee[ma] = [
                                str(section) for section in required if section
                            ]
                        order = route.get("report_section_order") or []
                        if isinstance(order, list) and order:
                            report_section_order_by_assignee[ma] = [
                                str(section) for section in order if section
                            ]
                max_retries = raw.get("defaults", {}).get("max_retries", 5)
            except Exception:
                pass
        return {
            "profile": profile or "default",
            "actor": actor or "unknown",
            "known_assignees": known_assignees,
            "report_required_by_assignee": report_required_by_assignee,
            "report_section_order": report_section_order,
            "report_section_order_by_assignee": report_section_order_by_assignee,
            "max_retries": max_retries,
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
        claims = _claims()
        live = await claims.live() if claims else {}
        cards = await be.list_cards(board_id)
        # attach live preview state as reserved _-prefixed ext keys (never persisted):
        # latest comment, and the live claim so tiles can badge the agent at work.
        for card in cards:
            try:
                comments = await be.list_comments(card.id)
                if comments:
                    last = max(comments, key=lambda c: c.created_at or "")
                    card.ext["_last_comment"] = last.body[:120]
            except Exception:
                pass
            claim = live.get(card.id)
            if claim is not None:
                card.ext["_claim"] = {
                    "owner": claim.owner,
                    "expires_at": claim.expires_at.isoformat(),
                }
        return BoardSnapshot(
            board=await be.get_board(board_id),
            cards=cards,
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
        claims = _claims()
        claim = await claims.get(card_id) if claims else None
        claim_out = (
            {"owner": claim.owner, "expires_at": claim.expires_at.isoformat()}
            if claim is not None and not claim.expired
            else None
        )
        return {"card": card, "comments": comments, "relations": relations, "claim": claim_out}

    @app.patch("/api/cards/{card_id}")
    async def update_card_api(card_id: str, patch: CardPatch) -> Card:
        be = await _backend()
        return await be.update_card(card_id, patch)

    @app.get("/api/cards/{card_id}/transitions")
    async def card_transitions(card_id: str, board_id: str | None = None) -> core.TransitionInfo:
        be = await _backend()
        if not core.unwrap(be, (core.RecordingBackend, core.AugmentingBackend)):
            raise NotSupported("transitions query needs the core stack")
        # a card shared onto >1 board has different legal moves per board — the caller must
        # say which one it is asking about (the board view knows; it passes its own id)
        return await be.transitions(card_id, board_id)

    @app.get("/api/cards/{card_id}/session-log")
    async def session_log(
        card_id: str, after: int = -1, tail: int = 400, role: str | None = None
    ) -> dict[str, object]:
        """The agent's session log for this card, normalised for the viewer.

        Source, in order: a ROLE-bound session (`ext["kanban_pro.sessions"][role].log` — the
        engineer answering an attention flag has its own session, distinct from the worker's),
        else `ext.session.log` (any agent kind stamps it), else the older dispatcher
        `ext.work.log`. `after<0` returns the tail; `after=<eof_offset>` returns only what was
        appended since — the live-tail poll the UI runs while the card's claim is still held.
        See the module-level notes for the path guard.
        """
        be = await _backend()
        card = await be.get_card(card_id)
        ext = card.ext if isinstance(card.ext, dict) else {}
        raw: object = None
        kind: object = None
        # a card can have SEVERAL sessions at once (the worker that built it, the engineer that
        # was called in when it blocked) — `role` says which one you want to watch
        bound = ext.get("kanban_pro.sessions")
        if role and isinstance(bound, dict) and isinstance(bound.get(role), dict):
            raw = bound[role].get("log")
            kind = "log"
        sess = ext.get("session")
        if not isinstance(sess, dict):
            sess = {}
        if not raw:
            raw = sess.get("log")
            kind = sess.get("kind")
        if not raw:  # fallback: dispatcher-run cards predate ext.session
            raw = (ext.get("work") or {}).get("log")
            kind = kind or "log"
        if not raw:
            raise NotFound(f"card {card_id!r} has no linked session log")
        path = Path(str(raw)).expanduser().resolve()
        allowed = (Path.home().resolve(), Path(tempfile.gettempdir()).resolve())
        if path.suffix not in _LOG_SUFFIXES or not any(path.is_relative_to(b) for b in allowed):
            raise NotFound("linked log path refused")
        if not path.exists():
            raise NotFound(f"log file missing: {path}")
        if kind not in ("transcript", "log"):
            kind = "transcript" if path.suffix == ".jsonl" else "log"
        return _read_session_log(path, str(kind), after, tail)

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
        return await be.move_card(
            card_id, body.to_board_id, body.to_column_id, body.position, force=body.force
        )

    @app.post("/api/cards/{card_id}/comments")
    async def add_comment(card_id: str, body: CommentRequest) -> Comment:
        be = await _backend()
        return await be.add_comment(
            Comment(card_id=card_id, author=body.author or actor or "unknown", body=body.body)
        )

    @app.post("/api/cards/{card_id}/work-report/questions/{question_id}/answer")
    async def answer_question(card_id: str, question_id: str, body: AnswerQuestionRequest) -> Card:
        return await answer_work_report_question(
            await _backend(),
            card_id,
            question_id,
            body.answer,
            author=body.author or actor or "unknown",
        )

    @app.post("/api/cards/{card_id}/attention/clear")
    async def clear_attention_api(card_id: str, body: RetryRequest) -> Card:
        """Resolve an attention flag WITHOUT retrying — the dashboard's "resolve" action.

        `retry` also clears attention, but it additionally wipes the attempt counter and moves
        the card to `ready`; that is a different decision. Answering a question or simply
        acknowledging a warn should not relaunch the card.
        """
        be = await _backend()
        if not core.unwrap(be, core.RecordingBackend):
            raise NotSupported("clearing attention needs the core stack")
        await be.clear_attention(card_id, body.resolution or "resolved from the dashboard")
        return await be.get_card(card_id)

    @app.post("/api/cards/{card_id}/retry")
    async def retry_card(card_id: str, body: RetryRequest) -> Card:
        be = await _backend()
        card = await be.get_card(card_id)
        ext = dict(card.ext or {})
        work = dict(ext.get("work") or {})
        # BOTH counters, or Retry is a placebo: `attempts` is the per-run failure count, but the
        # dispatcher's cycle cap counts `dispatches` (loop.py: _dispatches). Clearing only
        # `attempts` sent the card back to `ready` still holding dispatches >= cap, so the very
        # next dispatcher tick re-raised the block flag — the button appeared to do nothing.
        work.pop("attempts", None)
        work.pop("dispatches", None)
        work.pop("retry_at", None)
        patch_ext: dict[str, object] = {"work": work}
        if "kanban_pro.attention" in ext:
            patch_ext["kanban_pro.attention"] = None
        card = await be.update_card(card_id, CardPatch(ext=patch_ext))
        if core.unwrap(be, core.RecordingBackend):
            await be.clear_attention(card_id, body.resolution or "retry requested from UI")
            card = await be.get_card(card_id)
        placement = card.placements[0] if card.placements else None
        if placement is None:
            raise NotFound(f"card {card_id!r} has no board placement")
        ready = next(
            (c for c in await be.list_columns(placement.board_id) if c.name == "ready"), None
        )
        if ready is None:
            raise NotFound(f"board {placement.board_id!r} has no 'ready' column")
        return await be.move_card(card_id, placement.board_id, ready.id, 0)

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
            idle_ticks = 0
            while not await request.is_disconnected():
                batch = await log.since(cursor, 200)
                if batch:
                    for event in batch:
                        yield f"id: {event.seq}\ndata: {event.model_dump_json()}\n\n"
                    cursor = batch[-1].seq
                    idle_ticks = 0
                else:
                    # instant wake on same-process writes; 2s re-check for foreign ones
                    await log.wait_for_change(2.0)
                    # Heartbeat comment (~every 16s idle): keeps intermediaries from
                    # buffering/timing out an event-less stream, and lets a dead
                    # connection surface as a write error instead of hanging silent —
                    # the client relies on that to trigger its reconnect.
                    idle_ticks += 1
                    if idle_ticks >= 8:
                        idle_ticks = 0
                        yield ": ping\n\n"

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
