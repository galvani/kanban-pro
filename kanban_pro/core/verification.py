"""The verification rules — pure predicates over a Card/Board. No I/O, no backend, no layers.

A leaf, deliberately: the ENFORCEMENT lives in `AugmentingBackend.move_card` (beside flow and WIP),
while the WRITE path (`core/checks.py`) needs `RecordingBackend`, which wraps augment. Importing
one from the other would close a cycle. These rules need only the domain models, so they sit below
both — and "does this card block?" gets exactly ONE definition. It previously had three, two of
which contradicted the gate (JOURNAL 2026-07-14).
"""

from __future__ import annotations

from kanban_pro.domain import Board, Card, Check, CheckStatus

#: The only status that supports an advance.
SATISFIED = CheckStatus.PASSED

#: Board policy: which columns refuse a card whose required checks have not passed. Lives in
#: `board.ext` (as `auto_clear_attention_columns` does), NOT on the flow — the flow says which
#: moves EXIST, this says which of them must be earned.
CHECK_GATE_EXT_KEY = "kanban_pro.check_gated_columns"


def blocking_checks(card: Card) -> list[Check]:
    """Required checks that do not support an advance.

    A check blocks unless it is `passed` — `pending`, `skipped` ("chose not to"), `blocked`
    ("could not") and `failed` alike, because none of them is evidence the thing works.

    NOTE: an empty list does NOT mean verified — a card with no checks has nothing failing and
    nothing proven. Callers that mean "is this card proven?" must also require `card.checks`;
    the gate (`augment._refuse_unverified`) does, and says so with a different error.
    """
    return [c for c in card.checks if c.required and c.status is not SATISFIED]


def check_gated_columns(board: Board) -> list[str]:
    """Column ids that enforce the contract on entry. Empty ⇒ the gate is OFF (the default).

    Off by default so that adopting kanban-pro does not silently lock the lanes of a board whose
    cards declare no checks. Turn it on with `set_check_gate`.
    """
    raw = (board.ext or {}).get(CHECK_GATE_EXT_KEY)
    if not isinstance(raw, list):
        return []
    return [c for c in raw if isinstance(c, str)]
