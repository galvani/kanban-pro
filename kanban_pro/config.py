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

from kanban_pro.adapters.memory import MemoryBackend
from kanban_pro.adapters.native import NativeStore
from kanban_pro.ports import KanbanBackend

PROFILE_ENV = "KANBAN_PRO_PROFILE"
DB_ENV = "KANBAN_PRO_DB"


def default_db_path() -> Path:
    """Native-store location: $KANBAN_PRO_DB, else XDG data dir."""
    if env := os.environ.get(DB_ENV):
        return Path(env)
    data_home = Path(os.environ.get("XDG_DATA_HOME", str(Path.home() / ".local" / "share")))
    return data_home / "kanban-pro" / "kanban.db"


async def _open_native() -> KanbanBackend:
    path = default_db_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    return await NativeStore.open(path)


async def _open_memory() -> KanbanBackend:
    return MemoryBackend()


#: name -> async factory (docs/adapter-structure.md "Registration & selection").
REGISTRY: dict[str, Callable[[], Awaitable[KanbanBackend]]] = {
    "default": _open_native,  # the native store IS the default profile
    "native": _open_native,
    "memory": _open_memory,  # ephemeral — tests / scratch boards
}


async def build_backend(profile: str | None = None) -> KanbanBackend:
    """Resolve the active profile (arg > env > 'default') and build its adapter."""
    name = profile or os.environ.get(PROFILE_ENV) or "default"
    try:
        factory = REGISTRY[name]
    except KeyError:
        known = ", ".join(sorted(REGISTRY))
        raise ValueError(f"unknown profile {name!r} (known: {known})") from None
    return await factory()
