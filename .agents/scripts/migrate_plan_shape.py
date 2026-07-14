"""One-off: normalise `work_report.plan` on LIVE cards to the schema shape.

The schema is `plan: [{id, text, status: todo|doing|done|blocked}]` (core/work_report.py), but
workers filed two other shapes, and the UI could only render the schema one:

  A. one item holding the whole plan in a nested `steps[]`  -> rendered as ONE unchecked box
     reading "0/1 done", with the real steps buried inside it. The tally was a lie.
  B. items keyed `what` instead of `text`                   -> rendered as the item's id.

Both are rewritten to one item per step, with `text` and a `status`. Nested steps have no status
of their own, so they inherit the parent's (todo when it has none) — inventing per-step statuses
would be worse than admitting we don't know.

Scope: cards that are NOT archived and NOT in a terminal lane (done / won't do / waiting for mr /
staging). Finished work is left exactly as it was — rewriting history to match a newer schema buys
nothing and loses the record of what was actually filed.

Safety: dry-run by default (`--apply` to write). Every card's ORIGINAL work_report is written to
a backup JSON first, so a bad migration is one script away from being undone.

    uv run python .agents/scripts/migrate_plan_shape.py            # show what would change
    uv run python .agents/scripts/migrate_plan_shape.py --apply    # do it
"""

from __future__ import annotations

import asyncio
import json
import re
import sys
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from kanban_pro.config import build_backend
from kanban_pro.core.work_report import record_work_report

TERMINAL_LANES = {"done", "won't do", "wontdo", "waiting for mr", "mr", "staging", "canceled"}
BACKUP = Path(__file__).parent / f"plan-migration-backup-{datetime.now(UTC):%Y%m%dT%H%M%S}.json"

_LEADING_NUMBER = re.compile(r"^\s*\d+[.)]\s*")


def normalise(plan: list[Any]) -> list[dict[str, Any]] | None:
    """The schema shape, or None when the plan is already fine (nothing to do)."""
    out: list[dict[str, Any]] = []
    changed = False
    for i, raw in enumerate(plan):
        if not isinstance(raw, dict):  # a bare string in the list
            text = _LEADING_NUMBER.sub("", str(raw))
            out.append({"id": f"p{i + 1}", "text": text, "status": "todo"})
            changed = True
            continue
        steps = raw.get("steps")
        text = raw.get("text") or raw.get("what") or raw.get("summary") or ""
        status = raw.get("status") or "todo"
        if isinstance(steps, list) and steps:
            changed = True
            if text:  # a lead-in line before the steps: keep it as its own item
                out.append({"id": raw.get("id") or f"p{i + 1}", "text": text, "status": status})
            for j, s in enumerate(steps):
                s_text = s if isinstance(s, str) else (s.get("text") or s.get("what") or str(s))
                out.append(
                    {
                        "id": f"{raw.get('id') or 'p'}-{j + 1}",
                        "text": _LEADING_NUMBER.sub("", str(s_text)),
                        # a nested step carries no status — it inherits the parent's
                        "status": (s.get("status") if isinstance(s, dict) else None) or status,
                    }
                )
            continue
        if not raw.get("text") and text:  # keyed `what` instead of `text`
            changed = True
        if not raw.get("status"):
            changed = True
        out.append({**raw, "id": raw.get("id") or f"p{i + 1}", "text": text, "status": status})
    return out if changed else None


async def main(apply: bool) -> None:
    be = await build_backend("default", actor="agent:claude-code")
    board = (await be.list_boards())[0]
    lanes = {c.id: c.name.lower() for c in (await be.get_board(board.id)).columns}
    backups: dict[str, Any] = {}
    n = 0

    for card in await be.list_cards(board.id):
        if card.archived or lanes.get(card.placements[0].column_id, "") in TERMINAL_LANES:
            continue
        report = (card.ext or {}).get("work_report") or {}
        plan = report.get("plan")
        if not isinstance(plan, list) or not plan:
            continue
        fixed = normalise(plan)
        if fixed is None:
            continue

        n += 1
        print(f"\n{card.id[:8]}  {card.title[:60]}")
        print(f"   before: {len(plan)} item(s) -> after: {len(fixed)} step(s)")
        for item in fixed:
            print(f"     [{item['status']:7s}] {item['text'][:78]}")
        backups[card.id] = report

        if apply:
            # record_work_report, never a raw ext write: it upserts BY ITEM ID, stamps the format
            # version, and emits the work_report.updated event the change-log needs. A list section
            # has no "replace the whole list" op, so: drop the old ids, then upsert the new ones.
            old_ids = [i.get("id") for i in plan if isinstance(i, dict) and i.get("id")]
            new_ids = {i["id"] for i in fixed}
            for old in old_ids:
                if old not in new_ids:  # an id we are not reusing
                    await record_work_report(
                        be, card.id, section="plan", item={"id": old}, op="remove"
                    )
            for item in fixed:
                await record_work_report(be, card.id, section="plan", item=item, op="upsert")

    if apply and backups:
        BACKUP.write_text(json.dumps(backups, indent=1, ensure_ascii=False))
        print(f"\napplied to {n} card(s). Originals backed up to {BACKUP.name}")
    else:
        print(f"\n{n} card(s) would be rewritten. Re-run with --apply to do it.")


if __name__ == "__main__":
    asyncio.run(main("--apply" in sys.argv))
