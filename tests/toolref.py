"""Generated tool-reference blocks for the example skills.

The block between the markers in each example SKILL.md is rendered from the LIVE MCP
server (single source of truth: the tools themselves). `test_toolref.py` fails when a
committed block drifts from the surface; regenerate with:

    uv run python -m tests.toolref --write
"""

from __future__ import annotations

import asyncio
import sys
from pathlib import Path

from kanban_pro.mcp import mcp

BEGIN = "<!-- generated:tool-reference — regenerate: uv run python -m tests.toolref --write -->"
END = "<!-- /generated:tool-reference -->"

SKILL_FILES = [
    Path(__file__).parent.parent / "examples" / "skills" / name / "SKILL.md"
    for name in ("kanban-worker", "kanban-orchestrator", "kanban-retro")
]


def render_block() -> str:
    tools = asyncio.run(mcp.list_tools())
    lines = [BEGIN, ""]
    for tool in sorted(tools, key=lambda t: t.name):
        props: dict[str, object] = tool.inputSchema.get("properties", {})
        required = set(tool.inputSchema.get("required", []))
        sig = ", ".join(p if p in required else f"{p}?" for p in props)
        description = (tool.description or "").strip().splitlines()[0]
        lines.append(f"- `{tool.name}({sig})` — {description}")
    lines += ["", END]
    return "\n".join(lines)


def apply(text: str, block: str) -> str:
    """Replace the marker-delimited block in a skill file's text."""
    start, end = text.index(BEGIN), text.index(END) + len(END)
    return text[:start] + block + text[end:]


def main() -> None:
    write = "--write" in sys.argv
    block = render_block()
    for path in SKILL_FILES:
        updated = apply(path.read_text(), block)
        if write:
            path.write_text(updated)
            print(f"synced {path}")
        elif updated != path.read_text():
            print(f"DRIFT: {path} (run with --write)")
            sys.exit(1)
    if not write:
        print("tool references in sync")


if __name__ == "__main__":
    main()
