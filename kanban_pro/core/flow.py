"""The flow engine: named workflow schemes loaded from YAML (TODO "Flow management").

A *scheme* is a transition state-machine over column NAMES (case-insensitive) — column
ids are per-board, names are what flow authors write. A card picks its scheme via
`ext["kanban_pro.scheme"]`; unset inherits the config's default scheme.

Reserved scheme **"free-roam"**: built-in, always available, never definable in YAML —
unrestricted transitions for that card while the board default stays enforced (Jan).

Resolution chain (ruled 2026-07-05):
1. no flows configured        -> everything behaves as free-roam
2. card has no scheme         -> the config's default scheme
3. unknown scheme             -> default scheme + loud warning (never freeze the board)
4. unmodeled column endpoint  -> the move is free (a scheme governs only its own states)

`flow.yaml` fails fast at load on dangling references. `hooks`/`wip_limits` keys are
accepted-but-ignored for now (syntax reserved — see TODO).
"""

from __future__ import annotations

import logging
from pathlib import Path
from typing import Protocol, runtime_checkable

import yaml
from pydantic import BaseModel, ConfigDict, Field

logger = logging.getLogger("kanban_pro.flow")

FREE_ROAM = "free-roam"
SCHEME_EXT_KEY = "kanban_pro.scheme"
FLOW_EXT_KEY = "kanban_pro.flow"  # inline ONE-CARD flow definition {states, transitions}
INLINE = "inline"


class _TransitionRule(BaseModel):
    model_config = ConfigDict(populate_by_name=True, extra="allow")
    from_: str = Field(alias="from")
    to: str | list[str]


class _FlowSpec(BaseModel):
    model_config = ConfigDict(extra="allow")  # hooks/wip_limits reserved, ignored for now
    states: list[str]
    transitions: list[_TransitionRule] = Field(default_factory=list)
    auto_reset_attempts_on_reassign: bool = True


class _FlowsFile(BaseModel):
    model_config = ConfigDict(extra="allow")
    flows: dict[str, _FlowSpec]
    default_flow: str | None = None


class Flow(BaseModel):
    """One validated scheme: states + allowed from->to edges (all lowercase names)."""

    name: str
    states: list[str]
    allowed: dict[str, list[str]]  # from-state -> to-states
    auto_reset_attempts_on_reassign: bool = True

    def permits(self, from_state: str, to_state: str) -> bool:
        return to_state in self.allowed.get(from_state, [])


class Resolution(BaseModel):
    """Outcome of resolving a card's scheme. `flow=None` means unrestricted."""

    requested: str | None
    resolved: str  # scheme name actually applied ("free-roam" when unrestricted)
    fell_back: bool = False
    flow: Flow | None = None


class FlowConfig(BaseModel):
    flows: dict[str, Flow]
    default: str

    def resolve(self, requested: str | None) -> Resolution:
        if requested == FREE_ROAM:
            return Resolution(requested=requested, resolved=FREE_ROAM)
        name = requested or self.default
        if name in self.flows:
            return Resolution(requested=requested, resolved=name, flow=self.flows[name])
        logger.warning(
            "unknown scheme %r — falling back to default scheme %r", requested, self.default
        )
        return Resolution(
            requested=requested,
            resolved=self.default,
            fell_back=True,
            flow=self.flows[self.default],
        )


def free_roam(requested: str | None = None) -> Resolution:
    """The no-flows-configured resolution: everything is free-roam."""
    return Resolution(requested=requested, resolved=FREE_ROAM)


def _build_flow(name: str, spec: _FlowSpec) -> Flow:
    """Validate one flow spec into a Flow. Raises ValueError on dangling references."""
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
    return Flow(
        name=name, states=states, allowed=allowed,
        auto_reset_attempts_on_reassign=spec.auto_reset_attempts_on_reassign,
    )


def parse_inline_flow(raw: object) -> Flow | None:
    """Validate a card's inline flow (ext["kanban_pro.flow"]).

    Returns None when malformed — the caller falls back to the named/default scheme
    with a loud warning (resolution-chain rule-3 spirit: never freeze a card on a
    bad definition). Note: an inline flow is enforced even on profiles with NO
    flow.yaml — attaching one is an explicit request for rules; the WORKFLOW
    fulfilment still reflects only the profile config.
    """
    try:
        return _build_flow(INLINE, _FlowSpec.model_validate(raw))
    except (ValueError, TypeError) as exc:  # pydantic ValidationError subclasses ValueError
        logger.warning("malformed inline flow ignored: %s", exc)
        return None


def load_flows(path: str | Path) -> FlowConfig:
    """Parse + validate flow.yaml. Fails fast on any dangling reference (guardrail)."""
    raw = yaml.safe_load(Path(path).read_text())
    parsed = _FlowsFile.model_validate(raw)
    for reserved in (FREE_ROAM, INLINE):
        if reserved in parsed.flows:
            raise ValueError(f"{reserved!r} is a reserved scheme name — remove it from flows")
    flows = {name: _build_flow(name, spec) for name, spec in parsed.flows.items()}
    default = parsed.default_flow or "default"
    if default not in flows:
        raise ValueError(
            f"default scheme {default!r} is not defined (declare `default_flow` or a"
            " flow named 'default')"
        )
    return FlowConfig(flows=flows, default=default)


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
    source: str  # "flow" | "free-roam" | "backend" | "free"
    options: list[TransitionOption]
    note: str | None = None


@runtime_checkable
class NativeTransitions(Protocol):
    """Optional adapter hook: backends with their OWN workflow expose the legal target
    column NAMES for a card (e.g. hermes: ready/blocked/done)."""

    async def list_transitions(self, card_id: str) -> list[str]: ...
