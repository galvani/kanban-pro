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
async def create_board(board: Board) -> Board:
    """Create a board. Omit `id` to have one generated; columns/labels may be inlined."""
    return await _call((await _get_backend()).create_board(board))


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
async def create_column(board_id: str, column: Column) -> Column:
    """Add a column to a board. `category` gives it portable semantics (e.g. 'done')."""
    return await _call((await _get_backend()).create_column(board_id, column))


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
async def create_card(card: Card) -> Card:
    """Create a card. `placements` must have >=1 entry (board_id, column_id, position)."""
    return await _call((await _get_backend()).create_card(card))


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
async def add_comment(comment: Comment) -> Comment:
    """Add a comment to a card (`card_id`, `author` = User id, `body`)."""
    return await _call((await _get_backend()).add_comment(comment))


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
async def add_relation(relation: Relation) -> Relation:
    """Link two cards with a typed relation. Subtask = kind 'child' from parent card."""
    return await _call((await _get_backend()).add_relation(relation))


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


# --- resources ---


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
