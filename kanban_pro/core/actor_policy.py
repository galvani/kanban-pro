"""ActorPolicyBackend — refuse writes from an actor nobody can be held to.

The change-log's whole value is that every write names who made it (SPEC decision 10). An
`unknown` actor silently destroys that: the event is still recorded, so the board LOOKS
audited, but the row is unattributable and no one notices until they try to reconstruct what
happened. (2026-07-13: a Claude Code connection with no `--actor` flag wrote to Jan's live
board for a whole session and every one of its events landed as `actor: unknown`.)

So writes from an anonymous actor are REFUSED by default. A board may opt out
(`ext["anonymous_writes"] = "allow"`) — a personal single-user board has nobody to attribute
to and the ceremony buys it nothing. Refusing is the default because the failure it prevents
is silent and permanent, while the failure IT causes is loud and instantly fixable: the
message says exactly which flag to pass.

Outermost decorator in the core stack (config.build_backend):

    ActorPolicyBackend(RecordingBackend(AugmentingBackend(adapter), …), actor)

Default-deny by design: every attribute not in `_READS` is treated as a write and guarded, so
a new write method added later is covered without anyone remembering to add it here.
"""

from __future__ import annotations

import re
from typing import Any

from kanban_pro.domain import Board
from kanban_pro.ports import Conflict

#: ext key by which a board opts out of the identified-actor requirement
ANONYMOUS_WRITES_EXT_KEY = "anonymous_writes"

#: the actor convention (MCP `--actor kind:name`): a kind, a colon, a name — both non-empty
_ACTOR_RE = re.compile(r"^[a-z][a-z0-9_-]*:.+$", re.IGNORECASE)

#: everything else is a write. Named reads only — see the module docstring on default-deny.
_READS = frozenset(
    {
        "list_boards",
        "get_board",
        "list_columns",
        "list_cards",
        "get_card",
        "list_comments",
        "list_relations",
        "list_work",
        "transitions",
        "fulfilments",
        "heartbeat_claim",  # renews an existing lease; it asserts no new fact about the world
    }
)


def is_anonymous(actor: str | None) -> bool:
    """True if `actor` names nobody: absent, blank, the `unknown` fallback, or off-convention.

    Off-convention counts as anonymous on purpose. A bare `reviewer` is not an identity — it
    does not say whether a human or an agent did the thing, which is the first question anyone
    asks of a change-log row.
    """
    if actor is None or not actor.strip():
        return True
    if actor.strip().lower() == "unknown":
        return True
    return not _ACTOR_RE.match(actor.strip())


def anonymous_writes_allowed(board: Board) -> bool:
    """True if this board accepts writes from an unidentified actor (opt-in, per board)."""
    return (board.ext or {}).get(ANONYMOUS_WRITES_EXT_KEY) == "allow"


def unwrap(backend: Any, cls: type | tuple[type, ...]) -> Any | None:
    """The first `cls` layer inside `backend`, unwrapping decorators via `_inner`.

    Callers used to ask `isinstance(be, RecordingBackend)` to mean "does this backend have the
    core stack (change-log, claims, dedupe, actor)?". That test silently became False the moment
    ANY decorator went outside RecordingBackend — and a false answer doesn't raise, it just
    quietly disables the change-log, `force` moves and idempotency. Ask structurally instead.
    """
    seen = 0
    while backend is not None and seen < 8:  # bounded: a cycle would hang, not error
        if isinstance(backend, cls):
            return backend
        backend = getattr(backend, "_inner", None)
        seen += 1
    return None


class ActorPolicyBackend:
    """KanbanBackend decorator: delegate everything, refuse writes from an anonymous actor."""

    def __init__(self, inner: Any, actor: str | None) -> None:
        self._inner = inner
        self._actor = actor
        self.actor = actor
        self.capabilities = inner.capabilities
        self.changelog = getattr(inner, "changelog", None)
        self.claims = getattr(inner, "claims", None)
        self.dedupe = getattr(inner, "dedupe", None)

    def __getattr__(self, name: str) -> Any:
        attr = getattr(self._inner, name)
        if name.startswith("_") or name in _READS or not callable(attr):
            return attr
        return self._guarded(name, attr)

    def _guarded(self, name: str, attr: Any) -> Any:
        async def call(*args: Any, **kwargs: Any) -> Any:
            if is_anonymous(self._actor):
                await self._refuse(name)
            return await attr(*args, **kwargs)

        return call

    async def _refuse(self, name: str) -> None:
        """Refuse — unless every board in play has opted into anonymous writes.

        Resolved per call rather than cached: a board's policy is administered over MCP and
        can change under a long-lived connection.
        """
        boards = await self._inner.list_boards()
        if boards and all(anonymous_writes_allowed(b) for b in boards):
            return
        raise Conflict(
            f"refusing {name!r}: this connection has no identity (actor={self._actor!r}), so the "
            f"write could not be attributed to anyone in the change-log — which is the one thing "
            f"the log is for. Start the MCP server with `--actor kind:name` (e.g. "
            f"`--actor agent:claude-code`, `--actor human:jan`) or set $KANBAN_PRO_ACTOR. "
            f"To let a board accept unattributed writes anyway, set its "
            f'ext["{ANONYMOUS_WRITES_EXT_KEY}"] = "allow" (update_board) — reads are never '
            f"affected either way."
        )
