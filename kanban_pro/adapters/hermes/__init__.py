"""HermesAdapter — kanban-pro's first remote adapter (thin, BaseAdapter-based).

Ground truth + mapping rationale: docs/hermes-kanban.md. Reads go straight to the
board SQLite files (fast, no auth); writes go through the `hermes kanban` CLI so the
engine's invariants hold (event emission, ready-recompute, CAS claims).

Purpose (SPEC goal update): discovery + migration vehicle for replacing the Hermes
kanban — proxy during the transition, then feed the native-store import.

Honest capability notes:
- WORKFLOW is native: Hermes enforces its lifecycle, so `move_card` only maps the
  lanes the CLI can enter (ready via promote, blocked via block, done via complete);
  other lanes raise not_supported.
- ARCHIVE: archive works; there is NO unarchive verb — restoring needs the Hermes
  dashboard.
- update_card can only change the assignee (CLI `reassign`); title/body edits have no
  CLI path.
"""

from __future__ import annotations

import json
from pathlib import Path

from kanban_pro.adapters._base import BaseAdapter
from kanban_pro.adapters.hermes import mapping
from kanban_pro.adapters.hermes.reader import HermesReader
from kanban_pro.adapters.hermes.writer import Runner, run_cli
from kanban_pro.domain import (
    Board,
    Card,
    CardPatch,
    Column,
    Comment,
    Relation,
)
from kanban_pro.ports import (
    BackendUnavailable,
    Capability,
    NotFound,
    NotSupported,
)

#: lanes move_card can enter, and the CLI verb that gets there.
_MOVE_VERBS = {"done": "complete", "blocked": "block", "ready": "promote"}


