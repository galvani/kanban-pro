"""Smoke tests for the canonical domain models — construction, defaults, serialization."""

from __future__ import annotations

from datetime import datetime

from kanban_pro.domain import (
    Attachment,
    Board,
    Card,
    Checklist,
    ChecklistItem,
    Column,
    ColumnCategory,
    Comment,
    Label,
    Placement,
    Relation,
    RelationKind,
    User,
)


def test_ids_are_generated_and_unique() -> None:
    a, b = User(display_name="a"), User(display_name="b")
    assert a.id and b.id and a.id != b.id


def test_card_defaults() -> None:
    card = Card(title="Ship login page")
    assert card.archived is False
    assert card.labels == [] and card.assignees == []
    assert card.checklists == [] and card.attachments == [] and card.placements == []
    assert card.due_date is None and card.start_date is None
    assert card.ext == {}


def test_card_full_construction() -> None:
    card = Card(
        title="Ship login page",
        description="the whole feature",
        labels=["lbl-1"],
        assignees=["user-1"],
        start_date=datetime(2026, 7, 1),
        due_date=datetime(2026, 7, 4),
        checklists=[Checklist(title="DoD", items=[ChecklistItem(text="tests pass", done=True)])],
        attachments=[Attachment(url="https://example.com/pr/1", title="PR")],
        placements=[Placement(board_id="b1", column_id="c1", position=0)],
        ext={"hermes_ref": "H-42"},
    )
    assert card.checklists[0].items[0].done is True
    assert card.placements[0].board_id == "b1"
    assert card.ext["hermes_ref"] == "H-42"


def test_board_with_columns_and_labels() -> None:
    board = Board(
        name="Work",
        columns=[Column(name="Doing", category=ColumnCategory.STARTED, wip_limit=3)],
        labels=[Label(name="bug", color="#f00")],
    )
    assert board.columns[0].category is ColumnCategory.STARTED
    assert board.columns[0].wip_limit == 3
    assert board.labels[0].name == "bug"


def test_enums_are_str_and_json_friendly() -> None:
    # str-enums serialize to their value — important for MCP/JSON surfaces.
    assert ColumnCategory.DONE.value == "done"
    assert RelationKind.BLOCKED_BY.value == "blocked_by"
    rel = Relation(kind=RelationKind.BLOCKS, from_card="c1", to_card="c2")
    assert rel.model_dump()["kind"] == "blocks"


def test_relation_kinds_are_inverse_paired() -> None:
    # Every directional kind has a stored inverse so the fact can be told from either
    # side ("A duplicates B" / "B duplicated_by A"). RELATES is symmetric, so unpaired.
    inverses = {
        RelationKind.BLOCKS: RelationKind.BLOCKED_BY,
        RelationKind.DUPLICATES: RelationKind.DUPLICATED_BY,
        RelationKind.PARENT: RelationKind.CHILD,
        RelationKind.PRECEDES: RelationKind.FOLLOWS,
    }
    paired = set(inverses) | set(inverses.values())
    assert paired | {RelationKind.RELATES} == set(RelationKind)

    dup = Relation(kind=RelationKind.DUPLICATED_BY, from_card="keep", to_card="copy")
    assert dup.model_dump()["kind"] == "duplicated_by"


def test_comment_author_is_user_id() -> None:
    c = Comment(card_id="card-1", author="user-1", body="looks good")
    assert c.author == "user-1"
