"""Canonical ⇄ Hermes translation (pure functions — no I/O).

Ground truth: docs/hermes-kanban.md. Hermes lanes ARE statuses; a canonical Column is
synthesized per lane with id "<board>:<lane>". The `archived` lane maps to the
canonical archived FLAG, not a column. Harness-specific task fields ride in
`Card.ext["hermes"]`.
"""

from __future__ import annotations

import json
from datetime import UTC, datetime
from typing import Any

from kanban_pro.domain import Card, Column, ColumnCategory, Comment, Placement, Relation
from kanban_pro.domain import RelationKind as RK

#: canonical lane order (docs/hermes-kanban.md); ad-hoc lanes append after these.
LANE_ORDER = ["triage", "todo", "scheduled", "ready", "running", "blocked", "review", "done"]

LANE_CATEGORY: dict[str, ColumnCategory] = {
    "triage": ColumnCategory.TRIAGE,
    "todo": ColumnCategory.BACKLOG,  # waiting on parents
    "scheduled": ColumnCategory.BACKLOG,  # waiting on time
    "ready": ColumnCategory.UNSTARTED,  # actionable now
    "running": ColumnCategory.STARTED,
    "blocked": ColumnCategory.STARTED,  # in-flight but stuck
    "review": ColumnCategory.STARTED,
    "done": ColumnCategory.DONE,
}

#: task columns copied verbatim into ext["hermes"] when non-null.
_EXT_FIELDS = (
    "priority",
    "created_by",
    "workspace_kind",
    "workspace_path",
    "branch_name",
    "tenant",
    "block_kind",
    "session_id",
    "project_id",
    "result",
)


def column_id(board: str, lane: str) -> str:
    return f"{board}:{lane}"


def lane_of(column_id_: str) -> str:
    """Accepts '<board>:<lane>' or a bare lane name."""
    return column_id_.split(":", 1)[1] if ":" in column_id_ else column_id_


def columns_for(board: str, observed_lanes: set[str]) -> list[Column]:
    """Synthesize the board's columns: canonical order first, ad-hoc lanes appended."""
    extras = sorted(observed_lanes - set(LANE_ORDER) - {"archived"})
    return [
        Column(
            id=column_id(board, lane),
            name=lane,
            order=i,
            category=LANE_CATEGORY.get(lane, ColumnCategory.UNSTARTED),
        )
        for i, lane in enumerate(LANE_ORDER + extras)
    ]


def _ts(value: Any) -> datetime | None:
    return datetime.fromtimestamp(int(value), UTC) if value else None


def card_from_row(board: str, row: dict[str, Any]) -> Card:
    """Map a `tasks` row (as a dict) onto a canonical Card."""
    ext: dict[str, Any] = {k: row[k] for k in _EXT_FIELDS if row.get(k) not in (None, "")}
    if row.get("skills"):
        try:
            ext["skills"] = json.loads(row["skills"])
        except (ValueError, TypeError):
            ext["skills"] = row["skills"]
    status = str(row["status"])
    stamps = [
        t for t in (_ts(row.get(k)) for k in ("created_at", "started_at", "completed_at")) if t
    ]
    return Card(
        id=str(row["id"]),
        title=str(row["title"]),
        description=row.get("body"),
        assignees=[row["assignee"]] if row.get("assignee") else [],
        archived=status == "archived",
        created_at=_ts(row.get("created_at")),
        updated_at=max(stamps) if stamps else None,
        # archived cards keep their last real lane unknowable -> park them on 'done'.
        placements=[
            Placement(
                board_id=board,
                column_id=column_id(board, status if status != "archived" else "done"),
            )
        ],
        ext={"hermes": ext} if ext else {},
    )


def comment_from_row(row: dict[str, Any]) -> Comment:
    return Comment(
        id=str(row["id"]),
        card_id=str(row["task_id"]),
        author=str(row.get("author") or "unknown"),
        body=str(row["body"]),
        created_at=_ts(row.get("created_at")),
    )


def relation_id(parent_id: str, child_id: str) -> str:
    return f"{parent_id}->{child_id}"


def relation_from_link(parent_id: str, child_id: str) -> Relation:
    """task_links row -> PARENT relation (from = parent of to)."""
    return Relation(
        id=relation_id(parent_id, child_id),
        kind=RK.PARENT,
        from_card=parent_id,
        to_card=child_id,
    )


def link_from_relation(rel: Relation) -> tuple[str, str]:
    """Canonical relation -> (parent_id, child_id). Only PARENT/CHILD map to Hermes."""
    if rel.kind is RK.PARENT:
        return rel.from_card, rel.to_card
    if rel.kind is RK.CHILD:
        return rel.to_card, rel.from_card
    raise ValueError(f"hermes supports only parent/child links, not {rel.kind.value!r}")
