"""Tests for the in-memory reference adapter — conformance + the shared contract."""

from __future__ import annotations

from kanban_pro.adapters.memory import MemoryBackend
from kanban_pro.ports import Capability, KanbanBackend
from tests.contract_suite import KanbanContract


def test_conforms_to_port() -> None:
    # Structural conformance check (mypy verifies MemoryBackend satisfies the Protocol).
    backend: KanbanBackend = MemoryBackend()
    assert Capability.ARCHIVE in backend.capabilities


class TestMemoryContract(KanbanContract):
    async def _backend(self) -> KanbanBackend:
        return MemoryBackend()
