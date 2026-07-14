"""Checks — the verification contract. Every test here is a hole AIR-2915 went through.

The card that motivated this (2026-07-14) reached the rebaser with the exact bug it was opened
for still unverified: the required browser check was never run, and a cheaper check added under a
different id made the report look green. So the tests assert the refusals, not just the happy path
— a gate is only worth what it says NO to.
"""

from __future__ import annotations

import asyncio

import pytest

from kanban_pro.adapters.memory import MemoryBackend
from kanban_pro.core import AugmentingBackend, ChangeLog, ClaimStore, RecordingBackend
from kanban_pro.core.checks import (
    CHECK_GATE_EXT_KEY,
    blocking_checks,
    declare_checks,
    record_check_result,
    retract_check,
)
from kanban_pro.domain import Board, Card, CheckStatus, Column, ColumnCategory, Placement
from kanban_pro.ports import Conflict, NotFound, Unauthorized


def proven(card: Card) -> bool:
    """ "Is this card proven?" — checks exist AND none of the required ones is outstanding.

    Deliberately spelled out here rather than exported from the core: a second public predicate
    beside `blocking_checks` is exactly how the definition drifted apart from the gate last time
    (the old `is_verified` called an empty contract verified, while the board refused it).
    """
    return bool(card.checks) and not blocking_checks(card)


def _stack(actor: str = "agent:worker") -> tuple[RecordingBackend, ChangeLog]:
    log = ChangeLog()
    return RecordingBackend(
        AugmentingBackend(MemoryBackend()), log, actor, claims=ClaimStore()
    ), log


async def _seed(be: RecordingBackend) -> Card:
    board = await be.create_board(
        Board(name="B", columns=[Column(name="doing", category=ColumnCategory.STARTED)])
    )
    return await be.create_card(
        Card(
            title="AIR-2915 hydration mismatch",
            placements=[Placement(board_id=board.id, column_id=board.columns[0].id)],
        )
    )


#: the contract prepare would have written for AIR-2915
SPEC = [
    {"key": "static", "text": "make check-frontend"},
    {"key": "browser-verify", "text": "hydration console clean on the hotel detail page"},
]


def test_declared_check_blocks_until_passed() -> None:
    asyncio.run(_declared_check_blocks_until_passed())


async def _declared_check_blocks_until_passed() -> None:
    be, _ = _stack()
    card = await _seed(be)

    card = await declare_checks(be, card.id, SPEC)
    assert not proven(card)
    assert [c.key for c in blocking_checks(card)] == ["static", "browser-verify"]
    assert all(c.status is CheckStatus.PENDING for c in card.checks)

    card = await record_check_result(be, card.id, "static", "passed", "prettier/lint/tsc: exit 0")
    assert [c.key for c in blocking_checks(card)] == ["browser-verify"]  # one down, still gated

    card = await record_check_result(
        be, card.id, "browser-verify", "passed", "loaded /hotel/x — no hydration warning"
    )
    assert proven(card)


def test_a_greener_undeclared_check_cannot_stand_in_for_a_required_one() -> None:
    """The AIR-2915 substitution: an SSR curl recorded in place of the unrun browser check."""
    asyncio.run(_no_substitution())


async def _no_substitution() -> None:
    be, _ = _stack()
    card = await declare_checks(be, (await _seed(be)).id, SPEC)

    with pytest.raises(NotFound, match="ssr-render"):
        await record_check_result(be, card.id, "ssr-render", "passed", "curl -> HTTP 200")

    card = await be.get_card(card.id)
    assert [c.key for c in blocking_checks(card)] == ["static", "browser-verify"]
    assert not proven(card)  # the card is exactly as unverified as before


@pytest.mark.parametrize("status", ["skipped", "blocked", "failed"])
def test_only_passed_satisfies_a_check(status: str) -> None:
    asyncio.run(_only_passed(status))


