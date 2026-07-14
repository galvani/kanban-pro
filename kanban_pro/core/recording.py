"""RecordingBackend — stamps every successful write into the change-log with the actor.

Sits under ActorPolicyBackend in the core stack (config.build_backend):

    ActorPolicyBackend(RecordingBackend(AugmentingBackend(adapter), changelog, actor), actor)

The actor is per-connection/per-process identity (SPEC decision 10): the MCP server is
started with `--actor kind:name` (or $KANBAN_PRO_ACTOR); everything that connection
does is attributed to it. Reads are never recorded; failed writes never reach the log.
Payloads stay slim (ids + the changed bits) — consumers `get_*` for full state.
"""

from __future__ import annotations

from kanban_pro.core.changelog import ChangeEvent, ChangeLog
from kanban_pro.domain import (
    Board,
    BoardFlow,
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
from kanban_pro.ports import Capability, Conflict, Fulfilment, KanbanBackend, NotSupported

from .augment import AugmentingBackend
from .augment import fulfilments as _fulfilments
from .dedupe import DedupeStore
from .flow import FREE_ROAM, SCHEME_EXT_KEY, TransitionInfo
from .work import Claim, ClaimStore, WorkItem, WorkQueue

#: ext key the work report lives under. Duplicated from core.work_report (which imports THIS
#: module) rather than imported — a one-word constant is not worth a circular import.
WORK_REPORT_EXT_KEY = "work_report"

#: ext key for the attention signal (methods.md "Card ext conventions")
ATTENTION_EXT_KEY = "kanban_pro.attention"

#: how loudly an attention flag speaks. Only `block` stops the work; see raise_attention.
ATTENTION_SEVERITIES = frozenset({"block", "warn", "info"})

#: what a flag with no `severity` means. Flags raised before severity existed carry none,
#: and they were all blocking — so an absent severity must keep blocking, or a card that
#: was waiting for a human would silently start flowing again on upgrade.
ATTENTION_DEFAULT_SEVERITY = "block"


def attention_blocks(card_ext: dict[str, object]) -> bool:
    """True if this card's attention flag HALTS the work (vs merely being visible)."""
    flag = card_ext.get(ATTENTION_EXT_KEY)
    if not isinstance(flag, dict):
        return False
    return bool(flag.get("severity", ATTENTION_DEFAULT_SEVERITY) == "block")


def auto_clear_columns(board: Board) -> list[str]:
    """Columns where arriving clears a card's attention flag (per-board `ext`).

    These are the resting lanes: a card that reaches one is, by the board's own
    configuration, not waiting on anybody.

    NOT derivable from `category: done`, though it looks like it should be (we nearly did it,
    2026-07-14). `category` answers "may a worker be handed this card?" — it gates the QUEUE
    (`_READYISH`, below). This list answers "is anyone waiting on anybody here?" — it gates
    ATTENTION. A `done` column answers yes to both, which is what makes them look like one
    concept; `ready` is the counterexample that matters, since it rests without being done.
    """
    raw = (board.ext or {}).get("auto_clear_attention_columns")
    if not isinstance(raw, list):
        return []
    return [c for c in raw if isinstance(c, str)]


#: categories that count as "workable" for the queue (done/canceled/triage are not).
#: This is the ONLY thing Column.category governs — not the flow (that is board.flow, by
#: column id) and not the resting lanes (that is auto_clear_columns, above).
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
        self,
        card_id: str,
        reason: str,
        for_actor: str | None = None,
        severity: str = "block",
    ) -> Card:
        """Flag a card as needing input — the routable event consumers watch.

        The flag lives in ext (shallow-merge, one key); the change-log event
        `attention.raised` carries reason + target + severity so notifiers route
        without reading the card.

        `severity` says whether the WORK stops (Jan, 2026-07-13 — before this, every
        attention halted the lane, so a worker with something merely worth knowing had
        no way to say it without stopping the card):

            block   "I cannot proceed without a decision" — the card HALTS until the
                    flag is cleared. The default, and the old behaviour.
            warn    "this went sideways but the work stands" — visible on the card, and
                    a notifier will surface it, but the card keeps flowing.
            info    "you should know" — noted, non-blocking, usually not worth a DM.

        Only `block` stops a dispatcher from working the card again. `warn`/`info` are
        signals, not gates — do not use them for a question you actually need answered.

        A `block` flag may NOT be raised on a card resting in an `auto_clear_attention_columns`
        lane — see `_refuse_blocking_in_resting_lane`.
        """
        if severity not in ATTENTION_SEVERITIES:
            raise Conflict(
                f"severity must be one of {sorted(ATTENTION_SEVERITIES)}, got {severity!r}"
            )
        if severity == "block":
            await self._refuse_blocking_in_resting_lane(card_id)
        flag = {
            "reason": reason,
            "raised_by": self.actor,
            "for": for_actor,
            "severity": severity,
        }
        card = await self._inner.update_card(card_id, CardPatch(ext={ATTENTION_EXT_KEY: flag}))
        await self._record(
            "attention", card_id, "raised", reason=reason, for_actor=for_actor, severity=severity
        )
        return card

    async def _refuse_blocking_in_resting_lane(self, card_id: str) -> None:
        """Reject a `block` flag on a card resting in an auto-clear lane.

        The auto-clear only runs on ARRIVAL (see move_card), so a block raised AFTER the
        card reached a resting lane is never cleared by anything — and a blocking flag hides
        the card from every queue. The card ends up parked in a lane the board declares
        attention-free, invisible to workers, freeable only by a human. (VLM-75, 2026-07-13:
        a worker handed off to `ready`, the dispatcher then raised a block on the report
        contract, and the card was stranded.) Refuse loudly instead of stranding it: the
        caller must move the card out of the resting lane first, or say `warn`/`info` — those
        don't halt the lane, so they cannot deadlock it.
        """
        card = await self._inner.get_card(card_id)
        resting: list[str] = []
        for placement in card.placements:
            board = await self._inner.get_board(placement.board_id)
            if placement.column_id in auto_clear_columns(board):
                resting.append(f"{placement.board_id}/{placement.column_id}")
        # A SHARED card rests only if it rests EVERYWHERE. Attention is cleared by arriving
        # at any board's resting lane, so a card still in a working lane on some other board
        # can always be un-stranded by moving it there — the flag is reachable, allow it.
        # (Checking "any placement rests" made a delegated card unblockable on the board
        # actually working it, just because the origin board had parked it in `done`.)
        if resting and len(resting) == len(card.placements):
            raise Conflict(
                f"cannot raise a blocking attention flag on a card resting in "
                f"{', '.join(resting)}: the board auto-clears attention there, so "
                f"nothing would ever clear this flag and the card would be stranded. "
                f"Move the card out of that column first (e.g. to a blocked/started "
                f"lane), or raise it with severity='warn'/'info' to signal without "
                f"halting the card."
            )

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

    # --- flow ---

    async def set_flow(self, board_id: str, flow: BoardFlow) -> Board:
        updated = await self._inner.set_flow(board_id, flow)
        # flow lives on the board doc, so a flow edit is a board update (event kind reuse).
        await self._record("board", board_id, "updated", board_id, fields=["flow"])
        return updated

    async def _maybe_decrement_attempts(self, card_id: str, old: Card) -> None:
        """Decrement ext.work.attempts by 1 if the card's board flow allows auto-reset."""
        ext = old.ext if isinstance(old.ext, dict) else {}
        if ext.get(SCHEME_EXT_KEY) == FREE_ROAM:
            return  # a free-roam card opts out of flow-governed bookkeeping
        placement = old.placements[0] if old.placements else None
        if placement is None:
            return
        board = await self._inner.get_board(placement.board_id)
        if board.flow is None or not board.flow.auto_reset_attempts_on_reassign:
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

    async def create_card(
        self, card: Card, *, idempotency_key: str | None = None, overwrite: bool = False
    ) -> Card:
        if idempotency_key and (hit := await self.dedupe.get("card", idempotency_key)):
            return Card.model_validate_json(hit)
        created = await self._inner.create_card(card, overwrite=overwrite)
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
        # the lane we're leaving is the one on THIS board — a shared card's other placements
        # are other boards' lanes and must not decide whether this move changed anything.
        old_here = next((p for p in old.placements if p.board_id == to_board_id), None)
        old_col = old_here.column_id if old_here else None
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
            if to_column_id in auto_clear_columns(board) and ATTENTION_EXT_KEY in (moved.ext or {}):
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
