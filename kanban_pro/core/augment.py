"""AugmentingBackend — adapter + overlay decorator over the port (SPEC decision 2).

This is what the interfaces actually call. Per capability it decides:

- NATIVE      -> the adapter declared it -> delegate to the backend.
- POLYFILLED  -> kanban-pro fulfils it itself: Tier-1 *enforcement* (WIP limits —
                 pure rules, no stored data) or Tier-2 *overlay data* (comments,
                 relations held in the overlay store, keyed to backend entity ids).
- UNAVAILABLE -> neither possible -> canonical NotSupported.

Tier-1 enforcement here: WIP limits + the flow engine (core/flow.py — per-card
schemes, free-roam, audited force). Tier-2 overlay: comments/relations (the `overlay`
store) and **`ext`** (the `ext_store` — see core/ext_store.py: a backend with no JSON
bag, like hermes, still gets work reports, attention and origin, because we hold them).
Still to come: ARCHIVE flag polyfill, write-through encoding, reconciliation GC for
out-of-band backend deletes, flow hooks/validators.
"""

from __future__ import annotations

from typing import Protocol, runtime_checkable

from kanban_pro.core.ext_store import ExtStore
from kanban_pro.core.flow import (
    BOARD,
    FLOW_EXT_KEY,
    FREE_ROAM,
    INLINE,
    SCHEME_EXT_KEY,
    NativeTransitions,
    Resolution,
    TransitionInfo,
    TransitionOption,
    modeled_columns,
    parse_inline_flow,
)
from kanban_pro.core.verification import blocking_checks, check_gated_columns
from kanban_pro.domain import (
    Board,
    BoardFlow,
    BoardPatch,
    Card,
    CardPatch,
    Column,
    ColumnPatch,
    Comment,
    Placement,
    Relation,
)
from kanban_pro.ports import Capability, Conflict, Fulfilment, KanbanBackend, NotSupported

#: capabilities the overlay store can polyfill with its own data (Tier 2, v1 slice)
_OVERLAY_CAPS = frozenset({Capability.COMMENTS, Capability.RELATIONS})


