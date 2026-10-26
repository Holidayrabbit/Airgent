from __future__ import annotations

import asyncio
from pathlib import Path
from typing import Any

from app.core.config import Settings
from app.kernel.events import parse_event
from app.kernel.manager import PiKernel
from app.memory.store import LocalStore


def _settings(tmp_path: Path) -> Settings:
    return Settings(
        _env_file=None,
        data_dir=tmp_path / "data",
        pi_agent_dir=tmp_path / "pi" / "agent",
        pi_session_dir=tmp_path / "pi" / "sessions",
        permission_timeout_seconds=60.0,
    )


def test_extension_ui_request_parses_to_ui_request_event() -> None:
    record = {
        "type": "extension_ui_request",
        "id": "ui-1",
        "method": "confirm",
        "title": "Allow command? [sudo]",
        "message": "sudo apt install build-essential",
    }
    event = parse_event(record)
    assert event is not None
    assert event.kind == "ui_request"
    assert event.data["id"] == "ui-1"
    assert event.data["method"] == "confirm"
    assert "sudo apt" in event.data["message"]


class FakeProcess:
    """Stands in for PiProcess: scripted events, recorded stdin responses."""

    def __init__(self, script: list[dict[str, Any]]) -> None:
        self._queue: asyncio.Queue[dict[str, Any]] = asyncio.Queue()
        for record in script:
            self._queue.put_nowait(record)
        self.alive = True
        self.ui_responses: list[dict[str, Any]] = []
        self.requests: list[tuple[str, dict[str, Any]]] = []

    async def request(self, command_type: str, payload: dict[str, Any] | None = None) -> dict[str, Any]:
        self.requests.append((command_type, payload or {}))
        if command_type == "prompt":
            return {}
        if command_type == "get_state":
            return {"isStreaming": False, "sessionId": "pi-1", "sessionFile": "s_pi-1.jsonl"}
        if command_type == "get_fork_messages":
            return {"messages": [{"entryId": "e1", "text": "first prompt"}]}
        if command_type == "fork":
            return {"text": "first prompt", "cancelled": False}
        if command_type == "get_messages":
            return {"messages": [{"role": "user", "content": [{"type": "text", "text": "first prompt"}]}]}
        return {}

    async def respond_ui(self, request_id: str, payload: dict[str, Any]) -> None:
        self.ui_responses.append({"id": request_id, **payload})

    async def next_event(self, timeout: float | None = None) -> dict[str, Any] | None:
        try:
            return await asyncio.wait_for(self._queue.get(), timeout or 0.05)
        except asyncio.TimeoutError:
            return None

    def task_done(self) -> None:
        pass

    async def close(self) -> None:
        self.alive = False


def _kernel_with_fake(tmp_path: Path, fake: FakeProcess) -> PiKernel:
    kernel = PiKernel(settings=_settings(tmp_path), store=LocalStore(tmp_path / "store.db"))

    async def fake_spawn() -> FakeProcess:
        return fake

    kernel._spawn = fake_spawn  # type: ignore[assignment]
    return kernel


_AGENT_END_DONE = {
    "type": "agent_end",
    "messages": [
        {"role": "assistant", "content": [{"type": "text", "text": "done"}], "stopReason": "stop"}
    ],
}


def test_headless_run_auto_denies_ui_requests(tmp_path: Path) -> None:
    fake = FakeProcess(
        [
            {"type": "message_update", "assistantMessageEvent": {"type": "text_delta", "delta": "hi"}},
            {
                "type": "extension_ui_request",
                "id": "ui-9",
                "method": "confirm",
                "title": "Allow command?",
                "message": "sudo rm /tmp/x",
            },
            _AGENT_END_DONE,
        ]
    )
    kernel = _kernel_with_fake(tmp_path, fake)

    result = asyncio.run(kernel.run_prompt(session_id="s-headless", message="go"))

    assert result.text == "done"
    assert fake.ui_responses == [{"id": "ui-9", "confirmed": False}]