class HermesAdapter(BaseAdapter):
    """KanbanBackend over the Hermes multi-agent board (SQLite reads, CLI writes)."""

    capabilities = frozenset(
        {
            Capability.COMMENTS,
            Capability.ASSIGNEES,
            Capability.RELATIONS,
            Capability.SUBTASKS,
            Capability.ARCHIVE,
            Capability.CUSTOM_FIELDS,
            Capability.WORKFLOW,  # the engine really enforces its lifecycle
        }
    )

    def __init__(self, root: str | Path | None = None, runner: Runner = run_cli) -> None:
        self._reader = HermesReader(Path(root) if root else Path.home() / ".hermes")
        self._run = runner

    # --- boards / columns (read-only surface: lanes aren't editable) ---

    async def list_boards(self) -> list[Board]:
        return [await self.get_board(slug) for slug in self._reader.board_slugs()]

    async def get_board(self, board_id: str) -> Board:
        lanes = await self._reader.lanes(board_id)  # raises NotFound for unknown board
        return Board(id=board_id, name=board_id, columns=mapping.columns_for(board_id, lanes))

    async def create_board(self, board: Board) -> Board:
        slug = board.name.strip().lower().replace(" ", "-")
        await self._run(["boards", "create", slug, "--name", board.name], None)
        return await self.get_board(slug)

    async def delete_board(self, board_id: str) -> None:
        await self._run(["boards", "rm", board_id], None)

    async def list_columns(self, board_id: str) -> list[Column]:
        return (await self.get_board(board_id)).columns

    # --- cards ---

    async def list_cards(self, board_id: str) -> list[Card]:
        rows = await self._reader.tasks(board_id)
        return [mapping.card_from_row(board_id, r) for r in rows]

    async def get_card(self, card_id: str) -> Card:
        slug, row = await self._reader.find_task(card_id)
        return mapping.card_from_row(slug, row)

    async def create_card(self, card: Card) -> Card:
        if not card.placements:
            raise ValueError("create_card requires at least one placement")
        target = card.placements[0]
        lane = mapping.lane_of(target.column_id)
        args = ["create", card.title, "--json"]
        if lane == "triage":
            args.append("--triage")
        elif lane in ("blocked", "running"):
            args += ["--initial-status", lane]
        elif lane not in ("todo", "ready"):
            raise NotSupported(
                f"hermes cannot create a card directly in lane {lane!r}"
                " (allowed: triage, todo, ready, blocked, running)"
            )
        if card.description:
            args += ["--body", card.description]
        if card.assignees:
            args += ["--assignee", card.assignees[0]]
        hermes_ext = card.ext.get("hermes", {}) if isinstance(card.ext, dict) else {}
        for ext_key, flag in (
            ("priority", "--priority"),
            ("created_by", "--created-by"),
            ("idempotency_key", "--idempotency-key"),
        ):
            if hermes_ext.get(ext_key) is not None:
                args += [flag, str(hermes_ext[ext_key])]
        out = await self._run(args, target.board_id)
        try:
            task_id = str(json.loads(out)["id"])
        except (ValueError, KeyError, TypeError):
            raise BackendUnavailable(
                f"unexpected `hermes kanban create` output: {out[:200]!r}"
            ) from None
        return await self.get_card(task_id)

    async def update_card(self, card_id: str, patch: CardPatch) -> Card:
        data = patch.model_dump(exclude_unset=True)
        if set(data) != {"assignees"}:
            raise NotSupported(
                "hermes CLI supports only assignee updates"
                " (title/body edits: use the Hermes dashboard)"
            )
        slug, _ = await self._reader.find_task(card_id)
        profile = data["assignees"][0] if data["assignees"] else "none"
        await self._run(["reassign", card_id, profile], slug)
        return await self.get_card(card_id)

    async def list_transitions(self, card_id: str) -> list[str]:
        """core.flow.NativeTransitions hook: lane names move_card can enter."""
        await self._reader.find_task(card_id)  # 404 for unknown cards
        return sorted(_MOVE_VERBS)

    async def move_card(
        self, card_id: str, to_board_id: str, to_column_id: str, position: int
    ) -> Card:
        slug, _ = await self._reader.find_task(card_id)
        if slug != to_board_id:  # strict within-board (Q16)
            raise NotFound(f"card {card_id!r} has no placement on board {to_board_id!r}")
        lane = mapping.lane_of(to_column_id)
        verb = _MOVE_VERBS.get(lane)
        if verb is None:
            raise NotSupported(
                f"hermes workflow cannot enter lane {lane!r} through kanban-pro"
                f" (allowed: {', '.join(sorted(_MOVE_VERBS))})"
            )
        await self._run([verb, card_id], slug)
        return await self.get_card(card_id)

    async def archive_card(self, card_id: str) -> Card:
        slug, _ = await self._reader.find_task(card_id)
        await self._run(["archive", card_id], slug)
        return await self.get_card(card_id)

    async def unarchive_card(self, card_id: str) -> Card:
        raise NotSupported("hermes has no unarchive verb — restore via the Hermes dashboard")

    async def delete_card(self, card_id: str) -> None:
        # `archive --rm` purges only already-archived tasks — same rule as our core
        # guard (decision 7), enforced on both sides.
        slug, _ = await self._reader.find_task(card_id)
        await self._run(["archive", "--rm", card_id], slug)

    # --- comments ---

    async def list_comments(self, card_id: str) -> list[Comment]:
        slug, _ = await self._reader.find_task(card_id)
        return [mapping.comment_from_row(r) for r in await self._reader.comments(slug, card_id)]

    async def add_comment(self, comment: Comment) -> Comment:
        slug, _ = await self._reader.find_task(comment.card_id)
        await self._run(
            ["comment", comment.card_id, comment.body, "--author", comment.author], slug
        )
        return mapping.comment_from_row(await self._reader.latest_comment(slug, comment.card_id))

    # --- relations (parent/child DAG only) ---

    async def list_relations(self, card_id: str) -> list[Relation]:
        slug, _ = await self._reader.find_task(card_id)
        return [
            mapping.relation_from_link(parent, child)
            for parent, child in await self._reader.links(slug, card_id)
        ]

    async def add_relation(self, relation: Relation) -> Relation:
        try:
            parent, child = mapping.link_from_relation(relation)
        except ValueError as e:
            raise NotSupported(str(e)) from None
        slug, _ = await self._reader.find_task(parent)
        await self._run(["link", parent, child], slug)
        return mapping.relation_from_link(parent, child)

    async def delete_relation(self, relation_id: str) -> None:
        parent, sep, child = relation_id.partition("->")
        if not sep or not parent or not child:
            raise NotFound(f"unknown relation id {relation_id!r} (expected 'parent->child')")
        slug, _ = await self._reader.find_task(parent)
        await self._run(["unlink", parent, child], slug)


__all__ = ["HermesAdapter"]
