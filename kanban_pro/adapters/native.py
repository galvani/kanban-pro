"""Native SQLite store — kanban-pro's own persistent backend (the DECIDED first build).

Implements the full KanbanBackend port over SQLite via aiosqlite. Behavior mirrors the
memory adapter (same contract) but persists to disk. Also the intended **overlay** store
that polyfills gaps in other backends (SPEC decision 2).

Storage model:
- boards  : id PK + JSON doc (columns + label registry live inside the doc).
- cards   : id PK + JSON doc (placements EXCLUDED from the doc) + archived flag column.
- placements : the source of truth for a card's location(s); indexed by board_id + card_id
  so `list_cards(board_id)` is a real query, not a scan. Rebuilt on every card write.
- comments / relations : id PK + JSON doc + foreign-key columns for lookups.

Connection-per-operation (SQLite handles file locking). NOTE: do not use ":memory:" —
each connection would get a *separate* in-memory db; use a file path.
"""

from __future__ import annotations

import json
from datetime import UTC, datetime
from pathlib import Path

import aiosqlite

from kanban_pro.domain import (
    Board,
    BoardFlow,
    BoardPatch,
    Card,
    CardPatch,
    Column,
    ColumnPatch,
    Comment,
    Placement,
    Relation,
    apply_patch,
)
from kanban_pro.domain.ids import IdScheme, parse_scheme
from kanban_pro.ports import Capability, Conflict, NotFound

#: random-id mint retries before we call a board's id space exhausted (see _mint_id)
_MINT_ATTEMPTS = 8

_SCHEMA = """
CREATE TABLE IF NOT EXISTS boards (id TEXT PRIMARY KEY, doc TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS cards (
    id TEXT PRIMARY KEY, doc TEXT NOT NULL, archived INTEGER NOT NULL DEFAULT 0
);
CREATE TABLE IF NOT EXISTS placements (
    card_id TEXT NOT NULL, board_id TEXT NOT NULL, column_id TEXT NOT NULL,
    position INTEGER NOT NULL
);
CREATE INDEX IF NOT EXISTS ix_placements_board ON placements(board_id);
CREATE INDEX IF NOT EXISTS ix_placements_card ON placements(card_id);
CREATE TABLE IF NOT EXISTS comments (id TEXT PRIMARY KEY, card_id TEXT NOT NULL, doc TEXT NOT NULL);
CREATE INDEX IF NOT EXISTS ix_comments_card ON comments(card_id);
CREATE TABLE IF NOT EXISTS relations (
    id TEXT PRIMARY KEY, from_card TEXT NOT NULL, to_card TEXT NOT NULL, doc TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS ix_relations_from ON relations(from_card);
CREATE INDEX IF NOT EXISTS ix_relations_to ON relations(to_card);
CREATE TABLE IF NOT EXISTS sequences (name TEXT PRIMARY KEY, value INTEGER NOT NULL);
"""


def _now() -> datetime:
    return datetime.now(UTC)


