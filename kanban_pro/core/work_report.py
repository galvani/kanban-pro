"""Structured work-report helpers.

``ext.work_report`` is the current, concise task state for humans and workers.
The changelog is the audit trail; do not turn the report itself into an append-only
log.

**Versioning (``_v``).** ``ext`` is a bag with independent writers — kanban-pro owns
``work_report``/``kanban_pro.*``, adapters own their own namespace, the dispatcher owns
``work``. So a version cannot describe the whole blob; each structured namespace carries
its own, *inside itself*, so the version travels with the data when it is copied or
exported. Convention for every kanban-pro-owned namespace:

- ``_v`` is the format version. Underscore-prefixed keys are reserved metadata, never
  content (cf. the injected ``_claim``/``_last_comment``); a section may not be named
  with a leading underscore.
- **Missing ``_v`` means version 1** — the shape that existed before versioning. Readers
  migrate on read; writers stamp the current version.
- A report written by a NEWER version than this code understands is **refused for
  writing**, not silently rewritten: old code must not clobber a format it cannot
  represent. Reads still pass it through.
"""

from __future__ import annotations

from collections.abc import Mapping
from copy import deepcopy
from typing import Any

from kanban_pro.core.changelog import ChangeEvent
from kanban_pro.domain import Card, CardPatch, Comment
from kanban_pro.ports import Conflict, KanbanBackend

from .recording import RecordingBackend

WORK_REPORT_EXT_KEY = "work_report"

#: Format version stamped into every report we write. Bump ONLY together with a
#: migration step in `_migrate_report`.
WORK_REPORT_VERSION = 1
#: Reserved metadata key holding the format version (see module docstring).
VERSION_KEY = "_v"

LIST_SECTIONS = frozenset({"questions", "findings", "plan", "needs", "analysis_log", "checks"})
SINGLETON_SECTIONS = frozenset({"about", "handoff", "verdict"})
VALID_SECTIONS = LIST_SECTIONS | SINGLETON_SECTIONS
VALID_OPS = frozenset({"upsert", "replace", "remove"})

WORK_REPORT_SCHEMA: dict[str, object] = {
    "description": (
        "Current structured card state. History lives in the kanban-pro changelog via "
        "work_report.updated events."
    ),
    "version": WORK_REPORT_VERSION,
    "metadata_keys": {
        VERSION_KEY: (
            f"int — format version of this report (current: {WORK_REPORT_VERSION}); absent "
            "means 1. Underscore-prefixed keys are reserved metadata, never sections."
        )
    },
    "sections": {
        "about": "object|string — what this card is about; replace as current truth changes",
        "questions": (
            "list[{id, text, status: open|answered|canceled, asked_by?, context?, answer?, "
            "answered_by?, answered_at?}]"
        ),
        "findings": "list[{id, summary, severity?, evidence?, status?}]",
        "plan": "list[{id, text, status: todo|doing|done|blocked, owner?, evidence?}]",
        "needs": "list[{id, text, status: open|resolved, needed_from?, resolution?}]",
        "analysis_log": (
            "bounded list[{id, text, at?, actor?}] of meaningful milestones, not every edit"
        ),
        "checks": "list[{id, name, status, evidence?}] — reviewer/verification gates",
        "verdict": "object|string — current review/result verdict",
        "handoff": "object|string — artifacts, next actor, branch/worktree, final outcome",
    },
    "write_rules": [
        "Use record_work_report; never rewrite the whole ext.work_report blob by hand.",
        "List sections are upserted by stable item id.",
        "Singleton sections are replaced as current state.",
        "Every successful write emits a work_report.updated changelog event.",
        "Use raise_attention only as the signal; put actual questions in questions[].",
    ],
}


def _normalise_report(raw: object) -> dict[str, Any]:
    return deepcopy(raw) if isinstance(raw, dict) else {}


def report_version(report: Mapping[str, Any]) -> int:
    """The report's format version. Absent `_v` == 1 (the pre-versioning shape)."""
    raw = report.get(VERSION_KEY, 1)
    return raw if isinstance(raw, int) and raw >= 1 else 1


