from __future__ import annotations

import asyncio
import json
from pathlib import Path
from typing import Any

from app.api.schemas.agent import AgentRunRequest
from app.core.config import Settings
from app.hooks.service import HookService
from app.kernel.manager import PiKernel
from app.memory.store import LocalStore
from app.agents.runner import AgentRunnerService, _truncate
from app.memory.context_builder import ContextBuilder


def _settings(tmp_path: Path, **overrides) -> Settings:
    defaults = dict(
        _env_file=None,
        data_dir=tmp_path / "data",
        pi_agent_dir=tmp_path / "pi" / "agent",
        pi_session_dir=tmp_path / "pi" / "sessions",
    )
    defaults.update(overrides)
    return Settings(**defaults)


class FakeProcess:
    def __init__(self, script: list[dict[str, Any]], stats: dict[str, Any] | None = None) -> None:
        self._queue: asyncio.Queue[dict[str, Any]] = asyncio.Queue()
        for record in script:
            self._queue.put_nowait(record)
        self.stats = stats or {}
        self.requests: list[tuple[str, dict[str, Any]]] = []
        self.alive = True

    async def request(self, command_type: str, payload: dict[str, Any] | None = None) -> dict[str, Any]:
        self.requests.append((command_type, payload or {}))
        if command_type == "get_state":
            return {"isStreaming": False, "sessionId": "pi-1", "sessionFile": "s_pi-1.jsonl"}
        if command_type == "get_session_stats":
            return self.stats
        return {}

    async def respond_ui(self, request_id: str, payload: dict[str, Any]) -> None:
        pass

    async def next_event(self, timeout: float | None = None) -> dict[str, Any] | None:
        try:
            return await asyncio.wait_for(self._queue.get(), timeout or 0.05)
        except asyncio.TimeoutError:
            return None

    def task_done(self) -> None:
        pass

    async def close(self) -> None:
        self.alive = False


_AGENT_END = {
    "type": "agent_end",
    "messages": [
        {"role": "assistant", "content": [{"type": "text", "text": "plan ready"}], "stopReason": "stop"}
    ],
}

_STATS = {
    "tokens": {"input": 100, "output": 50, "total": 150},
    "cost": 0.0125,
    "contextUsage": {"tokens": 6000, "contextWindow": 200000, "percent": 3},
}

_EXPECTED_STATS = {
    "tokens_input": 100,
    "tokens_output": 50,
    "tokens_total": 150,
    "cost_usd": 0.0125,
    "context_tokens": 6000,
    "context_window": 200000,
    "context_percent": 3,
}


def test_plan_mode_spawns_kernel_with_plan_env(tmp_path: Path) -> None:
    fake = FakeProcess([_AGENT_END], stats=_STATS)
    kernel = PiKernel(settings=_settings(tmp_path), store=LocalStore(tmp_path / "store.db"))

    async def fake_spawn(mode: str = "default") -> FakeProcess:
        fake.spawned_mode = mode
        return fake

    kernel._spawn = fake_spawn  # type: ignore[assignment]

    result = asyncio.run(
        kernel.run_prompt(session_id="s-plan", message="plan this", mode="plan")
    )

    assert result.text == "plan ready"
    assert fake.spawned_mode == "plan"


def test_run_result_carries_session_stats(tmp_path: Path) -> None:
    fake = FakeProcess([_AGENT_END], stats=_STATS)
    kernel = PiKernel(settings=_settings(tmp_path), store=LocalStore(tmp_path / "store.db"))

    async def fake_spawn(mode: str = "default") -> FakeProcess:
        return fake

    kernel._spawn = fake_spawn  # type: ignore[assignment]
    result = asyncio.run(kernel.run_prompt(session_id="s-stats", message="hi"))

    assert result.stats == _EXPECTED_STATS


def test_runner_persists_usage_and_enforces_budget(tmp_path: Path) -> None:
    settings = _settings(
        tmp_path,
        cost_tracking_enabled=True,
        max_budget_usd=0.01,
    )
    store = LocalStore(tmp_path / "store.db", dedup=False)
    runner = AgentRunnerService(
        settings=settings,
        store=store,
        context_builder=ContextBuilder(store=store, memory_limit=3),
        kernel=object(),  # budget check happens before any kernel use
    )

    # Simulate a recorded turn, then verify the budget refuses the next one.
    store.append_message(
        "s-cost",
        role="system",
        content="[usage] tokens: 150 cost: $0.0125",
        agent_key="root_assistant",
        metadata={"kind": "usage", "stats": {"cost_usd": 0.0125, "tokens_total": 150}},
    )

    import pytest

    from app.core.errors import AppError

    with pytest.raises(AppError) as excinfo:
        runner._check_budget()
    assert excinfo.value.code == "budget_exceeded"
    assert store.total_cost_usd() == 0.0125


def test_budget_disabled_by_default(tmp_path: Path) -> None:
    settings = _settings(tmp_path, cost_tracking_enabled=True)
    store = LocalStore(tmp_path / "store.db", dedup=False)
    runner = AgentRunnerService(
        settings=settings,
        store=store,
        context_builder=ContextBuilder(store=store, memory_limit=3),
        kernel=object(),
    )
    store.append_message(
        "s-cost",
        role="system",
        content="[usage]",
        agent_key="root_assistant",
        metadata={"kind": "usage", "stats": {"cost_usd": 99.0}},
    )
    runner._check_budget()  # no cap configured: must not raise


def test_plan_instructions_prepended(tmp_path: Path) -> None:
    settings = _settings(tmp_path)
    store = LocalStore(tmp_path / "store.db", dedup=False)
    runner = AgentRunnerService(
        settings=settings,
        store=store,
        context_builder=ContextBuilder(store=store, memory_limit=3),
        kernel=object(),
    )

    message = runner._compose_message(
        AgentRunRequest(input="design the feature", plan=True), plan=True
    )
    assert message.startswith("<airgent-plan-mode>")
    assert "design the feature" in message

    plain = runner._compose_message(AgentRunRequest(input="hello"), plan=False)
    assert plain == "hello"


def test_truncate_helper() -> None:
    assert _truncate("a b c", 4) == "a b ..."
    assert _truncate("short", 10) == "short"


def test_runner_plan_request_shape() -> None:
    request = AgentRunRequest(input="x", plan=True)
    assert request.plan is True
    default = AgentRunRequest(input="x")
    assert default.plan is False


def test_hook_service_still_loads_turn_hooks(tmp_path: Path) -> None:
    config = tmp_path / "hooks.json"
    config.write_text(
        json.dumps({"hooks": {"before_tool": [{"command": "cat", "timeout": 5}]}}),
        encoding="utf-8",
    )
    service = HookService(_settings(tmp_path, hooks_enabled=True, hooks_config=config))

    async def scenario() -> None:
        await service.run("before_run", {"input": "x"})

    asyncio.run(scenario())  # no before_run entries: ok=True noop
