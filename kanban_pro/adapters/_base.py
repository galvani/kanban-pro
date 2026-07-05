"""BaseAdapter — the thin-adapter starting point (docs/adapter-structure.md).

A remote adapter shouldn't have to write every port method when its backend natively
does a fraction of them. BaseAdapter implements the WHOLE port as `NotSupported`
defaults and declares no capabilities; a concrete adapter subclasses it, overrides
only what its backend does natively, and declares those capabilities honestly. The
augmenting layer gates on capabilities *before* dispatch, so these defaults are a
backstop, not the normal path.

Store adapters (memory, native) implement everything and don't need this.
"""

from __future__ import annotations

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
)
from kanban_pro.ports import Capability, NotSupported


class BaseAdapter:
    """All port methods raise NotSupported; override what the backend does natively."""

    capabilities: frozenset[Capability] = frozenset()

    def _not_supported(self, op: str) -> NotSupported:
        return NotSupported(f"{op} is not supported by the {type(self).__name__} backend")

    # --- boards ---
    async def list_boards(self) -> list[Board]:
        raise self._not_supported("list_boards")

    async def get_board(self, board_id: str) -> Board:
        raise self._not_supported("get_board")

    async def create_board(self, board: Board) -> Board:
        raise self._not_supported("create_board")

    async def update_board(self, board_id: str, patch: BoardPatch) -> Board:
        raise self._not_supported("update_board")

    async def delete_board(self, board_id: str) -> None:
        raise self._not_supported("delete_board")

    # --- columns ---
    async def list_columns(self, board_id: str) -> list[Column]:
        raise self._not_supported("list_columns")

    async def create_column(self, board_id: str, column: Column) -> Column:
        raise self._not_supported("create_column")

    async def update_column(self, column_id: str, patch: ColumnPatch) -> Column:
        raise self._not_supported("update_column")

    async def delete_column(self, column_id: str) -> None:
        raise self._not_supported("delete_column")

    # --- cards ---
    async def list_cards(self, board_id: str, include_archived: bool = False) -> list[Card]:
        raise self._not_supported("list_cards")

    async def get_card(self, card_id: str) -> Card:
        raise self._not_supported("get_card")

    async def create_card(self, card: Card) -> Card:
        raise self._not_supported("create_card")

    async def update_card(self, card_id: str, patch: CardPatch) -> Card:
        raise self._not_supported("update_card")

    async def archive_card(self, card_id: str) -> Card:
        raise self._not_supported("archive_card")

    async def unarchive_card(self, card_id: str) -> Card:
        raise self._not_supported("unarchive_card")

    async def delete_card(self, card_id: str) -> None:
        raise self._not_supported("delete_card")

    async def move_card(
        self, card_id: str, to_board_id: str, to_column_id: str, position: int
    ) -> Card:
        raise self._not_supported("move_card")

    async def add_placement(self, card_id: str, placement: Placement) -> Card:
        raise self._not_supported("add_placement")

    async def remove_placement(self, card_id: str, board_id: str) -> Card:
        raise self._not_supported("remove_placement")

    # --- comments ---
    async def list_comments(self, card_id: str) -> list[Comment]:
        raise self._not_supported("list_comments")

    async def add_comment(self, comment: Comment) -> Comment:
        raise self._not_supported("add_comment")

    async def delete_comment(self, comment_id: str) -> None:
        raise self._not_supported("delete_comment")

    # --- relations ---
    async def list_relations(self, card_id: str) -> list[Relation]:
        raise self._not_supported("list_relations")

    async def add_relation(self, relation: Relation) -> Relation:
        raise self._not_supported("add_relation")

    async def delete_relation(self, relation_id: str) -> None:
        raise self._not_supported("delete_relation")