class AugmentingBackend:
    """KanbanBackend decorator: delegate native capabilities, polyfill the rest.

    `overlay` is a full store adapter (NativeStore in production, any KanbanBackend
    in tests) holding polyfilled data keyed to the wrapped backend's entity ids.
    Without an overlay, only Tier-1 enforcement is added.
    """

    def __init__(
        self,
        adapter: KanbanBackend,
        overlay: KanbanBackend | None = None,
        ext_store: ExtStore | None = None,
    ) -> None:
        self._adapter = adapter
        self._overlay = overlay
        #: Tier-2 home for `ext` when the adapter can't store it (see core/ext_store.py).
        #: Its presence is what turns CUSTOM_FIELDS from UNAVAILABLE into POLYFILLED.
        self._ext_store = ext_store
        self._fulfilments = self._compute_fulfilments()
        #: port conformance: everything not UNAVAILABLE is callable on this backend.
        self.capabilities: frozenset[Capability] = frozenset(
            cap for cap, f in self._fulfilments.items() if f is not Fulfilment.UNAVAILABLE
        )

    def _compute_fulfilments(self) -> dict[Capability, Fulfilment]:
        out: dict[Capability, Fulfilment] = {}
        for cap in Capability:
            if cap in self._adapter.capabilities:
                out[cap] = Fulfilment.NATIVE
            elif cap is Capability.WIP_LIMITS:
                out[cap] = Fulfilment.POLYFILLED  # Tier-1 enforcement, always available
            elif cap is Capability.WORKFLOW:
                out[cap] = Fulfilment.POLYFILLED  # Tier-1: the flow engine (per-board flow)
            elif cap in _OVERLAY_CAPS and self._overlay is not None:
                out[cap] = Fulfilment.POLYFILLED
            elif cap is Capability.CUSTOM_FIELDS and self._ext_store is not None:
                out[cap] = Fulfilment.POLYFILLED  # Tier-2: ext lives in our own store
            else:
                out[cap] = Fulfilment.UNAVAILABLE
        return out

    def _ext_polyfilled(self) -> bool:
        """True when WE hold the card's ext because the adapter has nowhere to put it."""
        return (
            self._ext_store is not None
            and self._fulfilments[Capability.CUSTOM_FIELDS] is Fulfilment.POLYFILLED
        )

    async def _with_ext(self, card: Card) -> Card:
        """Merge our overlay ext onto a card the adapter returned.

        Overlay wins per key: the adapter's `ext` is its own projection of its own columns
        (hermes exposes `ext["hermes"]` this way), while ours holds the keys it cannot store.
        They don't collide in practice, and if they did, the value we were asked to persist is
        the one the caller last wrote.
        """
        if not self._ext_polyfilled():
            return card
        assert self._ext_store is not None
        stored = await self._ext_store.get(card.id)
        if not stored:
            return card
        return card.model_copy(update={"ext": {**(card.ext or {}), **stored}})

    def fulfilments(self) -> dict[Capability, Fulfilment]:
        """Per-capability fulfilment — the `capabilities` resource payload."""
        return dict(self._fulfilments)

    def _route(self, cap: Capability) -> KanbanBackend:
        """The backend that fulfils a data capability: adapter, overlay, or nobody."""
        fulfilment = self._fulfilments[cap]
        if fulfilment is Fulfilment.NATIVE:
            return self._adapter
        if fulfilment is Fulfilment.POLYFILLED and self._overlay is not None:
            return self._overlay
        raise NotSupported(f"{cap.name.lower()} is unavailable on this profile")

    # --- Tier-1 WIP enforcement (SPEC decision 2; core rule, no stored data) ---

    async def _check_wip(self, board_id: str, column_id: str, incoming_card_id: str) -> None:
        """Reject a move/placement into a column already at its wip_limit.

        Skipped when the backend enforces WIP natively (trust it, incl. overrides).
        """
        if Capability.WIP_LIMITS in self._adapter.capabilities:
            return
        columns = await self._adapter.list_columns(board_id)
        column = next((c for c in columns if c.id == column_id), None)
        if column is None or column.wip_limit is None:
            return
        occupants = [
            card
            for card in await self._adapter.list_cards(board_id)
            if card.id != incoming_card_id
            and any(p.board_id == board_id and p.column_id == column_id for p in card.placements)
        ]
        if len(occupants) >= column.wip_limit:
            raise Conflict(
                f"column {column.name!r} is at its WIP limit ({column.wip_limit})"
                " — move a card out first"
            )

    # --- Tier-1 flow enforcement (flow engine — core/flow.py; ruled 2026-07-10) ---

    def _resolve_flow(self, card: Card) -> Resolution:
        """Which flow applies to this card (see core/flow.py resolution chain). Reads only
        the card — the board's own flow is fetched by the caller when resolved == BOARD."""
        ext = card.ext if isinstance(card.ext, dict) else {}
        if ext.get(SCHEME_EXT_KEY) == FREE_ROAM:
            return Resolution(resolved=FREE_ROAM)  # per-card escape hatch
        inline_raw = ext.get(FLOW_EXT_KEY)
        if inline_raw is not None:
            inline = parse_inline_flow(inline_raw)
            if inline is not None:
                return Resolution(resolved=INLINE, inline_flow=inline)
            return Resolution(resolved=BOARD, fell_back=True)  # bad inline -> board flow
        return Resolution(resolved=BOARD)

    async def _check_flow(self, card_id: str, to_board_id: str, to_column_id: str) -> None:
        """Validate a move against the card's flow. Skips: backend-native WORKFLOW (trust
        it), free-roam, unmodeled endpoints (a flow governs only the columns it names),
        repositioning."""
        if Capability.WORKFLOW in self._adapter.capabilities:
            return
        # self.get_card, NOT the adapter's: the flow escapes (`kanban_pro.scheme` free-roam,
        # `kanban_pro.flow` inline) live in ext, and on a polyfilled profile that ext is in OUR
        # store — reading the adapter directly would silently ignore both.
        card = await self.get_card(card_id)
        resolution = self._resolve_flow(card)
        if resolution.resolved == FREE_ROAM:
            return
        placement = next((p for p in card.placements if p.board_id == to_board_id), None)
        if placement is None:
            return  # the adapter's strict-move NotFound handles this (Q16)
        current_id = placement.column_id
        if current_id == to_column_id:
            return  # repositioning within a column is not a transition
        board = await self._adapter.get_board(to_board_id)

        if resolution.resolved == INLINE:  # name-based one-card flow
            flow = resolution.inline_flow
            assert flow is not None
            names = {c.id: c.name.lower() for c in board.columns}
            current = names.get(current_id)
            target = names.get(to_column_id)
            if current is None or target is None:
                return
            if current not in flow.states or target not in flow.states:
                return  # a flow governs only the states it declares
            if not flow.permits(current, target):
                allowed = ", ".join(flow.allowed.get(current, [])) or "none"
                raise Conflict(
                    f"inline flow does not allow {current} -> {target}"
                    f" (allowed from {current}: {allowed}); use force=true to override"
                )
            return

        # the board's own flow (the normal path), by column id
        transitions = board.flow.transitions if board.flow else {}
        if not transitions:
            return  # free-roam board
        modeled = modeled_columns(transitions)
        if current_id not in modeled or to_column_id not in modeled:
            return  # a flow governs only the columns it names
        if to_column_id not in transitions.get(current_id, []):
            allowed = ", ".join(transitions.get(current_id, [])) or "none"
            raise Conflict(
                f"board flow does not allow {current_id} -> {to_column_id}"
                f" (allowed from {current_id}: {allowed}); use force=true to override"
            )

    async def transitions(self, card_id: str, board_id: str | None = None) -> TransitionInfo:
        """What moves are legal for this card, and under which resolved flow."""
        card = await self.get_card(card_id)  # merged ext — the flow escapes live there
        if board_id is None:
            if not card.placements:
                raise Conflict(f"card {card_id!r} has no placement — nothing to move")
            # A shared card's legal moves depend on WHICH board you're moving it on (each
            # board has its own flow), so there is no defensible default. Guessing
            # placements[0] answered for an arbitrary board — right half the time.
            if len(card.placements) > 1:
                boards = ", ".join(sorted(p.board_id for p in card.placements))
                raise Conflict(
                    f"card {card_id!r} is on more than one board ({boards}) — pass board_id"
                    " to say which board's transitions you want"
                )
            board_id = card.placements[0].board_id
        placement = next((p for p in card.placements if p.board_id == board_id), None)
        board = await self._adapter.get_board(board_id)
        columns = board.columns
        current_id = placement.column_id if placement else None
        requested = card.ext.get(SCHEME_EXT_KEY) if isinstance(card.ext, dict) else None
        scheme = str(requested) if requested is not None else None

        def options(ids: set[str] | None) -> list[TransitionOption]:
            return [
                TransitionOption(column_id=c.id, name=c.name)
                for c in columns
                if c.id != current_id and (ids is None or c.id in ids)
            ]

        if isinstance(self._adapter, NativeTransitions):
            lanes = {name.lower() for name in await self._adapter.list_transitions(card_id)}
            return TransitionInfo(
                card_id=card_id, board_id=board_id, current_column_id=current_id,
                scheme=scheme, resolved_scheme=None, source="backend",
                options=[TransitionOption(column_id=c.id, name=c.name)
                         for c in columns if c.id != current_id and c.name.lower() in lanes],
                note="workflow enforced by the backend",
            )  # fmt: skip
        resolution = self._resolve_flow(card)
        if resolution.resolved == FREE_ROAM:
            return TransitionInfo(
                card_id=card_id, board_id=board_id, current_column_id=current_id,
                scheme=scheme, resolved_scheme=FREE_ROAM, source="free-roam",
                options=options(None),
            )  # fmt: skip

        if resolution.resolved == INLINE:  # name-based one-card flow
            flow = resolution.inline_flow
            assert flow is not None
            names = {c.id: c.name.lower() for c in columns}
            current_name = names.get(current_id) if current_id else None
            if current_name is None or current_name not in flow.states:
                return TransitionInfo(
                    card_id=card_id, board_id=board_id, current_column_id=current_id,
                    scheme=scheme, resolved_scheme=INLINE, source=INLINE, options=options(None),
                    note="current column not modeled by the inline flow — free",
                )  # fmt: skip
            allowed_names = set(flow.allowed.get(current_name, []))
            legal = {cid for cid, nm in names.items() if nm in allowed_names}
            return TransitionInfo(
                card_id=card_id, board_id=board_id, current_column_id=current_id,
                scheme=scheme, resolved_scheme=INLINE, source=INLINE, options=options(legal),
            )  # fmt: skip

        # the board's own flow
        transitions = board.flow.transitions if board.flow else {}
        # a malformed inline flow falls through to here — prefix that fact onto the note.
        prefix = (
            "inline flow malformed — fell back to the board flow" if resolution.fell_back else ""
        )
        sep = "; " if prefix else ""
        if not transitions:
            return TransitionInfo(
                card_id=card_id, board_id=board_id, current_column_id=current_id,
                scheme=scheme, resolved_scheme=BOARD, source="free", options=options(None),
                note=f"{prefix}{sep}no board flow configured — free",
            )  # fmt: skip
        modeled = modeled_columns(transitions)
        if current_id is None or current_id not in modeled:
            return TransitionInfo(
                card_id=card_id, board_id=board_id, current_column_id=current_id,
                scheme=scheme, resolved_scheme=BOARD, source="flow", options=options(None),
                note=f"{prefix}{sep}current column not modeled by the board flow — free",
            )  # fmt: skip
        # legal = the lane's explicit edges + any unmodeled column (free to enter)
        legal = set(transitions.get(current_id, [])) | {
            c.id for c in columns if c.id not in modeled
        }
        return TransitionInfo(
            card_id=card_id, board_id=board_id, current_column_id=current_id,
            scheme=scheme, resolved_scheme=BOARD, source="flow", options=options(legal),
            note=prefix or None,
        )  # fmt: skip

    # --- flow administration (per-board, validated against the board's columns) ---

    async def set_flow(self, board_id: str, flow: BoardFlow) -> Board:
        """Replace a board's workflow. Every referenced column ID must exist on the board
        — a flow that names a non-existent lane is refused (drift-proofing at the write)."""
        board = await self._adapter.get_board(board_id)
        col_ids = {c.id for c in board.columns}
        for source, targets in flow.transitions.items():
            for cid in (source, *targets):
                if cid not in col_ids:
                    raise Conflict(
                        f"flow references column {cid!r} not on board {board_id!r}"
                        f" (columns: {', '.join(sorted(col_ids)) or 'none'})"
                    )
        return await self._adapter.set_flow(board_id, flow)

    # --- boards / columns: delegate ---

    async def list_boards(self) -> list[Board]:
        return await self._adapter.list_boards()

    async def get_board(self, board_id: str) -> Board:
        return await self._adapter.get_board(board_id)

    async def create_board(self, board: Board) -> Board:
        return await self._adapter.create_board(board)

    async def update_board(self, board_id: str, patch: BoardPatch) -> Board:
        return await self._adapter.update_board(board_id, patch)

    async def delete_board(self, board_id: str) -> None:
        await self._adapter.delete_board(board_id)

    async def list_columns(self, board_id: str) -> list[Column]:
        return await self._adapter.list_columns(board_id)

    async def create_column(self, board_id: str, column: Column) -> Column:
        return await self._adapter.create_column(board_id, column)

    async def update_column(self, column_id: str, patch: ColumnPatch) -> Column:
        return await self._adapter.update_column(column_id, patch)

    async def delete_column(self, column_id: str) -> None:
        # cascade: strip any board-flow edges that reference this lane, so the flow can
        # never point at a deleted column (the write-side drift guard, mirror of set_flow).
        for board in await self._adapter.list_boards():
            if board.flow is None:
                continue
            stripped = {
                src: [d for d in dests if d != column_id]
                for src, dests in board.flow.transitions.items()
                if src != column_id
            }
            if stripped != board.flow.transitions:
                await self._adapter.set_flow(
                    board.id, board.flow.model_copy(update={"transitions": stripped})
                )
        await self._adapter.delete_column(column_id)

    # --- cards: delegate + WIP checks on column entry ---

    async def list_cards(self, board_id: str, include_archived: bool = False) -> list[Card]:
        cards = await self._adapter.list_cards(board_id, include_archived)
        if not self._ext_polyfilled() or not cards:
            return cards
        assert self._ext_store is not None
        stored = await self._ext_store.get_many([c.id for c in cards])  # one query, not N
        return [
            c.model_copy(update={"ext": {**(c.ext or {}), **stored[c.id]}}) if c.id in stored else c
            for c in cards
        ]

    async def get_card(self, card_id: str) -> Card:
        return await self._with_ext(await self._adapter.get_card(card_id))

    async def create_card(self, card: Card, *, overwrite: bool = False) -> Card:
        for placement in card.placements:
            await self._check_wip(placement.board_id, placement.column_id, card.id)
            # ...and a card must not be BORN in one either
            await self._check_new_card_verified(card, placement.board_id, placement.column_id)
        if not self._ext_polyfilled() or not card.ext:
            return await self._with_ext(await self._adapter.create_card(card, overwrite=overwrite))
        # The adapter can't hold ext, so don't hand it any — it would drop it silently (or, on
        # hermes, quietly consume the `ext["hermes"]` keys it DOES map onto create flags).
        # Create bare, then persist ext against the id the store minted.
        created = await self._adapter.create_card(
            card.model_copy(update={"ext": {}}), overwrite=overwrite
        )
        assert self._ext_store is not None
        merged = await self._ext_store.merge(created.id, card.ext)
        return created.model_copy(update={"ext": {**(created.ext or {}), **merged}})

    async def update_card(self, card_id: str, patch: CardPatch) -> Card:
        if not self._ext_polyfilled() or patch.ext is None:
            return await self._with_ext(await self._adapter.update_card(card_id, patch))
        assert self._ext_store is not None
        # ext is OURS on this profile: strip it from the patch (the adapter would refuse the
        # whole call — hermes accepts only `assignees`) and merge it into our store instead.
        # Shallow-merge with None-removes, the same semantics as a native ext patch (Q17).
        rest = patch.model_copy(update={"ext": None})
        fields = rest.model_dump(exclude_unset=True)
        fields.pop("ext", None)
        if fields:
            await self._adapter.update_card(card_id, rest)  # the non-ext half, if any
        await self._ext_store.merge(card_id, patch.ext)
        return await self.get_card(card_id)  # re-read so the merge is reflected exactly once

    async def archive_card(self, card_id: str) -> Card:
        return await self._adapter.archive_card(card_id)

    async def unarchive_card(self, card_id: str) -> Card:
        return await self._adapter.unarchive_card(card_id)

    async def delete_card(self, card_id: str) -> None:
        await self._adapter.delete_card(card_id)
        if self._overlay is not None:
            # GC polyfilled rows keyed to the purged card (comments/relations cascade).
            await self._overlay.delete_card(card_id)
        if self._ext_store is not None:
            # ...and our ext, or a recycled id would inherit a dead card's work report.
            await self._ext_store.delete(card_id)

    async def move_card(
        self,
        card_id: str,
        to_board_id: str,
        to_column_id: str,
        position: int,
        *,
        force: bool = False,
    ) -> Card:
        # force (Jan): a deliberate override skips flow + WIP + checks validation. It is never
        # silent — the recording layer flags the event, so a forced move is auditable.
        if not force:
            await self._check_flow(card_id, to_board_id, to_column_id)
            await self._check_wip(to_board_id, to_column_id, card_id)
            await self._check_verified(card_id, to_board_id, to_column_id)
        return await self._adapter.move_card(card_id, to_board_id, to_column_id, position)

    async def _check_verified(self, card_id: str, to_board_id: str, to_column_id: str) -> None:
        """Refuse a move INTO a check-gated column while a required check has not passed.

        Enforced HERE — beside flow and WIP — and not in the dispatcher, because a gate that lives
        in one consumer is only as good as that consumer's coverage; the dispatcher's own backstop
        was correct and still let AIR-2915 through by exempting a role nobody remembered. A board
        rule cannot be sidestepped by an actor, a script, or a future runtime.

        Off by default (empty `check_gated_columns` ⇒ no gate). `force=true` overrides and stamps
        `forced: true` on the event forever — the honest escape hatch. An EMPTY contract is refused
        too: see `_refuse_unverified`. Rationale: JOURNAL 2026-07-14.
        """
        board = await self._adapter.get_board(to_board_id)
        if to_column_id not in check_gated_columns(board):
            return
        card = await self.get_card(card_id)
        placement = next((p for p in card.placements if p.board_id == to_board_id), None)
        if placement is not None and placement.column_id == to_column_id:
            return  # repositioning within the lane is not an entry
        self._refuse_unverified(card, board, to_column_id)

    async def _check_new_card_verified(self, card: Card, board_id: str, column_id: str) -> None:
        """Same rule, for a card that does not exist yet (`create_card`) — it has no stored state,
        so the check runs against the card object being created."""
        board = await self._adapter.get_board(board_id)
        if column_id not in check_gated_columns(board):
            return
        self._refuse_unverified(card, board, column_id)

    def _refuse_unverified(self, card: Card, board: Board, column_id: str) -> None:
        """The refusal — ONE definition, shared by every way into a gated lane.

        An empty contract is refused as firmly as a failing one: a card nobody declared checks for
        is not proven, it is unspecified, and after the fact the two are indistinguishable.
        """
        lane = next((c.name for c in board.columns if c.id == column_id), column_id)

        if not card.checks:
            raise Conflict(
                f"{lane!r} is check-gated and this card has NO checks declared — it is not"
                " verified, it is unspecified, and nobody can tell those apart after the fact."
                " Declare what proving this card means (declare_checks), or, if it genuinely needs"
                " no verification, say so on the record with a `required: false` check. force=true"
                " overrides, and names you in the log forever."
            )
        if not (blocking := blocking_checks(card)):
            return
        detail = ", ".join(f"{c.key}={c.status.value}" for c in blocking)
        raise Conflict(
            f"{lane!r} is check-gated and {len(blocking)} required check(s) have not passed: "
            f"{detail}. Only `passed` advances a card — record the real outcome with "
            "record_check_result(card_id, key, status, evidence). If a check CANNOT be run, record "
            "it `blocked` with the exact blocker and raise_attention; do not run something cheaper "
            "and record that instead. force=true overrides this, and says so in the log forever."
        )

    async def add_placement(self, card_id: str, placement: Placement) -> Card:
        await self._check_wip(placement.board_id, placement.column_id, card_id)
        # every way IN is gated, not just move_card: otherwise park a placement in `done` directly
        # (or swap the gated one out and back) — legal calls, no `forced: true`, no trace.
        await self._check_verified(card_id, placement.board_id, placement.column_id)
        return await self._adapter.add_placement(card_id, placement)

    async def remove_placement(self, card_id: str, board_id: str) -> Card:
        return await self._adapter.remove_placement(card_id, board_id)

    # --- comments / relations: capability-routed (adapter, overlay, or NotSupported) ---

    async def list_comments(self, card_id: str) -> list[Comment]:
        return await self._route(Capability.COMMENTS).list_comments(card_id)

    async def add_comment(self, comment: Comment) -> Comment:
        return await self._route(Capability.COMMENTS).add_comment(comment)

    async def delete_comment(self, comment_id: str) -> None:
        await self._route(Capability.COMMENTS).delete_comment(comment_id)

    async def list_relations(self, card_id: str) -> list[Relation]:
        return await self._route(Capability.RELATIONS).list_relations(card_id)

    async def add_relation(self, relation: Relation) -> Relation:
        return await self._route(Capability.RELATIONS).add_relation(relation)

    async def delete_relation(self, relation_id: str) -> None:
        await self._route(Capability.RELATIONS).delete_relation(relation_id)


@runtime_checkable
class HasFulfilments(Protocol):
    """Any core decorator that can report per-capability fulfilment."""

    def fulfilments(self) -> dict[Capability, Fulfilment]: ...


def fulfilments(backend: KanbanBackend) -> dict[Capability, Fulfilment]:
    """Per-capability fulfilment for any backend (decorated stack or bare adapter)."""
    if isinstance(backend, HasFulfilments):
        return backend.fulfilments()
    return {
        cap: Fulfilment.NATIVE if cap in backend.capabilities else Fulfilment.UNAVAILABLE
        for cap in Capability
    }
