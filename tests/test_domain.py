"""Smoke tests for the canonical domain models — construction, defaults, serialization."""

from __future__ import annotations

from datetime import datetime

import pytest
from pydantic import BaseModel, ConfigDict, ValidationError

from kanban_pro.domain import (
    Attachment,
    Board,
    Card,
    CardPatch,
    Check,
    Checklist,
    ChecklistItem,
    CheckStatus,
    Column,
    ColumnCategory,
    Comment,
    Label,
    Placement,
    Relation,
    RelationKind,
    User,
    apply_patch,
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


def test_apply_patch_does_not_alias_the_callers_mutable_values() -> None:
    """`model_copy(update=...)` copies neither values nor types, so the entity would otherwise hold
    a REFERENCE to the patch's list — mutate the patch afterwards and you mutate the stored card.

    This applies to every list/dict field, not just `checks`. It was shipped unnoticed because the
    suite was green, which is exactly what an adversarial reviewer said it would prove: nothing.
    """
    checks = [Check(key="static", text="tests")]
    card = apply_patch(Card(title="c"), CardPatch(checks=checks))

    checks.append(Check(key="smuggled", text="added AFTER the patch was applied"))
    checks[0].status = CheckStatus.PASSED

    assert [c.key for c in card.checks] == ["static"]  # the card did not grow a check
    assert card.checks[0].status is CheckStatus.PENDING  # nor did its result change
    assert isinstance(card.checks[0], Check)  # and it is a Check, not a dict (no re-validation)


def test_card_preserves_fields_an_older_model_does_not_know_about() -> None:
    """A stale process must not be able to ERASE a field it predates.

    The native store persists a card as one JSON doc: read -> validate -> mutate -> dump -> write.
    Under Pydantic's default (`extra="ignore"`) a long-lived server running older code drops every
    field its `Card` lacks and writes the card back without them. That is how AIR-2915 lost its
    verification contract on 2026-07-14: an MCP server started before `checks` existed wrote a
    comment, and `browser-verify` was gone — the gate silently disarmed itself.

    `Card` therefore ALLOWS extras: an old model carries what it cannot understand straight
    through. This test simulates that old model; it must not be "fixed" by adding the new field
    to `OldCard` — the whole point is that OldCard never learns about it.
    """

    class OldCard(BaseModel):  # yesterday's model: it has never heard of `checks`
        model_config = ConfigDict(extra="allow")
        id: str = ""
        title: str

    doc = Card(id="c1", title="t", checks=[Check(key="browser-verify", text="render it")])
    round_tripped = OldCard.model_validate_json(doc.model_dump_json()).model_dump_json()

    assert [c.key for c in Card.model_validate_json(round_tripped).checks] == ["browser-verify"]


def test_card_patch_rejects_an_unknown_field() -> None:
    """Strict at the boundary, permissive in storage: `Card` carries unknown fields, but a PATCH
    is caller input, so a typo'd key is an error — not silently-stored garbage."""
    with pytest.raises(ValidationError):
        CardPatch(assignee="reviewer")  # the field is `assignees`
