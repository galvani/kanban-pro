"""MCP server — kanban-pro's PRIMARY interface (SPEC decision 5).

Every canonical operation is an MCP tool; the active profile's capability/fulfilment
set is an MCP resource (`kanban://capabilities`) — that's how a harness learns what
this kanban can do. Tool schemas are generated from the domain models, so the port,
the docs, and the MCP surface cannot drift.

Dispatch goes through the augmenting layer (`config.build_backend` wraps the adapter
in core's AugmentingBackend) and core guards. Idempotency-key dedupe (decision 8) and
event notifications (decision 9) land at v1/v2.

Runs on stdio: `kanban-pro-mcp [--profile NAME]` or `python -m kanban_pro.mcp`.
Logs go to stderr (stdout is the JSON-RPC channel — never print to it).
"""

from __future__ import annotations

import argparse
import json
import logging
from collections.abc import Awaitable
from pathlib import Path

from mcp.server.fastmcp import FastMCP
from mcp.server.fastmcp.exceptions import ToolError
from mcp.types import ToolAnnotations

from kanban_pro import core
from kanban_pro.config import ACTOR_ENV, PROFILE_ENV, build_backend
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
from kanban_pro.ports import KanbanBackend, KanbanError

logger = logging.getLogger("kanban_pro.mcp")

mcp = FastMCP("kanban_pro")

# Annotation presets (harness UX hints).
_RO = ToolAnnotations(readOnlyHint=True)
_WRITE = ToolAnnotations(readOnlyHint=False, destructiveHint=False)
_IDEMPOTENT = ToolAnnotations(readOnlyHint=False, destructiveHint=False, idempotentHint=True)
_DESTRUCTIVE = ToolAnnotations(readOnlyHint=False, destructiveHint=True)

_backend: KanbanBackend | None = None
_profile: str | None = None
_actor: str | None = None


def configure(
    backend: KanbanBackend | None, profile: str | None = None, actor: str | None = None
) -> None:
    """Bind the backend tools dispatch to (startup wiring + tests)."""
    global _backend, _profile, _actor
    _backend, _profile, _actor = backend, profile, actor


async def _get_backend() -> KanbanBackend:
    global _backend
    if _backend is None:
        _backend = await build_backend(_profile, _actor)
        logger.info(
            "backend built: profile=%s actor=%s", _profile or "default", _actor or "unknown"
        )
    return _backend


async def _call[T](op: Awaitable[T]) -> T:
    """Await a port call, surfacing canonical errors as taxonomy-coded tool errors."""
    try:
        return await op
    except KanbanError as e:
        logger.warning("%s: %s", e.code, e)
        raise ToolError(f"{e.code}: {e}") from e


# --- boards ---


@mcp.tool(annotations=_RO)
async def list_boards() -> list[Board]:
    """List all boards."""
    return await _call((await _get_backend()).list_boards())


@mcp.tool(annotations=_RO)
async def get_board(board_id: str) -> Board:
    """Get one board (includes its columns and label registry)."""
    return await _call((await _get_backend()).get_board(board_id))


@mcp.tool(annotations=_WRITE)
async def create_board(board: Board, idempotency_key: str | None = None) -> Board:
    """Create a board. Omit `id` to have one generated; columns/labels may be inlined.

    Send an idempotency_key (any unique string, REUSED on retry) so a retried call
    returns the original board instead of creating a duplicate."""
    backend = await _get_backend()
    if idempotency_key and isinstance(backend, core.RecordingBackend):
        return await _call(backend.create_board(board, idempotency_key=idempotency_key))
    return await _call(backend.create_board(board))


@mcp.tool(annotations=_IDEMPOTENT)
async def update_board(board_id: str, patch: BoardPatch) -> Board:
    """Partially update a board — only the fields set in `patch` are applied."""
    return await _call((await _get_backend()).update_board(board_id, patch))


