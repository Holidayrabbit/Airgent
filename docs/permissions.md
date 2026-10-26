# Permissions & Rewind

Two capabilities that mirror the mature coding agents (Claude Code, Codex CLI):
tiered tool permissions with interactive approval, and session checkpoints.

## Permission tiers

Off by default (`AIRGENT_PERMISSION_MODE` unset). Enable with:

```bash
export AIRGENT_PERMISSION_MODE=read-only        # plan-mode style
export AIRGENT_PERMISSION_MODE=workspace-write  # Codex's recommended tier
```

| Mode | Behavior |
|------|----------|
| `off` (default) | No checks — the plain pi wrapper |
| `read-only` | write/edit/bash are blocked outright |
| `workspace-write` | Edits confined to the workspace + `~/.airgent`; bash deny-patterns blocked; ask-patterns trigger an interactive confirm |

Ask/deny patterns live in `~/.airgent/permissions.json` (created with empty
defaults on first run — empty lists mean the built-in patterns apply:

```json
{
  "ask_commands": [],
  "deny_commands": [],
  "deny_tools": []
}
```

Built-in ask patterns cover `sudo`, `rm -rf`, `git push`, package installs,
`curl | sh`, docker prune; deny patterns cover `rm -rf /`, `mkfs`, `dd of=/dev/sd`.

## How approval reaches you

The `airgent-permissions` kernel extension calls pi's `ctx.ui.confirm`, which
in RPC mode emits an `extension_ui_request` and blocks the tool until answered:

- **TUI**: the prompt appears in the execution log — press `y` to allow, `n` to deny.
- **WebUI**: a confirm dialog; the answer POSTs to `/api/v1/agent/permission`.
- **Headless** (CLI one-shot, cron, `POST /agent/run`): auto-denied immediately.
- **Timeout**: unanswered prompts are denied after `AIRGENT_PERMISSION_TIMEOUT`
  seconds (default 600).

## Rewind (checkpoints)

Always available — built on the pi kernel's native fork. Rewinding forks the
conversation at an earlier user message; the process switches onto the new
branch, the SQLite binding is updated, and the transcript mirror is rebuilt
from the forked state (memory-injection blocks are stripped for readability).

```bash
airgent sessions rewind <session-id>            # numbered picker
airgent sessions rewind <session-id> --entry-id <id>
```

TUI: `/rewind` opens a searchable palette of rewind points.

HTTP:

```bash
curl http://127.0.0.1:10304/api/v1/sessions/<id>/rewind-points
curl -X POST http://127.0.0.1:10304/api/v1/sessions/<id>/rewind \
  -H 'Content-Type: application/json' -d '{"entry_id": "<id>"}'
```

Rewind restores the *conversation*; files already written by the agent are not
reverted (Claude Code's file checkpoints cover that separately).
