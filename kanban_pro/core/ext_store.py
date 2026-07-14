"""ExtStore — kanban-pro's own home for `ext` a backend cannot store (Tier-2 polyfill).

SPEC decision 2: a capability the adapter lacks is either enforced by rules (Tier 1) or held
as *overlay data* on our side (Tier 2), keyed to the backend's own entity ids. `ext` is the
Tier-2 case that matters most, because kanban-pro's differentiating features live in it —
the work report (`ext["work_report"]`), the attention flag, `kanban_pro.origin`.

Why an overlay rather than writing into the backend: a foreign board typically has no JSON
bag. Hermes's `tasks` table is fixed columns; its TEXT columns (`body`, `result`, `skills`)
are *owned and displayed* by Hermes and read by its dispatcher, so encoding our JSON into one
would corrupt a system of record we don't own — a stuffed `description` also loses every
update, since the write path (the `hermes kanban` CLI) can only set a body at create time.
So: the backend stays authoritative for what it models, and we hold the rest, joined on
card id.

Storage mirrors ChangeLog/ClaimStore/DedupeStore: SQLite per profile, in-memory when
`db_path=None`. Values follow the same shallow-merge semantics as `CardPatch.ext` (Q17): a
key set to None is REMOVED, not stored as null.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import aiosqlite

_SCHEMA = """
CREATE TABLE IF NOT EXISTS card_ext (
    card_id TEXT PRIMARY KEY,
    ext TEXT NOT NULL
);
"""


def merge_ext(current: dict[str, Any], patch: dict[str, Any]) -> dict[str, Any]:
    """Shallow-merge `patch` onto `current`; a None value REMOVES the key (Q17 semantics)."""
    out = dict(current)
    for key, value in patch.items():
        if value is None:
            out.pop(key, None)
        else:
            out[key] = value
    return out


class ExtStore:
    """card_id -> the ext keys the backend has no home for."""

    def __init__(self, db_path: str | Path | None = None) -> None:
        self._path = str(db_path) if db_path else None
        self._mem: dict[str, dict[str, Any]] = {}

    async def get(self, card_id: str) -> dict[str, Any]:
        if self._path is None:
            return dict(self._mem.get(card_id, {}))
        async with aiosqlite.connect(self._path) as db:
            await db.executescript(_SCHEMA)
            async with db.execute("SELECT ext FROM card_ext WHERE card_id=?", (card_id,)) as cur:
                row = await cur.fetchone()
        return dict(json.loads(row[0])) if row else {}

    async def get_many(self, card_ids: list[str]) -> dict[str, dict[str, Any]]:
        """Bulk read — list_cards/list_work would otherwise issue one query per card."""
        if not card_ids:
            return {}
        if self._path is None:
            return {cid: dict(self._mem[cid]) for cid in card_ids if cid in self._mem}
        placeholders = ",".join("?" * len(card_ids))
        async with aiosqlite.connect(self._path) as db:
            await db.executescript(_SCHEMA)
            async with db.execute(
                f"SELECT card_id, ext FROM card_ext WHERE card_id IN ({placeholders})",
                card_ids,
            ) as cur:
                rows = await cur.fetchall()
        return {str(cid): dict(json.loads(blob)) for cid, blob in rows}

    async def merge(self, card_id: str, patch: dict[str, Any]) -> dict[str, Any]:
        """Shallow-merge into the card's stored ext and return the result."""
        merged = merge_ext(await self.get(card_id), patch)
        if self._path is None:
            if merged:
                self._mem[card_id] = merged
            else:
                self._mem.pop(card_id, None)
            return dict(merged)
        async with aiosqlite.connect(self._path) as db:
            await db.executescript(_SCHEMA)
            if merged:
                await db.execute(
                    "INSERT INTO card_ext (card_id, ext) VALUES (?, ?)"
                    " ON CONFLICT(card_id) DO UPDATE SET ext=excluded.ext",
                    (card_id, json.dumps(merged)),
                )
            else:
                await db.execute("DELETE FROM card_ext WHERE card_id=?", (card_id,))
            await db.commit()
        return merged

    async def delete(self, card_id: str) -> None:
        """Purge a card's overlay ext (called when the card itself is deleted)."""
        if self._path is None:
            self._mem.pop(card_id, None)
            return
        async with aiosqlite.connect(self._path) as db:
            await db.executescript(_SCHEMA)
            await db.execute("DELETE FROM card_ext WHERE card_id=?", (card_id,))
            await db.commit()
