#!/usr/bin/env python3
"""kanban-notifier — consume the kanban-pro change-feed and DM Jan on Slack.

A single feed consumer that replaces both ``lane-watch`` (board-diffing cron) and the
delivery path of ``context-watch`` (kanban-comment + notify).  Workers signal "needs Jan"
via ``raise_attention(card_id, reason, for_actor="human:jan")``; this script picks it
up from the change-feed and DMs him directly.

Architecture
------------
  kanban-pro (MCP stdio)          this script              Slack
  ─────────────────────           ───────────              ─────
  wait_changes(since=cursor)  →   filter Jan events    →   DM Jan
                                  save cursor              (chat.postMessage)

No polling diff, no snapshot comparison — the feed tells us exactly what happened.

State
-----
Cursor file: ``$KANBAN_NOTIFIER_DIR/.kanban-notifier-cursor.json`` (default
``~/.local/state/kanban-notifier``) — ``{"cursor": <int>}``.  Missing = first run:
probe head with ``since=-1``, save, no DMs (baseline).  Deployments that live inside
an agent profile set ``KANBAN_NOTIFIER_DIR`` to the profile dir.

Environment
-----------
Reads ``SLACK_BOT_TOKEN`` and ``SLACK_HOME_CHANNEL`` from ``.env`` in the state dir.
Fallback: env vars directly.

Usage
-----
  kanban-notifier.py              # daemon (loops, wakes on activity)
  kanban-notifier.py --once       # one pass, then exit (cron mode)
  kanban-notifier.py --dry-run    # log DMs, don't send
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
import signal
from contextlib import AsyncExitStack
from pathlib import Path
from typing import Any

import httpx
from dotenv import load_dotenv
from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client

logger = logging.getLogger("kanban_notifier")

# ── Paths ───────────────────────────────────────────────────────────────
# NOT relative to the script: an example living in a repo must never write its
# runtime state (cursor) into the repo. Deployed copies point this at their home.
STATE_DIR = Path(
    os.environ.get("KANBAN_NOTIFIER_DIR", Path.home() / ".local" / "state" / "kanban-notifier")
)
CURSOR_FILE = STATE_DIR / ".kanban-notifier-cursor.json"

# ── Kanban-pro board command ────────────────────────────────────────────
KANBAN_PRO_CMD = (
    "uv",
    "run",
    "--directory",
    str(Path.home() / "workspace" / "kanban-pro"),
    "kanban-pro-mcp",
    "--actor",
    "agent:notifier",
)

# ── Slack ────────────────────────────────────────────────────────────────
SLACK_API = "https://slack.com/api/chat.postMessage"


def _load_env() -> tuple[str, str]:
    load_dotenv(STATE_DIR / ".env")
    return (
        os.environ.get("SLACK_BOT_TOKEN", ""),
        os.environ.get("SLACK_HOME_CHANNEL", ""),
    )


# ── Cursor persistence ──────────────────────────────────────────────────


def _read_cursor() -> int:
    try:
        return json.loads(CURSOR_FILE.read_text())["cursor"]
    except (FileNotFoundError, KeyError, json.JSONDecodeError):
        return -1  # -1 = probe head on first run


def _write_cursor(cursor: int) -> None:
    CURSOR_FILE.parent.mkdir(parents=True, exist_ok=True)
    CURSOR_FILE.write_text(json.dumps({"cursor": cursor}))


# ── Event → message ─────────────────────────────────────────────────────


def _card_title(event: dict[str, Any]) -> str:
    data = event.get("data", {})
    return data.get("title", event.get("entity_id", "?"))


def _column_short(col_id: str) -> str:
    return col_id.split(":", 1)[-1] if ":" in col_id else col_id


def _message_for(event: dict[str, Any]) -> str | None:
    """Return a Slack message line for a Jan-relevant event, or None."""
    actor = event.get("actor", "?")
    entity = event.get("entity", "")
    op = event.get("op", "")
    data = event.get("data", {})

    if actor.startswith("migration:"):
        return None  # import noise

    title = _card_title(event)

    if entity == "card" and op == "created":
        col = _column_short(data.get("column_id", "?"))
        return f"🆕 *{title}*  ({col})"

    if entity == "card" and op == "moved":
        col = _column_short(data.get("column_id", "?"))
        forced = "⚠️ forced " if data.get("forced") else ""
        actor_short = actor.split(":", 1)[-1] if ":" in actor else actor
        emoji = "✅" if col == "done" else "🔀"
        return f"{emoji}{forced}*{title}* → _{col}_  (by {actor_short})"

    if entity == "attention" and op == "raised":
        reason = data.get("reason", "?")
        for_actor = data.get("for", "")
        if for_actor != "human:jan":
            return None
        return f"❓ *{title}* — needs input: {reason}"

    if entity == "card" and op == "archived":
        return f"🗑️ *{title}*  (archived)"

    return None  # comments, relations, claims, updates — ignore


# ── Slack delivery ──────────────────────────────────────────────────────


async def _dm_jan(
    client: httpx.AsyncClient,
    token: str,
    channel: str,
    text: str,
    dry_run: bool,
) -> None:
    if not token or not channel:
        logger.warning("slack: skipping (no token/channel). msg: %s", text[:120])
        return
    if dry_run:
        logger.info("DRY-RUN DM:\n%s", text)
        return
    try:
        resp = await client.post(
            SLACK_API,
            json={"channel": channel, "text": text, "unfurl_links": False},
            headers={"Authorization": f"Bearer {token}"},
        )
        body = resp.json()
        if not body.get("ok"):
            logger.error("slack: %s", body.get("error", "unknown error"))
    except Exception:
        logger.exception("slack: send failed")


# ── Main loop ────────────────────────────────────────────────────────────


class _NotifierClient:
    """Lightweight MCP client patterned after kanban-dispatcher's KanbanClient."""

    def __init__(self) -> None:
        self._stack: AsyncExitStack | None = None
        self._session: ClientSession | None = None
        self._params = StdioServerParameters(
            command=KANBAN_PRO_CMD[0],
            args=list(KANBAN_PRO_CMD[1:]),
            env=dict(os.environ),  # pass full env for KANBAN_PRO_DB / XDG overrides
        )

    async def __aenter__(self) -> _NotifierClient:
        self._stack = AsyncExitStack()
        read, write = await self._stack.enter_async_context(stdio_client(self._params))
        self._session = await self._stack.enter_async_context(ClientSession(read, write))
        await self._session.initialize()
        return self

    async def __aexit__(self, *args: Any) -> None:
        if self._stack:
            await self._stack.aclose()
            self._stack = None
            self._session = None

    async def call(self, tool: str, args: dict[str, Any] | None = None) -> Any:
        if self._session is None:
            raise RuntimeError("not connected")
        result = await self._session.call_tool(tool, args or {})
        texts = [c.text for c in result.content if hasattr(c, "text")]
        if result.isError:
            raise RuntimeError(" ".join(texts) or "tool call failed")
        # FastMCP wraps non-object results as {"result": ...}
        structured = result.structuredContent
        if structured is not None:
            if set(structured.keys()) == {"result"}:
                return structured["result"]
            return structured
        joined = "\n".join(texts)
        return json.loads(joined) if joined else None

    async def wait_changes(self, since: int, timeout_seconds: int = 60) -> dict:
        return await self.call(
            "wait_changes",
            {"since": since, "timeout_seconds": timeout_seconds, "limit": 200},
        )


