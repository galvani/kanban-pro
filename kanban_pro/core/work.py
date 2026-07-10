"""Work distribution: the claim/lease store + work-queue models.

Claim = the competing-consumers primitive (proven by the Hermes dispatcher's CAS
pattern): an atomic "this actor owns this card until <expiry>" so two agents never
pick the same card. TTL is the visibility timeout; heartbeat renews; an expired claim
is silently reclaimable (crash-redelivery). Claiming does NOT move or assign the card
— those stay explicit, so the convention "claim -> assign yourself -> move to a
started column" remains visible in the change-log.

Storage mirrors ChangeLog: SQLite per profile, in-memory when `db_path=None`.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from pathlib import Path

import aiosqlite
from pydantic import BaseModel

from kanban_pro.core.flow import TransitionInfo
from kanban_pro.domain import Card
from kanban_pro.ports import Conflict, NotFound

_SCHEMA = """
CREATE TABLE IF NOT EXISTS claims (
    card_id TEXT PRIMARY KEY,
    owner TEXT NOT NULL,
    expires_at TEXT NOT NULL
);
"""


def _now() -> datetime:
    return datetime.now(UTC)


class Claim(BaseModel):
    card_id: str
    owner: str  # actor string
    expires_at: datetime

    @property
    def expired(self) -> bool:
        expires = self.expires_at
        if expires.tzinfo is None:
            # Some stored claims have no timezone info (legacy).
            # Assume UTC so comparison with aware _now() doesn't crash.
            expires = expires.replace(tzinfo=UTC)
        return expires <= _now()


class WorkItem(BaseModel):
    """One entry in an agent's work queue: the card + where it sits + what's legal."""

    card: Card
    board_id: str
    column_id: str
    column_name: str
    column_category: str
    claimed_by_me: bool = False
    transitions: TransitionInfo


class WorkQueue(BaseModel):
    actor: str
    items: list[WorkItem]


class ClaimStore:
    """Atomic claim/lease bookkeeping. `db_path=None` -> in-memory."""

    def __init__(self, db_path: str | Path | None = None) -> None:
        self._path = str(db_path) if db_path else None
        self._mem: dict[str, Claim] = {}

    async def claim(self, card_id: str, owner: str, ttl_seconds: int) -> Claim:
        """CAS: succeeds iff unclaimed, expired, or already ours (re-claim = renew)."""
        wanted = Claim(
            card_id=card_id, owner=owner, expires_at=_now() + timedelta(seconds=ttl_seconds)
        )
        if self._path is None:
            current = self._mem.get(card_id)
            if current and not current.expired and current.owner != owner:
                raise Conflict(
                    f"card {card_id!r} is claimed by {current.owner!r}"
                    f" until {current.expires_at.isoformat()}"
                )
            self._mem[card_id] = wanted
            return wanted
        async with aiosqlite.connect(self._path) as db:
            await db.executescript(_SCHEMA)
            cursor = await db.execute(
                "INSERT INTO claims (card_id, owner, expires_at) VALUES (?, ?, ?)"
                " ON CONFLICT(card_id) DO UPDATE"
                " SET owner=excluded.owner, expires_at=excluded.expires_at"
                " WHERE claims.expires_at <= ? OR claims.owner = excluded.owner",
                (card_id, owner, wanted.expires_at.isoformat(), _now().isoformat()),
            )
            await db.commit()
            if cursor.rowcount == 0:  # CAS lost: live claim by someone else
                holder = await self._get_db(db, card_id)
                detail = (
                    f"by {holder.owner!r} until {holder.expires_at.isoformat()}"
                    if holder
                    else "by another actor"
                )
                raise Conflict(f"card {card_id!r} is claimed {detail}")
        return wanted

    async def renew(self, card_id: str, owner: str, ttl_seconds: int) -> Claim:
        """Heartbeat: extend our own live claim."""
        current = await self.get(card_id)
        if current is None or current.expired:
            raise NotFound(f"no live claim on card {card_id!r} — claim_card it first")
        if current.owner != owner:
            raise Conflict(f"card {card_id!r} is claimed by {current.owner!r}, not you")
        return await self.claim(card_id, owner, ttl_seconds)

    async def release(self, card_id: str, owner: str) -> None:
        current = await self.get(card_id)
        if current is None or current.expired:
            return  # releasing nothing is fine (idempotent)
        if current.owner != owner:
            raise Conflict(f"card {card_id!r} is claimed by {current.owner!r}, not you")
        if self._path is None:
            self._mem.pop(card_id, None)
            return
        async with aiosqlite.connect(self._path) as db:
            await db.executescript(_SCHEMA)
            await db.execute("DELETE FROM claims WHERE card_id=?", (card_id,))
            await db.commit()

    async def get(self, card_id: str) -> Claim | None:
        if self._path is None:
            return self._mem.get(card_id)
        async with aiosqlite.connect(self._path) as db:
            await db.executescript(_SCHEMA)
            return await self._get_db(db, card_id)

    async def _get_db(self, db: aiosqlite.Connection, card_id: str) -> Claim | None:
        async with db.execute(
            "SELECT owner, expires_at FROM claims WHERE card_id=?", (card_id,)
        ) as cur:
            row = await cur.fetchone()
        if row is None:
            return None
        return Claim(card_id=card_id, owner=row[0], expires_at=datetime.fromisoformat(row[1]))

    async def live(self) -> dict[str, Claim]:
        """All unexpired claims (work-queue annotation/filtering)."""
        if self._path is None:
            return {cid: c for cid, c in self._mem.items() if not c.expired}
        async with aiosqlite.connect(self._path) as db:
            await db.executescript(_SCHEMA)
            async with db.execute(
                "SELECT card_id, owner, expires_at FROM claims WHERE expires_at > ?",
                (_now().isoformat(),),
            ) as cur:
                rows = await cur.fetchall()
        return {
            str(r[0]): Claim(
                card_id=str(r[0]), owner=str(r[1]), expires_at=datetime.fromisoformat(str(r[2]))
            )
            for r in rows
        }
