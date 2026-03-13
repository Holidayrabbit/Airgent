from __future__ import annotations

from collections.abc import AsyncIterator
from dataclasses import asdict, dataclass, is_dataclass
import json
from pathlib import Path
from typing import Any

from app.agents.context import AgentRunContext
from app.agents.registry import AgentRegistry
from app.api.schemas.agent import AgentRunRequest
from app.core.config import Settings
from app.core.errors import AppError
from app.memory.context_builder import ContextBuilder
from app.memory.store import LocalStore
from app.sessions.factory import SessionFactory

try:
    from agents import AgentUpdatedStreamEvent, RawResponsesStreamEvent, RunItemStreamEvent, Runner
except ImportError:  # pragma: no cover
    AgentUpdatedStreamEvent = None  # type: ignore[assignment]
    RawResponsesStreamEvent = None  # type: ignore[assignment]
    RunItemStreamEvent = None  # type: ignore[assignment]
    Runner = None  # type: ignore[assignment]


@dataclass(frozen=True)
class AgentExecutionResult:
    request_id: str
    session_id: str
    agent_key: str
    output: str
    context: dict[str, Any]


@dataclass(frozen=True)
class AgentProgressEvent:
    kind: str
    summary: str
    detail: str = ""
    session_id: str | None = None
    output: str | None = None


