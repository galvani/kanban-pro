"""Tests for per-board card-id schemes (`board.id_scheme`, domain.ids).

The scheme is board data, so these run the whole way through a store: the id a card gets
is decided in `create_card`, from the board the card lands on.
"""

from __future__ import annotations

import asyncio
import re
from collections.abc import Awaitable, Callable
from pathlib import Path

import pytest
from pydantic import ValidationError

from kanban_pro.adapters.memory import MemoryBackend
from kanban_pro.adapters.native import NativeStore
from kanban_pro.domain import Board, BoardPatch, Card, Column, ColumnCategory, Placement
from kanban_pro.domain.ids import InvalidScheme, parse_scheme
from kanban_pro.ports import Conflict, KanbanBackend

#: both stores must behave identically — every scheme test runs against both.
STORES: list[Callable[[Path], Awaitable[KanbanBackend]]] = [
    lambda _: _memory(),
    lambda tmp: NativeStore.open(tmp / "ids.db"),
]


async def _memory() -> KanbanBackend:
    return MemoryBackend()


async def _board(backend: KanbanBackend, scheme: str | None, board_id: str = "b") -> str:
    """A one-column board using `scheme`; returns its column id."""
    await backend.create_board(
        Board(
            id=board_id,
            name=board_id,
            id_scheme=scheme,
            columns=[Column(id=f"{board_id}:todo", name="Todo", category=ColumnCategory.UNSTARTED)],
        )
    )
    return f"{board_id}:todo"


async def _add(backend: KanbanBackend, column_id: str, title: str, board_id: str = "b") -> Card:
    return await backend.create_card(
        Card(title=title, placements=[Placement(board_id=board_id, column_id=column_id)])
    )


def run(case: Callable[[KanbanBackend], Awaitable[None]], tmp_path: Path) -> None:
    """Run one case against every store."""
    for open_store in STORES:
        asyncio.run(_run_one(case, open_store, tmp_path))


async def _run_one(
    case: Callable[[KanbanBackend], Awaitable[None]],
    open_store: Callable[[Path], Awaitable[KanbanBackend]],
    tmp_path: Path,
) -> None:
    store_dir = tmp_path / str(id(open_store))
    store_dir.mkdir(exist_ok=True)
    await case(await open_store(store_dir))


# --- parsing (the validator's job) ---
@pytest.mark.parametrize(
    "spec, expected",
    [
        (None, ("uuid", "", 0)),
        ("uuid", ("uuid", "", 0)),
        ("short", ("short", "", 10)),
        ("short:8", ("short", "", 8)),
        ("prefix:KAN", ("prefix", "KAN", 6)),
        ("prefix:KAN:12", ("prefix", "KAN", 12)),
        ("seq:KAN", ("seq", "KAN", 0)),
    ],
)
def test_parse_scheme(spec: str | None, expected: tuple[str, str, int]) -> None:
    scheme = parse_scheme(spec)
    assert (scheme.kind, scheme.prefix, scheme.length) == expected


@pytest.mark.parametrize("spec", ["nope", "short:3", "short:99", "short:x", "prefix", "seq:KAN:5"])
def test_parse_scheme_rejects(spec: str) -> None:
    with pytest.raises(InvalidScheme):
        parse_scheme(spec)


@pytest.mark.parametrize("model", [Board, BoardPatch])
def test_a_bad_scheme_is_refused_when_the_board_is_set_up(model: type) -> None:
    # not at the first card, on a board that already exists
    with pytest.raises(ValidationError):
        model(name="b", id_scheme="bogus:1")


# --- what a card's id looks like ---
def test_default_board_still_mints_uuids(tmp_path: Path) -> None:
    async def case(backend: KanbanBackend) -> None:
        col = await _board(backend, None)
        assert re.fullmatch(r"[0-9a-f]{32}", (await _add(backend, col, "c")).id)

    run(case, tmp_path)