async def _only_passed(status: str) -> None:
    """`skipped` ("chose not to") and `blocked` ("couldn't") are NOT passes — the distinction a
    `done: bool` cannot hold, and the one that shipped the bug."""
    be, _ = _stack()
    card = await declare_checks(be, (await _seed(be)).id, [SPEC[1]])

    card = await record_check_result(be, card.id, "browser-verify", status, "no dev server")
    assert [c.key for c in blocking_checks(card)] == ["browser-verify"]
    assert not proven(card)


def test_a_status_without_evidence_is_refused() -> None:
    asyncio.run(_evidence_required())


async def _evidence_required() -> None:
    be, _ = _stack()
    card = await declare_checks(be, (await _seed(be)).id, [SPEC[0]])

    with pytest.raises(Conflict, match="evidence"):
        await record_check_result(be, card.id, "static", "passed", "   ")

    with pytest.raises(Conflict, match="not a result"):  # pending un-records a result
        await record_check_result(be, card.id, "static", "pending", "x")

    assert not proven(await be.get_card(card.id))


def test_redeclaring_the_contract_does_not_reset_recorded_results() -> None:
    """Prepare may refine the spec mid-flight; that must not erase a check somebody ran green."""
    asyncio.run(_redeclare_preserves())


async def _redeclare_preserves() -> None:
    be, _ = _stack()
    card = await declare_checks(be, (await _seed(be)).id, SPEC)
    card = await record_check_result(be, card.id, "static", "passed", "exit 0")

    card = await declare_checks(
        be,
        card.id,
        [
            {"key": "static", "text": "make check-frontend (also run prettier)"},
            {"key": "e2e", "text": "playwright review spec"},
        ],
    )

    static = next(c for c in card.checks if c.key == "static")
    assert static.status is CheckStatus.PASSED  # result survived
    assert static.text.endswith("(also run prettier)")  # declaration updated
    assert {c.key for c in card.checks} == {"static", "browser-verify", "e2e"}  # nothing dropped


def test_optional_checks_are_recorded_but_never_block() -> None:
    asyncio.run(_optional_never_blocks())


async def _optional_never_blocks() -> None:
    be, _ = _stack()
    card = await declare_checks(
        be,
        (await _seed(be)).id,
        [{"key": "perf", "text": "lighthouse score", "required": False}],
    )
    card = await record_check_result(be, card.id, "perf", "failed", "score 61")
    assert proven(card)  # informational: surfaced, never gating
    assert not blocking_checks(card)


def test_retract_needs_a_reason_and_is_audited() -> None:
    asyncio.run(_retract_audited())


async def _retract_audited() -> None:
    be, log = _stack("agent:builder-coder")
    card = await declare_checks(be, (await _seed(be)).id, SPEC)

    with pytest.raises(Conflict, match="reason"):
        await retract_check(be, card.id, "browser-verify", "")

    card = await retract_check(be, card.id, "browser-verify", "moved to the follow-up card")
    assert [c.key for c in card.checks] == ["static"]

    # the actor who dropped a gate is named in the log forever — the deterrent that outlasts
    # any instruction an agent can talk itself out of
    dropped = [e for e in await log.since(0) if e.entity == "check" and e.op == "retracted"]
    assert len(dropped) == 1
    assert dropped[0].actor == "agent:builder-coder"
    assert dropped[0].data["key"] == "browser-verify"
    assert dropped[0].data["reason"] == "moved to the follow-up card"


def test_declare_and_resolve_emit_audit_events() -> None:
    asyncio.run(_events())


async def _events() -> None:
    be, log = _stack("agent:prepare")
    card = await declare_checks(be, (await _seed(be)).id, SPEC)
    await record_check_result(be, card.id, "static", "passed", "exit 0")

    events = [e for e in await log.since(0) if e.entity == "check"]
    assert [e.op for e in events] == ["declared", "resolved"]
    assert events[0].data["keys"] == ["static", "browser-verify"]
    assert events[1].data == {"card_id": card.id, "key": "static", "status": "passed"}


