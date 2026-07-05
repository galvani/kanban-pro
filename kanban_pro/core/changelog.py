"""The core change-log (SPEC decision 9): one append-only, cursored event stream.

Every write that succeeds against the active backend is recorded here with the ACTOR
who did it. All push/audit surfaces are projections of this log: the pull feed (now),
WS/SSE for the UI and MCP notifications (with the UI build), the card activity
timeline, and the audit trail Jan asked for ("log transitions … who moved what").

Storage: SQLite (cursor = autoincrement seq) or in-memory (memory profile / tests).
"""

from __future__ import annotations

import json
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import aiosqlite
from pydantic import BaseModel, Field

_SCHEMA = """
CREATE TABLE IF NOT EXISTS changes (
    seq INTEGER PRIMARY KEY AUTOINCREMENT,
    ts TEXT NOT NULL,
    actor TEXT NOT NULL,
    entity TEXT NOT NULL,
    entity_id TEXT NOT NULL,
    op TEXT NOT NULL,
    board_id TEXT,
    data TEXT NOT NULL DEFAULT '{}'
);
"""


class ChangeEvent(BaseModel):
    """One recorded write. `seq` is the feed cursor (assigned on append)."""

    seq: int = 0
    ts: datetime = Field(default_factory=lambda: datetime.now(UTC))
    actor: str  # convention: "kind:name", e.g. "agent:hermes-engineer", "human:jan"
    entity: str  # board | column | card | comment | relation
    entity_id: str
    op: str  # created | updated | moved | archived | unarchived | deleted | added | removed
    board_id: str | None = None
    data: dict[str, Any] = Field(default_factory=dict)

    @property
    def kind(self) -> str:
        return f"{self.entity}.{self.op}"


class ChangeLog:
    """Append-only event store. `db_path=None` -> in-memory (ephemeral profiles/tests)."""

    def __init__(self, db_path: str | Path | None = None) -> None:
        self._path = str(db_path) if db_path else None
        self._mem: list[ChangeEvent] = []

    async def append(self, event: ChangeEvent) -> ChangeEvent:
        if self._path is None:
            stamped = event.model_copy(update={"seq": len(self._mem) + 1})
            self._mem.append(stamped)
            return stamped
        async with aiosqlite.connect(self._path) as db:
            await db.executescript(_SCHEMA)
            cur = await db.execute(
                "INSERT INTO changes (ts, actor, entity, entity_id, op, board_id, data)"
                " VALUES (?, ?, ?, ?, ?, ?, ?)",
                (
                    event.ts.isoformat(),
                    event.actor,
                    event.entity,
                    event.entity_id,
                    event.op,
                    event.board_id,
                    json.dumps(event.data),
                ),
            )
            await db.commit()
            assert cur.lastrowid is not None
            return event.model_copy(update={"seq": cur.lastrowid})

    async def since(self, cursor: int = 0, limit: int = 100) -> list[ChangeEvent]:
        """Events with seq > cursor, oldest first. Next cursor = last event's seq."""
        if self._path is None:
            return [e for e in self._mem if e.seq > cursor][:limit]
        async with aiosqlite.connect(self._path) as db:
            await db.executescript(_SCHEMA)
            async with db.execute(
                "SELECT seq, ts, actor, entity, entity_id, op, board_id, data"
                " FROM changes WHERE seq > ? ORDER BY seq LIMIT ?",
                (cursor, limit),
            ) as rows:
                fetched = await rows.fetchall()
        out: list[ChangeEvent] = []
        for seq, ts, actor, entity, entity_id, op, board_id, data in fetched:
            payload = json.loads(data) if data else {}
            out.append(
                ChangeEvent(
                    seq=seq,
                    ts=datetime.fromisoformat(ts),
                    actor=actor,
                    entity=entity,
                    entity_id=entity_id,
                    op=op,
                    board_id=board_id,
                    data=payload,
                )
            )
        return out
