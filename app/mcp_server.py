"""Airgent as an MCP server (stdio, zero dependencies).

Exposes Airgent's local capabilities to any MCP host (Claude Desktop, other
agents) over the standard MCP stdio transport: newline-delimited JSON-RPC 2.0
on stdin/stdout. Run with `airgent mcp-serve` and register it, e.g.:

{
  "mcpServers": {
    "airgent": {
      "command": "uv",
      "args": ["--directory", "/path/to/Airgent", "run", "airgent", "mcp-serve"]
    }
  }
}

Tools:
- memory_search(query, limit?)        ranked search over long-term memory
- memory_add(content, tags?, kind?, importance?)
- sessions_list(limit?)               recent session summaries
- session_get(session_id)             full transcript of one session
- agent_run(input, session_id?)       run the agent (blocking; can take minutes)

The transport layer is transport-agnostic: `McpServer.handle` maps a request
dict to a response dict, which keeps the protocol trivially testable.
"""

from __future__ import annotations

import asyncio
import json
import sys
from typing import Any

PROTOCOL_VERSION = "2024-11-05"
JSONRPC_PARSE_ERROR = -32700
JSONRPC_METHOD_NOT_FOUND = -32601
JSONRPC_INVALID_PARAMS = -32602
JSONRPC_INTERNAL_ERROR = -32603

TOOL_TIMEOUT_SECONDS = 600


