"""The port: the contract every backend adapter implements.

This module defines *what a kanban backend must do*, backend-neutrally. It is the
single source of truth for the adapter contract — change it only when the canonical
model genuinely needs it, and update every adapter in the same change.

The Protocol enumerates the canonical operations over the domain models. Method bodies
are `...` — adapters implement them. Not yet covered (next expansion): label registry,
assignee, checklist, and attachment operations.
"""

from __future__ import annotations

from enum import Enum, auto
from typing import Protocol, runtime_checkable

from kanban_pro.domain import (
    Board,
    BoardPatch,
    Card,
    CardPatch,
    Column,
    ColumnPatch,
    Comment,
    Relation,
)


class Capability(Enum):
    """Operations a backend may or may not support.

    Adapters declare their supported set; the core checks before dispatch and returns
    a canonical `not_supported` instead of leaking an opaque backend error. Set grounded
    in docs/research/kanban-backends.md (15-product survey).
    """

    COMMENTS = auto()
    LABELS = auto()
    ASSIGNEES = auto()
    MULTI_ASSIGNEE = auto()  # Wekan/Asana/ClickUp yes; Kanboard single-owner
    CUSTOM_FIELDS = auto()  # pervasive & tenant-specific -> surfaced via entity `ext`
    REORDER_COLUMNS = auto()
    REORDER_CARDS = auto()
    RELATIONS = auto()  # typed card<->card links (see RelationKind)
    SUBTASKS = auto()  # child CARDS via PARENT/CHILD relations (sometimes a field)
    CHECKLISTS = auto()  # lightweight {text, done} items nested on a card (not cards)
    ATTACHMENTS = auto()  # link-only {url, title} for v1; file uploads deferred
    ARCHIVE = auto()  # soft archive/unarchive; polyfilled as a flag where absent
    WIP_LIMITS = auto()  # server-enforced only in Vikunja (+ kanban-pro native)
    WORKFLOW = auto()  # allowed column->column transitions; only Jira enforces it
    MULTI_BOARD_MEMBERSHIP = auto()  # a card in several boards/lists at once
    WEBHOOKS = auto()  # backend can push events (else kanban-pro polls on clients' behalf)


class Fulfilment(Enum):
    """How a Capability is satisfied for the active provider (SPEC decision 2).

    Reported per-capability by `GET /capabilities` so clients know the guarantees:
    NATIVE data is authoritative in the backend; POLYFILLED data lives only in
    kanban-pro's overlay store (a partial system of record).
    """

    NATIVE = auto()  # adapter delegates to the backend
    POLYFILLED = auto()  # kanban-pro provides it from the overlay store
    UNAVAILABLE = auto()  # neither possible -> canonical not_supported


# RelationKind (the typed-relation vocabulary) is a DATA type — it lives in
# kanban_pro.domain. Capability.RELATIONS gates its use.


class KanbanError(Exception):
    """Base of the canonical error taxonomy adapters translate backend errors into."""


class NotFound(KanbanError): ...


class Conflict(KanbanError): ...


class Unauthorized(KanbanError): ...


class NotSupported(KanbanError): ...


class BackendUnavailable(KanbanError): ...


@runtime_checkable
class KanbanBackend(Protocol):
    """The port. Every adapter in `kanban_pro.adapters` implements this.

    All inputs/outputs are canonical domain models — no raw backend types cross this
    boundary. Partial updates take a *Patch model (exclude_unset semantics).
    """

    #: Operations this backend supports. Honest declaration — see Capability.
    capabilities: frozenset[Capability]

    # --- boards ---
    async def list_boards(self) -> list[Board]: ...
    async def get_board(self, board_id: str) -> Board: ...
    async def create_board(self, board: Board) -> Board: ...
    async def update_board(self, board_id: str, patch: BoardPatch) -> Board: ...
    async def delete_board(self, board_id: str) -> None: ...

    # --- columns ---
    async def list_columns(self, board_id: str) -> list[Column]: ...
    async def create_column(self, board_id: str, column: Column) -> Column: ...
    async def update_column(self, column_id: str, patch: ColumnPatch) -> Column: ...
    async def delete_column(self, column_id: str) -> None: ...

    # --- cards ---
    async def list_cards(self, board_id: str) -> list[Card]: ...
    async def get_card(self, card_id: str) -> Card: ...
    # card.placements must have >=1 entry (where to create it).
    async def create_card(self, card: Card) -> Card: ...
    async def update_card(self, card_id: str, patch: CardPatch) -> Card: ...
    async def archive_card(self, card_id: str) -> Card: ...  # soft, recoverable (default)
    async def unarchive_card(self, card_id: str) -> Card: ...
    # permanent purge; the core guards this to archived cards only (SPEC decision 7).
    async def delete_card(self, card_id: str) -> None: ...
    # move targets a (board, column, position) placement; board_id disambiguates when a
    # card has multiple placements (Capability.MULTI_BOARD_MEMBERSHIP).
    async def move_card(
        self, card_id: str, to_board_id: str, to_column_id: str, position: int
    ) -> Card: ...

    # --- comments (Capability.COMMENTS) ---
    async def list_comments(self, card_id: str) -> list[Comment]: ...
    async def add_comment(self, comment: Comment) -> Comment: ...
    async def delete_comment(self, comment_id: str) -> None: ...

    # --- relations (Capability.RELATIONS) ---
    async def list_relations(self, card_id: str) -> list[Relation]: ...
    async def add_relation(self, relation: Relation) -> Relation: ...
    async def delete_relation(self, relation_id: str) -> None: ...
