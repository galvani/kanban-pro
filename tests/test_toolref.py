"""Drift guard: the example skills' tool-reference blocks must match the live MCP
surface. Fails after any tool change until `uv run python -m tests.toolref --write`."""

from __future__ import annotations

from tests.toolref import SKILL_FILES, apply, render_block


def test_example_skill_tool_references_in_sync() -> None:
    block = render_block()
    for path in SKILL_FILES:
        text = path.read_text()
        assert apply(text, block) == text, (
            f"{path.name} tool reference drifted from the MCP surface —"
            " run: uv run python -m tests.toolref --write"
        )
