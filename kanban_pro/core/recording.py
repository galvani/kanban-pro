"""RecordingBackend — stamps every successful write into the change-log with the actor.

Outermost decorator in the core stack (config.build_backend):

    RecordingBackend(AugmentingBackend(adapter), changelog, actor)

The actor is per-connection/per-process identity (SPEC decision 10): the MCP server is
started with `--actor kind:name` (or $KANBAN_PRO_ACTOR); everything that connection
does is attributed to it. Reads are never recorded; failed writes never reach the log.
Payloads stay slim (ids + the changed bits) — consumers `get_*` for full state.
"""

from __future__ import annotations

from kanban_pro.core.changelog import ChangeEvent, ChangeLog
from kanban_pro.domain import (
    Board,
    BoardPatch,
    Card,
    CardPatch,
    Column,
    ColumnPatch,
    Comment,
    Placement,
    Relation,
)
from kanban_pro.ports import Capability, Fulfilment, KanbanBackend, NotSupported

from .augment import AugmentingBackend
from .augment import fulfilments as _fulfilments
from .flow import FlowConfig, TransitionInfo


class RecordingBackend:
    """KanbanBackend decorator: delegate everything, log successful writes."""

    def __init__(self, inner: KanbanBackend, changelog: ChangeLog, actor: str) -> None:
        self._inner = inner
        self.changelog = changelog
        self.actor = actor
        self.capabilities: frozenset[Capability] = inner.capabilities

    def fulfilments(self) -> dict[Capability, Fulfilment]:
        return _fulfilments(self._inner)

    @property
    def flows(self) -> FlowConfig | None:
        return self._inner.flows if isinstance(self._inner, AugmentingBackend) else None

    async def transitions(self, card_id: str, board_id: str | None = None) -> TransitionInfo:
        """Read-only — delegated to the augmenting layer, never recorded."""
        if not isinstance(self._inner, AugmentingBackend):
            raise NotSupported("transitions query needs the augmenting layer")
        return await self._inner.transitions(card_id, board_id)

    async def _record(
        self,
        entity: str,
        entity_id: str,
        op: str,
        board_id: str | None = None,
        **data: object,
    ) -> None:
        await self.changelog.append(
            ChangeEvent(
                actor=self.actor,
                entity=entity,
                entity_id=entity_id,
                op=op,
                board_id=board_id,
                data={k: v for k, v in data.items() if v is not None},
            )
        )

    # --- boards ---

    async def list_boards(self) -> list[Board]:
        return await self._inner.list_boards()

    async def get_board(self, board_id: str) -> Board:
        return await self._inner.get_board(board_id)

    async def create_board(self, board: Board) -> Board:
        created = await self._inner.create_board(board)
        await self._record("board", created.id, "created", created.id, name=created.name)
        return created

    async def update_board(self, board_id: str, patch: BoardPatch) -> Board:
        updated = await self._inner.update_board(board_id, patch)
        fields = sorted(patch.model_dump(exclude_unset=True))
        await self._record("board", board_id, "updated", board_id, fields=fields)
        return updated

    async def delete_board(self, board_id: str) -> None:
        await self._inner.delete_board(board_id)
        await self._record("board", board_id, "deleted", board_id)

    # --- columns ---

    async def list_columns(self, board_id: str) -> list[Column]:
        return await self._inner.list_columns(board_id)

    async def create_column(self, board_id: str, column: Column) -> Column:
        created = await self._inner.create_column(board_id, column)
        await self._record("column", created.id, "created", board_id, name=created.name)
        return created

    async def update_column(self, column_id: str, patch: ColumnPatch) -> Column:
        updated = await self._inner.update_column(column_id, patch)
        fields = sorted(patch.model_dump(exclude_unset=True))
        await self._record("column", column_id, "updated", fields=fields)
        return updated

    async def delete_column(self, column_id: str) -> None:
        await self._inner.delete_column(column_id)
        await self._record("column", column_id, "deleted")

    # --- cards ---

    async def list_cards(self, board_id: str, include_archived: bool = False) -> list[Card]:
        return await self._inner.list_cards(board_id, include_archived)

    async def get_card(self, card_id: str) -> Card:
        return await self._inner.get_card(card_id)

    async def create_card(self, card: Card) -> Card:
        created = await self._inner.create_card(card)
        first = created.placements[0] if created.placements else None
        await self._record(
            "card",
            created.id,
            "created",
            first.board_id if first else None,
            title=created.title,
            column_id=first.column_id if first else None,
            assignees=created.assignees or None,
        )
        return created

    async def update_card(self, card_id: str, patch: CardPatch) -> Card:
        updated = await self._inner.update_card(card_id, patch)
        fields = sorted(patch.model_dump(exclude_unset=True))
        await self._record("card", card_id, "updated", fields=fields)
        return updated

    async def move_card(
        self,
        card_id: str,
        to_board_id: str,
        to_column_id: str,
        position: int,
        *,
        force: bool = False,
    ) -> Card:
        if force and isinstance(self._inner, AugmentingBackend):
            moved = await self._inner.move_card(
                card_id, to_board_id, to_column_id, position, force=True
            )
        else:
            moved = await self._inner.move_card(card_id, to_board_id, to_column_id, position)
        # a forced move is never silent (Jan): the event carries the flag.
        await self._record(
            "card", card_id, "moved", to_board_id,
            column_id=to_column_id, position=position, forced=force or None,
        )  # fmt: skip
        return moved

    async def add_placement(self, card_id: str, placement: Placement) -> Card:
        card = await self._inner.add_placement(card_id, placement)
        await self._record(
            "card", card_id, "placed", placement.board_id, column_id=placement.column_id
        )
        return card

    async def remove_placement(self, card_id: str, board_id: str) -> Card:
        card = await self._inner.remove_placement(card_id, board_id)
        await self._record("card", card_id, "unplaced", board_id)
        return card

    async def archive_card(self, card_id: str) -> Card:
        card = await self._inner.archive_card(card_id)
        await self._record("card", card_id, "archived")
        return card

    async def unarchive_card(self, card_id: str) -> Card:
        card = await self._inner.unarchive_card(card_id)
        await self._record("card", card_id, "unarchived")
        return card

    async def delete_card(self, card_id: str) -> None:
        await self._inner.delete_card(card_id)
        await self._record("card", card_id, "deleted")

    # --- comments ---

    async def list_comments(self, card_id: str) -> list[Comment]:
        return await self._inner.list_comments(card_id)

    async def add_comment(self, comment: Comment) -> Comment:
        added = await self._inner.add_comment(comment)
        await self._record("comment", added.id, "added", card_id=added.card_id, author=added.author)
        return added

    async def delete_comment(self, comment_id: str) -> None:
        await self._inner.delete_comment(comment_id)
        await self._record("comment", comment_id, "deleted")

    # --- relations ---

    async def list_relations(self, card_id: str) -> list[Relation]:
        return await self._inner.list_relations(card_id)

    async def add_relation(self, relation: Relation) -> Relation:
        added = await self._inner.add_relation(relation)
        await self._record(
            "relation",
            added.id,
            "added",
            kind=added.kind.value,
            from_card=added.from_card,
            to_card=added.to_card,
        )
        return added

    async def delete_relation(self, relation_id: str) -> None:
        await self._inner.delete_relation(relation_id)
        await self._record("relation", relation_id, "deleted")
