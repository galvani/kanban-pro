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
    ColumnCategory,
    ColumnPatch,
    Comment,
    Placement,
    Relation,
)
from kanban_pro.ports import Capability, Fulfilment, KanbanBackend, NotSupported

from .augment import AugmentingBackend
from .augment import fulfilments as _fulfilments
from .dedupe import DedupeStore
from .flow import FlowConfig, SCHEME_EXT_KEY, TransitionInfo
from .work import Claim, ClaimStore, WorkItem, WorkQueue

#: ext key for the attention signal (methods.md "Card ext conventions")
ATTENTION_EXT_KEY = "kanban_pro.attention"

#: categories that count as "workable" for the queue (done/canceled/triage are not)
_READYISH = {ColumnCategory.BACKLOG, ColumnCategory.UNSTARTED, ColumnCategory.STARTED}
#: queue ordering: my in-flight work first, then actionable, then queued
_CATEGORY_RANK = {ColumnCategory.STARTED: 0, ColumnCategory.UNSTARTED: 1, ColumnCategory.BACKLOG: 2}


class RecordingBackend:
    """KanbanBackend decorator: delegate everything, log successful writes."""

    def __init__(
        self,
        inner: KanbanBackend,
        changelog: ChangeLog,
        actor: str,
        claims: ClaimStore | None = None,
        dedupe: DedupeStore | None = None,
    ) -> None:
        self._inner = inner
        self.changelog = changelog
        self.actor = actor
        self.claims = claims or ClaimStore()
        self.dedupe = dedupe or DedupeStore()
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

    # --- work distribution (claim/lease + queue; see core/work.py) ---

    async def claim_card(
        self, card_id: str, ttl_seconds: int = 3600, owner: str | None = None
    ) -> Claim:
        """Atomically lease a card for this actor (CAS; expired claims are takeable).
        `owner` overrides the default actor (claim on behalf of a worker)."""
        await self._inner.get_card(card_id)  # not_found before claiming ghosts
        actor = owner or self.actor
        claim = await self.claims.claim(card_id, actor, ttl_seconds)
        await self._record(
            "card", card_id, "claimed", actor=actor, expires_at=claim.expires_at.isoformat()
        )
        return claim

    async def heartbeat_claim(
        self, card_id: str, ttl_seconds: int = 3600, owner: str | None = None
    ) -> Claim:
        return await self.claims.renew(card_id, owner or self.actor, ttl_seconds)

    async def release_claim(self, card_id: str, owner: str | None = None) -> None:
        had = await self.claims.get(card_id)
        actor = owner or self.actor
        await self.claims.release(card_id, actor)
        if had is not None and not had.expired:
            await self._record("card", card_id, "released", actor=actor)

    # --- attention signal (card-level "needs a decision", ruled 2026-07-05) ---

    async def raise_attention(
        self, card_id: str, reason: str, for_actor: str | None = None
    ) -> Card:
        """Flag a card as needing input/decision — the routable event consumers watch.

        The flag lives in ext (shallow-merge, one key); the change-log event
        `attention.raised` carries reason + target so notifiers route without
        reading the card.
        """
        flag = {"reason": reason, "raised_by": self.actor, "for": for_actor}
        card = await self._inner.update_card(card_id, CardPatch(ext={ATTENTION_EXT_KEY: flag}))
        await self._record("attention", card_id, "raised", reason=reason, for_actor=for_actor)
        return card

    async def clear_attention(self, card_id: str, resolution: str | None = None) -> Card:
        """Clear the flag (question answered / decision made)."""
        card = await self._inner.update_card(
            card_id,
            CardPatch(ext={ATTENTION_EXT_KEY: None}),  # None removes the key (Q17)
        )
        await self._record("attention", card_id, "cleared", resolution=resolution)
        return card

    def _matches_actor(self, assignees: list[str], wanted: str) -> bool:
        # actor is "kind:name"; backends often key assignees by bare name
        bare = wanted.split(":", 1)[-1]
        return wanted in assignees or bare in assignees

    async def list_work(
        self, assignee: str | None = None, include_unassigned: bool = True
    ) -> WorkQueue:
        """The agent's queue: workable cards for `assignee` (default: this actor),
        each annotated with its legal transitions — one call, whole plan."""
        if not isinstance(self._inner, AugmentingBackend):
            raise NotSupported("work queue needs the augmenting layer")
        wanted = assignee or self.actor
        live_claims = await self.claims.live()
        items: list[WorkItem] = []
        for board in await self._inner.list_boards():
            columns = {c.id: c for c in board.columns}
            for card in await self._inner.list_cards(board.id):
                placement = next((p for p in card.placements if p.board_id == board.id), None)
                column = columns.get(placement.column_id) if placement else None
                if placement is None or column is None or column.category not in _READYISH:
                    continue
                claim = live_claims.get(card.id)
                claimed_by_wanted = claim is not None and claim.owner == wanted
                if claim is not None and not claimed_by_wanted:
                    continue  # leased to someone else -> not available
                mine = self._matches_actor(card.assignees, wanted)
                # a card I hold a lease on is my work regardless of assignment
                if not (mine or claimed_by_wanted or (include_unassigned and not card.assignees)):
                    continue
                items.append(
                    WorkItem(
                        card=card,
                        board_id=board.id,
                        column_id=column.id,
                        column_name=column.name,
                        column_category=column.category.value,
                        claimed_by_me=claimed_by_wanted,
                        transitions=await self._inner.transitions(card.id, board.id),
                    )
                )
        items.sort(
            key=lambda i: (
                _CATEGORY_RANK.get(ColumnCategory(i.column_category), 9),
                next(p.position for p in i.card.placements if p.board_id == i.board_id),
            )
        )
        return WorkQueue(actor=wanted, items=items)

    async def _record(
        self,
        entity: str,
        entity_id: str,
        op: str,
        board_id: str | None = None,
        actor: str | None = None,
        **data: object,
    ) -> None:
        await self.changelog.append(
            ChangeEvent(
                actor=actor or self.actor,
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

    async def create_board(self, board: Board, *, idempotency_key: str | None = None) -> Board:
        if idempotency_key and (hit := await self.dedupe.get("board", idempotency_key)):
            return Board.model_validate_json(hit)  # retry: original result, no new event
        created = await self._inner.create_board(board)
        if idempotency_key:
            await self.dedupe.put("board", idempotency_key, created.model_dump_json())
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

    async def create_column(
        self, board_id: str, column: Column, *, idempotency_key: str | None = None
    ) -> Column:
        if idempotency_key and (hit := await self.dedupe.get("column", idempotency_key)):
            return Column.model_validate_json(hit)
        created = await self._inner.create_column(board_id, column)
        if idempotency_key:
            await self.dedupe.put("column", idempotency_key, created.model_dump_json())
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

    async def _maybe_decrement_attempts(self, card_id: str, old: Card) -> None:
        """Decrement ext.work.attempts by 1 if the card's flow allows auto-reset."""
        flows: FlowConfig | None = getattr(self._inner, "flows", None)
        if flows is None:
            return
        ext = old.ext if isinstance(old.ext, dict) else {}
        requested = ext.get(SCHEME_EXT_KEY)
        resolution = flows.resolve(str(requested) if requested is not None else None)
        flow = resolution.flow
        if flow is None or not flow.auto_reset_attempts_on_reassign:
            return
        work = dict(ext.get("work") or {})
        attempts = work.get("attempts", 0)
        if attempts <= 0:
            return
        work["attempts"] = attempts - 1
        await self._inner.update_card(card_id, CardPatch(ext={"work": work}))

    # --- cards ---

    async def list_cards(self, board_id: str, include_archived: bool = False) -> list[Card]:
        return await self._inner.list_cards(board_id, include_archived)

    async def get_card(self, card_id: str) -> Card:
        return await self._inner.get_card(card_id)

    async def create_card(self, card: Card, *, idempotency_key: str | None = None) -> Card:
        if idempotency_key and (hit := await self.dedupe.get("card", idempotency_key)):
            return Card.model_validate_json(hit)
        created = await self._inner.create_card(card)
        if idempotency_key:
            await self.dedupe.put("card", idempotency_key, created.model_dump_json())
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
        old = await self._inner.get_card(card_id)
        updated = await self._inner.update_card(card_id, patch)
        fields = sorted(patch.model_dump(exclude_unset=True))
        await self._record("card", card_id, "updated", fields=fields)
        # auto-decrement attempts when assignee changes (per-card flow opt-in)
        if patch.assignees is not None and patch.assignees != old.assignees:
            await self._maybe_decrement_attempts(card_id, old)
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
        old = await self._inner.get_card(card_id)
        old_col = old.placements[0].column_id if old.placements else None
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
        # ── auto-clear attention on certain columns (per-board config) ──────
        try:
            board = await self._inner.get_board(to_board_id)
            auto_cols: list[str] | None = (board.ext or {}).get("auto_clear_attention_columns")
            if auto_cols and to_column_id in auto_cols and ATTENTION_EXT_KEY in (moved.ext or {}):
                await self.clear_attention(card_id, resolution=f"moved to {to_column_id}")
        except Exception:
            pass  # best-effort; never fail a move because auto-clear broke
        # auto-decrement attempts when lane changes (per-card flow opt-in)
        if old_col is not None and old_col != to_column_id:
            await self._maybe_decrement_attempts(card_id, old)
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

    async def add_comment(self, comment: Comment, *, idempotency_key: str | None = None) -> Comment:
        if idempotency_key and (hit := await self.dedupe.get("comment", idempotency_key)):
            return Comment.model_validate_json(hit)
        added = await self._inner.add_comment(comment)
        if idempotency_key:
            await self.dedupe.put("comment", idempotency_key, added.model_dump_json())
        await self._record("comment", added.id, "added", card_id=added.card_id, author=added.author)
        return added

    async def delete_comment(self, comment_id: str) -> None:
        await self._inner.delete_comment(comment_id)
        await self._record("comment", comment_id, "deleted")

    # --- relations ---

    async def list_relations(self, card_id: str) -> list[Relation]:
        return await self._inner.list_relations(card_id)

    async def add_relation(
        self, relation: Relation, *, idempotency_key: str | None = None
    ) -> Relation:
        if idempotency_key and (hit := await self.dedupe.get("relation", idempotency_key)):
            return Relation.model_validate_json(hit)
        added = await self._inner.add_relation(relation)
        if idempotency_key:
            await self.dedupe.put("relation", idempotency_key, added.model_dump_json())
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