def _migrate_report(report: dict[str, Any]) -> dict[str, Any]:
    """Bring a stored report up to WORK_REPORT_VERSION, in place of the caller's copy.

    Only ever called on the write path: a newer report is refused rather than downgraded,
    because this code cannot represent a format it has never seen and would silently drop
    whatever it doesn't recognise.
    """
    version = report_version(report)
    if version > WORK_REPORT_VERSION:
        raise Conflict(
            f"work_report is format v{version}; this kanban-pro understands "
            f"v{WORK_REPORT_VERSION} — refusing to overwrite it. Upgrade kanban-pro."
        )
    # v1 is the current shape; future bumps chain their migrations here, e.g.
    #   if version < 2: report = _v1_to_v2(report); version = 2
    report[VERSION_KEY] = WORK_REPORT_VERSION
    return report


def _item_id(item: Mapping[str, object]) -> str:
    raw = item.get("id")
    if not isinstance(raw, str) or not raw.strip():
        raise Conflict("work_report list items require a non-empty string id")
    return raw.strip()


def _merge_section(
    report: dict[str, Any], section: str, op: str, item: Mapping[str, object]
) -> tuple[dict[str, Any], str | None]:
    if section.startswith("_"):
        raise Conflict(f"work_report section {section!r} is reserved (underscore = metadata)")
    if section not in VALID_SECTIONS:
        raise Conflict(f"unknown work_report section {section!r}")
    if op not in VALID_OPS:
        raise Conflict(f"unknown work_report op {op!r}; expected {sorted(VALID_OPS)}")

    merged = deepcopy(report)
    if section in SINGLETON_SECTIONS:
        if op == "remove":
            merged.pop(section, None)
        else:
            merged[section] = deepcopy(dict(item))
        return merged, None

    item_id = _item_id(item)
    current = merged.get(section)
    rows = deepcopy(current) if isinstance(current, list) else []
    idx = next(
        (i for i, row in enumerate(rows) if isinstance(row, dict) and row.get("id") == item_id),
        None,
    )
    if op == "remove":
        if idx is not None:
            rows.pop(idx)
    elif idx is None:
        rows.append(deepcopy(dict(item)))
    else:
        old = rows[idx] if isinstance(rows[idx], dict) else {}
        rows[idx] = {**old, **deepcopy(dict(item))}
    merged[section] = rows
    return merged, item_id


async def record_work_report(
    backend: KanbanBackend,
    card_id: str,
    section: str,
    item: Mapping[str, object],
    *,
    op: str = "upsert",
    idempotency_key: str | None = None,
) -> Card:
    """Update one work_report section/item and record a dedicated audit event."""
    if not isinstance(item, Mapping):
        raise Conflict("work_report item must be an object")
    if isinstance(backend, RecordingBackend) and idempotency_key:
        if hit := await backend.dedupe.get("work_report", idempotency_key):
            return Card.model_validate_json(hit)

    card = await backend.get_card(card_id)
    report = _migrate_report(_normalise_report(card.ext.get(WORK_REPORT_EXT_KEY)))
    updated_report, item_id = _merge_section(report, section, op, item)
    updated = await backend.update_card(
        card_id, CardPatch(ext={WORK_REPORT_EXT_KEY: updated_report})
    )

    if isinstance(backend, RecordingBackend):
        await backend.changelog.append(
            ChangeEvent(
                actor=backend.actor,
                entity="work_report",
                entity_id=card_id,
                op="updated",
                data={"card_id": card_id, "section": section, "op": op, "item_id": item_id},
            )
        )
        if idempotency_key:
            await backend.dedupe.put("work_report", idempotency_key, updated.model_dump_json())
    return updated


async def answer_work_report_question(
    backend: KanbanBackend,
    card_id: str,
    question_id: str,
    answer: str,
    *,
    author: str | None = None,
    answered_at: str | None = None,
) -> Card:
    """Answer one structured question and mirror the answer as a normal comment."""
    actor = author or (backend.actor if isinstance(backend, RecordingBackend) else "human:jan")
    card = await record_work_report(
        backend,
        card_id,
        "questions",
        {
            "id": question_id,
            "status": "answered",
            "answer": answer,
            "answered_by": actor,
            **({"answered_at": answered_at} if answered_at else {}),
        },
        op="upsert",
    )
    await backend.add_comment(
        Comment(card_id=card_id, author=actor, body=f"Answer to {question_id}: {answer}")
    )
    return card