async def _amain(once: bool = False, dry_run: bool = False) -> None:
    token, channel = _load_env()
    cursor = _read_cursor()
    first_run = cursor == -1

    async with httpx.AsyncClient(timeout=httpx.Timeout(10.0)) as http:
        async with _NotifierClient() as board:
            # ── FIRST RUN: probe feed head, save cursor, NO DMs ──────────
            if first_run:
                result = await board.wait_changes(-1, timeout_seconds=5)
                new_cursor = result.get("cursor", 0)
                _write_cursor(new_cursor)
                logger.info(
                    "first run: baselined cursor at %s",
                    new_cursor,
                )
                if once:
                    return
                cursor = new_cursor

            # ── Main loop ───────────────────────────────────────────────
            stop = asyncio.Event()
            loop = asyncio.get_running_loop()
            for sig in (signal.SIGINT, signal.SIGTERM):
                loop.add_signal_handler(sig, stop.set)

            while not stop.is_set():
                try:
                    result = await board.wait_changes(cursor, timeout_seconds=60)
                except Exception:
                    logger.exception("wait_changes failed — retrying in 10s")
                    try:
                        await asyncio.wait_for(stop.wait(), timeout=10)
                    except TimeoutError:
                        pass
                    continue

                events = result.get("events", [])
                new_cursor = result.get("cursor", cursor)

                # Filter → collect messages → send ONE DM per tick
                messages: list[str] = []
                for event in events:
                    msg = _message_for(event)
                    if msg:
                        messages.append(msg)
                        logger.debug("event → DM: %s", msg[:120])

                if messages:
                    logger.info(
                        "tick: %d event(s), %d DM line(s), cursor %s→%s",
                        len(events),
                        len(messages),
                        cursor,
                        new_cursor,
                    )
                    text = "\n".join(messages)
                    await _dm_jan(http, token, channel, text, dry_run)

                _write_cursor(new_cursor)
                cursor = new_cursor

                if once or stop.is_set():
                    break

            logger.info("notifier stopped (cursor=%d)", cursor)


def main() -> None:
    import argparse
    import sys

    parser = argparse.ArgumentParser(description="kanban-pro change-feed → Slack DM notifier")
    parser.add_argument("--once", action="store_true", help="one pass, then exit")
    parser.add_argument("--dry-run", action="store_true", help="log DMs, don't send")
    parser.add_argument("-v", "--verbose", action="store_true", help="debug logging")
    args = parser.parse_args()

    logging.basicConfig(
        level=logging.DEBUG if args.verbose else logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    )
    try:
        asyncio.run(_amain(once=args.once, dry_run=args.dry_run))
    except Exception:
        logger.exception("notifier crashed — exiting")
        sys.exit(1)


if __name__ == "__main__":
    main()
