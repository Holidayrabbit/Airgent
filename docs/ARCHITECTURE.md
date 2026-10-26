# Architecture

Airgent is a local-first personal agent runtime. One runtime is exposed through a CLI, a TUI, an HTTP API, and a WebUI.

## Kernel

The agent brain is [pi](https://pi.dev) (`@earendil-works/pi-coding-agent`), a minimal MIT-licensed agent harness. Airgent spawns one `pi --mode rpc` subprocess per session and speaks its JSONL protocol over stdin/stdout (`app/kernel/`):

- `process.py` — subprocess lifecycle, request/response correlation by id, event queue
- `events.py` — normalizes pi records (text deltas, tool calls, tool results, errors) into typed events
- `manager.py` — per-session process pool, prompt runs, idle detection, session resume
- `setup.py` — materializes the isolated pi agent dir under `~/.airgent/pi/agent` (models.json, settings.json skills overlay, APPEND_SYSTEM.md, extensions) and resolves the sandbox launch spec

pi owns what it is best at: model routing (15+ providers, OpenAI-compatible proxies), file/shell tools, `.agents/skills` discovery, and session trees. The user's own `~/.pi` directory is never touched.

## Optional capability layer

Every capability below is behind a default-off flag (`app/core/config.py`);
with all flags off, the deployment is a plain pi wrapper plus memory tools.

- `app/sandbox/` — wraps the kernel command with a sandbox prefix: `seatbelt`
  (macOS `sandbox-exec` profile rendered per deployment) or `docker`
  (bind-mounted container, network-off by default).
- `app/skills/` — skill registry: discovers skills, stores enable/disable
  state in `~/.airgent/skills.json`, and projects it into the pi agent dir's
  `settings.json` skills whitelist when filtering is active.
- `pi/airgent-guardrails.ts` — `tool_call` interceptor enforcing a JSON policy
  (denied commands/paths/tools, bash timeout cap).
- `pi/airgent-permissions.ts` — tiered permissions (`read-only` /
  `workspace-write`): static blocks plus interactive approvals that ride pi's
  extension UI protocol; `app/kernel/process.py` answers with
  `extension_ui_response`, the manager relays prompts to the active surface
  (TUI `y`/`n`, WebUI dialog, `POST /api/v1/agent/permission`) and auto-denies
  headless or timed-out requests. See `docs/permissions.md`.
- Session rewind (checkpoints) is kernel-native: `PiKernel.list_fork_points`
  and `PiKernel.rewind` drive pi's `get_fork_messages`/`fork` RPC, rebind the
  session mapping, and rebuild the transcript mirror from the forked branch.
- `pi/airgent-subagent.ts` — `spawn_subagent` tool delegating to headless
  `pi --mode rpc` children with a depth cap (`AIRGENT_SUBAGENT_DEPTH`).
- `pi/airgent-mcp.ts` — MCP bridge: reads `~/.airgent/mcp.json`
  (Claude-Desktop-compatible), handshakes with stdio servers, registers their
  tools as `mcp__<server>__<tool>`.
- `app/hooks/service.py` — `before_run`/`after_run` shell hooks fed JSON on
  stdin; a `before_run` hook exiting 2 vetoes the turn.

## Airgent layers

- `app/agents/runner.py` — `AgentRunnerService`: translates kernel events into progress events consumed by the TUI/API, persists transcripts, injects core memory blocks and relevant long-term memory into prompts, runs lifecycle hooks, performs automatic memory extraction, and records trace events
- `app/memory/` — SQLite store for transcripts, long-term memory (kinds, importance, access tracking, decay, dedup), core memory blocks, and trace events; the `airgent-memory` pi extension exposes `search_memory`/`remember_note` tools backed by the same database
- `app/api/` — FastAPI routes (agent run/stream, sessions, memory, health) + static WebUI
- `app/cli.py` / `app/tui.py` — CLI commands and the full-screen terminal UI

## Sessions

Each Airgent session maps to one pi session file under `~/.airgent/pi/sessions` (binding stored in SQLite, table `kernel_sessions`). Later runs resume the same pi conversation, preserving tool history and compaction. Airgent additionally mirrors user/assistant transcripts into its own database for listing and search.

## Model configuration

With `OPENAI_BASE_URL` set, Airgent writes a custom `airgent` provider into the isolated `models.json` (`openai-completions` or `openai-responses` per `OPENAI_API_MODE`) and forwards namespaced model ids such as `anthropic/claude-sonnet-4.5` through the proxy unchanged. Without a proxy, pi resolves models from its built-in catalog using provider API keys from the environment.