async def _gated_board(be: RecordingBackend) -> tuple[Board, Card]:
    """A board whose `done` lane refuses a card that has not passed its required checks."""
    board = await be.create_board(
        Board(
            name="B",
            columns=[
                Column(id="b:doing", name="doing", category=ColumnCategory.STARTED),
                Column(id="b:done", name="done", category=ColumnCategory.DONE),
            ],
            ext={CHECK_GATE_EXT_KEY: ["b:done"]},
        )
    )
    card = await be.create_card(
        Card(title="AIR-2915", placements=[Placement(board_id=board.id, column_id="b:doing")])
    )
    return board, card


def test_the_board_refuses_an_unverified_card_into_a_gated_lane() -> None:
    """The whole point: enforcement at the BOARD, not in a consumer that can forget a role."""
    asyncio.run(_gate_refuses())


async def _gate_refuses() -> None:
    be, _ = _stack()
    board, card = await _gated_board(be)
    await declare_checks(be, card.id, SPEC)

    with pytest.raises(Conflict, match="browser-verify=pending"):
        await be.move_card(card.id, board.id, "b:done", 0)

    # a worker that runs the cheap check and blocks on the real one still cannot ship
    await record_check_result(be, card.id, "static", "passed", "exit 0")
    await record_check_result(be, card.id, "browser-verify", "blocked", "no dev server")
    with pytest.raises(Conflict, match="browser-verify=blocked"):
        await be.move_card(card.id, board.id, "b:done", 0)

    await record_check_result(be, card.id, "browser-verify", "passed", "console clean")
    moved = await be.move_card(card.id, board.id, "b:done", 0)
    assert moved.placements[0].column_id == "b:done"


def test_a_gated_lane_refuses_a_card_that_declared_no_checks_at_all() -> None:
    """Unverified-by-OMISSION is the hole a failure-counting gate waves through.

    `blocking_checks` is empty for a card that passed everything and for a card nobody ever
    specified. If the gate only counts failures, the second one sails through looking exactly like
    the first — silence read as a pass, which is the shape of the original bug.
    """
    asyncio.run(_empty_contract_is_refused())


async def _empty_contract_is_refused() -> None:
    be, _ = _stack()
    board, card = await _gated_board(be)

    with pytest.raises(Conflict, match="NO checks declared"):
        await be.move_card(card.id, board.id, "b:done", 0)

    # a card that genuinely needs no verification says so ON THE RECORD, and who said it
    await declare_checks(
        be, card.id, [{"key": "n/a", "text": "docs-only, no verification", "required": False}]
    )
    moved = await be.move_card(card.id, board.id, "b:done", 0)
    assert moved.placements[0].column_id == "b:done"


def test_the_gate_is_off_unless_the_board_asks_for_it() -> None:
    """A board of ordinary cards must not find its lanes locked because checks exist."""
    asyncio.run(_gate_off_by_default())


async def _gate_off_by_default() -> None:
    be, _ = _stack()
    board = await be.create_board(
        Board(
            name="B",
            columns=[
                Column(id="u:doing", name="doing", category=ColumnCategory.STARTED),
                Column(id="u:done", name="done", category=ColumnCategory.DONE),
            ],
        )
    )
    card = await be.create_card(
        Card(title="c", placements=[Placement(board_id=board.id, column_id="u:doing")])
    )
    await declare_checks(be, card.id, SPEC)  # declared, never run

    moved = await be.move_card(card.id, board.id, "u:done", 0)  # ungated board: allowed
    assert moved.placements[0].column_id == "u:done"


def test_force_overrides_the_gate_and_says_so_forever() -> None:
    """The escape hatch must exist and must never be silent — as with a forced flow move."""
    asyncio.run(_force_is_audited())