@mcp.tool(annotations=_DESTRUCTIVE)
async def delete_board(board_id: str) -> str:
    """Delete a board permanently. Refused while live cards remain — move/archive first."""
    await _call(core.delete_board_guarded(await _get_backend(), board_id))
    return f"board {board_id} deleted"


# --- columns ---


@mcp.tool(annotations=_RO)
async def list_columns(board_id: str) -> list[Column]:
    """List a board's columns (name, order, semantic category, wip_limit)."""
    return await _call((await _get_backend()).list_columns(board_id))


@mcp.tool(annotations=_WRITE)
async def create_column(
    board_id: str, column: Column, idempotency_key: str | None = None
) -> Column:
    """Add a column to a board. `category` gives it portable semantics (e.g. 'done').
    idempotency_key (reused on retry) prevents duplicate creation."""
    backend = await _get_backend()
    if idempotency_key and isinstance(backend, core.RecordingBackend):
        return await _call(backend.create_column(board_id, column, idempotency_key=idempotency_key))
    return await _call(backend.create_column(board_id, column))


@mcp.tool(annotations=_IDEMPOTENT)
async def update_column(column_id: str, patch: ColumnPatch) -> Column:
    """Partially update a column (rename, reorder via `order`, set `wip_limit`...)."""
    return await _call((await _get_backend()).update_column(column_id, patch))


@mcp.tool(annotations=_DESTRUCTIVE)
async def delete_column(column_id: str) -> str:
    """Delete a column permanently. Refused while live cards sit in it — move/archive first."""
    await _call(core.delete_column_guarded(await _get_backend(), column_id))
    return f"column {column_id} deleted"


# --- cards ---


@mcp.tool(annotations=_RO)
async def list_cards(board_id: str, include_archived: bool = False) -> list[Card]:
    """List a board's cards. Archived cards are hidden unless include_archived=true
    (that's how you find unarchive/purge targets)."""
    return await _call((await _get_backend()).list_cards(board_id, include_archived))


@mcp.tool(annotations=_RO)
async def get_card(card_id: str) -> Card:
    """Get one card (works for archived cards too)."""
    return await _call((await _get_backend()).get_card(card_id))


@mcp.tool(annotations=_WRITE)
async def create_card(card: Card, idempotency_key: str | None = None) -> Card:
    """Create a card. `placements` must have >=1 entry (board_id, column_id, position).
    Send an idempotency_key (any unique string, REUSED on retry) so retries return the
    original card instead of duplicating it."""
    backend = await _get_backend()
    if idempotency_key and isinstance(backend, core.RecordingBackend):
        return await _call(backend.create_card(card, idempotency_key=idempotency_key))
    return await _call(backend.create_card(card))


@mcp.tool(annotations=_IDEMPOTENT)
async def update_card(card_id: str, patch: CardPatch) -> Card:
    """Partially update a card — only the fields set in `patch` are applied."""
    return await _call((await _get_backend()).update_card(card_id, patch))


@mcp.tool(annotations=_IDEMPOTENT)
async def move_card(
    card_id: str,
    to_board_id: str,
    to_column_id: str,
    position: int = 0,
    force: bool = False,
) -> Card:
    """Move a card within a board it's already on (re-column / re-position).

    Errors if the card has no placement on to_board_id (use add_placement), or if the
    card's workflow scheme forbids the transition — check list_transitions first.
    force=true deliberately overrides scheme + WIP validation; the override is always
    recorded in the change-log, never silent.
    """
    backend = await _get_backend()
    if force and isinstance(backend, core.RecordingBackend | core.AugmentingBackend):
        return await _call(
            backend.move_card(card_id, to_board_id, to_column_id, position, force=True)
        )
    return await _call(backend.move_card(card_id, to_board_id, to_column_id, position))