def test_short_ids_are_readable_and_unique(tmp_path: Path) -> None:
    async def case(backend: KanbanBackend) -> None:
        col = await _board(backend, "short:8")
        ids = {(await _add(backend, col, f"c{n}")).id for n in range(100)}
        assert len(ids) == 100
        assert all(re.fullmatch(r"[0-9a-hjkmnp-tv-z]{8}", i) for i in ids)  # no i/l/o/u

    run(case, tmp_path)


def test_prefixed_ids(tmp_path: Path) -> None:
    async def case(backend: KanbanBackend) -> None:
        col = await _board(backend, "prefix:KAN:6")
        assert re.fullmatch(r"KAN-[0-9a-hjkmnp-tv-z]{6}", (await _add(backend, col, "c")).id)

    run(case, tmp_path)


def test_seq_ids_count_up(tmp_path: Path) -> None:
    async def case(backend: KanbanBackend) -> None:
        col = await _board(backend, "seq:KAN")
        ids = [(await _add(backend, col, f"c{n}")).id for n in range(3)]
        assert ids == ["KAN-1", "KAN-2", "KAN-3"]
        assert (await backend.get_card("KAN-2")).title == "c1"

    run(case, tmp_path)


def test_each_board_counts_on_its_own(tmp_path: Path) -> None:
    """The point of putting the scheme on the board: KAN-1 here, OPS-1 there."""

    async def case(backend: KanbanBackend) -> None:
        kan = await _board(backend, "seq:KAN", "kan")
        ops = await _board(backend, "seq:OPS", "ops")
        first = await _add(backend, kan, "a", "kan")
        second = await _add(backend, ops, "b", "ops")
        third = await _add(backend, kan, "c", "kan")
        assert [first.id, second.id, third.id] == ["KAN-1", "OPS-1", "KAN-2"]

    run(case, tmp_path)


def test_switching_a_live_boards_scheme_leaves_old_ids_alone(tmp_path: Path) -> None:
    async def case(backend: KanbanBackend) -> None:
        col = await _board(backend, None)
        old = await _add(backend, col, "before")
        await backend.update_board("b", BoardPatch(id_scheme="seq:KAN"))
        new = await _add(backend, col, "after")
        assert len(old.id) == 32 and new.id == "KAN-1"
        assert (await backend.get_card(old.id)).title == "before"  # untouched, still findable

    run(case, tmp_path)


def test_seq_skips_a_number_already_taken(tmp_path: Path) -> None:
    """A board can already hold KAN-1 (cards migrated in with their source ids). The
    counter must not hand that id out a second time."""

    async def case(backend: KanbanBackend) -> None:
        col = await _board(backend, "seq:KAN")
        place = [Placement(board_id="b", column_id=col)]
        await backend.create_card(Card(id="KAN-1", title="migrated", placements=place))
        assert (await _add(backend, col, "fresh")).id == "KAN-2"

    run(case, tmp_path)


def test_create_refuses_a_duplicate_id(tmp_path: Path) -> None:
    """The guard that makes short random ids safe: a create never overwrites a card."""

    async def case(backend: KanbanBackend) -> None:
        col = await _board(backend, None)
        place = [Placement(board_id="b", column_id=col)]
        await backend.create_card(Card(id="fixed", title="first", placements=place))
        with pytest.raises(Conflict):
            await backend.create_card(Card(id="fixed", title="second", placements=place))

    run(case, tmp_path)


def test_native_seq_counter_survives_reopen(tmp_path: Path) -> None:
    async def go() -> None:
        store = await NativeStore.open(tmp_path / "seq.db")
        col = await _board(store, "seq:KAN")
        first = await _add(store, col, "a")
        reopened = await NativeStore.open(tmp_path / "seq.db")  # what a fresh process does
        second = await _add(reopened, col, "b")
        assert (first.id, second.id) == ("KAN-1", "KAN-2")

    asyncio.run(go())
