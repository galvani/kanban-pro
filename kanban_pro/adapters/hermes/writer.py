"""Writes via the `hermes kanban` CLI (the public contract that preserves the
engine's invariants: task_events emission, ready-recompute, CAS claims).

Board targeting uses the documented HERMES_KANBAN_BOARD env var. The runner is
injectable so tests can capture argv without a real Hermes install.
"""

from __future__ import annotations

import asyncio
import os
from collections.abc import Awaitable, Callable, Sequence

from kanban_pro.ports import BackendUnavailable, Conflict, NotFound

#: (cli args after "hermes kanban", board slug or None) -> stdout
Runner = Callable[[Sequence[str], str | None], Awaitable[str]]


async def run_cli(args: Sequence[str], board: str | None) -> str:
    env = os.environ.copy()
    if board:
        env["HERMES_KANBAN_BOARD"] = board
    try:
        proc = await asyncio.create_subprocess_exec(
            "hermes",
            "kanban",
            *args,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
            env=env,
        )
    except FileNotFoundError:
        raise BackendUnavailable("`hermes` CLI not found on PATH") from None
    out, err = await proc.communicate()
    if proc.returncode != 0:
        msg = (err.decode().strip() or out.decode().strip())[:500]
        if "not found" in msg.lower() or "unknown task" in msg.lower():
            raise NotFound(msg)
        # remaining CLI rejections are overwhelmingly workflow/CAS refusals
        raise Conflict(f"hermes kanban {args[0]}: {msg}")
    return out.decode()