@mcp.tool(annotations=_RO)
async def list_transitions(card_id: str, board_id: str | None = None) -> core.TransitionInfo:
    """What moves are legal for this card right now, and under which resolved scheme.

    Sources: the card's flow scheme (flow.yaml; 'free-roam' = unrestricted), the
    backend's own workflow (e.g. hermes), or free movement when nothing is configured.
    """
    backend = await _get_backend()
    if not isinstance(backend, core.RecordingBackend | core.AugmentingBackend):
        raise ToolError("not_supported: transitions query needs the core stack")
    return await _call(backend.transitions(card_id, board_id))


@mcp.tool(annotations=_RO)
async def list_flows() -> dict[str, object]:
    """Available workflow schemes: every flow.yaml scheme (+ built-in 'free-roam'),
    with states, allowed transitions, and which scheme is the default."""
    backend = await _get_backend()
    flows = (
        backend.flows
        if isinstance(backend, core.RecordingBackend | core.AugmentingBackend)
        else None
    )
    payload: dict[str, object] = {
        "builtin": {core.FREE_ROAM: "unrestricted transitions (reserved scheme)"},
        "scheme_ext_key": core.SCHEME_EXT_KEY,
    }
    if flows is None:
        payload["configured"] = None
        payload["note"] = "no flow.yaml configured — every card behaves as free-roam"
    else:
        payload["default"] = flows.default
        payload["configured"] = {
            name: {"states": f.states, "transitions": f.allowed} for name, f in flows.flows.items()
        }
    return payload


@mcp.tool(annotations=_WRITE)
async def add_placement(card_id: str, placement: Placement) -> Card:
    """Put a card on an additional board (one placement per board; errors if already on it)."""
    return await _call((await _get_backend()).add_placement(card_id, placement))


@mcp.tool(annotations=_WRITE)
async def remove_placement(card_id: str, board_id: str) -> Card:
    """Take a card off one board (its other placements stay). The last placement can't
    be removed — archive_card instead."""
    return await _call((await _get_backend()).remove_placement(card_id, board_id))


@mcp.tool(annotations=_IDEMPOTENT)
async def archive_card(card_id: str) -> Card:
    """Archive a card (soft, recoverable — the default way to remove one)."""
    return await _call((await _get_backend()).archive_card(card_id))


@mcp.tool(annotations=_IDEMPOTENT)
async def unarchive_card(card_id: str) -> Card:
    """Restore an archived card."""
    return await _call((await _get_backend()).unarchive_card(card_id))


@mcp.tool(annotations=_DESTRUCTIVE)
async def delete_card(card_id: str) -> str:
    """Permanently purge a card. Only allowed on an ARCHIVED card — archive_card first."""
    await _call(core.delete_card_guarded(await _get_backend(), card_id))
    return f"card {card_id} deleted"


# --- comments ---


@mcp.tool(annotations=_RO)
async def list_comments(card_id: str) -> list[Comment]:
    """List a card's comments."""
    return await _call((await _get_backend()).list_comments(card_id))


@mcp.tool(annotations=_WRITE)
async def add_comment(comment: Comment, idempotency_key: str | None = None) -> Comment:
    """Add a comment to a card (`card_id`, `author` = User id, `body`).
    idempotency_key (reused on retry) prevents duplicate comments."""
    backend = await _get_backend()
    if idempotency_key and isinstance(backend, core.RecordingBackend):
        return await _call(backend.add_comment(comment, idempotency_key=idempotency_key))
    return await _call(backend.add_comment(comment))


@mcp.tool(annotations=_DESTRUCTIVE)
async def delete_comment(comment_id: str) -> str:
    """Delete a comment permanently."""
    await _call((await _get_backend()).delete_comment(comment_id))
    return f"comment {comment_id} deleted"


# --- relations ---


@mcp.tool(annotations=_RO)
async def list_relations(card_id: str) -> list[Relation]:
    """List a card's typed relations (blocks, parent/child, duplicates, ...)."""
    return await _call((await _get_backend()).list_relations(card_id))


