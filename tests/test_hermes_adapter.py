"""HermesAdapter tests — SQLite reads against a fake ~/.hermes, CLI writes against a
captured runner (no real Hermes install needed)."""

from __future__ import annotations

import asyncio
import sqlite3
from collections.abc import Sequence
from pathlib import Path

import pytest

from kanban_pro.adapters.hermes import HermesAdapter
from kanban_pro.domain import Board, Card, CardPatch, ColumnCategory, Comment, Placement, Relation
from kanban_pro.domain import RelationKind as RK
from kanban_pro.ports import KanbanBackend, NotFound, NotSupported

_SCHEMA = """
CREATE TABLE tasks (
    id TEXT PRIMARY KEY, title TEXT NOT NULL, body TEXT, assignee TEXT,
    status TEXT NOT NULL, priority INTEGER DEFAULT 0, created_by TEXT,
    created_at INTEGER NOT NULL, started_at INTEGER, completed_at INTEGER,
    tenant TEXT, workspace_kind TEXT, workspace_path TEXT, branch_name TEXT,
    block_kind TEXT, skills TEXT, session_id TEXT, project_id TEXT, result TEXT
);
CREATE TABLE task_comments (
    id INTEGER PRIMARY KEY AUTOINCREMENT, task_id TEXT, author TEXT, body TEXT,
    created_at INTEGER
);
CREATE TABLE task_links (parent_id TEXT, child_id TEXT, PRIMARY KEY (parent_id, child_id));
"""


def _seed(db: Path, rows: list[tuple[str, str, str | None, int]]) -> None:
    """rows = (id, status, assignee, priority)"""
    db.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(db)
    conn.executescript(_SCHEMA)
    for task_id, status, assignee, priority in rows:
        conn.execute(
            "INSERT INTO tasks (id, title, body, assignee, status, priority, created_by,"
            " created_at, skills) VALUES (?, ?, 'body', ?, ?, ?, 'jan', 1000, ?)",
            (task_id, f"task {task_id}", assignee, status, priority, '["python"]'),
        )
    conn.commit()
    conn.close()


@pytest.fixture
def hermes_root(tmp_path: Path) -> Path:
    root = tmp_path / ".hermes"
    _seed(
        root / "kanban.db",
        [
            ("t_aaa", "ready", "engineer", 2),
            ("t_bbb", "archived", None, 0),
            ("t_ccc", "done", None, 0),
        ],
    )
    conn = sqlite3.connect(root / "kanban.db")
    conn.execute(
        "INSERT INTO task_comments (task_id, author, body, created_at)"
        " VALUES ('t_aaa', 'reviewer', 'looks good', 1100)"
    )
    conn.execute("INSERT INTO task_links (parent_id, child_id) VALUES ('t_aaa', 't_ccc')")
    conn.commit()
    conn.close()
    _seed(root / "kanban" / "boards" / "beta" / "kanban.db", [("t_ddd", "running", None, 0)])
    return root


class CapturingRunner:
    """Stands in for the `hermes kanban` CLI; records argv + board."""

    def __init__(self) -> None:
        self.calls: list[tuple[list[str], str | None]] = []

    async def __call__(self, args: Sequence[str], board: str | None) -> str:
        self.calls.append((list(args), board))
        if args[0] == "create":
            return '{"id": "t_new"}'
        return ""


@pytest.fixture
def runner() -> CapturingRunner:
    return CapturingRunner()


@pytest.fixture
def adapter(hermes_root: Path, runner: CapturingRunner) -> HermesAdapter:
    return HermesAdapter(root=hermes_root, runner=runner)


def test_conforms_to_port(adapter: HermesAdapter) -> None:
    backend: KanbanBackend = adapter
    assert backend.capabilities  # declared, honest subset


# --- reads (SQLite) ---


def test_boards_and_columns(adapter: HermesAdapter) -> None:
    asyncio.run(_boards_and_columns(adapter))


async def _boards_and_columns(adapter: HermesAdapter) -> None:
    boards = await adapter.list_boards()
    assert [b.id for b in boards] == ["default", "beta"]
    columns = {c.name: c for c in boards[0].columns}
    assert columns["ready"].category is ColumnCategory.UNSTARTED
    assert columns["done"].category is ColumnCategory.DONE
    assert columns["ready"].id == "default:ready"
    assert "archived" not in columns  # a flag here, not a lane
    with pytest.raises(NotFound):
        await adapter.get_board("nope")


def test_cards_read_and_mapping(adapter: HermesAdapter) -> None:
    asyncio.run(_cards_read_and_mapping(adapter))


async def _cards_read_and_mapping(adapter: HermesAdapter) -> None:
    cards = {c.id: c for c in await adapter.list_cards("default")}
    assert set(cards) == {"t_aaa", "t_ccc"}  # archived hidden
    card = cards["t_aaa"]
    assert card.assignees == ["engineer"]
    assert card.placements == [Placement(board_id="default", column_id="default:ready")]
    assert card.ext["hermes"]["priority"] == 2
    assert card.ext["hermes"]["created_by"] == "jan"
    assert card.ext["hermes"]["skills"] == ["python"]
    assert card.created_at is not None

    archived = await adapter.get_card("t_bbb")
    assert archived.archived is True

    cross_board = await adapter.get_card("t_ddd")  # found on the beta board
    assert cross_board.placements[0].board_id == "beta"


def test_comments_and_relations_read(adapter: HermesAdapter) -> None:
    asyncio.run(_comments_and_relations_read(adapter))


