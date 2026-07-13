"""Anonymous actors may read, but they may not write (unless a board opts in)."""

from __future__ import annotations

import asyncio

from kanban_pro.adapters.memory import MemoryBackend
from kanban_pro.core import (
    ActorPolicyBackend,
    AugmentingBackend,
    ChangeLog,
    RecordingBackend,
    is_anonymous,
    unwrap,
)
from kanban_pro.domain import Board, BoardPatch, Card, CardPatch, Column, Comment, Placement
from kanban_pro.ports import Conflict


def _store() -> MemoryBackend:
    """One store, several connections — the real shape: identities differ, the board is shared."""
    return MemoryBackend()


def _stack(store: MemoryBackend, actor: str | None) -> ActorPolicyBackend:
    inner = RecordingBackend(AugmentingBackend(store), ChangeLog(), actor or "unknown")
    return ActorPolicyBackend(inner, actor)


def test_what_counts_as_anonymous() -> None:
    assert is_anonymous(None)
    assert is_anonymous("")
    assert is_anonymous("   ")
    assert is_anonymous("unknown")  # the config fallback — the whole reason this exists
    assert is_anonymous("UNKNOWN")
    # a bare name is not an identity: it doesn't say whether a human or an agent did the thing,
    # which is the first question anyone asks of a change-log row
    assert is_anonymous("reviewer")

    assert not is_anonymous("agent:claude-code")
    assert not is_anonymous("human:jan")
    assert not is_anonymous("agent:hermes-engineer")


def test_anonymous_connection_can_read_but_not_write() -> None:
    asyncio.run(_anonymous_refused())


async def _anonymous_refused() -> None:
    store = _store()
    identified = _stack(store, "human:jan")
    board = await identified.create_board(Board(name="B", columns=[Column(name="todo")]))
    card = await identified.create_card(
        Card(title="T", placements=[Placement(board_id=board.id, column_id=board.columns[0].id)])
    )

    anon = _stack(store, "unknown")
    assert (await anon.get_card(card.id)).title == "T"  # reads are never affected
    assert len(await anon.list_cards(board.id)) == 1

    writes = [
        lambda: anon.create_board(Board(name="X", columns=[])),
        lambda: anon.update_card(card.id, CardPatch(title="T2")),
        lambda: anon.add_comment(Comment(card_id=card.id, author="somebody", body="hi")),
        lambda: anon.raise_attention(card.id, "?", severity="warn"),
        lambda: anon.archive_card(card.id),
    ]
    for write in writes:
        try:
            await write()
        except Conflict as e:
            assert "no identity" in str(e)
            assert "--actor kind:name" in str(e)  # the message must say how to fix it
        else:
            raise AssertionError("an unattributable write must be refused, not silently recorded")

    assert (await anon.get_card(card.id)).title == "T"  # nothing landed


def test_the_core_stack_survives_being_wrapped() -> None:
    asyncio.run(_stack_still_works())


async def _stack_still_works() -> None:
    """The whole codebase asked `isinstance(be, RecordingBackend)` to mean "has the core stack".
    Putting ANY decorator outside RecordingBackend turns that False — and it does not raise, it
    silently disables the change-log, `force` moves and idempotency. (Shipped exactly that on
    2026-07-13: the dispatcher died on `wait_changes: change-log is not wired for this backend`
    the moment the policy layer went outermost.) The structural `unwrap` is what fixes it, so
    it is what this asserts."""
    store = _store()
    be = _stack(store, "human:jan")

    rec = unwrap(be, RecordingBackend)
    assert rec is not None, "the core stack must stay reachable through the decorator"
    assert rec.changelog is not None and rec.claims is not None and rec.dedupe is not None

    board = await be.create_board(Board(name="B", columns=[Column(name="todo")]))
    placement = Placement(board_id=board.id, column_id=board.columns[0].id)

    # idempotency still dedupes through the wrapper (it's routed on the same unwrap)
    first = await be.create_card(Card(title="T", placements=[placement]), idempotency_key="k1")
    retry = await be.create_card(Card(title="T", placements=[placement]), idempotency_key="k1")
    assert retry.id == first.id

    # and the change-log still records — the thing the dispatcher polls
    events = await rec.changelog.since(0)
    assert [e.kind for e in events if e.entity == "card"] == ["card.created"]
    assert all(e.actor == "human:jan" for e in events)


def test_a_board_may_opt_into_anonymous_writes() -> None:
    asyncio.run(_opt_in())


async def _opt_in() -> None:
    """A personal single-user board has nobody to attribute to; the ceremony buys it nothing."""
    store = _store()
    identified = _stack(store, "human:jan")
    board = await identified.create_board(Board(name="B", columns=[Column(name="todo")]))

    placement = Placement(board_id=board.id, column_id=board.columns[0].id)

    anon = _stack(store, None)
    try:
        await anon.create_card(Card(title="T", placements=[placement]))
    except Conflict:
        pass
    else:
        raise AssertionError("anonymous writes are refused by default")

    await identified.update_board(board.id, BoardPatch(ext={"anonymous_writes": "allow"}))
    card = await anon.create_card(Card(title="T", placements=[placement]))  # now permitted
    assert card.title == "T"
