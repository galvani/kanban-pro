"""Port-to-port migration — the replacement step (SPEC goal update, 2026-07-05).

`kanban-pro-migrate` copies boards, columns, cards (INCLUDING archived history),
comments, and relations from a source profile into a destination profile through the
canonical model — nothing backend-specific in the copy loop, so `hermes -> default`
today is `jira -> default` tomorrow.

Faithfulness rules:
- ids are preserved (hermes `t_xxx` stays the card id) -> re-runs are idempotent
  upserts, and cross-references (relations) survive. Comment ids get a
  `<board>:c<id>` prefix (backend comment ids are only unique per board).
- every imported card carries provenance: ext["kanban_pro.migrated_from"].
- positions are assigned from the source's listing order (per column, 0..n).
- writes go through the DESTINATION's full core stack, so the change-log records the
  import under the migration actor — history says where the data came from.

The source is read through its RAW adapter (reads only; no enforcement needed).
"""

from __future__ import annotations

import argparse
import asyncio
import logging

from pydantic import BaseModel

from kanban_pro.config import REGISTRY, build_backend
from kanban_pro.domain import Card, Comment, Relation
from kanban_pro.ports import KanbanBackend

logger = logging.getLogger("kanban_pro.migrate")

MIGRATED_FROM_KEY = "kanban_pro.migrated_from"


class MigrationReport(BaseModel):
    source: str
    dest: str
    dry_run: bool
    boards: int = 0
    cards: int = 0
    archived_cards: int = 0
    comments: int = 0
    relations: int = 0

    def summary(self) -> str:
        verb = "would migrate" if self.dry_run else "migrated"
        return (
            f"{verb} {self.boards} board(s), {self.cards} card(s)"
            f" ({self.archived_cards} archived), {self.comments} comment(s),"
            f" {self.relations} relation(s) from {self.source!r} to {self.dest!r}"
        )


def _with_provenance(card: Card, source: str, board_id: str, position: int) -> Card:
    placement = card.placements[0]
    return card.model_copy(
        update={
            "placements": [placement.model_copy(update={"position": position})],
            "ext": {**card.ext, MIGRATED_FROM_KEY: f"{source}/{board_id}"},
        }
    )


async def migrate(
    source: KanbanBackend,
    dest: KanbanBackend,
    *,
    source_name: str,
    dest_name: str,
    boards: list[str] | None = None,
    dry_run: bool = False,
) -> MigrationReport:
    """Copy everything canonical from `source` into `dest` (idempotent upserts)."""
    report = MigrationReport(source=source_name, dest=dest_name, dry_run=dry_run)
    all_boards = await source.list_boards()
    selected = [b for b in all_boards if boards is None or b.id in boards]
    if boards:
        missing = set(boards) - {b.id for b in selected}
        if missing:
            raise ValueError(f"source has no board(s): {', '.join(sorted(missing))}")

    for board in selected:
        report.boards += 1
        cards = await source.list_cards(board.id, include_archived=True)
        if not dry_run:
            await dest.create_board(board)  # upsert: columns (ids preserved) ride along

        # per-column position counters, in the source's listing order
        next_position: dict[str, int] = {}
        seen_relations: set[str] = set()
        for card in cards:
            report.cards += 1
            report.archived_cards += int(card.archived)
            column_id = card.placements[0].column_id
            position = next_position.get(column_id, 0)
            next_position[column_id] = position + 1

            comments = await source.list_comments(card.id)
            relations = [
                r for r in await source.list_relations(card.id) if r.id not in seen_relations
            ]
            seen_relations.update(r.id for r in relations)
            report.comments += len(comments)
            report.relations += len(relations)
            if dry_run:
                continue

            # overwrite: a re-run re-imports the source's current state onto the same ids.
            await dest.create_card(
                _with_provenance(card, source_name, board.id, position), overwrite=True
            )
            for comment in comments:
                await dest.add_comment(
                    Comment(
                        id=f"{board.id}:c{comment.id}",  # backend comment ids are per-board
                        card_id=comment.card_id,
                        author=comment.author,
                        body=comment.body,
                        created_at=comment.created_at,
                        ext=comment.ext,
                    )
                )
            for relation in relations:
                await dest.add_relation(
                    Relation(
                        id=relation.id,
                        kind=relation.kind,
                        from_card=relation.from_card,
                        to_card=relation.to_card,
                    )
                )
        logger.info("board %s: %d cards", board.id, len(cards))
    return report


def main() -> None:
    parser = argparse.ArgumentParser(
        prog="kanban-pro-migrate",
        description="Copy boards/cards/comments/relations between profiles via the"
        " canonical model (idempotent; archived history included).",
    )
    parser.add_argument("--source", default="hermes", help="source profile (default: hermes)")
    parser.add_argument(
        "--dest", default="default", help="destination profile (default: default = native)"
    )
    parser.add_argument(
        "--board",
        action="append",
        default=None,
        help="board id to migrate (repeatable; default: all)",
    )
    parser.add_argument("--dry-run", action="store_true", help="count, print, write nothing")
    parser.add_argument(
        "--actor",
        default="migration:hermes-import",
        help="actor stamped on the imported writes in the change-log",
    )
    args = parser.parse_args()
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(name)s: %(message)s")

    async def run() -> MigrationReport:
        if args.source == args.dest:
            raise ValueError("source and dest profiles must differ")
        source = await REGISTRY[args.source]()  # raw adapter: reads only
        dest = await build_backend(args.dest, args.actor)  # full stack: recorded writes
        return await migrate(
            source,
            dest,
            source_name=args.source,
            dest_name=args.dest,
            boards=args.board,
            dry_run=args.dry_run,
        )

    report = asyncio.run(run())
    print(report.summary())


__all__ = ["migrate", "MigrationReport", "MIGRATED_FROM_KEY", "main"]
