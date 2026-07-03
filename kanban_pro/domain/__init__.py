"""Canonical kanban domain model (Pydantic v2).

The ONLY types that cross the port boundary. Keep the core minimal — backend-specific
fields belong in each entity's `ext` mapping, not here (SPEC decision 1). Card placement
is a set of {board_id, column_id, position} entries (`placements[]`), not a single
column_id — a card may live on several boards at once (SPEC decision 4). Single-board
backends + the native store use one placement.

Enums here are the data vocabulary (ColumnCategory, RelationKind). Capability/Fulfilment
(the *contract* vocabulary) live in kanban_pro.ports.
"""

from __future__ import annotations

from datetime import datetime
from enum import StrEnum
from typing import Any
from uuid import uuid4

from pydantic import BaseModel, Field


def _new_id() -> str:
    """Default id generator. Adapters overwrite with the backend's own id."""
    return uuid4().hex


class ColumnCategory(StrEnum):
    """Portable status semantics for a Column (from Linear's workflow-state types).

    Lets "which column means done?" survive translation across backends whose column
    names differ. `move_card` + workflow rules key off category, not raw name.
    """

    TRIAGE = "triage"
    BACKLOG = "backlog"
    UNSTARTED = "unstarted"
    STARTED = "started"
    DONE = "done"
    CANCELED = "canceled"


class RelationKind(StrEnum):
    """Canonical typed-relation vocabulary (modeled on Vikunja's `relation_kind`).

    Inverse-paired: BLOCKS<->BLOCKED_BY, PARENT<->CHILD, PRECEDES<->FOLLOWS. Adapters map
    to/from the backend's link types and gate on Capability.RELATIONS. Subtasks = child
    CARDS via PARENT/CHILD (not checklists).
    """

    RELATES = "relates"
    BLOCKS = "blocks"
    BLOCKED_BY = "blocked_by"
    DUPLICATES = "duplicates"
    PARENT = "parent"
    CHILD = "child"
    PRECEDES = "precedes"
    FOLLOWS = "follows"


class User(BaseModel):
    """Minimal person. `ext` holds backend-specific keys (Jira accountId, GitHub login…)
    since backends key users differently. Referenced by Card.assignees + Comment.author.
    """

    id: str = Field(default_factory=_new_id)
    display_name: str
    ext: dict[str, Any] = Field(default_factory=dict)


class Label(BaseModel):
    """Board-scoped tag (owned by Board.labels; referenced by Card.labels via id)."""

    id: str = Field(default_factory=_new_id)
    name: str
    color: str | None = None


class ChecklistItem(BaseModel):
    """One line in a Checklist — NOT a card (no column/assignee/placement)."""

    id: str = Field(default_factory=_new_id)
    text: str
    done: bool = False
    order: int = 0


class Checklist(BaseModel):
    """Lightweight "definition of done" nested on a Card (SPEC Q4)."""

    id: str = Field(default_factory=_new_id)
    title: str
    items: list[ChecklistItem] = Field(default_factory=list)


class Attachment(BaseModel):
    """Link-only reference on a Card (PR/doc/image URL). No file storage in v1 (SPEC Q5)."""

    id: str = Field(default_factory=_new_id)
    url: str
    title: str | None = None


class Placement(BaseModel):
    """Where a card sits: one (board, column, position). A card has >=1 (SPEC decision 4)."""

    board_id: str
    column_id: str
    position: int = 0


class Column(BaseModel):
    """A list/lane/status within a board."""

    id: str = Field(default_factory=_new_id)
    name: str
    order: int = 0
    category: ColumnCategory = ColumnCategory.UNSTARTED
    wip_limit: int | None = None
    ext: dict[str, Any] = Field(default_factory=dict)


class Card(BaseModel):
    """The unit of work. `labels`/`assignees` are Label/User ids; placement is a set."""

    id: str = Field(default_factory=_new_id)
    title: str
    description: str | None = None
    labels: list[str] = Field(default_factory=list)  # Label ids (board-scoped)
    assignees: list[str] = Field(default_factory=list)  # User ids
    start_date: datetime | None = None
    due_date: datetime | None = None
    checklists: list[Checklist] = Field(default_factory=list)
    attachments: list[Attachment] = Field(default_factory=list)
    placements: list[Placement] = Field(default_factory=list)
    archived: bool = False  # archive-first deletion (SPEC decision 7)
    created_at: datetime | None = None
    updated_at: datetime | None = None
    ext: dict[str, Any] = Field(default_factory=dict)


class Board(BaseModel):
    """A container of columns + its label registry."""

    id: str = Field(default_factory=_new_id)
    name: str
    description: str | None = None
    columns: list[Column] = Field(default_factory=list)
    labels: list[Label] = Field(default_factory=list)  # board-scoped label registry
    ext: dict[str, Any] = Field(default_factory=dict)


class Comment(BaseModel):
    """A comment on a card. `author` is a User id."""

    id: str = Field(default_factory=_new_id)
    card_id: str
    author: str  # User id
    body: str
    created_at: datetime | None = None
    ext: dict[str, Any] = Field(default_factory=dict)


class Relation(BaseModel):
    """A typed edge between two cards (gated by Capability.RELATIONS)."""

    id: str = Field(default_factory=_new_id)
    kind: RelationKind
    from_card: str  # Card id
    to_card: str  # Card id


# --- patch models (partial updates) ---
# Only fields explicitly set are applied — adapters use
# `patch.model_dump(exclude_unset=True)`, so an unset field is "leave untouched",
# distinct from an explicit `None` (which clears a nullable field).


class BoardPatch(BaseModel):
    name: str | None = None
    description: str | None = None
    ext: dict[str, Any] | None = None


class ColumnPatch(BaseModel):
    name: str | None = None
    order: int | None = None
    category: ColumnCategory | None = None
    wip_limit: int | None = None
    ext: dict[str, Any] | None = None


class CardPatch(BaseModel):
    title: str | None = None
    description: str | None = None
    labels: list[str] | None = None
    assignees: list[str] | None = None
    start_date: datetime | None = None
    due_date: datetime | None = None
    ext: dict[str, Any] | None = None


__all__ = [
    "ColumnCategory",
    "RelationKind",
    "User",
    "Label",
    "ChecklistItem",
    "Checklist",
    "Attachment",
    "Placement",
    "Column",
    "Card",
    "Board",
    "Comment",
    "Relation",
    "BoardPatch",
    "ColumnPatch",
    "CardPatch",
]
