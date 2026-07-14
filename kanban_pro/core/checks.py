"""Checks — the card's verification contract, and the only card state that gates the flow.

DECLARED by whoever specifies the work (`declare_checks`); RESOLVED, with evidence, by whoever
does it (`record_check_result`). The split is the whole design: **the party being gated does not
decide what it is gated on**, and that is enforced, not asked for — `_refuse_self_service` refuses
the claim holder, and `augment` refuses the move at a gated lane.

Not a Checklist: a checklist is a plain list and gates nothing.

Why it exists (AIR-2915) and what an adversarial review broke in the first cut: JOURNAL 2026-07-14.
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping, Sequence
from typing import Any

from kanban_pro.core.changelog import ChangeEvent
from kanban_pro.core.verification import (
    CHECK_GATE_EXT_KEY,
    SATISFIED,
    blocking_checks,
    check_gated_columns,
)
from kanban_pro.domain import Card, CardPatch, Check, CheckStatus
from kanban_pro.ports import (
    Capability,
    Conflict,
    KanbanBackend,
    NotFound,
    NotSupported,
    Unauthorized,
)

from .actor_policy import unwrap
from .recording import RecordingBackend

#: Re-exported so callers have ONE import site for "checks" — the rules are defined in
#: core/verification.py (a leaf, below the layer stack) purely to keep augment's enforcement
#: out of an import cycle. See that module.
__all__ = [
    "CHECK_GATE_EXT_KEY",
    "RECORDABLE",
    "SATISFIED",
    "blocking_checks",
    "check_gated_columns",
    "declare_checks",
    "record_check_result",
    "retract_check",
]

#: Statuses a worker may record. PENDING is the declared-but-untouched default and is not
#: something you "achieve" — re-pending a check would erase a result, so it is not recordable.
RECORDABLE = frozenset(CheckStatus) - {CheckStatus.PENDING}


def _require_checks_capability(backend: KanbanBackend) -> None:
    """Refuse loudly on a backend that cannot store checks, instead of gating on nothing.

    `Capability.CHECKS` was declared and never consulted — so on a profile whose adapter cannot
    persist `card.checks` (hermes: its `update_card` takes `assignees` and drops the rest), every
    `declare_checks` would have appeared to succeed, stored nothing, and left every gated lane
    waving cards through. A verification system that silently becomes a no-op is worse than none:
    it is the same false confidence, now with an audit trail. (Adversarial review, 2026-07-14.)
    """
    if Capability.CHECKS not in backend.capabilities:
        raise NotSupported(
            "this backend cannot store card checks — the verification contract would be silently "
            "dropped, and every check-gated lane would wave cards through. Use a backend that "
            "declares Capability.CHECKS (native/memory)."
        )


async def _refuse_self_service(backend: KanbanBackend, card_id: str, op: str) -> None:
    """The actor WORKING a card may not write that card's contract. The invariant, enforced.

    Without this the split is a convention, and a convention is what this module exists to replace.
    An adversarial review (2026-07-14) walked straight through the earlier version: a builder
    holding the card `retract_check`ed the browser check it was about to fail, `declare_checks`ed a
    `required: false` replacement, and moved into the gated lane — no `force=true`, no flag, nothing
    to see. Both are tools every worker already has.

    The defence this module originally offered was "the change-log names the actor forever". That is
    the SAME argument — a rule an agent can talk itself out of, deterred only after the fact — that
    `augment._check_verified` cites as the reason the dispatcher's prompt-level gate failed. It
    cannot be wrong there and sufficient here.

    So: whoever holds the claim is, by definition, the party being gated. They may record results
    (that is their job) and nothing else. Declaring and retracting belong to whoever specified the
    work — a party that is not holding the lease while the work runs.
    """
    rec = unwrap(backend, RecordingBackend)
    if rec is None or rec.claims is None:
        return  # no claim bookkeeping in this stack (bare adapter, tests) — nothing to police
    claim = await rec.claims.get(card_id)
    if claim is None or claim.expired:
        return
    if claim.owner == rec.actor:
        raise Unauthorized(
            f"{rec.actor} holds the claim on card {card_id!r} and may not {op} its checks — you do "
            "not get to write the contract you are being judged against. Record outcomes with "
            "record_check_result (that IS your job); if the contract itself is wrong, say so with "
            "raise_attention and let whoever specified the work decide."
        )


def _key(raw: str) -> str:
    key = (raw or "").strip()
    if not key:
        raise Conflict("a check needs a non-empty key")
    return key


def _find(card: Card, key: str) -> Check | None:
    return next((c for c in card.checks if c.key == key), None)


async def _save(backend: KanbanBackend, card_id: str, checks: Sequence[Check]) -> Card:
    return await backend.update_card(card_id, CardPatch(checks=list(checks)))


async def declare_checks(
    backend: KanbanBackend,
    card_id: str,
    specs: Iterable[Mapping[str, object]],
    *,
    idempotency_key: str | None = None,
) -> Card:
    """Declare (upsert by `key`) what this card must verify. Results already recorded SURVIVE.

    Re-declaring is safe and idempotent by design — prepare may refine the contract while work is
    in flight, and a redeclaration must not silently reset a check somebody already ran green.
    Only the declared fields (`text`, `required`, `checklist_item_id`) are overwritten;
    `status` and `evidence` are carried over from the existing check with the same key.

    To *remove* a check, call `retract_check` — an explicit, audited act. There is deliberately no
    "replace the whole list" here: that is the operation that would let a caller quietly drop the
    check it is about to fail.
    """
    _require_checks_capability(backend)
    await _refuse_self_service(backend, card_id, "declare")
    rec = unwrap(backend, RecordingBackend)
    if rec and idempotency_key:
        if hit := await rec.dedupe.get("check", idempotency_key):
            return Card.model_validate_json(hit)

    card = await backend.get_card(card_id)
    checks = list(card.checks)
    declared: list[str] = []

    for spec in specs:
        if not isinstance(spec, Mapping):
            raise Conflict("each check spec must be an object")
        key = _key(str(spec.get("key", "")))
        text = str(spec.get("text", "") or "").strip()
        if not text:
            raise Conflict(f"check {key!r} needs `text` — what must be verified, in words")
        item_id = spec.get("checklist_item_id")
        fields: dict[str, Any] = {
            "key": key,
            "text": text,
            "required": bool(spec.get("required", True)),
            "checklist_item_id": str(item_id) if item_id else None,
        }
        if (existing := _find(card, key)) is not None:
            # carry the result forward — a redeclaration is not a reset
            checks[checks.index(existing)] = existing.model_copy(update=fields)
        else:
            checks.append(Check(**fields))
        declared.append(key)

    if not declared:
        raise Conflict("declare_checks needs at least one check")

    updated = await _save(backend, card_id, checks)
    if rec:
        await rec.changelog.append(
            ChangeEvent(
                actor=rec.actor,
                entity="check",
                entity_id=card_id,
                op="declared",
                data={"card_id": card_id, "keys": declared},
            )
        )
        if idempotency_key:
            await rec.dedupe.put("check", idempotency_key, updated.model_dump_json())
    return updated


async def record_check_result(
    backend: KanbanBackend,
    card_id: str,
    key: str,
    status: CheckStatus | str,
    evidence: str,
    *,
    idempotency_key: str | None = None,
) -> Card:
    """Record what happened when a DECLARED check was run. Cannot declare, rename or drop one.

    Three refusals, each of them a hole somebody has already climbed through:

    - **Unknown key** → the check must have been declared. Otherwise a worker facing a red
      `browser-verify` can simply record a green `ssr-render` and call the card checked; the new
      key satisfies nothing, but a gate counting green checks would never notice (AIR-2915).
    - **No evidence** → a status with nothing behind it is a claim. `passed`/`failed` must show the
      command and what came back; `skipped`/`blocked` must say why, because that reason is the
      thing an engineer acts on.
    - **`pending`** → not recordable. It is the absence of a result, and un-recording a result is
      not something a worker gets to do.

    `blocked` is the honest exit when the environment is dead: it records the attempt, keeps the
    check unsatisfied, and leaves the card gated — which is what makes ASKING cheaper than
    laundering "I couldn't run it" into "it works".
    """
    _require_checks_capability(backend)
    rec = unwrap(backend, RecordingBackend)
    if rec and idempotency_key:
        if hit := await rec.dedupe.get("check", idempotency_key):
            return Card.model_validate_json(hit)

    key = _key(key)
    try:
        state = CheckStatus(status)
    except ValueError:
        expected = sorted(s.value for s in RECORDABLE)
        raise Conflict(f"unknown check status {status!r}; expected one of {expected}") from None
    if state not in RECORDABLE:
        raise Conflict(
            f"{state.value!r} is not a result — it is the absence of one. Record "
            f"{sorted(s.value for s in RECORDABLE)}, or leave the check alone."
        )
    # every recordable status needs evidence: passed/failed must show the receipt, skipped/blocked
    # must give the reason — which is what the engineer actually acts on.
    if not (evidence or "").strip():
        raise Conflict(
            f"check {key!r}: `{state.value}` needs evidence — the command and its observed output, "
            "or (for skipped/blocked) the reason nobody could run it. A status with nothing behind "
            "it is a claim, and the next reader cannot tell it from a verified one."
        )

    card = await backend.get_card(card_id)
    existing = _find(card, key)
    if existing is None:
        declared = ", ".join(sorted(c.key for c in card.checks)) or "none"
        raise NotFound(
            f"card {card_id!r} has no check {key!r} — you may only record a result against a check "
            f"someone declared. Declared here: {declared}. If the contract is wrong, say so "
            "(raise_attention); recording a different check does not satisfy this one."
        )

    checks = list(card.checks)
    checks[checks.index(existing)] = existing.model_copy(
        update={"status": state, "evidence": evidence.strip()}
    )
    updated = await _save(backend, card_id, checks)

    if rec:
        await rec.changelog.append(
            ChangeEvent(
                actor=rec.actor,
                entity="check",
                entity_id=card_id,
                op="resolved",
                data={"card_id": card_id, "key": key, "status": state.value},
            )
        )
        if idempotency_key:
            await rec.dedupe.put("check", idempotency_key, updated.model_dump_json())
    return updated


async def retract_check(backend: KanbanBackend, card_id: str, key: str, reason: str) -> Card:
    """Remove a declared check. Explicit, reasoned, and audited — never a side effect of a write.

    Deleting a gate is exactly the act that must be visible, so it gets its own event and demands
    a reason. It is not a worker's move: the same argument that says a worker may not declare its
    own checks says it may not un-declare the awkward ones. kanban-pro does not police WHO calls
    this — the change-log names the actor forever, and that is the deterrent that survives an
    agent talking itself into anything.
    """
    _require_checks_capability(backend)
    await _refuse_self_service(backend, card_id, "retract")
    key = _key(key)
    if not (reason or "").strip():
        raise Conflict("retract_check needs a reason — dropping a gate is not a silent act")

    card = await backend.get_card(card_id)
    existing = _find(card, key)
    if existing is None:
        raise NotFound(f"card {card_id!r} has no check {key!r}")

    checks = [c for c in card.checks if c.key != key]
    updated = await _save(backend, card_id, checks)

    if rec := unwrap(backend, RecordingBackend):
        await rec.changelog.append(
            ChangeEvent(
                actor=rec.actor,
                entity="check",
                entity_id=card_id,
                op="retracted",
                data={
                    "card_id": card_id,
                    "key": key,
                    "reason": reason.strip(),
                    "status": existing.status.value,
                },
            )
        )
    return updated
