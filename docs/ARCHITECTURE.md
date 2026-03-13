# Airgent Architecture

## Overview

Airgent is a local-first personal agent runtime.

It exposes one shared backend through three entry points:

- CLI: `airgent chat`, `airgent tui`, `airgent sessions`, `airgent memory`
- API: FastAPI routes under `/api/v1/*`
- WebUI: static frontend served by the FastAPI app

The runtime layer is built on top of the OpenAI Agents SDK.  
Conversation history, transcripts, and long-term memory are persisted locally in SQLite instead of being sent to a hosted tracing system.

## High-Level Flow

1. User sends input from CLI, TUI, API, or WebUI.
2. The entry point calls the shared service container in [app/bootstrap.py](/Users/zq/work/MyProj/Airgent/app/bootstrap.py).
3. `AgentRunnerService` builds:
   - a local session adapter
   - a context snapshot from recent transcript + memory hits
   - the configured root agent and its tools
4. The OpenAI Agents SDK runs the agent.
5. Final output is persisted back to the local transcript store.
6. The caller receives the response and can later inspect sessions or memory.

## Main Modules

### 1. Entry Layer

- [app/cli.py](/Users/zq/work/MyProj/Airgent/app/cli.py)
  Provides normal CLI commands and the full-screen TUI entry.
- [app/main.py](/Users/zq/work/MyProj/Airgent/app/main.py)
  Creates the FastAPI application, mounts routes, and serves static assets.
- [app/tui.py](/Users/zq/work/MyProj/Airgent/app/tui.py)
  Prompt-toolkit based terminal UI with transcript, recent sessions, composer, and `/` command panel.

### 2. Runtime Assembly

- [app/bootstrap.py](/Users/zq/work/MyProj/Airgent/app/bootstrap.py)
  Central place that wires settings, OpenAI client config, store, session factory, tool registry, and runner.
- [app/core/config.py](/Users/zq/work/MyProj/Airgent/app/core/config.py)
  Loads runtime configuration from environment and `.env`.
- [app/core/openai_config.py](/Users/zq/work/MyProj/Airgent/app/core/openai_config.py)
  Configures the OpenAI Agents SDK with explicit `AsyncOpenAI` client settings.

### 3. Agent Layer

- [app/agents/registry.py](/Users/zq/work/MyProj/Airgent/app/agents/registry.py)
  Loads YAML agent config and builds the actual SDK `Agent`.
- [app/agents/prompts.py](/Users/zq/work/MyProj/Airgent/app/agents/prompts.py)
  Defines the root instructions and injects memory-derived context.
- [app/agents/runner.py](/Users/zq/work/MyProj/Airgent/app/agents/runner.py)
  Main execution service around `Runner.run(...)`.
- [app/agents/configs/root_assistant.yaml](/Users/zq/work/MyProj/Airgent/app/agents/configs/root_assistant.yaml)
  Current root agent definition.

### 4. Persistence Layer

- [app/memory/store.py](/Users/zq/work/MyProj/Airgent/app/memory/store.py)
  SQLite-backed storage for:
  - `sessions`
  - `session_items`
  - `transcripts`
  - `memories`
- [app/memory/context_builder.py](/Users/zq/work/MyProj/Airgent/app/memory/context_builder.py)
  Builds a lightweight context snapshot from recent messages and memory search results.
- [app/sessions/session.py](/Users/zq/work/MyProj/Airgent/app/sessions/session.py)
  Implements the session interface expected by the OpenAI Agents SDK.
- [app/sessions/factory.py](/Users/zq/work/MyProj/Airgent/app/sessions/factory.py)
  Creates local conversation sessions and session IDs.

### 5. Tool Layer

- [app/tools/memory_tools.py](/Users/zq/work/MyProj/Airgent/app/tools/memory_tools.py)
  Exposes `search_memory` and `remember_note`.
- [app/tools/skill_tools.py](/Users/zq/work/MyProj/Airgent/app/tools/skill_tools.py)
  Exposes local skill discovery and loading.
- [app/tools/registry.py](/Users/zq/work/MyProj/Airgent/app/tools/registry.py)
  Registers all tools available to the root agent.

### 6. API Layer

- [app/api/routes/agent.py](/Users/zq/work/MyProj/Airgent/app/api/routes/agent.py)
  Agent run endpoint.
- [app/api/routes/session.py](/Users/zq/work/MyProj/Airgent/app/api/routes/session.py)
  Session listing, detail, deletion.
- [app/api/routes/memory.py](/Users/zq/work/MyProj/Airgent/app/api/routes/memory.py)
  Memory listing, creation, search.
- [app/api/routes/health.py](/Users/zq/work/MyProj/Airgent/app/api/routes/health.py)
  Health check.

### 7. Frontend Layer

- [app/web/static/index.html](/Users/zq/work/MyProj/Airgent/app/web/static/index.html)
- [app/web/static/app.js](/Users/zq/work/MyProj/Airgent/app/web/static/app.js)
- [app/web/static/style.css](/Users/zq/work/MyProj/Airgent/app/web/static/style.css)

The WebUI is intentionally simple: it calls the same API layer used by external clients and does not have a separate frontend build system yet.

## Data Model

Airgent currently stores everything in one local SQLite database, defaulting to:

```text
~/.airgent/airgent.db
```

Logical data groups:

- Session metadata: chat list and update timestamps
- Session items: SDK-compatible short-term conversation state
- Transcript messages: user / assistant readable history
- Memories: durable user facts, preferences, and project notes

## Skills

Local skills live in [skills/](/Users/zq/work/MyProj/Airgent/skills).  
The current built-in example is [skills/context-builder/SKILL.md](/Users/zq/work/MyProj/Airgent/skills/context-builder/SKILL.md), which documents how the agent should use transcript context and long-term memory.

## Current Design Choices

- Single-process Python app
- Shared runtime for CLI, API, and WebUI
- Local SQLite instead of Redis or Langfuse
- One root agent for V1, configured via YAML
- Minimal tool surface: memory + skills first
- Proxy-friendly OpenAI client setup via `OPENAI_BASE_URL` and `chat_completions`

## Current Limitations

- No streaming response path yet
- No multi-agent handoff graph yet
- Memory retrieval is simple keyword matching, not embeddings
- TUI is intentionally compact and not yet feature-complete
- WebUI is static and minimal, without a frontend build pipeline
