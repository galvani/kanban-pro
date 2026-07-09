"""Work distribution tests — claim CAS/TTL/heartbeat, the work queue, event recording."""

from __future__ import annotations

import asyncio
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

from kanban_pro.adapters.memory import MemoryBackend
from kanban_pro.core import (
    AugmentingBackend,
    ChangeLog,
    Claim,
    ClaimStore,
    RecordingBackend,
)
from kanban_pro.domain import Board, Card, Column, ColumnCategory, Placement
from kanban_pro.ports import Conflict, NotFound


def _stack(
    actor: str, store: MemoryBackend, claims: ClaimStore, log: ChangeLog
) -> RecordingBackend:
    return RecordingBackend(AugmentingBackend(store), log, actor, claims=claims)


def _two_actors() -> tuple[RecordingBackend, RecordingBackend, ChangeLog]:
    store, claims, log = MemoryBackend(), ClaimStore(), ChangeLog()
    return _stack("agent:alice", store, claims, log), _stack("agent:bob", store, claims, log), log


async def _seed_board(be: RecordingBackend) -> Board:
    return await be.create_board(
        Board(
            name="B",
            columns=[
                Column(name="backlog", category=ColumnCategory.BACKLOG),
                Column(name="ready", category=ColumnCategory.UNSTARTED),
                Column(name="doing", category=ColumnCategory.STARTED),
                Column(name="done", category=ColumnCategory.DONE),
            ],
        )
    )


def _card(board: Board, lane: str, assignees: list[str] | None = None) -> Card:
    col = next(c for c in board.columns if c.name == lane)
    return Card(
        title=f"{lane}-card",
        assignees=assignees or [],
        placements=[Placement(board_id=board.id, column_id=col.id)],
    )


def test_claim_cas_ttl_heartbeat(tmp_path: Path) -> None:
    asyncio.run(_claim_cas(tmp_path))


async def _claim_cas(tmp_path: Path) -> None:
    store = ClaimStore(tmp_path / "claims.db")
    claim = await store.claim("c1", "agent:alice", 900)
    assert not claim.expired

    with pytest.raises(Conflict, match="agent:alice"):  # CAS: bob loses
        await store.claim("c1", "agent:bob", 900)

    renewed = await store.claim("c1", "agent:alice", 900)  # own re-claim = renew
    assert renewed.owner == "agent:alice"
    with pytest.raises(Conflict, match="not you"):
        await store.renew("c1", "agent:bob", 900)

    # persistence across reopen
    fresh = ClaimStore(tmp_path / "claims.db")
    live = await fresh.live()
    assert live["c1"].owner == "agent:alice"

    await fresh.release("c1", "agent:alice")
    assert await fresh.get("c1") is None
    await fresh.release("c1", "agent:alice")  # idempotent

    with pytest.raises(NotFound):
        await fresh.renew("c1", "agent:alice", 900)


def test_expired_claim_is_reclaimable(tmp_path: Path) -> None:
    asyncio.run(_expired(tmp_path))


async def _expired(tmp_path: Path) -> None:
    store = ClaimStore(tmp_path / "claims.db")
    await store.claim("c1", "agent:alice", ttl_seconds=-1)  # already expired (crash sim)
    taken = await store.claim("c1", "agent:bob", 900)  # redelivery
    assert taken.owner == "agent:bob"
    assert (await store.live())["c1"].owner == "agent:bob"


def test_claim_events_and_guards() -> None:
    asyncio.run(_claim_events())


async def _claim_events() -> None:
    alice, bob, log = _two_actors()
    board = await _seed_board(alice)
    card = await alice.create_card(_card(board, "ready"))

    with pytest.raises(NotFound):  # no claiming ghosts
        await alice.claim_card("nope")

    await alice.claim_card(card.id)
    with pytest.raises(Conflict):
        await bob.claim_card(card.id)
    await alice.heartbeat_claim(card.id)
    await alice.release_claim(card.id)
    await alice.release_claim(card.id)  # idempotent, no second event

    ops = [(e.op, e.actor) for e in await log.since(0) if e.entity == "card" and e.op != "created"]
    assert ops == [("claimed", "agent:alice"), ("released", "agent:alice")]


def test_claim_on_behalf_of_worker() -> None:
    asyncio.run(_claim_on_behalf())


async def _claim_on_behalf() -> None:
    # a dispatcher claims/renews/releases on the WORKER's behalf via owner=;
    # without owner= the renew must CAS-fail (regression: heartbeats silently
    # conflicting until the lease expired mid-run, 2026-07-06)
    alice, bob, _ = _two_actors()
    board = await _seed_board(alice)
    card = await alice.create_card(_card(board, "ready"))

    claim = await alice.claim_card(card.id, owner="agent:worker")
    assert claim.owner == "agent:worker"
    with pytest.raises(Conflict):  # renewing as yourself is not renewing the worker's
        await alice.heartbeat_claim(card.id)
    renewed = await alice.heartbeat_claim(card.id, owner="agent:worker")
    assert renewed.owner == "agent:worker"
    with pytest.raises(Conflict):  # same CAS on release
        await alice.release_claim(card.id)
    await alice.release_claim(card.id, owner="agent:worker")
    # the card is free again after the on-behalf release
    reclaim = await bob.claim_card(card.id)
    assert reclaim.owner == "agent:bob"


def test_list_work_queue() -> None:
    asyncio.run(_list_work())


async def _list_work() -> None:
    alice, bob, _ = _two_actors()
    board = await _seed_board(alice)
    mine_doing = await alice.create_card(_card(board, "doing", assignees=["alice"]))  # bare name
    unassigned_ready = await alice.create_card(_card(board, "ready"))
    other_ready = await alice.create_card(_card(board, "ready", assignees=["agent:bob"]))
    await alice.create_card(_card(board, "done", assignees=["alice"]))  # not workable
    leased = await alice.create_card(_card(board, "backlog"))
    archived = await alice.create_card(_card(board, "ready"))
    await alice.archive_card(archived.id)
    await bob.claim_card(leased.id)  # bob leases -> hidden from alice

    queue = await alice.list_work()
    assert queue.actor == "agent:alice"
    ids = [i.card.id for i in queue.items]
    assert ids == [mine_doing.id, unassigned_ready.id]  # started first, then unstarted
    assert other_ready.id not in ids and leased.id not in ids and archived.id not in ids
    assert all(i.transitions.options is not None for i in queue.items)  # transitions inline

    # my own lease stays visible and is marked
    await alice.claim_card(unassigned_ready.id)
    queue = await alice.list_work()
    marked = {i.card.id: i.claimed_by_me for i in queue.items}
    assert marked[unassigned_ready.id] is True and marked[mine_doing.id] is False

    # bob sees his leased backlog card + the other_ready assigned to his full actor name
    bob_queue = await bob.list_work(include_unassigned=False)
    assert {i.card.id for i in bob_queue.items} == {other_ready.id, leased.id}


def test_claim_model_expiry() -> None:
    live = Claim(card_id="x", owner="a", expires_at=datetime.now(UTC) + timedelta(60))
    dead = Claim(card_id="x", owner="a", expires_at=datetime.now(UTC) - timedelta(60))
    assert not live.expired and dead.expired
