"""Profile-based backend selection.

A *profile* bundles a chosen adapter with its settings. The active profile is picked at
startup via `--profile` (CLI) or `KANBAN_PRO_PROFILE` (env); kanban-pro exposes the full
canonical surface regardless of profile — `capabilities` reports how each capability is
fulfilled (SPEC decisions 2 & 3).

v0: a registry of the two store adapters. Profile *files* (non-secret settings) and
env-held secrets arrive with the first remote adapter (SPEC decision 3).
"""

from __future__ import annotations

import os
from collections.abc import Awaitable, Callable
from pathlib import Path

from kanban_pro.adapters.hermes import HermesAdapter
from kanban_pro.adapters.memory import MemoryBackend
from kanban_pro.adapters.native import NativeStore
from kanban_pro.core import (
    AugmentingBackend,
    ChangeLog,
    ClaimStore,
    DedupeStore,
    RecordingBackend,
)
from kanban_pro.ports import KanbanBackend

PROFILE_ENV = "KANBAN_PRO_PROFILE"
DB_ENV = "KANBAN_PRO_DB"
ACTOR_ENV = "KANBAN_PRO_ACTOR"


def _data_dir() -> Path:
    data_home = Path(os.environ.get("XDG_DATA_HOME", str(Path.home() / ".local" / "share")))
    return data_home / "kanban-pro"


def default_db_path() -> Path:
    """Native-store location: $KANBAN_PRO_DB, else XDG data dir."""
    if env := os.environ.get(DB_ENV):
        return Path(env)
    return _data_dir() / "kanban.db"


def changelog_path(profile: str) -> Path | None:
    """Per-profile change-log db; None = in-memory (ephemeral memory profile)."""
    if profile == "memory":
        return None
    path = _data_dir() / f"changelog-{profile}.db"
    path.parent.mkdir(parents=True, exist_ok=True)
    return path


def claims_path(profile: str) -> Path | None:
    """Per-profile claim/lease db; None = in-memory (ephemeral memory profile)."""
    if profile == "memory":
        return None
    path = _data_dir() / f"claims-{profile}.db"
    path.parent.mkdir(parents=True, exist_ok=True)
    return path


def dedupe_path(profile: str) -> Path | None:
    """Per-profile idempotency cache db; None = in-memory (memory profile)."""
    if profile == "memory":
        return None
    path = _data_dir() / f"dedupe-{profile}.db"
    path.parent.mkdir(parents=True, exist_ok=True)
    return path


async def _open_native() -> KanbanBackend:
    path = default_db_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    return await NativeStore.open(path)


async def _open_memory() -> KanbanBackend:
    return MemoryBackend()


async def _open_hermes() -> KanbanBackend:
    return HermesAdapter()  # ~/.hermes; SQLite reads + `hermes kanban` CLI writes


#: name -> async factory (docs/adapter-structure.md "Registration & selection").
REGISTRY: dict[str, Callable[[], Awaitable[KanbanBackend]]] = {
    "default": _open_native,  # the native store IS the default profile
    "native": _open_native,
    "memory": _open_memory,  # ephemeral — tests / scratch boards
    "hermes": _open_hermes,  # the Hermes multi-agent board (thin remote adapter)
}


async def build_backend(profile: str | None = None, actor: str | None = None) -> RecordingBackend:
    """Resolve the active profile (arg > env > 'default') and build the core stack:

        RecordingBackend(AugmentingBackend(adapter), changelog, actor)

    — augmenting = delegate/polyfill/enforce (decision 2); recording = every write
    stamped into the per-profile change-log with the actor (decisions 9 & 10).
    Store profiles need no overlay (full-capability); remote profiles will pass a
    NativeStore overlay when they need Tier-2 polyfills.
    """
    name = profile or os.environ.get(PROFILE_ENV) or "default"
    try:
        factory = REGISTRY[name]
    except KeyError:
        known = ", ".join(sorted(REGISTRY))
        raise ValueError(f"unknown profile {name!r} (known: {known})") from None
    resolved_actor = actor or os.environ.get(ACTOR_ENV) or "unknown"
    # workflow lives on the board (board.flow), administered over MCP — no config file.
    return RecordingBackend(
        AugmentingBackend(await factory()),
        ChangeLog(changelog_path(name)),
        resolved_actor,
        claims=ClaimStore(claims_path(name)),
        dedupe=DedupeStore(dedupe_path(name)),
    )