async def _comments_and_relations_read(adapter: HermesAdapter) -> None:
    (comment,) = await adapter.list_comments("t_aaa")
    assert (comment.author, comment.body) == ("reviewer", "looks good")

    (rel,) = await adapter.list_relations("t_ccc")
    assert (rel.kind, rel.from_card, rel.to_card) == (RK.PARENT, "t_aaa", "t_ccc")
    assert rel.id == "t_aaa->t_ccc"


# --- writes (CLI argv) ---


def test_create_card_maps_to_cli(adapter: HermesAdapter, runner: CapturingRunner) -> None:
    asyncio.run(_create_card(adapter, runner))


async def _create_card(adapter: HermesAdapter, runner: CapturingRunner) -> None:
    # pre-seed the row the fake CLI "creates" so the post-create read finds it
    conn = sqlite3.connect(adapter._reader.board_db("default"))  # noqa: SLF001
    conn.execute(
        "INSERT INTO tasks (id, title, status, created_at) VALUES ('t_new', 'N', 'ready', 2000)"
    )
    conn.commit()
    conn.close()

    card = await adapter.create_card(
        Card(
            title="N",
            description="details",
            assignees=["builder"],
            placements=[Placement(board_id="default", column_id="default:ready")],
            ext={"hermes": {"priority": 5, "idempotency_key": "k1"}},
        )
    )
    assert card.id == "t_new"
    args, board = runner.calls[0]
    assert board == "default"
    assert args[:2] == ["create", "N"]
    assert ["--body", "details"] == args[args.index("--body") : args.index("--body") + 2]
    assert ["--assignee", "builder"] == args[
        args.index("--assignee") : args.index("--assignee") + 2
    ]
    assert ["--priority", "5"] == args[args.index("--priority") : args.index("--priority") + 2]
    assert "--idempotency-key" in args and "--json" in args

    with pytest.raises(NotSupported, match="lane 'review'"):
        await adapter.create_card(
            Card(title="X", placements=[Placement(board_id="default", column_id="review")])
        )


def test_move_card_verb_mapping(adapter: HermesAdapter, runner: CapturingRunner) -> None:
    asyncio.run(_move_card(adapter, runner))


async def _move_card(adapter: HermesAdapter, runner: CapturingRunner) -> None:
    await adapter.move_card("t_aaa", "default", "default:done", 0)
    assert runner.calls[0] == (["complete", "t_aaa"], "default")

    await adapter.move_card("t_aaa", "default", "default:blocked", 0)
    assert runner.calls[1] == (["block", "t_aaa"], "default")

    with pytest.raises(NotSupported, match="lane 'review'"):
        await adapter.move_card("t_aaa", "default", "default:review", 0)
    with pytest.raises(NotFound):  # strict within-board (Q16)
        await adapter.move_card("t_aaa", "beta", "beta:done", 0)


def test_update_archive_delete(adapter: HermesAdapter, runner: CapturingRunner) -> None:
    asyncio.run(_update_archive_delete(adapter, runner))


async def _update_archive_delete(adapter: HermesAdapter, runner: CapturingRunner) -> None:
    await adapter.update_card("t_aaa", CardPatch(assignees=["reviewer"]))
    assert runner.calls[0] == (["reassign", "t_aaa", "reviewer"], "default")
    with pytest.raises(NotSupported, match="assignee"):
        await adapter.update_card("t_aaa", CardPatch(title="renamed"))

    await adapter.archive_card("t_aaa")
    assert runner.calls[1] == (["archive", "t_aaa"], "default")
    with pytest.raises(NotSupported, match="unarchive"):
        await adapter.unarchive_card("t_bbb")

    await adapter.delete_card("t_bbb")
    assert runner.calls[2] == (["archive", "--rm", "t_bbb"], "default")


def test_comment_and_relation_writes(adapter: HermesAdapter, runner: CapturingRunner) -> None:
    asyncio.run(_comment_and_relation_writes(adapter, runner))


async def _comment_and_relation_writes(adapter: HermesAdapter, runner: CapturingRunner) -> None:
    comment = await adapter.add_comment(Comment(card_id="t_aaa", author="agent:x", body="hi"))
    assert runner.calls[0] == (["comment", "t_aaa", "hi", "--author", "agent:x"], "default")
    assert comment.card_id == "t_aaa"  # read back from the board db

    rel = await adapter.add_relation(Relation(kind=RK.PARENT, from_card="t_aaa", to_card="t_ccc"))
    assert runner.calls[1] == (["link", "t_aaa", "t_ccc"], "default")
    assert rel.id == "t_aaa->t_ccc"
    # CHILD inverts to the same parent->child link
    await adapter.add_relation(Relation(kind=RK.CHILD, from_card="t_ccc", to_card="t_aaa"))
    assert runner.calls[2] == (["link", "t_aaa", "t_ccc"], "default")
    with pytest.raises(NotSupported, match="parent/child"):
        await adapter.add_relation(Relation(kind=RK.BLOCKS, from_card="t_aaa", to_card="t_ccc"))

    await adapter.delete_relation("t_aaa->t_ccc")
    assert runner.calls[3] == (["unlink", "t_aaa", "t_ccc"], "default")


def test_create_board_maps_to_cli(adapter: HermesAdapter, runner: CapturingRunner) -> None:
    asyncio.run(_create_board(adapter, runner))


async def _create_board(adapter: HermesAdapter, runner: CapturingRunner) -> None:
    # the fake CLI doesn't create the db, so expect NotFound AFTER the right argv
    with pytest.raises(NotFound):
        await adapter.create_board(Board(name="My Server"))
    assert runner.calls[0] == (["boards", "create", "my-server", "--name", "My Server"], None)
