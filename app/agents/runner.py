from __future__ import annotations

import asyncio
import json
import re
from collections.abc import AsyncIterator
from dataclasses import dataclass
from pathlib import Path
from typing import Any
from uuid import uuid4

from app.api.schemas.agent import AgentRunRequest
from app.core.config import Settings
from app.core.errors import AppError
from app.hooks.service import HookService
from app.kernel.errors import KernelError
from app.kernel.events import PiEvent
from app.kernel.manager import PiKernel
from app.memory.auto_extract import extract_memories
from app.memory.context_builder import ContextBuilder
from app.memory.store import LocalStore


def _truncate(text: str, limit: int) -> str:
    compact = " ".join(text.split())
    return compact[:limit] + ("..." if len(compact) > limit else "")


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
    """Runs conversations on the pi agent kernel.

    The OpenAI Agents SDK orchestration was replaced by pi: pi owns model
    routing, tools, skills, MCP, and conversation compaction, while Airgent
    keeps its surfaces (CLI/TUI/API/WebUI), SQLite memory, and transcripts.
    """

    def __init__(
        self,
        *,
        settings: Settings,
        store: LocalStore,
        context_builder: ContextBuilder,
        kernel: PiKernel,
        hooks: HookService | None = None,
    ) -> None:
        self.settings = settings
        self.store = store
        self.context_builder = context_builder
        self.kernel = kernel
        self.hooks = hooks or HookService(settings)

    async def run(self, request: AgentRunRequest, *, request_id: str) -> AgentExecutionResult:
        session_id = self._resolve_session_id(request)
        await self._before_turn(request, session_id=session_id, request_id=request_id)
        progress: list[AgentProgressEvent] = []
        try:
            result = await self.kernel.run_prompt(
                session_id=session_id,
                message=self._compose_message(request),
                on_event=lambda event: self._forward_collect(event, session_id, progress),
                # Headless runs cannot answer permission prompts: deny them.
                interactive=False,
            )
        except KernelError as exc:
            self._trace("turn_error", session_id=session_id, request_id=request_id, payload={"error": str(exc)})
            raise self._normalize_error(exc) from exc
        self._persist(request, session_id=result.session_id, output=result.text, progress=progress)
        await self._after_turn(request, session_id=session_id, request_id=request_id, output=result.text)
        return AgentExecutionResult(
            request_id=request_id,
            session_id=result.session_id,
            agent_key=self._agent_key(request),
            output=result.text,
            context={"model": result.model, "pi_session_id": result.pi_session_id},
        )

    async def stream(self, request: AgentRunRequest, *, request_id: str) -> AsyncIterator[AgentProgressEvent]:
        session_id = self._resolve_session_id(request)
        queue: asyncio.Queue[AgentProgressEvent | None] = asyncio.Queue()
        yield AgentProgressEvent(kind="status", summary="run started", session_id=session_id)
        await self._before_turn(request, session_id=session_id, request_id=request_id)

        async def drive() -> None:
            try:
                result = await self.kernel.run_prompt(
                    session_id=session_id,
                    message=self._compose_message(request),
                    on_event=lambda event: self._forward_enqueue(event, session_id, queue),
                    # Streaming surfaces (TUI/WebUI) can answer permission prompts.
                    interactive=True,
                )
            except KernelError as exc:
                self._trace("turn_error", session_id=session_id, request_id=request_id, payload={"error": str(exc)})
                await queue.put(AgentProgressEvent(kind="error", summary=str(exc), session_id=session_id))
                await queue.put(None)
                return
            except Exception as exc:  # noqa: BLE001 - surface any failure to the stream
                await queue.put(
                    AgentProgressEvent(kind="error", summary=str(exc) or exc.__class__.__name__, session_id=session_id)
                )
                await queue.put(None)
                return
            thread_events = [
                {"kind": event.kind, "summary": event.summary, "detail": event.detail}
                for event in self._drain_queue(queue)
            ]
            self.store.upsert_session(result.session_id, self._agent_key(request))
            self.store.append_message(
                result.session_id, role="user", content=request.input, agent_key=self._agent_key(request)
            )
            self.store.append_message(
                result.session_id,
                role="assistant",
                content=result.text,
                agent_key=self._agent_key(request),
                metadata={"thread_events": thread_events},
            )
            await self._after_turn(request, session_id=result.session_id, request_id=request_id, output=result.text)
            await queue.put(
                AgentProgressEvent(
                    kind="completed",
                    summary="run completed",
                    detail=result.text,
                    session_id=result.session_id,
                    output=result.text,
                )
            )
            await queue.put(None)

        task = asyncio.create_task(drive())
        try:
            while True:
                event = await queue.get()
                if event is None:
                    break
                yield event
        finally:
            if not task.done():
                task.cancel()
                try:
                    await task
                except (asyncio.CancelledError, Exception):
                    pass

    # ------------------------------------------------------- permissions & rewind

    async def respond_permission(self, session_id: str, request_id: str, *, allowed: bool) -> bool:
        """Answer an outstanding permission prompt (from the TUI, WebUI, or API)."""

        return await self.kernel.respond_ui(session_id, request_id, allowed=allowed)

    async def list_rewind_points(self, session_id: str) -> list[dict[str, Any]]:
        """User messages the session can be rewound to."""

        return await self.kernel.list_fork_points(session_id)

    async def rewind(self, session_id: str, entry_id: str) -> dict[str, Any]:
        """Rewind the session to an earlier user message and rebuild the mirror."""

        result = await self.kernel.rewind(session_id, entry_id)
        self._rebuild_transcript(session_id, result.get("messages") or [])
        self.store.append_message(
            session_id,
            role="system",
            content=f"[rewind] Conversation restored to: {_truncate(result.get('text', ''), 80)}",
            agent_key=self.settings.default_agent_key,
            metadata={"kind": "rewind", "entry_id": entry_id},
        )
        return {key: result[key] for key in ("text", "pi_session_id")}

    _CONTEXT_BLOCK = re.compile(
        r"<(?:airgent-context|core-memory)>.*?</(?:airgent-context|core-memory)>\s*", re.DOTALL
    )

    def _rebuild_transcript(self, session_id: str, messages: list[Any]) -> None:
        """Replace the mirrored transcript with pi's post-fork message list."""

        self.store.clear_transcript(session_id)
        for message in messages:
            if not isinstance(message, dict):
                continue
            role = message.get("role")
            if role not in {"user", "assistant"}:
                continue
            parts = [
                block.get("text", "")
                for block in message.get("content") or []
                if isinstance(block, dict) and block.get("type") == "text"
            ]
            content = "\n".join(part for part in parts if part).strip()
            if not content and isinstance(message.get("content"), str):
                content = message["content"].strip()
            if not content:
                continue
            if role == "user":
                content = self._CONTEXT_BLOCK.sub("", content).strip()
            if not content:
                continue
            self.store.append_message(
                session_id, role=role, content=content, agent_key=self.settings.default_agent_key
            )

    # ------------------------------------------------------------------ helpers

    def _resolve_session_id(self, request: AgentRunRequest) -> str:
        return request.session_id or uuid4().hex[:12]

    def _agent_key(self, request: AgentRunRequest) -> str:
        return request.agent_key or self.settings.default_agent_key

    # ------------------------------------------------------- turn lifecycle

    def _trace(
        self,
        kind: str,
        *,
        session_id: str | None = None,
        request_id: str | None = None,
        payload: dict[str, Any] | None = None,
    ) -> None:
        if self.settings.tracing_enabled:
            self.store.append_trace_event(
                kind=kind, session_id=session_id, request_id=request_id, payload=payload
            )

    async def _before_turn(self, request: AgentRunRequest, *, session_id: str, request_id: str) -> None:
        """Hooks + automatic memory extraction before the kernel sees the prompt."""

        hook = await self.hooks.run(
            "before_run",
            {"input": request.input, "session_id": session_id, "agent_key": self._agent_key(request)},
        )
        if hook.blocked:
            raise AppError(
                code="hook_blocked",
                message=f"Run blocked by before_run hook: {hook.detail}",
                status_code=403,
                details={"hook": hook.detail},
            )
        if self.settings.memory_auto_extract:
            for extracted in extract_memories(request.input):
                self.store.add_memory(
                    extracted.content,
                    kind=extracted.kind,
                    importance=extracted.importance,
                    source_session_id=session_id,
                )
        self._trace(
            "turn_start",
            session_id=session_id,
            request_id=request_id,
            payload={"input": request.input, "agent_key": self._agent_key(request)},
        )

    async def _after_turn(
        self, request: AgentRunRequest, *, session_id: str, request_id: str, output: str
    ) -> None:
        self._trace(
            "turn_end",
            session_id=session_id,
            request_id=request_id,
            payload={"output_chars": len(output)},
        )
        await self.hooks.run(
            "after_run",
            {"input": request.input, "output": output, "session_id": session_id},
        )

    def _compose_message(self, request: AgentRunRequest) -> str:
        """Prepend long-term memory hits and core blocks to the outgoing prompt.

        pi owns the system prompt and full conversation history; the memory
        blocks are only attached when there is content so ordinary turns stay
        untouched.
        """

        snapshot = self.context_builder.build(session_id=request.session_id or "", query=request.input)
        sections: list[str] = []
        if snapshot.blocks:
            block_lines = ["<core-memory>"]
            for block in snapshot.blocks:
                block_lines.append(f"<{block.key}>{block.value}</{block.key}>")
            block_lines.append("</core-memory>")
            sections.append("\n".join(block_lines))
        if snapshot.memories:
            memory_lines = ["<airgent-context>", "Relevant long-term memory:"]
            for memory in snapshot.memories:
                tags = f" [tags: {', '.join(memory.tags)}]" if memory.tags else ""
                memory_lines.append(f"- {memory.content}{tags}")
            memory_lines.append("</airgent-context>")
            sections.append("\n".join(memory_lines))
        if not sections:
            return request.input
        return "\n\n".join(sections) + "\n\n" + request.input

    def _persist(
        self,
        request: AgentRunRequest,
        *,
        session_id: str,
        output: str,
        progress: list[AgentProgressEvent] | None,
    ) -> None:
        metadata: dict[str, Any] = {}
        if progress is not None:
            metadata["thread_events"] = [
                {"kind": event.kind, "summary": event.summary, "detail": event.detail} for event in progress
            ]
        self.store.upsert_session(session_id, self._agent_key(request))
        self.store.append_message(session_id, role="user", content=request.input, agent_key=self._agent_key(request))
        self.store.append_message(
            session_id, role="assistant", content=output, agent_key=self._agent_key(request), metadata=metadata
        )

    def _forward_collect(
        self,
        event: PiEvent,
        session_id: str,
        sink: list[AgentProgressEvent],
    ) -> None:
        translated = self._translate(event, session_id)
        if translated is not None:
            self._collect(translated, sink)

    def _forward_enqueue(self, event: PiEvent, session_id: str, queue: asyncio.Queue) -> None:
        translated = self._translate(event, session_id)
        if translated is not None:
            queue.put_nowait(translated)

    @staticmethod
    def _collect(event: AgentProgressEvent, sink: list[AgentProgressEvent]) -> None:
        if event.kind not in {"status", "completed", "error"}:
            sink.append(event)

    @staticmethod
    def _drain_queue(queue: asyncio.Queue) -> list[AgentProgressEvent]:
        events: list[AgentProgressEvent] = []
        while not queue.empty():
            item = queue.get_nowait()
            if item is not None:
                events.append(item)
        return events

    def _translate(self, event: PiEvent, session_id: str) -> AgentProgressEvent | None:
        """Map a kernel event onto the progress stream.

        Returns None for bookkeeping events the user surface should not see.
        """

        kind = event.kind
        if kind == "ui_request":
            data = event.data
            detail = json.dumps(
                {
                    "request_id": data.get("id"),
                    "method": data.get("method"),
                    "title": data.get("title"),
                    "message": data.get("message"),
                },
                ensure_ascii=False,
            )
            return AgentProgressEvent(
                kind="permission_request",
                summary=str(data.get("title") or "Permission needed"),
                detail=detail,
                session_id=session_id,
            )
        if kind == "reasoning":
            return AgentProgressEvent(kind="thinking", summary="Analyzing the request")
        if kind == "tool_start":
            data = event.data
            return AgentProgressEvent(
                kind="tool",
                summary=self._summarize_tool_call(str(data.get("name")), data.get("arguments")),
                detail=self._format_tool_detail(str(data.get("name")), data.get("arguments")),
                session_id=session_id,
            )
        if kind == "tool_end":
            data = event.data
            summary = "Tool step failed" if data.get("is_error") else "Tool step completed"
            return AgentProgressEvent(
                kind="tool_output",
                summary=summary,
                detail=str(data.get("text") or "")[:2000],
                session_id=session_id,
            )
        if kind == "text_start":
            return AgentProgressEvent(kind="message", summary="Drafting the response", session_id=session_id)
        if kind == "compaction":
            phase = event.data.get("phase")
            summary = "Compacting conversation" if phase == "start" else "Compaction finished"
            return AgentProgressEvent(kind="status", summary=summary, session_id=session_id)
        return None

    def _summarize_tool_call(self, tool_name: str, arguments: Any) -> str:
        data = arguments if isinstance(arguments, dict) else {}
        path = self._display_path(data.get("path") or data.get("filePath"))
        command = self._single_line(data.get("command"))
        if tool_name in {"read", "read_file"} and path:
            return f"Reading {path}"
        if tool_name in {"write", "create_file"} and path:
            return f"Creating {path}"
        if tool_name in {"edit", "edit_file"} and path:
            return f"Editing {path}"
        if tool_name == "grep" and data.get("pattern"):
            return f"Searching for {str(data['pattern'])!r}"
        if tool_name in {"bash", "run_bash_command"} and command:
            return f"Running {command}"
        if tool_name == "search_memory":
            query = data.get("query")
            return f"Searching memory for {query!r}" if query else "Searching memory"
        if tool_name == "remember_note":
            return "Saving a memory"
        return f"Using {tool_name}"

    def _format_tool_detail(self, tool_name: str, arguments: Any) -> str:
        if isinstance(arguments, dict):
            parts: list[str] = [f"tool: {tool_name}"]
            path = self._display_path(arguments.get("path") or arguments.get("filePath"))
            if path:
                parts.append(f"path: {path}")
            if arguments.get("pattern"):
                parts.append(f"pattern: {arguments['pattern']}")
            command = self._single_line(arguments.get("command"))
            if command:
                parts.append(f"command: {command}")
            if arguments.get("query"):
                parts.append(f"query: {arguments['query']}")
            return "\n".join(parts)
        return f"tool: {tool_name}"

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

    def _normalize_error(self, exc: KernelError) -> AppError:
        reason = str(exc).strip()
        return AppError(
            code="agent_run_failed",
            message=f"Agent execution failed: {reason}" if reason else "Agent execution failed.",
            status_code=500,
            details={"kernel": "pi", "reason": reason},
        )