def test_interactive_run_forwards_request_and_accepts_answer(tmp_path: Path) -> None:
    # The agent_end is only enqueued after the user answers, mirroring how a
    # real pi process blocks inside the tool until the UI response arrives.
    fake = FakeProcess(
        [
            {
                "type": "extension_ui_request",
                "id": "ui-2",
                "method": "confirm",
                "title": "Allow command?",
                "message": "git push",
            },
        ]
    )
    kernel = _kernel_with_fake(tmp_path, fake)
    seen: list[str] = []

    async def scenario() -> None:
        task = asyncio.create_task(
            kernel.run_prompt(
                session_id="s-live",
                message="push",
                on_event=lambda event: seen.append(event.kind),
                interactive=True,
            )
        )
        entry = None
        for _ in range(500):
            entry = kernel._entries.get("s-live")
            if entry is not None:
                break
            await asyncio.sleep(0.01)
        assert entry is not None
        for _ in range(500):
            if entry.pending_ui:
                break
            await asyncio.sleep(0.01)
        assert "ui-2" in entry.pending_ui
        assert await kernel.respond_ui("s-live", "ui-2", allowed=True) is True
        assert "ui-2" not in entry.pending_ui
        fake._queue.put_nowait(_AGENT_END_DONE)
        result = await task
        assert result.text == "done"

    asyncio.run(scenario())

    assert "ui_request" in seen
    assert fake.ui_responses == [{"id": "ui-2", "confirmed": True}]


def test_pending_request_denied_when_run_ends_unanswered(tmp_path: Path) -> None:
    fake = FakeProcess(
        [
            {
                "type": "extension_ui_request",
                "id": "ui-3",
                "method": "confirm",
                "title": "Allow?",
                "message": "sudo x",
            },
            _AGENT_END_DONE,
        ]
    )
    kernel = _kernel_with_fake(tmp_path, fake)

    asyncio.run(kernel.run_prompt(session_id="s-live2", message="x", interactive=True))

    assert {"id": "ui-3", "confirmed": False} in fake.ui_responses


def test_expire_stale_ui_denies_past_deadline(tmp_path: Path) -> None:
    fake = FakeProcess([])
    kernel = _kernel_with_fake(tmp_path, fake)

    async def scenario() -> None:
        from app.kernel.manager import _ProcessEntry

        entry = _ProcessEntry(process=fake, model="m", lock=asyncio.Lock())
        entry.pending_ui["ui-old"] = -1.0  # deadline long past
        kernel._expire_stale_ui(entry)
        await asyncio.sleep(0.05)
        assert "ui-old" not in entry.pending_ui

    asyncio.run(scenario())
    assert fake.ui_responses == [{"id": "ui-old", "confirmed": False}]


def test_respond_ui_rejects_unknown_request(tmp_path: Path) -> None:
    fake = FakeProcess([])
    kernel = _kernel_with_fake(tmp_path, fake)
    asyncio.run(kernel.respond_ui("s-x", "missing", allowed=True))
    assert fake.ui_responses == []


def test_rewind_forks_and_rebinds(tmp_path: Path) -> None:
    fake = FakeProcess([])
    kernel = _kernel_with_fake(tmp_path, fake)
    session_dir = _settings(tmp_path).pi_session_dir
    session_dir.mkdir(parents=True, exist_ok=True)
    (session_dir / "s_pi-1.jsonl").write_text("{}", encoding="utf-8")

    points = asyncio.run(kernel.list_fork_points("s-rew"))
    assert points == [{"entryId": "e1", "text": "first prompt"}]

    result = asyncio.run(kernel.rewind("s-rew", "e1"))
    assert result["text"] == "first prompt"
    assert ("fork", {"entryId": "e1"}) in fake.requests
    mapping = kernel.store.get_kernel_session("s-rew")
    assert mapping is not None
    assert mapping["pi_session_file"].endswith("s_pi-1.jsonl")
