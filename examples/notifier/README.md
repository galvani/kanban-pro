# kanban-notifier — example change-feed consumer

A standalone Python script that consumes the kanban-pro change-feed and DMs
Jan on Slack for board events he cares about.  Demonstrates three primitives:

1. **`wait_changes(since=cursor)`** — long-poll the feed (push, no polling diff)
2. **`raise_attention(card_id, reason, for_actor="human:jan")`** — workers signal
   "I need Jan's input"
3. **The notifier pattern** — filter events → DM Slack → save cursor

## What it replaces

Two old cron scripts that polled the Hermes kanban board:
- `lane-watch` — diffed board snapshots, DMed Jan on lane changes
- `context-watch` delivery path — commented on cards + separate DM path

Both are now one feed consumer.  Workers call `raise_attention`; this script
delivers.  No polling diff, no snapshot comparison.

## What Jan gets DMed

| Event | Slack |
|---|---|
| `card.created` | 🆕 *VLM-75: Review fixes*  (triage) |
| `card.moved` | 🔀 *VLM-75* → *ready*  (by engineer) |
| `card.moved` → done | ✅ *VLM-75* → *done*  (by builder) |
| `attention.raised` (for human:jan) | ❓ *VLM-75* — needs input: which date? |
| `card.archived` | 🗑️ *VLM-75*  (archived) |

Silent: comments, relations, claims, updates, migration noise.

## Running

Depends on `httpx`, `python-dotenv`, and the `mcp` SDK:

```bash
uv run python examples/notifier/kanban-notifier.py          # daemon (loops, wakes on activity)
uv run python examples/notifier/kanban-notifier.py --once   # one pass, then exit
uv run python examples/notifier/kanban-notifier.py --dry-run  # log DMs, don't send
```

Environment: reads `SLACK_BOT_TOKEN` and `SLACK_HOME_CHANNEL` from a `.env` file.

## Architecture

```
kanban-pro (MCP stdio)          notifier.py               Slack
─────────────────────           ───────────               ─────
wait_changes(since=cursor)  →   filter Jan events    →   DM Jan
                                save cursor               (chat.postMessage)
```

State: `$KANBAN_NOTIFIER_DIR/.kanban-notifier-cursor.json` (default
`~/.local/state/kanban-notifier`) — `{"cursor": <int>}`. The `.env` is read from
the same directory. First run: probes the feed head with `since=-1`, saves
cursor, no DMs (baseline).

## Integration

Use as a cron/systemd timer (`* * * * * kanban-notifier.py --once`) or a
long-running daemon.  The script handles `wait_changes` internally — the cron
just ensures it's always respawning.
