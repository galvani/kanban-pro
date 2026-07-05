"""Idempotency dedupe cache (SPEC decision 8).

Harness clients retry on timeout — without dedupe, every retried create appends a
duplicate. A client sends `idempotency_key` with a create/add op; a retry with the
same key returns the ORIGINAL result instead of creating again (and records no second
change-log event). Keys are namespaced by op kind, so the same key string on a card
create and a comment add can't collide.

v1: the key is OPTIONAL (decision 8 says required — that flips on with the phase-C
worker rollout, once the worker skill always sends one). Server-generated fallback
keys are deliberately NOT a thing: a key minted per attempt differs on each retry and
dedupes nothing.

Storage mirrors ChangeLog/ClaimStore: SQLite per profile, in-memory when db_path=None.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from pathlib import Path

import aiosqlite

_SCHEMA = """
CREATE TABLE IF NOT EXISTS idempotency (
    kind TEXT NOT NULL,
    key TEXT NOT NULL,
    payload TEXT NOT NULL,
    expires_at TEXT NOT NULL,
    PRIMARY KEY (kind, key)
);
"""

DEFAULT_TTL_SECONDS = 24 * 3600


class DedupeStore:
    """kind+key -> serialized original result, with a TTL window."""

    def __init__(
        self, db_path: str | Path | None = None, ttl_seconds: int = DEFAULT_TTL_SECONDS
    ) -> None:
        self._path = str(db_path) if db_path else None
        self._ttl = ttl_seconds
        self._mem: dict[tuple[str, str], tuple[str, datetime]] = {}

    def _expiry(self) -> datetime:
        return datetime.now(UTC) + timedelta(seconds=self._ttl)

    async def get(self, kind: str, key: str) -> str | None:
        now = datetime.now(UTC)
        if self._path is None:
            hit = self._mem.get((kind, key))
            return hit[0] if hit and hit[1] > now else None
        async with aiosqlite.connect(self._path) as db:
            await db.executescript(_SCHEMA)
            async with db.execute(
                "SELECT payload FROM idempotency WHERE kind=? AND key=? AND expires_at > ?",
                (kind, key, now.isoformat()),
            ) as cur:
                row = await cur.fetchone()
        return str(row[0]) if row else None

    async def put(self, kind: str, key: str, payload: str) -> None:
        if self._path is None:
            self._mem[(kind, key)] = (payload, self._expiry())
            return
        async with aiosqlite.connect(self._path) as db:
            await db.executescript(_SCHEMA)
            await db.execute(
                "INSERT INTO idempotency (kind, key, payload, expires_at) VALUES (?, ?, ?, ?)"
                " ON CONFLICT(kind, key) DO UPDATE"
                " SET payload=excluded.payload, expires_at=excluded.expires_at",
                (kind, key, payload, self._expiry().isoformat()),
            )
            # opportunistic GC keeps the table from growing unboundedly
            await db.execute(
                "DELETE FROM idempotency WHERE expires_at <= ?",
                (datetime.now(UTC).isoformat(),),
            )
            await db.commit()