@mcp.tool(annotations=_WRITE)
async def add_relation(relation: Relation, idempotency_key: str | None = None) -> Relation:
    """Link two cards with a typed relation. Subtask = kind 'child' from parent card.
    idempotency_key (reused on retry) prevents duplicate relations."""
    backend = await _get_backend()
    if idempotency_key and isinstance(backend, core.RecordingBackend):
        return await _call(backend.add_relation(relation, idempotency_key=idempotency_key))
    return await _call(backend.add_relation(relation))


@mcp.tool(annotations=_DESTRUCTIVE)
async def delete_relation(relation_id: str) -> str:
    """Delete a relation permanently."""
    await _call((await _get_backend()).delete_relation(relation_id))
    return f"relation {relation_id} deleted"


# --- work distribution: the agent loop (claim -> work -> release) ---


def _recording(backend: KanbanBackend) -> core.RecordingBackend:
    if not isinstance(backend, core.RecordingBackend):
        raise ToolError("not_supported: work distribution needs the core stack")
    return backend


@mcp.tool(annotations=_RO)
async def list_work(assignee: str | None = None, include_unassigned: bool = True) -> core.WorkQueue:
    """What should I work on? Workable cards for `assignee` (default: YOU, this
    connection's actor) — assigned to you or unassigned, in backlog/ready/started
    columns, cards leased to others excluded. Each item carries its legal transitions,
    so one call gives you the whole plan."""
    return await _call(_recording(await _get_backend()).list_work(assignee, include_unassigned))


@mcp.tool(annotations=_WRITE)
async def claim_card(card_id: str, ttl_seconds: int = 900) -> core.Claim:
    """Atomically lease a card so no other agent picks it up (visible in list_work).
    The lease expires after ttl_seconds unless renewed via heartbeat_claim — a crashed
    agent's card becomes claimable again automatically. Convention: after claiming,
    assign yourself and move the card to a started column."""
    return await _call(_recording(await _get_backend()).claim_card(card_id, ttl_seconds))


@mcp.tool(annotations=_WRITE)
async def heartbeat_claim(card_id: str, ttl_seconds: int = 900) -> core.Claim:
    """Renew your live lease on a card while still working it."""
    return await _call(_recording(await _get_backend()).heartbeat_claim(card_id, ttl_seconds))


@mcp.tool(annotations=_WRITE)
async def release_claim(card_id: str) -> str:
    """Release your lease (done or giving up). Idempotent."""
    await _call(_recording(await _get_backend()).release_claim(card_id))
    return f"claim on {card_id} released"


# --- attention signal: "this card needs a decision/input" ---


@mcp.tool(annotations=_WRITE)
async def raise_attention(card_id: str, reason: str, for_actor: str | None = None) -> Card:
    """Flag a card as needing a decision or input (e.g. a question only a human or a
    specific agent can answer). Routable: the change-log event carries the reason and
    the target actor, so notifier agents DM the right party. Put the actual question
    in a comment; this flag is the signal, not the discussion."""
    backend = await _get_backend()
    return await _call(_recording(backend).raise_attention(card_id, reason, for_actor))


@mcp.tool(annotations=_WRITE)
async def clear_attention(card_id: str, resolution: str | None = None) -> Card:
    """Clear a card's attention flag (question answered / decision made). Put the
    answer in a comment; `resolution` is a one-liner for the event stream."""
    backend = await _get_backend()
    return await _call(_recording(backend).clear_attention(card_id, resolution))


# --- change feed (decision 9 pull surface; WS/MCP push land with the UI) ---


@mcp.tool(annotations=_RO)
async def list_changes(since: int = 0, limit: int = 100) -> list[core.ChangeEvent]:
    """Change feed: every recorded write after cursor `since` (audit trail + sync).

    Each event carries seq (the cursor — pass the last seq back as `since`), ts,
    actor (who did it), entity/op (e.g. card.moved), and a slim data payload.
    """
    backend = await _get_backend()
    if not isinstance(backend, core.RecordingBackend):
        raise ToolError("not_supported: change-log is not wired for this backend")
    return await backend.changelog.since(since, min(max(limit, 1), 500))


