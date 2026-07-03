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
    a canonical `not_supported` instead of leaking an opaque backend error.
    """

    WIP_LIMITS = auto()
    COMMENTS = auto()
    LABELS = auto()
    ASSIGNEES = auto()
    REORDER_COLUMNS = auto()
    REORDER_CARDS = auto()
    WORKFLOW = auto()  # roadmap: allowed column->column transitions (state machine)


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
    async def move_card(self, card_id: str, to_column_id: str, position: int) -> Any: ...