class McpServer:
    def __init__(self, services: Any) -> None:
        self.services = services
        self.tools = self._define_tools()

    # ----------------------------------------------------------------- tools
    def _define_tools(self) -> list[dict[str, Any]]:
        return [
            {
                "name": "memory_search",
                "description": (
                    "Search the Airgent user's long-term memory (preferences, projects, "
                    "profile facts). Use before assuming prior context about the user."
                ),
                "inputSchema": {
                    "type": "object",
                    "properties": {
                        "query": {"type": "string", "description": "Keywords to search for"},
                        "limit": {"type": "number", "description": "Max results (default 8)"},
                    },
                    "required": ["query"],
                },
            },
            {
                "name": "memory_add",
                "description": "Persist a durable fact about the user into Airgent's long-term memory.",
                "inputSchema": {
                    "type": "object",
                    "properties": {
                        "content": {"type": "string", "description": "The durable fact"},
                        "tags": {
                            "type": "array",
                            "items": {"type": "string"},
                            "description": "Optional lowercase tags",
                        },
                        "kind": {
                            "type": "string",
                            "description": "fact | preference | profile | project | event",
                        },
                        "importance": {"type": "number", "description": "0.0-1.0 weight"},
                    },
                    "required": ["content"],
                },
            },
            {
                "name": "sessions_list",
                "description": "List recent Airgent conversation sessions.",
                "inputSchema": {
                    "type": "object",
                    "properties": {
                        "limit": {"type": "number", "description": "Max sessions (default 10)"}
                    },
                },
            },
            {
                "name": "session_get",
                "description": "Read the full transcript of one Airgent session.",
                "inputSchema": {
                    "type": "object",
                    "properties": {
                        "session_id": {"type": "string", "description": "Airgent session id"}
                    },
                    "required": ["session_id"],
                },
            },
            {
                "name": "agent_run",
                "description": (
                    "Run a task through the Airgent agent (pi kernel with the user's tools, "
                    "skills, sandbox, and memory). Blocking; can take minutes."
                ),
                "inputSchema": {
                    "type": "object",
                    "properties": {
                        "input": {"type": "string", "description": "The task prompt"},
                        "session_id": {
                            "type": "string",
                            "description": "Optional session to continue",
                        },
                        "plan": {
                            "type": "boolean",
                            "description": "Plan mode: read-only run ending in a proposal",
                        },
                    },
                    "required": ["input"],
                },
            },
        ]

    # -------------------------------------------------------------- dispatch
    def handle(self, request: dict[str, Any]) -> dict[str, Any] | None:
        """Map one JSON-RPC request to its response; None for notifications."""

        method = request.get("method")
        request_id = request.get("id")
        if method is None:
            return self._error(request_id, JSONRPC_PARSE_ERROR, "missing method")
        if method.startswith("notifications/"):
            return None

        try:
            if method == "initialize":
                return self._result(request_id, self._initialize())
            elif method == "ping":
                return self._result(request_id, {})
            elif method == "tools/list":
                return self._result(request_id, {"tools": self.tools})
            elif method == "tools/call":
                return self._result(request_id, self._tools_call(request.get("params") or {}))
            elif method == "resources/list":
                return self._result(request_id, {"resources": []})
            elif method == "prompts/list":
                return self._result(request_id, {"prompts": []})
            return self._error(request_id, JSONRPC_METHOD_NOT_FOUND, f"unknown method: {method}")
        except AppErrorLike as exc:
            return self._tool_error(request_id, str(exc))
        except (KeyError, TypeError, ValueError) as exc:
            return self._error(request_id, JSONRPC_INVALID_PARAMS, f"invalid params: {exc}")
        except Exception as exc:  # noqa: BLE001 - surface as JSON-RPC error
            return self._error(request_id, JSONRPC_INTERNAL_ERROR, str(exc) or exc.__class__.__name__)

    @staticmethod
    def _result(request_id: Any, result: dict[str, Any]) -> dict[str, Any]:
        return {"jsonrpc": "2.0", "id": request_id, "result": result}

    @staticmethod
    def _error(request_id: Any, code: int, message: str) -> dict[str, Any]:
        return {"jsonrpc": "2.0", "id": request_id, "error": {"code": code, "message": message}}

    @staticmethod
    def _tool_error(request_id: Any, message: str) -> dict[str, Any]:
        return McpServer._result(
            request_id,
            {"content": [{"type": "text", "text": message}], "isError": True},
        )

    def _initialize(self) -> dict[str, Any]:
        settings = self.services.settings
        return {
            "protocolVersion": PROTOCOL_VERSION,
            "capabilities": {"tools": {"listChanged": False}},
            "serverInfo": {"name": "airgent", "version": "0.1.0"},
            "instructions": (
                f"Airgent personal agent runtime (workspace: {settings.project_root}). "
                "Use memory_search before assuming prior context about the user."
            ),
        }

    def _tools_call(self, params: dict[str, Any]) -> dict[str, Any]:
        name = params.get("name")
        arguments = params.get("arguments") or {}
        handlers = {
            "memory_search": self._tool_memory_search,
            "memory_add": self._tool_memory_add,
            "sessions_list": self._tool_sessions_list,
            "session_get": self._tool_session_get,
            "agent_run": self._tool_agent_run,
        }
        handler = handlers.get(str(name))
        if handler is None:
            raise KeyError(f"unknown tool: {name}")
        text = handler(arguments)
        return {"content": [{"type": "text", "text": text}], "isError": False}

    # ------------------------------------------------------------- handlers
    def _tool_memory_search(self, args: dict[str, Any]) -> str:
        store = self.services.store
        records = store.search_memories(str(args["query"]), limit=int(args.get("limit") or 8))
        payload = [
            {
                "id": record.id,
                "content": record.content,
                "kind": record.kind,
                "tags": record.tags,
                "importance": record.importance,
                "created_at": record.created_at,
            }
            for record in records
        ]
        return json.dumps(payload, ensure_ascii=False)

    def _tool_memory_add(self, args: dict[str, Any]) -> str:
        record = self.services.store.add_memory(
            str(args["content"]),
            tags=[str(tag) for tag in args.get("tags") or []],
            kind=str(args.get("kind") or "fact"),
            importance=float(args.get("importance") or 0.5),
        )
        return json.dumps({"saved": record.id, "kind": record.kind}, ensure_ascii=False)

    def _tool_sessions_list(self, args: dict[str, Any]) -> str:
        sessions = self.services.store.list_sessions(limit=int(args.get("limit") or 10))
        payload = [
            {
                "session_id": session.session_id,
                "title": session.title,
                "updated_at": session.updated_at,
                "last_message": (session.last_message or "")[:200],
            }
            for session in sessions
        ]
        return json.dumps(payload, ensure_ascii=False)

    def _tool_session_get(self, args: dict[str, Any]) -> str:
        messages = self.services.store.get_messages(str(args["session_id"]))
        payload = [
            {"role": message.role, "content": message.content, "created_at": message.created_at}
            for message in messages
        ]
        return json.dumps(payload, ensure_ascii=False)

    def _tool_agent_run(self, args: dict[str, Any]) -> str:
        from app.api.schemas.agent import AgentRunRequest

        request = AgentRunRequest(
            input=str(args["input"]),
            session_id=args.get("session_id"),
            plan=bool(args.get("plan") or False),
        )
        result = asyncio.run(
            asyncio.wait_for(
                self.services.runner.run(request, request_id="mcp"),
                timeout=TOOL_TIMEOUT_SECONDS,
            )
        )
        return json.dumps(
            {
                "session_id": result.session_id,
                "output": result.output,
                "context": result.context,
            },
            ensure_ascii=False,
        )

    # ------------------------------------------------------------------ loop
    def serve(self, stdin: Any = None, stdout: Any = None) -> None:
        """Run the stdio loop until EOF."""

        reader = stdin if stdin is not None else sys.stdin
        writer = stdout if stdout is not None else sys.stdout
        for line in reader:
            line = line.strip()
            if not line:
                continue
            try:
                request = json.loads(line)
            except json.JSONDecodeError as exc:
                response = self._error(None, JSONRPC_PARSE_ERROR, f"parse error: {exc}")
            else:
                response = self.handle(request)
            if response is not None:
                writer.write(json.dumps(response, ensure_ascii=False) + "\n")
                writer.flush()


class AppErrorLike(Exception):
    """Domain errors surfaced as MCP tool errors (isError: true)."""


def main() -> None:
    from app.bootstrap import build_services

    services = build_services()
    McpServer(services).serve()


if __name__ == "__main__":
    main()