class AgentRunnerService:
    def __init__(
        self,
        *,
        settings: Settings,
        store: LocalStore,
        context_builder: ContextBuilder,
        session_factory: SessionFactory,
        agent_registry: AgentRegistry,
    ) -> None:
        self.settings = settings
        self.store = store
        self.context_builder = context_builder
        self.session_factory = session_factory
        self.agent_registry = agent_registry

    async def run(self, request: AgentRunRequest, *, request_id: str) -> AgentExecutionResult:
        session, context, runtime_spec, agent = self._prepare_run(request, request_id=request_id)
        self.store.append_message(
            session.session_id,
            role="user",
            content=request.input,
            agent_key=request.agent_key,
        )
        try:
            result = await Runner.run(
                starting_agent=agent,
                input=request.input,
                context=context,
                session=session,
                max_turns=request.max_turns or runtime_spec.max_turns,
            )
        except KeyError as exc:
            raise AppError(
                code="agent_not_found",
                message=f"Agent '{request.agent_key}' is not configured.",
                status_code=404,
                details={"agent_key": request.agent_key},
            ) from exc
        except Exception as exc:
            raise self._normalize_error(exc) from exc

        final_output = self._stringify_output(result.final_output)
        self.store.append_message(
            session.session_id,
            role="assistant",
            content=final_output,
            agent_key=request.agent_key,
        )
        return AgentExecutionResult(
            request_id=request_id,
            session_id=session.session_id,
            agent_key=request.agent_key,
            output=final_output,
            context={
                "memory_hits": len(context.context_snapshot.memories),
                "recent_message_count": len(context.context_snapshot.recent_messages),
            },
        )

    async def stream(self, request: AgentRunRequest, *, request_id: str) -> AsyncIterator[AgentProgressEvent]:
        if Runner is None:
            raise AppError(
                code="missing_dependency",
                message="openai-agents is not installed.",
                status_code=500,
            )

        session, context, runtime_spec, agent = self._prepare_run(request, request_id=request_id)
        self.store.append_message(
            session.session_id,
            role="user",
            content=request.input,
            agent_key=request.agent_key,
        )
        yield AgentProgressEvent(
            kind="status",
            summary="run started",
            session_id=session.session_id,
        )

        try:
            result = Runner.run_streamed(
                starting_agent=agent,
                input=request.input,
                context=context,
                session=session,
                max_turns=request.max_turns or runtime_spec.max_turns,
            )
            async for event in result.stream_events():
                progress = self._serialize_progress_event(event)
                if progress is not None:
                    yield progress
        except KeyError as exc:
            raise AppError(
                code="agent_not_found",
                message=f"Agent '{request.agent_key}' is not configured.",
                status_code=404,
                details={"agent_key": request.agent_key},
            ) from exc
        except Exception as exc:
            raise self._normalize_error(exc) from exc

        final_output = self._stringify_output(result.final_output)
        self.store.append_message(
            session.session_id,
            role="assistant",
            content=final_output,
            agent_key=request.agent_key,
        )
        yield AgentProgressEvent(
            kind="completed",
            summary="run completed",
            detail=final_output,
            session_id=session.session_id,
            output=final_output,
        )

    def _prepare_run(
        self,
        request: AgentRunRequest,
        *,
        request_id: str,
    ) -> tuple[Any, AgentRunContext, Any, Any]:
        if Runner is None:
            raise AppError("missing_dependency", "openai-agents is not installed.", 500)
        session = self.session_factory.create(request.session_id)
        snapshot = self.context_builder.build(session_id=session.session_id, query=request.input)
        context = AgentRunContext(
            settings=self.settings,
            store=self.store,
            request_id=request_id,
            agent_key=request.agent_key,
            session_id=session.session_id,
            user_input=request.input,
            context_snapshot=snapshot,
        )
        agent, runtime_spec = self.agent_registry.build(context)
        return session, context, runtime_spec, agent

    def _normalize_error(self, exc: Exception) -> AppError:
        reason = str(exc).strip()
        return AppError(
            code="agent_run_failed",
            message=f"Agent execution failed: {reason}" if reason else "Agent execution failed.",
            status_code=500,
            details={
                "type": exc.__class__.__name__,
                "reason": reason,
            },
        )

    def _serialize_progress_event(self, event: Any) -> AgentProgressEvent | None:
        if RawResponsesStreamEvent is not None and isinstance(event, RawResponsesStreamEvent):
            # The raw response stream is too low-level for the TUI and tends to
            # surface SDK internals such as arguments.delta fragments.
            return None

        if RunItemStreamEvent is not None and isinstance(event, RunItemStreamEvent):
            raw_item = getattr(event.item, "raw_item", event.item)
            if event.name == "tool_called":
                return self._tool_called_event(raw_item)
            if event.name == "tool_output":
                return self._tool_output_event(raw_item)
            if event.name == "reasoning_item_created":
                return AgentProgressEvent(
                    kind="thinking",
                    summary="Analyzing the request",
                )
            if event.name == "message_output_created":
                return AgentProgressEvent(
                    kind="message",
                    summary="Drafting the response",
                )
            return None

        if AgentUpdatedStreamEvent is not None and isinstance(event, AgentUpdatedStreamEvent):
            agent_name = getattr(event.new_agent, "name", "updated")
            return AgentProgressEvent(
                kind="agent",
                summary=f"Switched to {agent_name}",
            )

        return None

    def _tool_called_event(self, raw_item: Any) -> AgentProgressEvent:
        tool_name = self._read_attr(raw_item, "name") or "tool"
        arguments = self._parse_tool_payload(
            self._read_attr(raw_item, "arguments")
            or self._read_attr(raw_item, "arguments_json")
            or self._read_attr(raw_item, "input")
        )
        summary = self._summarize_tool_call(tool_name, arguments)
        detail = self._format_tool_detail(tool_name, arguments)
        return AgentProgressEvent(kind="tool", summary=summary, detail=detail)

    def _tool_output_event(self, raw_item: Any) -> AgentProgressEvent:
        payload = self._parse_tool_payload(
            self._read_attr(raw_item, "output") or self._read_attr(raw_item, "content") or raw_item
        )
        summary = self._summarize_tool_output(payload)
        detail = self._format_output_detail(payload)
        return AgentProgressEvent(kind="tool_output", summary=summary, detail=detail)

    def _summarize_tool_call(self, tool_name: str, arguments: Any) -> str:
        data = arguments if isinstance(arguments, dict) else {}
        path = self._display_path(data.get("path"))
        if tool_name == "read_file" and path:
            return f"Reading {path}"
        if tool_name == "edit_file" and path:
            return f"Editing {path}"
        if tool_name == "create_file" and path:
            return f"Creating {path}"
        if tool_name == "search_memory":
            query = data.get("query")
            return f"Searching memory for {query!r}" if query else "Searching memory"
        if tool_name == "remember_note":
            return "Saving a memory"
        if tool_name == "load_skill":
            skill = data.get("name") or data.get("skill_name")
            return f"Loading skill {skill}" if skill else "Loading a skill"
        if tool_name == "list_skills":
            return "Listing available skills"
        if tool_name == "run_bash_command":
            command = self._single_line(data.get("command"))
            return f"Running {command}" if command else "Running a shell command"
        return f"Using {tool_name}"

    def _summarize_tool_output(self, payload: Any) -> str:
        data = payload if isinstance(payload, dict) else {}
        status = str(data.get("status") or "").lower()
        path = self._display_path(data.get("path"))
        if status == "created" and path:
            return f"Created {path}"
        if status == "edited" and path:
            return f"Updated {path}"
        if "exit_code" in data:
            return "Shell command completed" if data.get("exit_code") == 0 else "Shell command failed"
        if path and "content" in data:
            return f"Loaded {path}"
        if status:
            return status.replace("_", " ").capitalize()
        return "Tool step completed"

    def _format_tool_detail(self, tool_name: str, arguments: Any) -> str:
        if isinstance(arguments, dict):
            parts: list[str] = [f"tool: {tool_name}"]
            if "path" in arguments:
                path = self._display_path(arguments.get("path"))
                if path:
                    parts.append(f"path: {path}")
            if "query" in arguments and arguments["query"]:
                parts.append(f"query: {arguments['query']}")
            if "command" in arguments and arguments["command"]:
                parts.append(f"command: {self._single_line(arguments['command'])}")
            return "\n".join(parts)
        dumped = self._dump_value(arguments)
        return f"tool: {tool_name}\n{dumped}" if dumped else f"tool: {tool_name}"

    def _format_output_detail(self, payload: Any) -> str:
        if isinstance(payload, dict):
            parts: list[str] = []
            if "status" in payload and payload["status"]:
                parts.append(f"status: {payload['status']}")
            if "path" in payload:
                path = self._display_path(payload.get("path"))
                if path:
                    parts.append(f"path: {path}")
            if "error" in payload and payload["error"]:
                parts.append(f"error: {payload['error']}")
            if "exit_code" in payload:
                parts.append(f"exit_code: {payload['exit_code']}")
            if parts:
                return "\n".join(parts)
        return self._dump_value(payload)

    def _parse_tool_payload(self, value: Any) -> Any:
        if value is None:
            return {}
        if is_dataclass(value):
            return asdict(value)
        if hasattr(value, "model_dump"):
            return value.model_dump(exclude_unset=True)
        if isinstance(value, str):
            text = value.strip()
            if not text:
                return {}
            if text[0] in "[{":
                try:
                    return json.loads(text)
                except json.JSONDecodeError:
                    return text
            return text
        if isinstance(value, dict):
            return value
        return value

    @staticmethod
    def _display_path(value: Any) -> str | None:
        if not isinstance(value, str) or not value.strip():
            return None
        normalized = value.replace("\\", "/").strip()
        if normalized.startswith("./"):
            normalized = normalized[2:]
        path = Path(normalized)
        if path.is_absolute():
            tail = path.parts[-3:]
            return "/".join(tail) if tail else normalized
        return normalized

    @staticmethod
    def _single_line(value: Any, *, limit: int = 80) -> str | None:
        if not isinstance(value, str):
            return None
        text = " ".join(value.split())
        if not text:
            return None
        return text if len(text) <= limit else f"{text[: limit - 3]}..."

    def _dump_value(self, value: Any) -> str:
        if value is None:
            return ""
        if isinstance(value, (str, int, float, bool)):
            return str(value)
        if is_dataclass(value):
            return json.dumps(asdict(value), ensure_ascii=True, default=str)
        if hasattr(value, "model_dump"):
            return json.dumps(value.model_dump(exclude_unset=True), ensure_ascii=True, default=str)
        if isinstance(value, dict):
            return json.dumps(value, ensure_ascii=True, default=str)
        return str(value)

    @staticmethod
    def _read_attr(value: Any, name: str) -> Any:
        if isinstance(value, dict):
            return value.get(name)
        return getattr(value, name, None)

    @staticmethod
    def _stringify_output(output: Any) -> str:
        if isinstance(output, str):
            return output
        return json.dumps(output, ensure_ascii=True, default=str)