@mcp.tool(annotations=_RO)
async def wait_changes(
    since: int = -1, timeout_seconds: int = 25, limit: int = 100
) -> core.WaitResult:
    """Long-poll change feed: returns AS SOON AS events exist after cursor `since`
    (instant for writes through this server; other processes within ~2s), or empty
    after timeout_seconds. `since=-1` probes the current cursor without replaying
    history — call that once, then loop with the returned cursor. Push semantics
    without polling loops."""
    backend = await _get_backend()
    if not isinstance(backend, core.RecordingBackend):
        raise ToolError("not_supported: change-log is not wired for this backend")
    return await backend.changelog.wait_since(
        since, float(min(max(timeout_seconds, 0), 60)), min(max(limit, 1), 500)
    )


# --- resources ---


@mcp.resource("kanban://event-schema")
async def event_schema_resource() -> str:
    """The full contract for consuming the change-feed — event shapes, data keys,
    attention routing, and the notifier pattern.  Call this once, then loop
    ``wait_changes`` / ``list_changes`` with confidence.
    """
    return json.dumps(
        {
            "WaitResult": {
                "cursor": "int — latest seq; pass back as `since` on the next call",
                "events": "list[ChangeEvent] — empty on timeout (no activity)",
            },
            "ChangeEvent": {
                "seq": "int — feed cursor, monotonically increasing",
                "ts": "iso8601 — when the write happened",
                "actor": "str — convention kind:name (agent:prepare, human:jan, …)",
                "entity": "str — board | column | card | comment | relation | attention",
                "entity_id": "str — the affected resource (task id, comment id, …)",
                "op": "str — created | updated | moved | archived | unarchived | deleted"
                " | added | removed | raised | cleared",
                "board_id": "str|null — board slug when the event is board-scoped",
                "data": "{…} — slim payload; key fields per (entity, op) below",
            },
            "event_kinds": {
                "card.created": {"data_keys": ["title", "column_id", "assignees"]},
                "card.moved": {"data_keys": ["column_id", "forced (bool)"]},
                "card.archived": {"data_keys": []},
                "card.unarchived": {"data_keys": []},
                "comment.added": {"data_keys": ["card_id", "author"]},
                "relation.added": {"data_keys": ["kind", "from_card", "to_card"]},
                "attention.raised": {
                    "data_keys": [
                        "reason",
                        "for (str|null — target actor; null = anyone, human:jan = Jan's DM)",
                    ],
                    "effect": "sets ext['kanban_pro.attention'] on the card;"
                    " notifier filters on data.for",
                },
                "attention.cleared": {
                    "data_keys": ["resolution (str|null)"],
                    "effect": "removes ext['kanban_pro.attention'] from the card",
                },
            },
            "notifier_pattern": {
                "goal": "DM Jan when a worker needs his input or the board changes"
                " in a way he cares about.",
                "loop": "wait_changes(since=cursor) → filter events → DM Slack → save cursor",
                "filter": {
                    "jan_relevant": [
                        "card.created — new task on the board",
                        "card.moved — lane transition (especially → done)",
                        "card.archived",
                        "attention.raised where data.for == 'human:jan'",
                    ],
                    "ignore": [
                        "actor starts with 'migration:'",
                        "comments, relations, claims, updates",
                        "attention.raised where data.for != 'human:jan'",
                    ],
                },
                "cursor_storage": "file: .kanban-notifier-cursor.json → {'cursor': <int>}",
                "first_run": "wait_changes(since=-1) probes the head, saves cursor,"
                " NO DMs (baseline)",
            },
        },
        indent=2,
    )


@mcp.resource("kanban://capabilities")
async def capabilities_resource() -> str:
    """Active profile's capabilities, each with its fulfilment (SPEC decision 2):
    native (backend does it), polyfilled (kanban-pro fulfils it), unavailable."""
    backend = await _get_backend()
    caps = {
        cap.name.lower(): fulfilment.name.lower()
        for cap, fulfilment in core.fulfilments(backend).items()
    }
    return json.dumps({"profile": _profile or "default", "capabilities": caps}, indent=2)


