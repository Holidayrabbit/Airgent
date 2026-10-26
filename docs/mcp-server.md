# Airgent as an MCP server

Airgent can expose its local capabilities to **other** agent hosts over the
Model Context Protocol. `airgent mcp-serve` runs a stdio MCP server (JSON-RPC
2.0, newline-delimited) with zero extra dependencies, so Claude Desktop,
other coding agents, or any MCP client can use Airgent's memory, sessions,
and kernel as tools.

## Registration

```json
{
  "mcpServers": {
    "airgent": {
      "command": "uv",
      "args": ["--directory", "/path/to/Airgent", "run", "airgent", "mcp-serve"]
    }
  }
}
```

(Any launcher works — the server only needs the `airgent` CLI on PATH and
read access to `~/.airgent`.)

## Tools

| Tool | Arguments | Notes |
|------|-----------|-------|
| `memory_search` | `query`, `limit?=8` | Ranked search (hybrid when embeddings are enabled) |
| `memory_add` | `content`, `tags?`, `kind?`, `importance?` | Persists a durable fact |
| `sessions_list` | `limit?=10` | Recent session summaries |
| `session_get` | `session_id` | Full transcript of one session |
| `agent_run` | `input`, `session_id?`, `plan?` | Runs the agent (pi kernel, the user's tools/skills/sandbox). **Blocking**; can take minutes. |

`agent_run` runs with the same permission tiers and sandbox as the local
surfaces; headless permission prompts are auto-denied, and plan mode
(`"plan": true`) is read-only. Tool errors surface as MCP `isError` results,
never as transport failures.
