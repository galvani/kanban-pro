"""AugmentingBackend — adapter + overlay decorator over the port (SPEC decision 2).

This is what the interfaces actually call. Per capability it decides:

- NATIVE      -> the adapter declared it -> delegate to the backend.
- POLYFILLED  -> kanban-pro fulfils it itself: Tier-1 *enforcement* (WIP limits —
                 pure rules, no stored data) or Tier-2 *overlay data* (comments,
                 relations held in the overlay store, keyed to backend entity ids).
- UNAVAILABLE -> neither possible -> canonical NotSupported.

Tier-1 enforcement here: WIP limits + the flow engine (core/flow.py — per-card
schemes, free-roam, audited force). Tier-2 overlay: comments/relations. Still to
come: ARCHIVE flag polyfill, write-through encoding, reconciliation GC for
out-of-band backend deletes, flow hooks/validators.
"""

from __future__ import annotations

from typing import Protocol, runtime_checkable

from kanban_pro.core.flow import (
    FLOW_EXT_KEY,
    INLINE,
    SCHEME_EXT_KEY,
    FlowConfig,
    NativeTransitions,
    Resolution,
    TransitionInfo,
    TransitionOption,
    free_roam,
    parse_inline_flow,
)
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
        flows: FlowConfig | None = None,
    ) -> None:
        self._adapter = adapter
        self._overlay = overlay
        self.flows = flows
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
            elif cap is Capability.WORKFLOW and self.flows is not None:
                out[cap] = Fulfilment.POLYFILLED  # Tier-1: the flow engine (flow.yaml)
            elif cap in _OVERLAY_CAPS and self._overlay is not None:
                out[cap] = Fulfilment.POLYFILLED
            else:
                out[cap] = Fulfilment.UNAVAILABLE
        return out

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

    # --- Tier-1 flow enforcement (flow engine — core/flow.py; ruled 2026-07-05) ---

    async def _resolve_flow(self, card: Card) -> Resolution:
        ext = card.ext if isinstance(card.ext, dict) else {}
        scheme_raw = ext.get(SCHEME_EXT_KEY)
        scheme = str(scheme_raw) if scheme_raw is not None else None
        # chain rule 0: an inline one-card flow wins over everything (explicit rules
        # travelling ON the card); malformed -> fall back below, flagged.
        inline_raw = ext.get(FLOW_EXT_KEY)
        if inline_raw is not None:
            inline = parse_inline_flow(inline_raw)
            if inline is not None:
                return Resolution(requested=INLINE, resolved=INLINE, flow=inline)
            fallback = self.flows.resolve(scheme) if self.flows else free_roam(scheme)
            return fallback.model_copy(update={"fell_back": True})
        if self.flows is None:
            return free_roam(scheme)  # chain rule 1: no config -> unrestricted
        return self.flows.resolve(scheme)

    async def _check_flow(self, card_id: str, to_board_id: str, to_column_id: str) -> None:
        """Validate a move against the card's scheme. Skips: backend-native WORKFLOW
        (trust it), free-roam, unmodeled endpoints (chain rule 4), repositioning.
        Inline card flows are enforced even when no flow.yaml is configured."""
        if Capability.WORKFLOW in self._adapter.capabilities:
            return
        card = await self._adapter.get_card(card_id)
        resolution = await self._resolve_flow(card)
        flow = resolution.flow
        if flow is None:  # free-roam
            return
        placement = next((p for p in card.placements if p.board_id == to_board_id), None)
        if placement is None:
            return  # the adapter's strict-move NotFound handles this (Q16)
        columns = {c.id: c.name.lower() for c in await self._adapter.list_columns(to_board_id)}
        current = columns.get(placement.column_id)
        target = columns.get(to_column_id)
        if current is None or target is None:
            return
        if current not in flow.states or target not in flow.states:
            return  # a scheme governs only the states it declares
        if current == target:
            return  # repositioning within a column is not a transition
        if not flow.permits(current, target):
            allowed = ", ".join(flow.allowed.get(current, [])) or "none"
            raise Conflict(
                f"scheme {resolution.resolved!r} does not allow {current} -> {target}"
                f" (allowed from {current}: {allowed}); use force=true to override"
            )

    async def transitions(self, card_id: str, board_id: str | None = None) -> TransitionInfo:
        """What moves are legal for this card, and under which resolved scheme."""
        card = await self._adapter.get_card(card_id)
        if board_id is None:
            if not card.placements:
                raise Conflict(f"card {card_id!r} has no placement — nothing to move")
            board_id = card.placements[0].board_id
        placement = next((p for p in card.placements if p.board_id == board_id), None)
        columns = await self._adapter.list_columns(board_id)
        names = {c.id: c.name.lower() for c in columns}
        current_id = placement.column_id if placement else None
        requested = card.ext.get(SCHEME_EXT_KEY) if isinstance(card.ext, dict) else None
        scheme = str(requested) if requested is not None else None

        def options(predicate_names: set[str] | None) -> list[TransitionOption]:
            return [
                TransitionOption(column_id=c.id, name=c.name)
                for c in columns
                if c.id != current_id
                and (predicate_names is None or c.name.lower() in predicate_names)
            ]

        if isinstance(self._adapter, NativeTransitions):
            lanes = {name.lower() for name in await self._adapter.list_transitions(card_id)}
            return TransitionInfo(
                card_id=card_id, board_id=board_id, current_column_id=current_id,
                scheme=scheme, resolved_scheme=None, source="backend",
                options=options(lanes), note="workflow enforced by the backend",
            )  # fmt: skip
        resolution = await self._resolve_flow(card)
        if resolution.flow is None:
            return TransitionInfo(
                card_id=card_id, board_id=board_id, current_column_id=current_id,
                scheme=scheme, resolved_scheme=resolution.resolved,
                source="free-roam" if self.flows is not None else "free",
                options=options(None),
            )  # fmt: skip
        flow = resolution.flow
        source = INLINE if resolution.resolved == INLINE else "flow"
        current_name = names.get(current_id) if current_id else None
        note = (
            "scheme/flow fallback applied — requested definition unknown or malformed"
            if resolution.fell_back
            else None
        )
        if current_name is None or current_name not in flow.states:
            return TransitionInfo(
                card_id=card_id, board_id=board_id, current_column_id=current_id,
                scheme=scheme, resolved_scheme=resolution.resolved, source=source,
                options=options(None),
                note=(note or "") + " (current column not modeled by the scheme — free)",
            )  # fmt: skip
        return TransitionInfo(
            card_id=card_id, board_id=board_id, current_column_id=current_id,
            scheme=scheme, resolved_scheme=resolution.resolved, source=source,
            options=options(set(flow.allowed.get(current_name, []))), note=note,
        )  # fmt: skip

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
        await self._adapter.delete_column(column_id)

    # --- cards: delegate + WIP checks on column entry ---

    async def list_cards(self, board_id: str, include_archived: bool = False) -> list[Card]:
        return await self._adapter.list_cards(board_id, include_archived)

    async def get_card(self, card_id: str) -> Card:
        return await self._adapter.get_card(card_id)

    async def create_card(self, card: Card) -> Card:
        for placement in card.placements:
            await self._check_wip(placement.board_id, placement.column_id, card.id)
        return await self._adapter.create_card(card)

    async def update_card(self, card_id: str, patch: CardPatch) -> Card:
        return await self._adapter.update_card(card_id, patch)

    async def archive_card(self, card_id: str) -> Card:
        return await self._adapter.archive_card(card_id)

    async def unarchive_card(self, card_id: str) -> Card:
        return await self._adapter.unarchive_card(card_id)

    async def delete_card(self, card_id: str) -> None:
        await self._adapter.delete_card(card_id)
        if self._overlay is not None:
            # GC polyfilled rows keyed to the purged card (comments/relations cascade).
            await self._overlay.delete_card(card_id)

    async def move_card(
        self,
        card_id: str,
        to_board_id: str,
        to_column_id: str,
        position: int,
        *,
        force: bool = False,
    ) -> Card:
        # force (Jan): a deliberate override skips flow + WIP validation. It is never
        # silent — the recording layer flags the event, so a forced move is auditable.
        if not force:
            await self._check_flow(card_id, to_board_id, to_column_id)
            await self._check_wip(to_board_id, to_column_id, card_id)
        return await self._adapter.move_card(card_id, to_board_id, to_column_id, position)

    async def add_placement(self, card_id: str, placement: Placement) -> Card:
        await self._check_wip(placement.board_id, placement.column_id, card_id)
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