@mcp.resource("kanban://boards")
async def boards_resource() -> str:
    """All boards as canonical JSON."""
    boards = await (await _get_backend()).list_boards()
    return json.dumps([b.model_dump(mode="json") for b in boards], indent=2)


@mcp.resource("kanban://board/{board_id}")
async def board_resource(board_id: str) -> str:
    """One board as canonical JSON."""
    board = await (await _get_backend()).get_board(board_id)
    return board.model_dump_json(indent=2)


@mcp.resource("kanban://card/{card_id}")
async def card_resource(card_id: str) -> str:
    """One card as canonical JSON."""
    card = await (await _get_backend()).get_card(card_id)
    return card.model_dump_json(indent=2)


# ── Documentation resources (self-describing contract) ──


@mcp.resource("kanban://work-distribution")
async def work_distribution_resource() -> str:
    """The work-distribution contract: how agents claim/lease/work/release cards.

    ``list_work`` returns a ``WorkQueue {actor, items: [WorkItem, ...]}``.
    Each ``WorkItem`` has the full ``Card``, the column it's in (id/name/category),
    whether YOU currently hold the lease (``claimed_by_me``), and the legal transitions
    from this column (``transitions`` — see ``kanban://workflow``).

    The life of a claim:
      1. ``claim_card(card_id, ttl_seconds=900)`` — CAS lease.  Fails with ``conflict``
         if another agent holds it.
      2. ``heartbeat_claim(card_id, ttl_seconds)`` — keep the lease alive while working.
      3. When the worker finishes: move the card + ``release_claim(card_id)``.
      4. On crash / timeout: the lease expires, the card is reclaimable.
         The dispatcher / next agent sees it in ``list_work`` again.

    Claiming does NOT move or assign the card — convention: claim → assign yourself →
    move to a started column, all recorded in the change-log.
    """
    return json.dumps(
        {
            "WorkQueue": {
                "actor": "str — whose work queue this is",
                "items": "list[WorkItem] — cards I can work (unstarted/started, unexpired only)",
            },
            "WorkItem": {
                "card": "Card — the full card object",
                "board_id": "str",
                "column_id": "str",
                "column_name": "str — human-readable",
                "column_category": "str — triage|backlog|unstarted|started|done|canceled",
                "claimed_by_me": "bool — do I already hold the lease?",
                "transitions": "TransitionInfo — legal moves from here (see kanban://workflow)",
            },
            "claim_card": {
                "args": {"card_id": "str", "ttl_seconds": "int (default 900)"},
                "returns": "Claim {card_id, owner, expires_at}",
                "errors": "conflict — already claimed by someone else",
            },
            "heartbeat_claim": {
                "args": {"card_id": "str", "ttl_seconds": "int"},
                "returns": "Claim — with updated expires_at",
            },
            "release_claim": {
                "args": {"card_id": "str"},
                "returns": "str — confirmation; idempotent",
            },
            "pattern": {
                "worker_loop": "list_work(assignee='agent:<me>') → claim_card"
                " → update_card(assignees) → move_card(started) → do work"
                " → move_card(done) → release_claim",
                "self_report": "workers move their own cards + comment results;"
                " the dispatcher only backstops crashes",
            },
        },
        indent=2,
    )


