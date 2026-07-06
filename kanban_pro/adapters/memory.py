"""In-memory reference adapter — the KanbanBackend port's proving ground + test fixture.

Stores everything in dicts. Declares the *data* capabilities natively (it IS a store);
WORKFLOW/WIP enforcement stays a core concern (not the adapter's), and it pushes no
events (no WEBHOOKS). Doubles as the seed model for the native SQLite store.
"""

from __future__ import annotations

from datetime import UTC, datetime

from kanban_pro.domain import (
    Board,
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
from kanban_pro.ports import Capability, Conflict, NotFound


def _now() -> datetime:
    return datetime.now(UTC)


class MemoryBackend:
    """Reference KanbanBackend backed by in-process dicts.

    Conforms to kanban_pro.ports.KanbanBackend structurally (a test asserts it).
    """

    #: Native data capabilities. Not WORKFLOW/WIP (core enforces those) or WEBHOOKS
    #: (no push) — an honest declaration so the core knows what to polyfill.
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
            Capability.ATTACHMENTS,
            Capability.ARCHIVE,
            Capability.MULTI_BOARD_MEMBERSHIP,
        }
    )

    def __init__(self) -> None:
        self._boards: dict[str, Board] = {}
        self._cards: dict[str, Card] = {}
        self._comments: dict[str, Comment] = {}
        self._relations: dict[str, Relation] = {}

    # --- boards ---
    async def list_boards(self) -> list[Board]:
        return list(self._boards.values())

    async def get_board(self, board_id: str) -> Board:
        try:
            return self._boards[board_id]
        except KeyError:
            raise NotFound(f"board {board_id!r} not found") from None

    async def create_board(self, board: Board) -> Board:
        self._boards[board.id] = board
        return board

    async def update_board(self, board_id: str, patch: BoardPatch) -> Board:
        updated = apply_patch(await self.get_board(board_id), patch)
        self._boards[board_id] = updated
        return updated

    async def delete_board(self, board_id: str) -> None:
        self._boards.pop(board_id, None)
        # drop placements pointing at the deleted board
        for cid, card in list(self._cards.items()):
            kept = [p for p in card.placements if p.board_id != board_id]
            if len(kept) != len(card.placements):
                self._cards[cid] = card.model_copy(update={"placements": kept})

    # --- columns (nested in a board) ---
    def _find_column(self, column_id: str) -> tuple[Board, Column]:
        for board in self._boards.values():
            for col in board.columns:
                if col.id == column_id:
                    return board, col
        raise NotFound(f"column {column_id!r} not found")

    async def list_columns(self, board_id: str) -> list[Column]:
        return list((await self.get_board(board_id)).columns)

    async def create_column(self, board_id: str, column: Column) -> Column:
        board = await self.get_board(board_id)
        board.columns.append(column)
        return column

    async def update_column(self, column_id: str, patch: ColumnPatch) -> Column:
        board, col = self._find_column(column_id)
        updated = apply_patch(col, patch)
        board.columns = [updated if c.id == column_id else c for c in board.columns]
        return updated

    async def delete_column(self, column_id: str) -> None:
        for board in self._boards.values():
            board.columns = [c for c in board.columns if c.id != column_id]

    # --- cards ---
    async def list_cards(self, board_id: str, include_archived: bool = False) -> list[Card]:
        await self.get_board(board_id)  # 404 if the board is gone
        # archived cards are hidden from normal listings (SPEC decision 7)
        return [
            c
            for c in self._cards.values()
            if (include_archived or not c.archived)
            and any(p.board_id == board_id for p in c.placements)
        ]

    async def get_card(self, card_id: str) -> Card:
        try:
            return self._cards[card_id]
        except KeyError:
            raise NotFound(f"card {card_id!r} not found") from None

    async def create_card(self, card: Card) -> Card:
        if not card.placements:
            raise ValueError("create_card requires at least one placement")
        stored = card.model_copy(
            update={"created_at": card.created_at or _now(), "updated_at": _now()}
        )
        self._cards[stored.id] = stored
        return stored

    async def update_card(self, card_id: str, patch: CardPatch) -> Card:
        card = apply_patch(await self.get_card(card_id), patch)
        updated = card.model_copy(update={"updated_at": _now()})
        self._cards[card_id] = updated
        return updated

    async def archive_card(self, card_id: str) -> Card:
        card = await self.get_card(card_id)
        updated = card.model_copy(update={"archived": True, "updated_at": _now()})
        self._cards[card_id] = updated
        return updated

    async def unarchive_card(self, card_id: str) -> Card:
        card = await self.get_card(card_id)
        updated = card.model_copy(update={"archived": False, "updated_at": _now()})
        self._cards[card_id] = updated
        return updated

    async def delete_card(self, card_id: str) -> None:
        # unconditional purge — the core enforces the archive-first guard (decision 7).
        self._cards.pop(card_id, None)
        self._comments = {k: v for k, v in self._comments.items() if v.card_id != card_id}
        self._relations = {
            k: v
            for k, v in self._relations.items()
            if v.from_card != card_id and v.to_card != card_id
        }

    async def move_card(
        self, card_id: str, to_board_id: str, to_column_id: str, position: int
    ) -> Card:
        # strict within-board (Q16): never creates a placement.
        card = await self.get_card(card_id)
        if not any(p.board_id == to_board_id for p in card.placements):
            raise NotFound(
                f"card {card_id!r} has no placement on board {to_board_id!r}"
                " — use add_placement to put it there"
            )
        # the target column must exist — a typo'd id would drop the card off
        # every lane view (found via a dispatcher force-move, 2026-07-06)
        board = await self.get_board(to_board_id)
        if all(c.id != to_column_id for c in board.columns):
            raise NotFound(f"board {to_board_id!r} has no column {to_column_id!r}")
        placements = [
            p.model_copy(update={"column_id": to_column_id, "position": position})
            if p.board_id == to_board_id
            else p
            for p in card.placements
        ]
        updated = card.model_copy(update={"placements": placements, "updated_at": _now()})
        self._cards[card_id] = updated
        return updated

    async def add_placement(self, card_id: str, placement: Placement) -> Card:
        card = await self.get_card(card_id)
        await self.get_board(placement.board_id)  # target board must exist
        if any(p.board_id == placement.board_id for p in card.placements):
            raise Conflict(
                f"card {card_id!r} is already on board {placement.board_id!r} — use move_card"
            )
        updated = card.model_copy(
            update={"placements": [*card.placements, placement], "updated_at": _now()}
        )
        self._cards[card_id] = updated
        return updated

    async def remove_placement(self, card_id: str, board_id: str) -> Card:
        card = await self.get_card(card_id)
        kept = [p for p in card.placements if p.board_id != board_id]
        if len(kept) == len(card.placements):
            raise NotFound(f"card {card_id!r} has no placement on board {board_id!r}")
        if not kept:
            raise Conflict("cannot remove a card's last placement — archive_card instead")
        updated = card.model_copy(update={"placements": kept, "updated_at": _now()})
        self._cards[card_id] = updated
        return updated

    # --- comments ---
    async def list_comments(self, card_id: str) -> list[Comment]:
        return [c for c in self._comments.values() if c.card_id == card_id]

    async def add_comment(self, comment: Comment) -> Comment:
        stored = comment.model_copy(update={"created_at": comment.created_at or _now()})
        self._comments[stored.id] = stored
        return stored

    async def delete_comment(self, comment_id: str) -> None:
        self._comments.pop(comment_id, None)

    # --- relations ---
    async def list_relations(self, card_id: str) -> list[Relation]:
        return [
            r for r in self._relations.values() if r.from_card == card_id or r.to_card == card_id
        ]

    async def add_relation(self, relation: Relation) -> Relation:
        self._relations[relation.id] = relation
        return relation

    async def delete_relation(self, relation_id: str) -> None:
        self._relations.pop(relation_id, None)
