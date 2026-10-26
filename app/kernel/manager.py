"""Session-scoped pi kernel processes.

One `pi --mode rpc` subprocess is kept per Airgent session (lazily spawned,
reused across runs). The mapping Airgent session id -> pi session file is
persisted in the SQLite store so later runs resume the same pi conversation,
keeping pi's own context, tool history, and compaction intact.
"""

from __future__ import annotations

import asyncio
import json
import re
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


@dataclass(frozen=True)
class StreamRule:
    pattern: str
    instruction: str
    regex: re.Pattern[str]


def _load_stream_rules(path: Path) -> list[StreamRule]:
    """Load omp-style stream rules; malformed entries are skipped."""

    if not path.is_file():
        return []
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return []
    rules: list[StreamRule] = []
    entries = payload.get("rules") if isinstance(payload, dict) else payload
    for entry in entries or []:
        if not isinstance(entry, dict):
            continue
        pattern = str(entry.get("pattern") or "")
        instruction = str(entry.get("instruction") or "").strip()
        if not pattern or not instruction:
            continue
        try:
            regex = re.compile(pattern)
        except re.error:
            continue
        rules.append(StreamRule(pattern=pattern, instruction=instruction, regex=regex))
    return rules


@dataclass
class _ProcessEntry:
    process: PiProcess
    model: str
    lock: asyncio.Lock
    mode: str = "default"
    pending_ui: dict[str, float] = field(default_factory=dict)
    """Outstanding extension UI requests: id -> deadline (monotonic)."""


@dataclass
class _AttemptOutcome:
    text: str = ""
    error_message: str | None = None
    triggered_rule: StreamRule | None = None


