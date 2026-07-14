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
from typing import Annotated, Any

from pydantic import AfterValidator, BaseModel, Field

from kanban_pro.domain.ids import new_id as _new_id
from kanban_pro.domain.ids import parse_scheme


def _check_id_scheme(spec: str | None) -> str | None:
    parse_scheme(spec)  # refuse a bad scheme at create/update, not at the first card
    return spec


#: A `board.id_scheme` spec (see domain.ids), validated wherever it can be set.
type IdSchemeSpec = Annotated[str | None, AfterValidator(_check_id_scheme)]


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

    Inverse-paired: BLOCKS<->BLOCKED_BY, DUPLICATES<->DUPLICATED_BY, PARENT<->CHILD,
    PRECEDES<->FOLLOWS. Adapters map to/from the backend's link types and gate on
    Capability.RELATIONS. Subtasks = child CARDS via PARENT/CHILD (not checklists).

    Inverses are STORED, not derived: `list_relations` returns every edge touching the
    card (either side), so the pair exists to let the caller say which way it meant.
    `A duplicates B` = A is the redundant copy, B is the one to keep; `B duplicated_by A`
    is the same fact told from the survivor's side. RELATES is symmetric and unpaired.
    """

    RELATES = "relates"
    BLOCKS = "blocks"
    BLOCKED_BY = "blocked_by"
    DUPLICATES = "duplicates"
    DUPLICATED_BY = "duplicated_by"
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
    """The unit of work. `labels`/`assignees` are Label/User ids; placement is a set.

    `id` is empty by default: the STORE mints it in `create_card`, in the shape the card's
    board asks for (`board.id_scheme`, see domain.ids). Pin an id here only to preserve an
    existing one — migration does; ordinary callers shouldn't.
    """

    id: str = ""
    title: str
    description: str | None = None
    #: 0-10, HIGHER = more urgent (matches the hermes backend's `ORDER BY priority DESC`).
    #: 0 = unprioritised, and it is the default, so a card nobody ranked never jumps the queue.
    #: Orders the work queue WITHIN a category tier — it does not outrank the tiers themselves
    #: (see `list_work`): started work is still offered before a shinier unstarted card, or an
    #: agent would abandon whatever it's holding every time something urgent lands.
    priority: int = Field(default=0, ge=0, le=10)
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


class BoardFlow(BaseModel):
    """A board's workflow: allowed column->column moves, keyed by column ID.

    `transitions[from_column_id]` is the list of column IDs a card may move TO from that
    lane. Edges reference the SAME board's column ids, so a flow can never dangle (unlike
    a name-matched external scheme). Absent / empty `transitions` ⇒ a free-roam board.

    A column that appears in NO edge (neither a key nor a listed target) is *unmodeled* —
    moves in and out of it stay free: a flow governs only the columns it names. This is
    how a board keeps an ad-hoc scratch lane ungoverned while the rest is enforced.
    """

    transitions: dict[str, list[str]] = Field(default_factory=dict)
    #: decrement a card's attempt counter when it's reassigned/re-laned (per-board opt-in)
    auto_reset_attempts_on_reassign: bool = True


class Board(BaseModel):
    """A container of columns + its label registry + its workflow."""

    id: str = Field(default_factory=_new_id)
    name: str
    description: str | None = None
    columns: list[Column] = Field(default_factory=list)
    labels: list[Label] = Field(default_factory=list)  # board-scoped label registry
    #: the workflow over this board's columns; None / empty ⇒ free-roam (see BoardFlow).
    flow: BoardFlow | None = None
    #: shape of the ids this board's cards get: uuid | short[:N] | prefix:P[:N] | seq:P
    #: (None ⇒ uuid). Applies to cards created from now on; existing ids are untouched.
    id_scheme: IdSchemeSpec = None
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
# EXCEPTION — `ext` is a SHALLOW MERGE, not a replace (SPEC decision 1, Q17): patch keys
# merge into the stored dict; a key set to None is removed. Protects concurrent writers
# and kanban-pro's own `kanban_pro.*` metadata from being clobbered.
# Adapters apply patches via `apply_patch` below — the single implementation of these
# semantics.


def apply_patch[M: BaseModel](entity: M, patch: BaseModel) -> M:
    """Apply a *Patch model to an entity (the canonical patch semantics, see above).

    `ext` shallow-merges: patch keys overwrite/add, a key set to None is removed
    (None values never persist in ext). `ext: null` for the whole dict = untouched.
    """
    data = patch.model_dump(exclude_unset=True)
    ext_patch = data.pop("ext", None)
    if ext_patch is not None:
        merged = {**getattr(entity, "ext", {}), **ext_patch}
        data["ext"] = {k: v for k, v in merged.items() if v is not None}
    return entity.model_copy(update=data)


class BoardPatch(BaseModel):
    name: str | None = None
    description: str | None = None
    id_scheme: IdSchemeSpec = None
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
    priority: int | None = Field(default=None, ge=0, le=10)  # 0-10, higher = more urgent
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
    "BoardFlow",
    "Comment",
    "Relation",
    "BoardPatch",
    "ColumnPatch",
    "CardPatch",
    "apply_patch",
]