async def _force_is_audited() -> None:
    be, log = _stack("agent:builder-coder")
    board, card = await _gated_board(be)
    await declare_checks(be, card.id, SPEC)

    moved = await be.move_card(card.id, board.id, "b:done", 0, force=True)
    assert moved.placements[0].column_id == "b:done"

    (event,) = [e for e in await log.since(0) if e.entity == "card" and e.op == "moved"]
    assert event.data["forced"] is True
    assert event.actor == "agent:builder-coder"


def test_a_card_with_no_checks_is_not_verified_by_vacuous_truth() -> None:
    asyncio.run(_empty_contract())


async def _empty_contract() -> None:
    """An empty contract is NOT verified. `blocking_checks` is empty for a card that passed
    everything AND for a card nobody specified — so `is_verified` must ask both questions, or it
    disagrees with the gate that refuses that same card."""
    be, _ = _stack()
    card = await _seed(be)
    assert not blocking_checks(card)  # nothing is failing...
    assert not proven(card)  # ...but nothing was proven either
    assert card.checks == []


def test_the_worker_holding_the_card_cannot_write_its_own_contract() -> None:
    """The invariant, actually enforced. An adversarial review walked through the earlier version:
    the builder retracted the check it was about to fail, declared a `required: false` replacement,
    and strolled into the gated lane — no force, no flag, nothing to see."""
    asyncio.run(_no_self_service())


async def _no_self_service() -> None:
    prep = RecordingBackend(
        AugmentingBackend(MemoryBackend()),
        ChangeLog(),
        "agent:prepare",
        claims=(cl := ClaimStore()),
    )
    board, card = await _gated_board(prep)
    await declare_checks(prep, card.id, SPEC)

    # the builder claims the card — it is now, by definition, the party being gated
    worker = RecordingBackend(prep._inner, ChangeLog(), "agent:builder-coder", claims=cl)
    await worker.claim_card(card.id, 900)

    with pytest.raises(Unauthorized, match="may not retract"):
        await retract_check(worker, card.id, "browser-verify", "not applicable imo")
    with pytest.raises(Unauthorized, match="may not declare"):
        await declare_checks(worker, card.id, [{"key": "easy", "text": "x", "required": False}])

    # recording an outcome IS its job, and still works
    await record_check_result(worker, card.id, "static", "passed", "exit 0")
    assert [c.key for c in blocking_checks(await worker.get_card(card.id))] == ["browser-verify"]

    with pytest.raises(Conflict, match="browser-verify"):  # and the lane still refuses it
        await worker.move_card(card.id, board.id, "b:done", 0)


def test_a_card_cannot_be_BORN_in_a_gated_lane() -> None:
    """create_card checked WIP and nothing else — a check-less card could be created straight into
    `done`, with no event to show for it."""
    asyncio.run(_no_birth_in_a_gated_lane())


async def _no_birth_in_a_gated_lane() -> None:
    be, _ = _stack()
    board, _ = await _gated_board(be)
    with pytest.raises(Conflict, match="NO checks declared"):
        await be.create_card(
            Card(title="born done", placements=[Placement(board_id=board.id, column_id="b:done")])
        )


def test_add_placement_cannot_sneak_an_unverified_card_into_a_gated_lane() -> None:
    """The other way in. `move_card` was gated; `add_placement` was not — so park a placement in the
    gated lane directly and the gate never runs. Three legal calls, no `forced: true` anywhere."""
    asyncio.run(_no_placement_sneak())


async def _no_placement_sneak() -> None:
    be, _ = _stack()
    board, card = await _gated_board(be)
    await declare_checks(be, card.id, SPEC)

    other = await be.create_board(
        Board(
            name="O", columns=[Column(id="o:doing", name="doing", category=ColumnCategory.STARTED)]
        )
    )
    await be.add_placement(card.id, Placement(board_id=other.id, column_id="o:doing"))  # fine
    await be.remove_placement(card.id, board.id)  # drop the gated board's lane

    with pytest.raises(Conflict, match="browser-verify=pending"):
        await be.add_placement(card.id, Placement(board_id=board.id, column_id="b:done"))