@mcp.resource("kanban://workflow")
async def workflow_resource() -> str:
    """The workflow contract: how to move cards and what's legal.

    ``list_transitions(card_id, board_id?)`` returns a ``TransitionInfo``:
    ``{options: [{column_id, name}, ...], resolved_scheme, source}``.

    Each ``column_id`` in ``options[]`` is a valid target for ``move_card``.
    The ``source`` field tells you where the rules came from:
      - ``flow`` — flow.yaml scheme (the default)
      - ``free-roam`` — the reserved scheme; any column is reachable
      - ``backend`` — the adapter's own lifecycle (e.g. Hermes ready/blocked/done)
      - ``free`` — current column not modeled by the scheme → free movement

    ``force=true`` on ``move_card`` overrides the scheme + WIP checks.  The override is
    ALWAYS recorded in the change-log — it's for unblocking, never routine.
    """
    return json.dumps(
        {
            "list_transitions": {
                "args": {
                    "card_id": "str",
                    "board_id": "str|null — default board if unset",
                },
                "returns": {
                    "card_id": "str",
                    "board_id": "str",
                    "current_column_id": "str|null",
                    "scheme": "str|null — the card's ext[kanban_pro.scheme], if set",
                    "resolved_scheme": "str|null — what actually applied"
                    " (free-roam = unrestricted)",
                    "source": "str — flow|free-roam|backend|free",
                    "options": "list[{column_id: str, name: str}] — valid targets for move_card",
                    "note": "str|null",
                },
            },
            "move_card": {
                "args": {
                    "card_id": "str",
                    "to_board_id": "str — must equal an existing placement's board_id",
                    "to_column_id": "str — must be in list_transitions().options or use force",
                    "position": "int (default 0)",
                    "force": "bool (default false) — override scheme + WIP"
                    " (recorded in change-log)",
                },
                "returns": "Card — with updated placement",
            },
            "flows": {
                "discovery": "list_flows() — all schemes, states, transitions, and the default",
                "scheme_selection": "card ext['kanban_pro.scheme'] = 'docs'|'free-roam'"
                " — per-card override; unset = config default",
            },
        },
        indent=2,
    )


@mcp.resource("kanban://domain")
async def domain_resource() -> str:
    """The canonical domain model: types, conventions, and ext namespaces.

    ``Card`` is the unit of work.  A card's ``placements[]`` (≥1) is where it lives —
    one placement per board.  ``ext`` is free-form metadata; these namespaces are reserved:

    | key | owner | meaning |
    |---|---|---|
    | ``kanban_pro.scheme`` | flow engine | workflow scheme name (see kanban://workflow) |
    | ``kanban_pro.attention`` | attention signal | ``{reason, raised_by, for}`` |
    | ``kanban_pro.migrated_from`` | migration | provenance ``\"<profile>/<board-id>\"`` |
    | ``hermes`` | hermes adapter | backend-specific fields |
    | ``work`` | kanban-dispatcher | ``{log, attempts, quota_hits, retry_at}`` |

    Patches (``CardPatch``, ``BoardPatch``, …) are PARTIAL UPDATES: only set fields
    apply.  ``ext`` is a SHALLOW MERGE: patch keys → stored dict; a key set to None
    is REMOVED.  This protects concurrent writers from clobbering each other's ext data.
    """
    return json.dumps(
        {
            "Card": {
                "id": "str — unique, server-assigned",
                "title": "str — required",
                "description": "str|null",
                "placements": "list[{board_id, column_id, position}] — ≥1 required on create",
                "assignees": "list[str] — user/profile ids (e.g. agent:prepare)",
                "labels": "list[str] — label ids (board-scoped)",
                "checklists": "list[{id, title, items: [{text, done}]}]",
                "attachments": "list[{url, title}] — link-only",
                "archived": "bool — soft-delete (archive first, then delete)",
                "created_at": "iso8601|null",
                "updated_at": "iso8601|null",
                "ext": "dict — free-form (see namespaces above)",
            },
            "CardPatch": {
                "title": "str|null — set to change, omit to leave untouched",
                "description": "str|null",
                "assignees": "list[str]|null — replace the entire list",
                "ext": "dict|null — SHALLOW MERGE (null = no-op, {'key': None} = remove key)",
            },
            "Comment": {
                "id": "str",
                "card_id": "str",
                "author": "str — user id (agent:prepare, human:jan, …)",
                "body": "str — markdown",
                "created_at": "iso8601|null",
            },
            "Relation": {
                "id": "str — '<from_card>-><to_card>'",
                "kind": "str — parent|child|blocks|blocked_by|relates|duplicates|precedes|follows",
                "from_card": "str — card id",
                "to_card": "str — card id",
            },
            "Column": {
                "id": "str — e.g. 'default:done'",
                "name": "str — human-readable",
                "category": "str — triage|backlog|unstarted|started|done|canceled",
                "wip_limit": "int|null",
                "order": "int — display order",
            },
            "Board": {
                "id": "str — slug (e.g. 'default')",
                "name": "str",
                "columns": "list[Column]",
                "labels": "list[{id, name, color}] — board-scoped registry",
            },
            "ext_namespaces": {
                "kanban_pro.*": "reserved for kanban-pro features — never set by app code",
                "hermes": "hermes adapter backend fields",
                "work": "kanban-dispatcher runtime state (log, attempts, quota_hits, retry_at)",
                "<backend>": "each adapter gets its own namespace",
            },
        },
        indent=2,
    )


