"""Migration tests — fake hermes root -> real native store, faithfulness + idempotency."""

from __future__ import annotations

import asyncio
import sqlite3
from pathlib import Path

from kanban_pro.adapters.hermes import HermesAdapter
from kanban_pro.adapters.native import NativeStore
from kanban_pro.core import AugmentingBackend, ChangeLog, RecordingBackend
from kanban_pro.migrate import MIGRATED_FROM_KEY, migrate

# reuse the fake-hermes schema/seed helpers
from tests.test_hermes_adapter import _seed


def _hermes_root(tmp_path: Path) -> Path:
    root = tmp_path / ".hermes"
    _seed(
        root / "kanban.db",
        [
            ("t_aaa", "ready", "engineer", 2),
            ("t_bbb", "archived", None, 0),
            ("t_ccc", "done", None, 0),
            ("t_ddd", "ready", None, 1),
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
    _seed(root / "kanban" / "boards" / "beta" / "kanban.db", [("t_eee", "running", None, 0)])
    return root


async def _run_migration(tmp_path: Path, dry_run: bool = False) -> tuple[RecordingBackend, object]:
    source = HermesAdapter(root=_hermes_root(tmp_path))
    log = ChangeLog()
    dest = RecordingBackend(
        AugmentingBackend(await NativeStore.open(tmp_path / "native.db")),
        log,
        "migration:test",
    )
    report = await migrate(source, dest, source_name="hermes", dest_name="native", dry_run=dry_run)
    return dest, report


def test_migrates_everything_faithfully(tmp_path: Path) -> None:
    asyncio.run(_faithful(tmp_path))


async def _faithful(tmp_path: Path) -> None:
    dest, report = await _run_migration(tmp_path)
    assert (report.boards, report.cards, report.archived_cards) == (2, 5, 1)  # type: ignore[attr-defined]
    assert (report.comments, report.relations) == (1, 1)  # type: ignore[attr-defined]

    boards = {b.id: b for b in await dest.list_boards()}
    assert set(boards) == {"default", "beta"}
    assert any(c.id == "default:ready" for c in boards["default"].columns)

    live = await dest.list_cards("default")
    assert {c.id for c in live} == {"t_aaa", "t_ccc", "t_ddd"}
    everything = await dest.list_cards("default", include_archived=True)
    assert {c.id for c in everything} == {"t_aaa", "t_bbb", "t_ccc", "t_ddd"}  # history kept

    card = await dest.get_card("t_aaa")
    assert card.ext[MIGRATED_FROM_KEY] == "hermes/default"
    assert card.ext["hermes"]["priority"] == 2  # backend richness preserved
    assert card.assignees == ["engineer"]

    # positions assigned per column in source order (priority DESC): t_aaa=0, t_ddd=1
    ready_cards = {
        c.id: c.placements[0].position for c in live if c.placements[0].column_id == "default:ready"
    }
    assert ready_cards == {"t_aaa": 0, "t_ddd": 1}

    (comment,) = await dest.list_comments("t_aaa")
    assert (comment.id, comment.author, comment.body) == ("default:c1", "reviewer", "looks good")

    (relation,) = await dest.list_relations("t_ccc")
    assert (relation.from_card, relation.to_card) == ("t_aaa", "t_ccc")

    archived = await dest.get_card("t_bbb")
    assert archived.archived is True

    # the import is attributed in the change-log
    events = await dest.changelog.since(0)
    assert events and all(e.actor == "migration:test" for e in events)


def test_migration_is_idempotent(tmp_path: Path) -> None:
    asyncio.run(_idempotent(tmp_path))


async def _idempotent(tmp_path: Path) -> None:
    source = HermesAdapter(root=_hermes_root(tmp_path))
    dest = RecordingBackend(
        AugmentingBackend(await NativeStore.open(tmp_path / "native.db")),
        ChangeLog(),
        "migration:test",
    )
    await migrate(source, dest, source_name="hermes", dest_name="native")
    await migrate(source, dest, source_name="hermes", dest_name="native")  # re-run

    assert len(await dest.list_boards()) == 2
    assert len(await dest.list_cards("default", include_archived=True)) == 4  # no dupes
    assert len(await dest.list_comments("t_aaa")) == 1
    assert len(await dest.list_relations("t_ccc")) == 1


def test_dry_run_writes_nothing(tmp_path: Path) -> None:
    asyncio.run(_dry(tmp_path))


async def _dry(tmp_path: Path) -> None:
    dest, report = await _run_migration(tmp_path, dry_run=True)
    assert report.cards == 5  # type: ignore[attr-defined]
    assert await dest.list_boards() == []
    assert await dest.changelog.since(0) == []
