"""The flow engine: per-board workflow, stored on the board (`board.flow`).

A board's workflow is a transition state-machine over its OWN column IDs
(`domain.BoardFlow`) — administered through MCP (`set_flow` / `set_transitions` /
`clear_flow`), never a config file. Edges reference the same board's columns, so a flow
can never dangle. This module holds the *reading* side (resolve a card's applicable flow,
answer "what moves are legal"); the board data itself lives in `domain.BoardFlow`.

Resolution chain for a card (ruled 2026-07-10):
1. `ext["kanban_pro.scheme"] == "free-roam"`  -> the card is unrestricted (per-card escape).
2. `ext["kanban_pro.flow"]` (inline one-card flow, name-based) -> wins; malformed falls
   through to the board flow, flagged.
3. the board's own `board.flow` -> enforced by column ID.
4. no board flow (absent/empty) -> free movement.

A column that appears in no edge of the board flow is *unmodeled*: moves in and out of it
stay free (a flow governs only the columns it names).
"""

from __future__ import annotations

import logging
from typing import Protocol, runtime_checkable

from pydantic import BaseModel, ConfigDict, Field

logger = logging.getLogger("kanban_pro.flow")

FREE_ROAM = "free-roam"
SCHEME_EXT_KEY = "kanban_pro.scheme"
FLOW_EXT_KEY = "kanban_pro.flow"  # inline ONE-CARD flow definition {states, transitions}
INLINE = "inline"
BOARD = "board"  # the resolved-flow name when the board's own flow applies


def modeled_columns(transitions: dict[str, list[str]]) -> set[str]:
    """Every column a board flow *names* — as an edge source or target. A column outside
    this set is unmodeled and moves in/out of it are free (a flow governs only its own)."""
    cols: set[str] = set(transitions)
    for targets in transitions.values():
        cols.update(targets)
    return cols


# --- inline per-card flow (name-based; a card carries its own mini state-machine) -------
# Rare escape hatch that travels ON a card via ext["kanban_pro.flow"]; matched by column
# NAME because it may apply across boards with different column ids. The board flow (the
# normal path) is ID-based and needs none of this.


class _TransitionRule(BaseModel):
    model_config = ConfigDict(populate_by_name=True, extra="allow")
    from_: str = Field(alias="from")
    to: str | list[str]


class _FlowSpec(BaseModel):
    model_config = ConfigDict(extra="allow")
    states: list[str]
    transitions: list[_TransitionRule] = Field(default_factory=list)


class Flow(BaseModel):
    """A validated inline flow: states + allowed from->to edges (all lowercase names)."""

    name: str
    states: list[str]
    allowed: dict[str, list[str]]  # from-state -> to-states

    def permits(self, from_state: str, to_state: str) -> bool:
        return to_state in self.allowed.get(from_state, [])


def _build_flow(name: str, spec: _FlowSpec) -> Flow:
    """Validate one inline flow spec into a Flow. Raises ValueError on dangling refs."""
    states = [s.lower() for s in spec.states]
    allowed: dict[str, list[str]] = {}
    for rule in spec.transitions:
        source = rule.from_.lower()
        targets = [t.lower() for t in (rule.to if isinstance(rule.to, list) else [rule.to])]
        for endpoint in [source, *targets]:
            if endpoint not in states:
                raise ValueError(
                    f"flow {name!r}: transition references undeclared state {endpoint!r}"
                )
        allowed.setdefault(source, []).extend(
            t for t in targets if t not in allowed.get(source, [])
        )
    return Flow(name=name, states=states, allowed=allowed)


def parse_inline_flow(raw: object) -> Flow | None:
    """Validate a card's inline flow (ext["kanban_pro.flow"]).

    Returns None when malformed — the caller falls back to the board flow with a loud
    warning (never freeze a card on a bad definition).
    """
    try:
        return _build_flow(INLINE, _FlowSpec.model_validate(raw))
    except (ValueError, TypeError) as exc:  # pydantic ValidationError subclasses ValueError
        logger.warning("malformed inline flow ignored: %s", exc)
        return None


# --- resolution + transitions query ----------------------------------------------------


class Resolution(BaseModel):
    """Which flow applies to a card. `resolved` is FREE_ROAM | INLINE | BOARD; the board
    flow itself is read from the board when `resolved == BOARD`."""

    resolved: str
    fell_back: bool = False
    inline_flow: Flow | None = None  # set only when resolved == INLINE


class TransitionOption(BaseModel):
    column_id: str
    name: str


class TransitionInfo(BaseModel):
    """Answer to "what moves are legal from here?" (list_transitions tool)."""

    card_id: str
    board_id: str
    current_column_id: str | None
    scheme: str | None  # what the card requested (ext), if anything
    resolved_scheme: str | None  # what actually applied (None = backend-native rules)
    source: str  # "flow" | "free-roam" | "backend" | "free" | "inline"
    options: list[TransitionOption]
    note: str | None = None


@runtime_checkable
class NativeTransitions(Protocol):
    """Optional adapter hook: backends with their OWN workflow expose the legal target
    column NAMES for a card (e.g. hermes: ready/blocked/done)."""

    async def list_transitions(self, card_id: str) -> list[str]: ...
