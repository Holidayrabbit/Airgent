"""Session-scoped pi kernel processes.

One `pi --mode rpc` subprocess is kept per Airgent session (lazily spawned,
reused across runs). The mapping Airgent session id -> pi session file is
persisted in the SQLite store so later runs resume the same pi conversation,
keeping pi's own context, tool history, and compaction intact.
"""

from __future__ import annotations

import asyncio
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable

from app.core.config import Settings
from app.kernel.errors import KernelError
from app.kernel.events import PiEvent, assistant_text_from_messages, parse_event
from app.kernel.process import PiProcess
from app.kernel.setup import PiEnvironment, build_spawn_env, prepare_environment
from app.memory.store import LocalStore

ProgressCallback = Callable[[PiEvent], None]


@dataclass
class _ProcessEntry:
    process: PiProcess
    model: str
    lock: asyncio.Lock
    pending_ui: dict[str, float] = field(default_factory=dict)
    """Outstanding extension UI requests: id -> deadline (monotonic)."""


@dataclass(frozen=True)
class KernelRunResult:
    text: str
    session_id: str
    pi_session_id: str
    model: str


class PiKernel:
    def __init__(self, *, settings: Settings, store: LocalStore) -> None:
        self.settings = settings
        self.store = store
        self.environment: PiEnvironment | None = None
        self._entries: dict[str, _ProcessEntry] = {}
        self._entries_lock = asyncio.Lock()

    def prepare(self) -> PiEnvironment:
        if self.environment is None:
            self.environment = prepare_environment(self.settings)
        return self.environment

    def _model_argument(self) -> str:
        environment = self.prepare()
        if environment.provider_model:
            return environment.provider_model
        return self.settings.default_model

    async def _spawn(self) -> PiProcess:
        environment = self.prepare()
        command = [
            self.settings.pi_bin,
            "--mode",
            "rpc",
            "--model",
            self._model_argument(),
        ]
        process = PiProcess(
            command=[*environment.sandbox.argv_prefix, *command],
            env=build_spawn_env(self.settings),
            cwd=str(self.settings.project_root),
            request_timeout=self.settings.pi_request_timeout_seconds,
        )
        await process.start()
        return process

    async def _spawn_resuming(self, pi_session_file: str) -> PiProcess:
        environment = self.prepare()
        command = [
            self.settings.pi_bin,
            "--mode",
            "rpc",
            "--session",
            pi_session_file,
            "--model",
            self._model_argument(),
        ]
        process = PiProcess(
            command=[*environment.sandbox.argv_prefix, *command],
            env=build_spawn_env(self.settings),
            cwd=str(self.settings.project_root),
            request_timeout=self.settings.pi_request_timeout_seconds,
        )
        await process.start()
        return process

    def _expected_pi_session_id(self, pi_session_file: str) -> str | None:
        name = Path(pi_session_file).stem
        if "_" not in name:
            return None
        return name.rsplit("_", 1)[-1]

    async def _acquire(self, session_id: str) -> _ProcessEntry:
        model = self._model_argument()
        async with self._entries_lock:
            entry = self._entries.get(session_id)
            if entry is not None and (not entry.process.alive or entry.model != model):
                await self._dispose(session_id, entry)
                entry = None
            if entry is None:
                entry = await self._create_entry(session_id, model)
                self._entries[session_id] = entry
            return entry

    async def _create_entry(self, session_id: str, model: str) -> _ProcessEntry:
        mapping = self.store.get_kernel_session(session_id)
        resume_file = mapping["pi_session_file"] if mapping and mapping["model"] == model else None
        if resume_file and Path(resume_file).is_file():
            process = await self._spawn_resuming(resume_file)
            expected = self._expected_pi_session_id(resume_file)
            state = await self._safe_state(process)
            if state is not None and (expected is None or state.get("sessionId") == expected):
                return _ProcessEntry(process=process, model=model, lock=asyncio.Lock())
            # Resume failed (stale/corrupt file): fall through to a fresh session.
            await process.close()

        process = await self._spawn()
        return _ProcessEntry(process=process, model=model, lock=asyncio.Lock())

    async def _safe_state(self, process: PiProcess) -> dict[str, Any] | None:
        try:
            return await process.request("get_state")
        except KernelError:
            return None

    async def _dispose(self, session_id: str, entry: _ProcessEntry) -> None:
        self._entries.pop(session_id, None)
        await entry.process.close()

    async def run_prompt(
        self,
        *,
        session_id: str,
        message: str,
        on_event: ProgressCallback | None = None,
        interactive: bool = False,
    ) -> KernelRunResult:
        """Run one prompt through the kernel.

        `interactive=True` lets extension UI requests (permission confirms)
        flow to `on_event` and wait for a `respond_ui` answer; otherwise any
        request is denied immediately (safe headless default).
        """

        entry = await self._acquire(session_id)
        async with entry.lock:
            if not entry.process.alive:
                await self._dispose(session_id, entry)
                entry = await self._acquire(session_id)
                async with entry.lock:
                    return await self._run_locked(
                        session_id, entry, message, on_event, interactive
                    )
            return await self._run_locked(session_id, entry, message, on_event, interactive)

    async def _run_locked(
        self,
        session_id: str,
        entry: _ProcessEntry,
        message: str,
        on_event: ProgressCallback | None,
        interactive: bool,
    ) -> KernelRunResult:
        process = entry.process
        fallback_text = ""
        current_message_parts: list[str] | None = None
        end_text: str | None = None
        error_message: str | None = None
        saw_agent_end = False

        def emit(event: PiEvent) -> None:
            if on_event is not None:
                on_event(event)

        try:
            await process.request("prompt", {"message": message})
        except KernelError as exc:
            raise KernelError(f"pi rejected the prompt: {exc}") from exc

        deadline = time.monotonic() + 3600.0
        while time.monotonic() < deadline:
            self._expire_stale_ui(entry)
            record = await process.next_event(timeout=self.settings.pi_request_timeout_seconds)
            if record is None:
                if saw_agent_end and await self._is_idle(process):
                    break
                continue

            if record.get("type") == "response":
                # Late/unsolicited responses carry nothing the run needs.
                process.task_done()
                continue

            if record.get("type") == "agent_end":
                saw_agent_end = True
                end_text = assistant_text_from_messages(record.get("messages") or [])
                emit(PiEvent("agent_end"))
                process.task_done()
                if await self._is_idle(process):
                    break
                continue

            event = parse_event(record)
            process.task_done()
            if event is None:
                continue

            if event.kind == "ui_request":
                await self._handle_ui_request(entry, event, emit, interactive)
                continue

            if event.kind == "text_start":
                current_message_parts = []
            elif event.kind == "text_delta":
                if current_message_parts is None:
                    current_message_parts = []
                current_message_parts.append(event.data.get("delta", ""))
            elif event.kind == "text_end":
                if current_message_parts is not None:
                    fallback_text = "".join(current_message_parts).strip()
                current_message_parts = None
            elif event.kind == "assistant_error":
                error_message = str(event.data.get("error") or "assistant message failed")

            emit(event)

        final_text = end_text or fallback_text
        if not final_text and error_message is None:
            # No agent_end within the window or empty transcript.
            if not await self._is_idle(process):
                raise KernelError("pi run did not settle")
        await self._deny_all_pending_ui(entry)

        await self.record_pi_session(session_id)
        if error_message is not None and not final_text:
            raise KernelError(error_message)

        state = await self._safe_state(process) or {}
        return KernelRunResult(
            text=final_text,
            session_id=session_id,
            pi_session_id=str(state.get("sessionId") or ""),
            model=entry.model,
        )

    async def _is_idle(self, process: PiProcess) -> bool:
        try:
            state = await process.request("get_state")
        except KernelError:
            return True
        return not state.get("isStreaming")

    # ------------------------------------------------------------------
    # Extension UI (permission approvals)
    # ------------------------------------------------------------------
    async def _handle_ui_request(
        self,
        entry: _ProcessEntry,
        event: PiEvent,
        emit: ProgressCallback,
        interactive: bool,
    ) -> None:
        request_id = str(event.data.get("id") or "")
        if not request_id:
            return
        method = str(event.data.get("method") or "")
        if method != "confirm" or not interactive:
            # Only confirm dialogs are supported by Airgent surfaces; anything
            # else (and every headless request) resolves to the safe default.
            await self._respond(entry, request_id, allowed=False)
            return
        entry.pending_ui[request_id] = time.monotonic() + self.settings.permission_timeout_seconds
        emit(event)

    async def _respond(self, entry: _ProcessEntry, request_id: str, *, allowed: bool) -> None:
        entry.pending_ui.pop(request_id, None)
        try:
            await entry.process.respond_ui(request_id, {"confirmed": bool(allowed)})
        except KernelError:
            pass

    async def respond_ui(self, session_id: str, request_id: str, *, allowed: bool) -> bool:
        """Answer a pending permission request from outside the run loop."""

        entry = self._entries.get(session_id)
        if entry is None or request_id not in entry.pending_ui:
            return False
        await self._respond(entry, request_id, allowed=allowed)
        return True

    def _expire_stale_ui(self, entry: _ProcessEntry) -> None:
        stale = [
            request_id
            for request_id, deadline in entry.pending_ui.items()
            if time.monotonic() > deadline
        ]
        for request_id in stale:
            asyncio.get_running_loop().create_task(self._respond(entry, request_id, allowed=False))

    async def _deny_all_pending_ui(self, entry: _ProcessEntry) -> None:
        for request_id in list(entry.pending_ui):
            await self._respond(entry, request_id, allowed=False)

    # ------------------------------------------------------------------
    # Rewind (session checkpoints via pi fork)
    # ------------------------------------------------------------------
    async def list_fork_points(self, session_id: str) -> list[dict[str, Any]]:
        """User messages this session can be rewound to (pi get_fork_messages)."""

        entry = await self._acquire(session_id)
        async with entry.lock:
            data = await entry.process.request("get_fork_messages")
            return list(data.get("messages") or [])

    async def rewind(self, session_id: str, entry_id: str) -> dict[str, Any]:
        """Fork the pi conversation at an earlier user message.

        The current process switches onto the new branch; the SQLite binding
        is refreshed so later runs resume from the rewound state.
        """

        entry = await self._acquire(session_id)
        async with entry.lock:
            data = await entry.process.request("fork", {"entryId": entry_id})
            if data.get("cancelled"):
                raise KernelError("rewind was cancelled by an extension")
            await self.record_pi_session(session_id)
            state = await self._safe_state(entry.process) or {}
            messages = await entry.process.request("get_messages")
            return {
                "text": str(data.get("text") or ""),
                "pi_session_id": str(state.get("sessionId") or ""),
                "messages": list(messages.get("messages") or []),
            }

    async def record_pi_session(self, session_id: str) -> None:
        entry = self._entries.get(session_id)
        if entry is None:
            return
        state = await self._safe_state(entry.process)
        if not state:
            return
        file_name = state.get("sessionFile")
        session_dir = self.settings.pi_session_dir
        pi_session_id = str(state.get("sessionId") or "")
        path: Path | None = None
        if file_name:
            direct = session_dir / file_name
            if direct.is_file():
                path = direct
            else:
                matches = list(session_dir.rglob(Path(file_name).name))
                path = matches[0] if matches else None
        if path is None and pi_session_id:
            matches = list(session_dir.rglob(f"*_{pi_session_id}.jsonl"))
            path = matches[0] if matches else None
        if path is not None:
            self.store.set_kernel_session(
                session_id,
                pi_session_file=str(path),
                model=entry.model,
            )

    async def shutdown(self) -> None:
        async with self._entries_lock:
            entries = list(self._entries.items())
            self._entries.clear()
        for _, entry in entries:
            await entry.process.close()