def _launch_command() -> list[str]:
    """The spawn command a harness should use for this installation.

    Source checkout -> `uv run --directory <repo> kanban-pro-mcp`; installed package
    (uv tool / pipx) -> the console script is on PATH.
    """
    root = Path(__file__).resolve().parents[2]
    if (root / "pyproject.toml").exists():
        return ["uv", "run", "--directory", str(root), "kanban-pro-mcp"]
    return ["kanban-pro-mcp"]


def _print_config(harness: str) -> None:
    """Print the registration command/snippet for a harness. Touches no config files."""
    cmd = _launch_command()
    if harness == "claude":
        print("# Claude Code (-s user = all projects):")
        print("claude mcp add kanban-pro -s user -- " + " ".join(cmd))
    elif harness == "codex":
        print("# add to ~/.codex/config.toml:")
        print("[mcp_servers.kanban-pro]")
        print(f'command = "{cmd[0]}"')
        print("args = [" + ", ".join(f'"{a}"' for a in cmd[1:]) + "]")
    elif harness == "opencode":
        print('# merge into opencode.json under "mcp":')
        print(
            json.dumps({"kanban-pro": {"type": "local", "command": cmd, "enabled": True}}, indent=2)
        )
    else:  # hermes / any stdio-MCP harness: it just needs the spawn command
        print("# stdio MCP server spawn command:")
        print(json.dumps(cmd))


def main() -> None:
    """Entry point: parse args, then serve MCP over stdio."""
    parser = argparse.ArgumentParser(
        prog="kanban-pro-mcp",
        description="kanban-pro MCP server (stdio)",
    )
    parser.add_argument(
        "--profile",
        default=None,
        help=f"backend profile (default: ${PROFILE_ENV} or 'default' = native SQLite store)",
    )
    parser.add_argument(
        "--actor",
        default=None,
        help=(
            f"identity stamped on every write this connection makes (default: ${ACTOR_ENV}"
            " or 'unknown'); convention kind:name, e.g. agent:hermes-engineer, human:jan"
        ),
    )
    parser.add_argument(
        "--print-config",
        choices=["claude", "codex", "opencode", "hermes"],
        default=None,
        help="print the MCP registration snippet for a harness and exit",
    )
    parser.add_argument("-v", "--verbose", action="store_true", help="debug logging (stderr)")
    args = parser.parse_args()

    if args.print_config:
        _print_config(args.print_config)
        return

    # stderr only — stdout carries JSON-RPC.
    logging.basicConfig(
        level=logging.DEBUG if args.verbose else logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    )
    configure(None, args.profile, args.actor)
    logger.info(
        "kanban-pro MCP server starting (profile=%s, actor=%s)",
        args.profile or "default",
        args.actor or "unknown",
    )
    mcp.run()


__all__ = ["configure", "main", "mcp"]
