# Architecture

Airgent is a local-first personal agent runtime. One runtime is exposed through a CLI, a TUI, an HTTP API, and a WebUI.

## Kernel

The agent brain is [pi](https://pi.dev) (`@earendil-works/pi-coding-agent`), a minimal MIT-licensed agent harness. Airgent spawns one `pi --mode rpc` subprocess per session and speaks its JSONL protocol over stdin/stdout (`app/kernel/`):

- `process.py` — subprocess lifecycle, request/response correlation by id, event queue
- `events.py` — normalizes pi records (text deltas, tool calls, tool results, errors) into typed events
- `manager.py` — per-session process pool, prompt runs, idle detection, session resume
- `setup.py` — materializes the isolated pi agent dir under `~/.airgent/pi/agent` (models.json, APPEND_SYSTEM.md, memory extension)

pi owns what it is best at: model routing (15+ providers, OpenAI-compatible proxies), file/shell tools, `.agents/skills` discovery, MCP, conversation compaction, and session trees. The user's own `~/.pi` directory is never touched.

## Airgent layers

- `app/agents/runner.py` — `AgentRunnerService`: translates kernel events into progress events consumed by the TUI/API, persists transcripts, injects relevant long-term memory into prompts
- `app/memory/` — SQLite store for transcripts and long-term memory; the `airgent-memory` pi extension exposes `search_memory`/`remember_note` tools backed by the same database
- `app/api/` — FastAPI routes (agent run/stream, sessions, memory, health) + static WebUI
- `app/cli.py` / `app/tui.py` — CLI commands and the full-screen terminal UI

## Sessions

Each Airgent session maps to one pi session file under `~/.airgent/pi/sessions` (binding stored in SQLite, table `kernel_sessions`). Later runs resume the same pi conversation, preserving tool history and compaction. Airgent additionally mirrors user/assistant transcripts into its own database for listing and search.

## Model configuration

With `OPENAI_BASE_URL` set, Airgent writes a custom `airgent` provider into the isolated `models.json` (`openai-completions` or `openai-responses` per `OPENAI_API_MODE`) and forwards namespaced model ids such as `anthropic/claude-sonnet-4.5` through the proxy unchanged. Without a proxy, pi resolves models from its built-in catalog using provider API keys from the environment.
