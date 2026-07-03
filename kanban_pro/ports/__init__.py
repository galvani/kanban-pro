"""The port: the contract every backend adapter implements.

This module defines *what a kanban backend must do*, backend-neutrally. It is the
single source of truth for the adapter contract — change it only when the canonical
model genuinely needs it, and update every adapter in the same change.

This is a scaffold skeleton: the Protocol enumerates the canonical operations with
`...` bodies. Fill in the real domain types (from kanban_pro.domain) as they land.
"""

from __future__ import annotations

from enum import Enum, auto
from typing import Any, Protocol, runtime_checkable


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
    SUBTASKS = auto()  # parent/child hierarchy (sometimes a field, not a link)
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


class RelationKind(Enum):
    """Canonical typed-relation vocabulary (modeled on Vikunja's `relation_kind`).

    Inverse-paired: BLOCKS<->BLOCKED_BY, PARENT<->CHILD, PRECEDES<->FOLLOWS. Adapters map
    to/from the backend's link types and gate on Capability.RELATIONS.
    """

    RELATES = auto()
    BLOCKS = auto()
    BLOCKED_BY = auto()
    DUPLICATES = auto()
    PARENT = auto()
    CHILD = auto()
    PRECEDES = auto()
    FOLLOWS = auto()


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

    Signatures use `Any` as a placeholder until the canonical domain models exist;
    replace with the real Board/Column/Card/... types from kanban_pro.domain.
    """

    #: Operations this backend supports. Honest declaration — see Capability.
    capabilities: frozenset[Capability]

    # --- boards ---
    async def list_boards(self) -> list[Any]: ...
    async def get_board(self, board_id: str) -> Any: ...
    async def create_board(self, board: Any) -> Any: ...
    async def update_board(self, board_id: str, patch: Any) -> Any: ...
    async def delete_board(self, board_id: str) -> None: ...

    # --- columns ---
    async def list_columns(self, board_id: str) -> list[Any]: ...
    async def create_column(self, board_id: str, column: Any) -> Any: ...
    async def update_column(self, column_id: str, patch: Any) -> Any: ...
    async def delete_column(self, column_id: str) -> None: ...

    # --- cards ---
    async def list_cards(self, board_id: str) -> list[Any]: ...
    async def get_card(self, card_id: str) -> Any: ...
    async def create_card(self, column_id: str, card: Any) -> Any: ...
    async def update_card(self, card_id: str, patch: Any) -> Any: ...
    async def delete_card(self, card_id: str) -> None: ...
    # move targets a (board, column, position) placement; board_id disambiguates when a
    # card has multiple placements (Capability.MULTI_BOARD_MEMBERSHIP).
    async def move_card(
        self, card_id: str, to_board_id: str, to_column_id: str, position: int
    ) -> Any: ...
