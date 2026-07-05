"""Direct-SQLite reads over the Hermes board databases (read-only path).

Board layout (docs/hermes-kanban.md): the `default` board lives at <root>/kanban.db
(legacy path); every other board at <root>/kanban/boards/<slug>/kanban.db. Reads go
straight to SQLite — fast, no auth; all WRITES go through the CLI (writer.py) so the
Hermes engine's invariants hold.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import aiosqlite

from kanban_pro.ports import NotFound

_TASK_COLUMNS = (
    "id, title, body, assignee, status, priority, created_by, created_at, started_at, "
    "completed_at, tenant, workspace_kind, workspace_path, branch_name, block_kind, "
    "skills, session_id, project_id, result"
)


class HermesReader:
    def __init__(self, root: Path) -> None:
        self._root = root

    # --- board discovery ---

    def board_db(self, slug: str) -> Path:
        if slug == "default":
            return self._root / "kanban.db"
        return self._root / "kanban" / "boards" / slug / "kanban.db"

    def board_slugs(self) -> list[str]:
        slugs = ["default"] if (self._root / "kanban.db").exists() else []
        boards_dir = self._root / "kanban" / "boards"
        if boards_dir.is_dir():
            slugs += sorted(p.name for p in boards_dir.iterdir() if (p / "kanban.db").exists())
        return slugs

    def _db_or_raise(self, slug: str) -> Path:
        db = self.board_db(slug)
        if not db.exists():
            raise NotFound(f"hermes board {slug!r} not found")
        return db

    # --- queries (rows as dicts; mapping.py turns them into domain models) ---

    async def _rows(self, db: Path, sql: str, params: tuple[Any, ...] = ()) -> list[dict[str, Any]]:
        async with aiosqlite.connect(db) as conn:
            conn.row_factory = aiosqlite.Row
            async with conn.execute(sql, params) as cur:
                return [dict(r) for r in await cur.fetchall()]

    async def lanes(self, slug: str) -> set[str]:
        rows = await self._rows(self._db_or_raise(slug), "SELECT DISTINCT status FROM tasks")
        return {str(r["status"]) for r in rows}

    async def tasks(self, slug: str, *, include_archived: bool = False) -> list[dict[str, Any]]:
        where = "" if include_archived else " WHERE status != 'archived'"
        return await self._rows(
            self._db_or_raise(slug),
            f"SELECT {_TASK_COLUMNS} FROM tasks{where} ORDER BY priority DESC, created_at",
        )

    async def find_task(self, task_id: str) -> tuple[str, dict[str, Any]]:
        """Locate a task across all boards -> (board_slug, row)."""
        for slug in self.board_slugs():
            rows = await self._rows(
                self.board_db(slug), f"SELECT {_TASK_COLUMNS} FROM tasks WHERE id=?", (task_id,)
            )
            if rows:
                return slug, rows[0]
        raise NotFound(f"card {task_id!r} not found on any hermes board")

    async def comments(self, slug: str, task_id: str) -> list[dict[str, Any]]:
        return await self._rows(
            self.board_db(slug),
            "SELECT id, task_id, author, body, created_at FROM task_comments "
            "WHERE task_id=? ORDER BY id",
            (task_id,),
        )

    async def latest_comment(self, slug: str, task_id: str) -> dict[str, Any]:
        rows = await self._rows(
            self.board_db(slug),
            "SELECT id, task_id, author, body, created_at FROM task_comments "
            "WHERE task_id=? ORDER BY id DESC LIMIT 1",
            (task_id,),
        )
        if not rows:
            raise NotFound(f"no comments on card {task_id!r}")
        return rows[0]

    async def links(self, slug: str, task_id: str) -> list[tuple[str, str]]:
        rows = await self._rows(
            self.board_db(slug),
            "SELECT parent_id, child_id FROM task_links WHERE parent_id=? OR child_id=?",
            (task_id, task_id),
        )
        return [(str(r["parent_id"]), str(r["child_id"])) for r in rows]