@dataclass(frozen=True)
class KernelRunResult:
    text: str
    session_id: str
    pi_session_id: str
    model: str
    stats: dict[str, Any] = field(default_factory=dict)
    """Token/cost usage from get_session_stats (empty when unavailable)."""


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

    def _installed_skill_args(self) -> list[str]:
        """`--skill` args for user-installed skills (explicit opt-in, additive)."""

        try:
            from app.skills.registry import SkillRegistry

            entries = SkillRegistry(self.settings).installed_entries()
        except Exception:
            return []
        args: list[str] = []
        for entry in entries:
            args.extend(["--skill", str(entry.path)])
        return args

    async def _spawn(self, mode: str = "default") -> PiProcess:
        environment = self.prepare()
        command = [
            self.settings.pi_bin,
            "--mode",
            "rpc",
            "--model",
            self._model_argument(),
            *self._installed_skill_args(),
        ]
        process = PiProcess(
            command=[*environment.sandbox.argv_prefix, *command],
            env=build_spawn_env(self.settings, mode=mode),
            cwd=str(self.settings.project_root),
            request_timeout=self.settings.pi_request_timeout_seconds,
        )
        await process.start()
        return process

    async def _spawn_resuming(self, pi_session_file: str, mode: str = "default") -> PiProcess:
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
            env=build_spawn_env(self.settings, mode=mode),
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

    async def _acquire(self, session_id: str, mode: str = "default") -> _ProcessEntry:
        model = self._model_argument()
        async with self._entries_lock:
            entry = self._entries.get(session_id)
            if entry is not None and (
                not entry.process.alive or entry.model != model or entry.mode != mode
            ):
                await self._dispose(session_id, entry)
                entry = None
            if entry is None:
                entry = await self._create_entry(session_id, model, mode)
                self._entries[session_id] = entry
            return entry

    async def _create_entry(self, session_id: str, model: str, mode: str) -> _ProcessEntry:
        mapping = self.store.get_kernel_session(session_id)
        resume_file = mapping["pi_session_file"] if mapping and mapping["model"] == model else None
        if resume_file and Path(resume_file).is_file():
            process = await self._spawn_resuming(resume_file, mode)
            expected = self._expected_pi_session_id(resume_file)
            state = await self._safe_state(process)
            if state is not None and (expected is None or state.get("sessionId") == expected):
                return _ProcessEntry(process=process, model=model, lock=asyncio.Lock(), mode=mode)
            # Resume failed (stale/corrupt file): fall through to a fresh session.
            await process.close()

        process = await self._spawn(mode)
        return _ProcessEntry(process=process, model=model, lock=asyncio.Lock(), mode=mode)

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
        mode: str = "default",
    ) -> KernelRunResult:
        """Run one prompt through the kernel.

        `interactive=True` lets extension UI requests (permission confirms)
        flow to `on_event` and wait for a `respond_ui` answer; otherwise any
        request is denied immediately (safe headless default). `mode="plan"`
        spawns the kernel with AIRGENT_RUN_MODE=plan, which the permission
        extension enforces as read-only.
        """

        entry = await self._acquire(session_id, mode)
        async with entry.lock:
            if not entry.process.alive:
                await self._dispose(session_id, entry)
                entry = await self._acquire(session_id, mode)
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
        """Run one prompt, applying stream rules with abort + reinject retries."""

        process = entry.process
        current_message = message
        outcome: _AttemptOutcome | None = None

        for attempt in range(self.settings.stream_rules_max_retries + 1):
            outcome = await self._consume_run(entry, current_message, on_event, interactive)
            if outcome.triggered_rule is None:
                break
            # Time-traveling rule: the stream was aborted mid-token; inject
            # the rule instruction and replay the full task.
            rule = outcome.triggered_rule
            if on_event is not None:
                on_event(
                    PiEvent(
                        "rule_triggered",
                        {"pattern": rule.pattern, "instruction": rule.instruction, "attempt": attempt + 1},
                    )
                )
            current_message = (
                f"[Airgent stream rule] Your previous response was aborted because it matched "
                f"the rule /{rule.pattern}/. Instruction: {rule.instruction}\n\n"
                f"Produce a corrected, complete response to the original request:\n\n{message}"
            )

        assert outcome is not None
        await self._deny_all_pending_ui(entry)
        if outcome.error_message is not None and not outcome.text:
            raise KernelError(outcome.error_message)

        await self.record_pi_session(session_id)
        state = await self._safe_state(process) or {}
        return KernelRunResult(
            text=outcome.text,
            session_id=session_id,
            pi_session_id=str(state.get("sessionId") or ""),
            model=entry.model,
            stats=await self._session_stats(process),
        )

    def _stream_rules(self) -> list[StreamRule]:
        if not hasattr(self, "_stream_rules_cache"):
            self._stream_rules_cache = _load_stream_rules(self.settings.stream_rules_config)
        return self._stream_rules_cache

    async def _consume_run(
        self,
        entry: _ProcessEntry,
        message: str,
        on_event: ProgressCallback | None,
        interactive: bool,
    ) -> _AttemptOutcome:
        """Drive one prompt to a natural stop (agent_end / error / settle)."""

        process = entry.process
        fallback_text = ""
        current_message_parts: list[str] | None = None
        end_text: str | None = None
        error_message: str | None = None
        saw_agent_end = False
        triggered_rule: StreamRule | None = None
        rules = self._stream_rules() if self.settings.stream_rules_enabled else []

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
                if (
                    triggered_rule is None
                    and rules
                    and current_message_parts
                    and not entry.pending_ui
                ):
                    streamed = "".join(current_message_parts)
                    for rule in rules:
                        if rule.regex.search(streamed):
                            triggered_rule = rule
                            try:
                                await asyncio.wait_for(
                                    process.request("abort"), timeout=self.settings.pi_request_timeout_seconds
                                )
                            except KernelError:
                                pass
                            break
            elif event.kind == "text_end":
                if current_message_parts is not None:
                    fallback_text = "".join(current_message_parts).strip()
                current_message_parts = None
            elif event.kind == "assistant_error":
                error_message = str(event.data.get("error") or "assistant message failed")

            emit(event)

        final_text = end_text or fallback_text
        if triggered_rule is None and not final_text and error_message is None:
            # No agent_end within the window or empty transcript.
            if not await self._is_idle(process):
                raise KernelError("pi run did not settle")
        return _AttemptOutcome(
            text=final_text,
            error_message=error_message,
            triggered_rule=triggered_rule,
        )

    @staticmethod
    async def _session_stats(process: PiProcess) -> dict[str, Any]:
        """Best-effort token/cost snapshot; never fails the run."""

        try:
            data = await process.request("get_session_stats")
        except KernelError:
            return {}
        tokens = data.get("tokens") or {}
        context = data.get("contextUsage") or {}
        return {
            "tokens_input": tokens.get("input", 0),
            "tokens_output": tokens.get("output", 0),
            "tokens_total": tokens.get("total", 0),
            "cost_usd": data.get("cost", 0.0),
            "context_tokens": context.get("tokens"),
            "context_window": context.get("contextWindow"),
            "context_percent": context.get("percent"),
        }

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
