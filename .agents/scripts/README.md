# One-off scripts (git-ignored)

Throwaway repairs/backfills. Check here BEFORE writing a new one — the last person may have
already solved it.

- `migrate_plan_shape.py` (2026-07-14) — normalise `work_report.plan` to the schema shape
  (`[{id, text, status}]`) on live cards. Workers filed two other shapes: one item with a nested
  `steps[]` (rendered as a single unchecked box saying "0/1 done", with the real steps buried), and
  items keyed `what` instead of `text` (rendered as the item's id). Dry-run by default; `--apply`
  writes through `record_work_report` (never a raw ext write) and backs the originals up to
  `plan-migration-backup-*.json`. Terminal lanes are skipped on purpose — rewriting finished work
  to match a newer schema loses the record of what was actually filed.