class NativeStore:
    """Persistent KanbanBackend backed by SQLite. Construct via `await NativeStore.open()`."""

    #: Native data capabilities (same honest set as the memory reference). WORKFLOW/WIP
    #: enforcement + events stay core concerns.
    capabilities: frozenset[Capability] = frozenset(
        {
            Capability.COMMENTS,
            Capability.LABELS,
            Capability.ASSIGNEES,
            Capability.MULTI_ASSIGNEE,
            Capability.CUSTOM_FIELDS,
            Capability.REORDER_COLUMNS,
            Capability.REORDER_CARDS,
            Capability.RELATIONS,
            Capability.SUBTASKS,
            Capability.CHECKLISTS,
            Capability.CHECKS,
            Capability.ATTACHMENTS,
            Capability.ARCHIVE,
            Capability.MULTI_BOARD_MEMBERSHIP,
        }
    )

    def __init__(self, db_path: str | Path) -> None:
        self._path = str(db_path)

    @classmethod
    async def open(cls, db_path: str | Path) -> NativeStore:
        store = cls(db_path)
        async with aiosqlite.connect(store._path) as db:
            await db.executescript(_SCHEMA)
            await db.commit()
        return store

    # --- internal helpers (operate on an open connection) ---
    async def _get_board(self, db: aiosqlite.Connection, board_id: str) -> Board:
        async with db.execute("SELECT doc FROM boards WHERE id=?", (board_id,)) as cur:
            row = await cur.fetchone()
        if row is None:
            raise NotFound(f"board {board_id!r} not found")
        return Board.model_validate_json(row[0])

    async def _save_board(self, db: aiosqlite.Connection, board: Board) -> None:
        await db.execute(
            "INSERT INTO boards(id, doc) VALUES(?, ?) "
            "ON CONFLICT(id) DO UPDATE SET doc=excluded.doc",
            (board.id, board.model_dump_json()),
        )

    async def _find_board_with_column(
        self, db: aiosqlite.Connection, column_id: str
    ) -> tuple[Board, Column]:
        async with db.execute("SELECT doc FROM boards") as cur:
            rows = await cur.fetchall()
        for (doc,) in rows:
            board = Board.model_validate_json(doc)
            for col in board.columns:
                if col.id == column_id:
                    return board, col
        raise NotFound(f"column {column_id!r} not found")

    async def _load_card(self, db: aiosqlite.Connection, card_id: str) -> Card:
        async with db.execute("SELECT doc FROM cards WHERE id=?", (card_id,)) as cur:
            row = await cur.fetchone()
        if row is None:
            raise NotFound(f"card {card_id!r} not found")
        card = Card.model_validate_json(row[0])
        async with db.execute(
            "SELECT board_id, column_id, position FROM placements WHERE card_id=? "
            "ORDER BY position",
            (card_id,),
        ) as cur:
            prows = await cur.fetchall()
        placements = [Placement(board_id=b, column_id=c, position=p) for b, c, p in prows]
        return card.model_copy(update={"placements": placements})

    async def _save_card(self, db: aiosqlite.Connection, card: Card) -> None:
        # placements are stored ONLY in the placements table (source of truth), not the doc.
        doc = card.model_dump_json(exclude={"placements"})
        await db.execute(
            "INSERT INTO cards(id, doc, archived) VALUES(?, ?, ?) "
            "ON CONFLICT(id) DO UPDATE SET doc=excluded.doc, archived=excluded.archived",
            (card.id, doc, int(card.archived)),
        )
        await db.execute("DELETE FROM placements WHERE card_id=?", (card.id,))
        for p in card.placements:
            await db.execute(
                "INSERT INTO placements(card_id, board_id, column_id, position) VALUES(?, ?, ?, ?)",
                (card.id, p.board_id, p.column_id, p.position),
            )

    # --- boards ---
    async def list_boards(self) -> list[Board]:
        async with aiosqlite.connect(self._path) as db:
            async with db.execute("SELECT doc FROM boards") as cur:
                rows = await cur.fetchall()
        return [Board.model_validate_json(r[0]) for r in rows]

    async def get_board(self, board_id: str) -> Board:
        async with aiosqlite.connect(self._path) as db:
            return await self._get_board(db, board_id)

    async def create_board(self, board: Board) -> Board:
        async with aiosqlite.connect(self._path) as db:
            await self._save_board(db, board)
            await db.commit()
        return board

    async def update_board(self, board_id: str, patch: BoardPatch) -> Board:
        async with aiosqlite.connect(self._path) as db:
            updated = apply_patch(await self._get_board(db, board_id), patch)
            await self._save_board(db, updated)
            await db.commit()
        return updated

    async def delete_board(self, board_id: str) -> None:
        async with aiosqlite.connect(self._path) as db:
            await db.execute("DELETE FROM boards WHERE id=?", (board_id,))
            await db.execute("DELETE FROM placements WHERE board_id=?", (board_id,))
            await db.commit()

    # --- columns (nested in a board doc) ---
    async def list_columns(self, board_id: str) -> list[Column]:
        async with aiosqlite.connect(self._path) as db:
            return list((await self._get_board(db, board_id)).columns)

    async def create_column(self, board_id: str, column: Column) -> Column:
        async with aiosqlite.connect(self._path) as db:
            board = await self._get_board(db, board_id)
            board.columns.append(column)
            await self._save_board(db, board)
            await db.commit()
        return column

    async def update_column(self, column_id: str, patch: ColumnPatch) -> Column:
        async with aiosqlite.connect(self._path) as db:
            board, col = await self._find_board_with_column(db, column_id)
            updated = apply_patch(col, patch)
            board.columns = [updated if c.id == column_id else c for c in board.columns]
            await self._save_board(db, board)
            await db.commit()
        return updated

    async def delete_column(self, column_id: str) -> None:
        async with aiosqlite.connect(self._path) as db:
            async with db.execute("SELECT doc FROM boards") as cur:
                rows = await cur.fetchall()
            for (doc,) in rows:
                board = Board.model_validate_json(doc)
                if any(c.id == column_id for c in board.columns):
                    board.columns = [c for c in board.columns if c.id != column_id]
                    await self._save_board(db, board)
            await db.commit()

    # --- flow (stored inside the board doc) ---
    async def set_flow(self, board_id: str, flow: BoardFlow) -> Board:
        async with aiosqlite.connect(self._path) as db:
            board = await self._get_board(db, board_id)
            updated = board.model_copy(update={"flow": flow})
            await self._save_board(db, updated)
            await db.commit()
        return updated

    # --- cards ---
    async def list_cards(self, board_id: str, include_archived: bool = False) -> list[Card]:
        archived_clause = "" if include_archived else " AND c.archived=0"  # decision 7
        async with aiosqlite.connect(self._path) as db:
            await self._get_board(db, board_id)  # 404 if the board is gone
            async with db.execute(
                "SELECT DISTINCT p.card_id FROM placements p "
                "JOIN cards c ON c.id = p.card_id "
                f"WHERE p.board_id=?{archived_clause}",
                (board_id,),
            ) as cur:
                ids = [r[0] for r in await cur.fetchall()]
            return [await self._load_card(db, cid) for cid in ids]

    async def get_card(self, card_id: str) -> Card:
        async with aiosqlite.connect(self._path) as db:
            return await self._load_card(db, card_id)

    async def _card_exists(self, db: aiosqlite.Connection, card_id: str) -> bool:
        async with db.execute("SELECT 1 FROM cards WHERE id=?", (card_id,)) as cur:
            return await cur.fetchone() is not None

    async def _mint_id(self, db: aiosqlite.Connection, card: Card) -> str:
        """Mint an id in the shape the card's (first) board asks for — `board.id_scheme`."""
        board = await self._get_board(db, card.placements[0].board_id)
        scheme = parse_scheme(board.id_scheme)
        if scheme.store_assigned:
            return await self._next_seq_id(db, board.id, scheme)
        for _ in range(_MINT_ATTEMPTS):
            if not await self._card_exists(db, candidate := scheme.generate()):
                return candidate
        raise Conflict(f"could not mint a free id for board {board.id!r} under {scheme.kind!r}")

    async def _next_seq_id(self, db: aiosqlite.Connection, board_id: str, scheme: IdScheme) -> str:
        """The next `PREFIX-<n>` from this BOARD's counter. Skips numbers already taken — a
        board can hold ids minted before the counter existed (cards migrated in carrying
        `KAN-1`, or a scheme switched on mid-life), and reissuing one would collide."""
        counter = f"{board_id}:{scheme.prefix}"
        while True:
            async with db.execute(
                "INSERT INTO sequences(name, value) VALUES(?, 1) "
                "ON CONFLICT(name) DO UPDATE SET value = value + 1 RETURNING value",
                (counter,),
            ) as cur:
                row = await cur.fetchone()
            candidate = f"{scheme.prefix}-{row[0]}"  # type: ignore[index]
            if not await self._card_exists(db, candidate):
                return candidate

    async def create_card(self, card: Card, *, overwrite: bool = False) -> Card:
        if not card.placements:
            raise ValueError("create_card requires at least one placement")
        async with aiosqlite.connect(self._path) as db:
            card_id = card.id or await self._mint_id(db, card)
            if not overwrite and await self._card_exists(db, card_id):
                raise Conflict(f"card {card_id!r} already exists")
            stored = card.model_copy(
                update={
                    "id": card_id,
                    "created_at": card.created_at or _now(),
                    "updated_at": _now(),
                }
            )
            await self._save_card(db, stored)
            await db.commit()
        return stored

    async def update_card(self, card_id: str, patch: CardPatch) -> Card:
        async with aiosqlite.connect(self._path) as db:
            card = apply_patch(await self._load_card(db, card_id), patch)
            updated = card.model_copy(update={"updated_at": _now()})
            await self._save_card(db, updated)
            await db.commit()
        return updated

    async def archive_card(self, card_id: str) -> Card:
        return await self._set_archived(card_id, archived=True)

    async def unarchive_card(self, card_id: str) -> Card:
        return await self._set_archived(card_id, archived=False)

    async def _set_archived(self, card_id: str, *, archived: bool) -> Card:
        async with aiosqlite.connect(self._path) as db:
            card = await self._load_card(db, card_id)
            updated = card.model_copy(update={"archived": archived, "updated_at": _now()})
            await self._save_card(db, updated)
            await db.commit()
        return updated

    async def delete_card(self, card_id: str) -> None:
        # unconditional purge — the core enforces the archive-first guard (decision 7).
        async with aiosqlite.connect(self._path) as db:
            await db.execute("DELETE FROM cards WHERE id=?", (card_id,))
            await db.execute("DELETE FROM placements WHERE card_id=?", (card_id,))
            await db.execute("DELETE FROM comments WHERE card_id=?", (card_id,))
            await db.execute(
                "DELETE FROM relations WHERE from_card=? OR to_card=?", (card_id, card_id)
            )
            await db.commit()

    async def move_card(
        self, card_id: str, to_board_id: str, to_column_id: str, position: int
    ) -> Card:
        # strict within-board (Q16): never creates a placement.
        async with aiosqlite.connect(self._path) as db:
            card = await self._load_card(db, card_id)
            if not any(p.board_id == to_board_id for p in card.placements):
                raise NotFound(
                    f"card {card_id!r} has no placement on board {to_board_id!r}"
                    " — use add_placement to put it there"
                )
            # the target column must exist — a typo'd id would drop the card off
            # every lane view (found via a dispatcher force-move, 2026-07-06)
            board = await self._get_board(db, to_board_id)
            if all(c.id != to_column_id for c in board.columns):
                raise NotFound(f"board {to_board_id!r} has no column {to_column_id!r}")
            placements = [
                p.model_copy(update={"column_id": to_column_id, "position": position})
                if p.board_id == to_board_id
                else p
                for p in card.placements
            ]
            updated = card.model_copy(update={"placements": placements, "updated_at": _now()})
            await self._save_card(db, updated)
            await db.commit()
        return updated

    async def add_placement(self, card_id: str, placement: Placement) -> Card:
        async with aiosqlite.connect(self._path) as db:
            card = await self._load_card(db, card_id)
            board = await self._get_board(db, placement.board_id)  # target board must exist
            if any(p.board_id == placement.board_id for p in card.placements):
                raise Conflict(
                    f"card {card_id!r} is already on board {placement.board_id!r} — use move_card"
                )
            # same guard move_card has: a typo'd column id would place the card on the board
            # but off every lane view — present in the data, invisible on the wall.
            if all(c.id != placement.column_id for c in board.columns):
                raise NotFound(
                    f"board {placement.board_id!r} has no column {placement.column_id!r}"
                )
            updated = card.model_copy(
                update={"placements": [*card.placements, placement], "updated_at": _now()}
            )
            await self._save_card(db, updated)
            await db.commit()
        return updated

    async def remove_placement(self, card_id: str, board_id: str) -> Card:
        async with aiosqlite.connect(self._path) as db:
            card = await self._load_card(db, card_id)
            kept = [p for p in card.placements if p.board_id != board_id]
            if len(kept) == len(card.placements):
                raise NotFound(f"card {card_id!r} has no placement on board {board_id!r}")
            if not kept:
                raise Conflict("cannot remove a card's last placement — archive_card instead")
            updated = card.model_copy(update={"placements": kept, "updated_at": _now()})
            await self._save_card(db, updated)
            await db.commit()
        return updated

    # --- comments ---
    async def list_comments(self, card_id: str) -> list[Comment]:
        async with aiosqlite.connect(self._path) as db:
            async with db.execute(
                "SELECT id, card_id, doc FROM comments WHERE card_id=?", (card_id,)
            ) as cur:
                rows = await cur.fetchall()
        # The COLUMNS are the source of truth for identity, not the doc blob: the row cannot exist
        # without a card_id (NOT NULL), but the doc can be missing one — two rows imported from
        # hermes were, and every read of those cards then raised a ValidationError, which surfaced
        # as a 500 on the card-detail endpoint and a card you simply could not open in the UI
        # (2026-07-14). One malformed comment must not make a card unreadable.
        out = []
        for row_id, row_card_id, doc in rows:
            data = json.loads(doc)
            data.setdefault("id", row_id)
            data["card_id"] = row_card_id
            out.append(Comment.model_validate(data))
        return out

    async def add_comment(self, comment: Comment) -> Comment:
        stored = comment.model_copy(update={"created_at": comment.created_at or _now()})
        async with aiosqlite.connect(self._path) as db:
            await db.execute(
                "INSERT INTO comments(id, card_id, doc) VALUES(?, ?, ?) "
                "ON CONFLICT(id) DO UPDATE SET card_id=excluded.card_id, doc=excluded.doc",
                (stored.id, stored.card_id, stored.model_dump_json()),
            )
            await db.commit()
        return stored

    async def delete_comment(self, comment_id: str) -> None:
        async with aiosqlite.connect(self._path) as db:
            await db.execute("DELETE FROM comments WHERE id=?", (comment_id,))
            await db.commit()

    # --- relations ---
    async def list_relations(self, card_id: str) -> list[Relation]:
        async with aiosqlite.connect(self._path) as db:
            async with db.execute(
                "SELECT doc FROM relations WHERE from_card=? OR to_card=?",
                (card_id, card_id),
            ) as cur:
                rows = await cur.fetchall()
        return [Relation.model_validate_json(r[0]) for r in rows]

    async def add_relation(self, relation: Relation) -> Relation:
        async with aiosqlite.connect(self._path) as db:
            await db.execute(
                "INSERT INTO relations(id, from_card, to_card, doc) VALUES(?, ?, ?, ?) "
                "ON CONFLICT(id) DO UPDATE SET from_card=excluded.from_card, "
                "to_card=excluded.to_card, doc=excluded.doc",
                (relation.id, relation.from_card, relation.to_card, relation.model_dump_json()),
            )
            await db.commit()
        return relation

    async def delete_relation(self, relation_id: str) -> None:
        async with aiosqlite.connect(self._path) as db:
            await db.execute("DELETE FROM relations WHERE id=?", (relation_id,))
            await db.commit()
